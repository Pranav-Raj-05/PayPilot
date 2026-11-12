"""PayPilot Copilot: LLM tool-calling loop.
Message lifecycle for sensitive actions (stored in ai_messages.tool_result.status):
  prepared -> (user approves) executed | failed      or      prepared -> cancelled
The model can only ever reach `prepared`; `executed` happens through the /actions/{id}/confirm endpoint."""
import json
import logging
from datetime import date, datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.providers import LLMProvider, ToolCall
from app.ai.tools import REGISTRY, ToolContext, tool_declarations
from app.core.errors import AppError, Conflict, NotFound
from app.models import AIConversation, AIMessage, utcnow
from app.services.common import as_uuid, audit, aware

log = logging.getLogger("paypilot.agent")
MAX_STEPS = 5
HISTORY = 20


def system_prompt(ctx: ToolContext) -> str:
    return f"""You are PayPilot Copilot, an AI financial operations assistant for freelancers and small businesses, built on PayPal (primary) and Razorpay (INR).
Today is {date.today().isoformat()}. The user's default currency is {ctx.default_currency}.

Rules:
- Use tools for every fact about invoices, customers, payments or revenue. Never invent numbers, ids, links or statuses.
- To create invoices, payment links, PayPal orders, customers, invoice updates or reminders you call the matching tool; that only PREPARES the operation and shows the user a preview card.
  The user must approve it in the UI. After preparing, say it is awaiting their approval - never say it was created or sent.
- If required information is missing (customer name, amount, what it is for) ask one concise question instead of guessing. Default currency to {ctx.default_currency} only if the user gave no currency; "$" means USD, "₹" means INR.
- Prefer PayPal. Use Razorpay only for INR when the user asks for it (UPI/India).
- Answer concisely, with the key numbers first. Use short bullet lists. Do not repeat data already shown in a preview card.
- Reminders are drafts; email/SMS delivery is not configured, so never claim a reminder was emailed."""


