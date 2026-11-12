from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.agent import Agent
from app.ai.providers import LLMProvider
from app.ai.tools import ToolContext
from app.api.deps import get_llm, get_tool_context
from app.core.db import get_db
from app.core.errors import NotFound
from app.core.ratelimit import ip_rate_limit, user_rate_limit
from app.core.security import CurrentUser, get_current_user
from app.models import AIConversation, AIMessage
from app.schemas.common import ORM
import uuid
from datetime import datetime

router = APIRouter(prefix="/api/ai", tags=["ai"])


class ChatIn(BaseModel):
    conversation_id: uuid.UUID | None = None
    message: str = Field(min_length=1, max_length=4000)


class MessageOut(ORM):
    id: uuid.UUID
    role: str
    content: str
    tool_name: str | None
    tool_result: dict | None
    created_at: datetime


class ConversationOut(ORM):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


@router.post("/chat", dependencies=[Depends(user_rate_limit("ai-chat", 20))])
def chat(body: ChatIn, ctx: ToolContext = Depends(get_tool_context), llm: LLMProvider = Depends(get_llm)):
    agent = Agent(ctx.db, ctx, llm)
    conv = agent.conversation(body.conversation_id, body.message)
    new = agent.chat(conv, body.message)
    return {"conversation_id": str(conv.id), "messages": [MessageOut.model_validate(m).model_dump(mode="json") for m in new]}


@router.get("/conversations", response_model=list[ConversationOut])
def conversations(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.scalars(select(AIConversation).where(AIConversation.user_id == uuid.UUID(user.id)).order_by(AIConversation.updated_at.desc()).limit(50)).all()


@router.get("/conversations/{conversation_id}", response_model=list[MessageOut])
def conversation_messages(conversation_id: uuid.UUID, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    conv = db.scalars(select(AIConversation).where(AIConversation.id == conversation_id, AIConversation.user_id == uuid.UUID(user.id))).first()
    if not conv:
        raise NotFound("Conversation not found")
    return db.scalars(select(AIMessage).where(AIMessage.conversation_id == conv.id).order_by(AIMessage.created_at)).all()


@router.delete("/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: uuid.UUID, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    conv = db.scalars(select(AIConversation).where(AIConversation.id == conversation_id, AIConversation.user_id == uuid.UUID(user.id))).first()
    if not conv:
        raise NotFound("Conversation not found")
    for m in db.scalars(select(AIMessage).where(AIMessage.conversation_id == conv.id)).all():
        db.delete(m)
    db.delete(conv)


@router.get("/actions")
def recent_actions(limit: int = 8, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Most recent prepared/executed AI operations (for the assistant sidebar)."""
    rows = db.scalars(
        select(AIMessage).join(AIConversation, AIConversation.id == AIMessage.conversation_id)
        .where(AIConversation.user_id == uuid.UUID(user.id), AIMessage.role == "tool")
        .order_by(AIMessage.created_at.desc()).limit(min(max(limit, 1), 30) * 3)).all()
    out = [MessageOut.model_validate(m).model_dump(mode="json") for m in rows if (m.tool_result or {}).get("status") in ("prepared", "approved", "executed", "failed", "cancelled")]
    return out[:limit]


def _agent(ctx: ToolContext) -> Agent:
    return Agent(ctx.db, ctx, llm=None)  # type: ignore[arg-type]


@router.post("/actions/{message_id}/confirm", dependencies=[Depends(user_rate_limit("ai-confirm", 20))])
def confirm_action(message_id: uuid.UUID, ctx: ToolContext = Depends(get_tool_context)):
    return MessageOut.model_validate(_agent(ctx).confirm(message_id)).model_dump(mode="json")


@router.post("/actions/{message_id}/cancel")
def cancel_action(message_id: uuid.UUID, ctx: ToolContext = Depends(get_tool_context)):
    return MessageOut.model_validate(_agent(ctx).cancel(message_id)).model_dump(mode="json")