class Agent:
    def __init__(self, db: Session, ctx: ToolContext, llm: LLMProvider):
        self.db, self.ctx, self.llm = db, ctx, llm

    # ------------------------------------------------------------------ conversations
    def conversation(self, conversation_id, first_message: str) -> AIConversation:
        uid = as_uuid(self.ctx.user_id)
        if conversation_id:
            c = self.db.scalars(select(AIConversation).where(AIConversation.id == as_uuid(conversation_id), AIConversation.user_id == uid)).first()
            if not c:
                raise NotFound("Conversation not found")
            return c
        c = AIConversation(user_id=uid, title=first_message.strip()[:60] or "New conversation")
        self.db.add(c)
        self.db.flush()
        return c

    def _history(self, conv: AIConversation) -> list[dict]:
        rows = self.db.scalars(select(AIMessage).where(AIMessage.conversation_id == conv.id).order_by(AIMessage.created_at.desc()).limit(HISTORY)).all()
        out: list[dict] = []
        for m in reversed(rows):
            if m.role in ("user", "assistant") and m.content:
                out.append({"role": m.role, "content": m.content})
            elif m.role == "tool" and m.tool_result and m.tool_result.get("status") in ("executed", "cancelled", "failed"):
                out.append({"role": "user", "content": f"[system note: the user {m.tool_result['status']} the prepared action '{m.tool_name}']"})
        # providers require the first turn to be a user turn
        while out and out[0]["role"] != "user":
            out.pop(0)
        return out

    def _next_timestamp(self, conv) -> datetime:
        """A timestamp strictly after the conversation's latest message.

        Messages are read back with `ORDER BY created_at`, but the messages of one turn are inserted microseconds apart and a coarse
        clock (Windows, or a fast machine) gives them identical timestamps - the order of ties is undefined on SQLite and PostgreSQL,
        which scrambled history sent to the LLM and shown in the UI. Making created_at strictly increasing per conversation (also
        when the clock stalls or steps backwards) makes it a total order, with no schema change.
        """
        now = utcnow()
        last = aware(self.db.scalar(select(func.max(AIMessage.created_at)).where(AIMessage.conversation_id == conv.id)))
        return last + timedelta(microseconds=1) if last is not None and now <= last else now

    def _store(self, conv, role, content="", tool_name=None, tool_result=None) -> AIMessage:
        m = AIMessage(conversation_id=conv.id, role=role, content=content, tool_name=tool_name, tool_result=tool_result,
                      created_at=self._next_timestamp(conv))
        self.db.add(m)
        self.db.flush()
        conv.updated_at = utcnow()
        return m

    # ------------------------------------------------------------------ chat
    def chat(self, conv: AIConversation, text: str) -> list[AIMessage]:
        history = self._history(conv)
        new: list[AIMessage] = [self._store(conv, "user", text)]
        messages = history + [{"role": "user", "content": text}]
        tools = tool_declarations()
        system = system_prompt(self.ctx)
        for _ in range(MAX_STEPS):
            result = self.llm.generate(system, messages, tools)
            if not result.tool_calls:
                new.append(self._store(conv, "assistant", result.text or "I'm not sure how to help with that. Could you rephrase?"))
                return new
            if result.text:
                new.append(self._store(conv, "assistant", result.text))
            messages.append({"role": "assistant", "content": result.text, "tool_calls": result.tool_calls, "raw": result.raw})
            for call in result.tool_calls:
                payload, stored = self._run_tool(conv, call)
                if stored:
                    new.append(stored)
                messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": json.dumps(payload, default=str)})
        new.append(self._store(conv, "assistant", "I couldn't finish that in a reasonable number of steps. Please try a simpler request."))
        return new

    def _run_tool(self, conv, call: ToolCall) -> tuple[dict, AIMessage | None]:
        spec = REGISTRY.get(call.name)
        if spec is None:
            return {"error": f"Unknown tool {call.name}"}, None
        try:
            args = spec.input_model.model_validate(call.args)
        except ValidationError as exc:
            return {"error": "Invalid arguments", "details": [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]}, None
        try:
            if spec.sensitive:
                preview = spec.prepare(self.ctx, args) if spec.prepare else {}
                audit(self.db, self.ctx.user_id, f"ai.prepare.{spec.name}", detail={"args": args.model_dump(mode="json")})
                stored = self._store(conv, "tool", "", spec.name, {"status": "prepared", "args": args.model_dump(mode="json"), "preview": preview})
                return {"status": "prepared_awaiting_user_approval", "message": "Preview shown to the user. NOT executed yet.", "preview": preview}, stored
            data = spec.handler(self.ctx, args)
            audit(self.db, self.ctx.user_id, f"ai.read.{spec.name}")
            stored = self._store(conv, "tool", "", spec.name, {"status": "read", "args": args.model_dump(mode="json"), "result": data})
            return data, stored
        except AppError as exc:
            return {"error": exc.message, "code": exc.code}, None

    # ------------------------------------------------------------------ approval
    def _action(self, message_id) -> AIMessage:
        m = self.db.scalars(
            select(AIMessage).join(AIConversation, AIConversation.id == AIMessage.conversation_id)
            .where(AIMessage.id == as_uuid(message_id), AIConversation.user_id == as_uuid(self.ctx.user_id), AIMessage.role == "tool").with_for_update(of=AIMessage)
        ).first()
        if not m or not m.tool_result or m.tool_result.get("status") not in ("prepared", "approved", "executed", "failed", "cancelled"):
            raise NotFound("Action not found")
        return m

    def confirm(self, message_id) -> AIMessage:
        m = self._action(message_id)
        status = m.tool_result["status"]
        if status != "prepared":
            if status == "executed":
                return m  # idempotent double-click / retry
            raise Conflict(f"Action is already {status}")
        spec = REGISTRY[m.tool_name]
        args = spec.input_model.model_validate(m.tool_result["args"])
        base = {**m.tool_result, "approved_at": datetime.now(timezone.utc).isoformat()}
        m.tool_result = {**base, "status": "approved"}
        self.db.flush()
        try:
            result = spec.handler(self.ctx, args)
            m.tool_result = {**base, "status": "executed", "result": result, "executed_at": datetime.now(timezone.utc).isoformat()}
            audit(self.db, self.ctx.user_id, f"ai.execute.{spec.name}", detail={"message_id": str(m.id)})
        except AppError as exc:
            m.tool_result = {**base, "status": "failed", "error": exc.message}
        except Exception:
            log.exception("action execution failed")
            m.tool_result = {**base, "status": "failed", "error": "Unexpected error while executing the action"}
        return m

    def cancel(self, message_id) -> AIMessage:
        m = self._action(message_id)
        if m.tool_result["status"] != "prepared":
            raise Conflict(f"Action is already {m.tool_result['status']}")
        m.tool_result = {**m.tool_result, "status": "cancelled"}
        audit(self.db, self.ctx.user_id, f"ai.cancel.{m.tool_name}")
        return m
