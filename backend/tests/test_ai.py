from datetime import datetime

import pytest

from app.ai.providers import LLMResult, ToolCall, clean_schema
from app.ai.tools import REGISTRY, tool_declarations
from app.api.deps import get_llm


class ScriptedLLM:
    """Replays a fixed list of LLM turns and records what it was given."""

    def __init__(self, turns):
        self.turns, self.seen = list(turns), []

    def generate(self, system, messages, tools):
        self.seen.append((system, [dict(m) for m in messages], tools))
        return self.turns.pop(0)


@pytest.fixture
def llm(client):
    holder = {}

    def install(*turns):
        holder["llm"] = ScriptedLLM(turns)
        client.app.dependency_overrides[get_llm] = lambda: holder["llm"]
        return holder["llm"]
    yield install
    client.app.dependency_overrides.clear()


INVOICE_ARGS = {"customer_name": "Sarah Johnson", "amount": 500, "currency": "USD", "description": "website development", "due_in_days": 14}


def prepare_turns():
    return [LLMResult(tool_calls=[ToolCall("1", "create_invoice", INVOICE_ARGS)]),
            LLMResult(text="I've prepared the invoice - please approve it.")]


def test_tool_registry_has_required_tools_and_clean_schemas():
    required = {"create_invoice", "get_invoice", "update_invoice", "list_customers", "create_customer", "generate_payment_link", "create_paypal_order",
                "get_transaction", "list_transactions", "get_revenue_summary", "get_pending_payments", "analyse_business_performance", "suggest_payment_reminder"}
    assert required <= set(REGISTRY)
    for t in tool_declarations():
        text = str(t["parameters"])
        assert "$ref" not in text and "anyOf" not in text and "title" not in text


def test_clean_schema_inlines_refs():
    out = clean_schema({"$defs": {"X": {"type": "object", "title": "X", "properties": {"a": {"type": "string"}}}},
                        "properties": {"x": {"anyOf": [{"$ref": "#/$defs/X"}, {"type": "null"}]}}, "type": "object", "title": "T"})
    assert out == {"properties": {"x": {"type": "object", "properties": {"a": {"type": "string"}}}}, "type": "object"}


def test_clean_schema_strips_exclusive_bounds_that_gemini_rejects():
    out = clean_schema({"type": "object", "properties": {"amount": {"type": "number", "exclusiveMinimum": 0.0, "exclusiveMaximum": 9, "minimum": 1}}})
    assert out == {"type": "object", "properties": {"amount": {"type": "number", "minimum": 1}}}
    for t in tool_declarations():  # every declaration sent to the provider
        assert "exclusiveMinimum" not in str(t["parameters"]) and "exclusiveMaximum" not in str(t["parameters"])


def test_tool_inputs_still_enforce_the_bounds_that_were_stripped_from_the_schema(user, llm):
    """The model is no longer told amount > 0 in the schema, so the server-side validation must still refuse it."""
    llm(LLMResult(tool_calls=[ToolCall("1", "create_invoice", {**INVOICE_ARGS, "amount": 0})]), LLMResult(text="done"))
    r = user.post("/api/ai/chat", json={"message": "invoice for 0 dollars"})
    assert r.status_code == 200
    assert user.get("/api/invoices").json()["total"] == 0  # nothing created
    assert not [a for a in user.get("/api/ai/actions").json() if (a.get("tool_result") or {}).get("status") == "prepared"]  # and nothing even prepared


@pytest.mark.parametrize("status,body,expected", [
    (400, {"error": {"details": [{"reason": "API_KEY_INVALID"}]}}, "GEMINI_API_KEY"),
    (403, {"error": {"message": "denied"}}, "GEMINI_API_KEY"),
    (404, {"error": {"message": "model gone"}}, "GEMINI_MODEL"),
    (429, {"error": {}}, "quota"),
    (503, {"error": {}}, "overloaded"),
    (400, {"error": {"message": "Invalid JSON payload"}}, "The AI provider returned an error"),
    (500, {"error": {}}, "The AI provider returned an error"),
])
def test_gemini_errors_give_a_specific_message_and_never_echo_the_key(status, body, expected):
    import httpx

    from app.ai.providers import GeminiProvider
    from app.core.config import Settings
    from app.core.errors import ProviderError

    secret = "AIza-secret-value-that-must-never-appear-0000"
    http = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, json=body)))
    provider = GeminiProvider(Settings(gemini_api_key=secret, gemini_model="m"), http=http)
    with pytest.raises(ProviderError) as e:
        provider.generate("sys", [{"role": "user", "content": "hi"}], [])
    assert expected in e.value.message and secret not in e.value.message and secret not in str(e.value.details)
    assert e.value.details == {"status": status}


def test_the_default_gemini_model_is_not_the_retired_one():
    from app.core.config import Settings

    assert Settings().gemini_model != "gemini-2.5-flash"


def test_chat_requires_auth_and_configured_llm(client, user):
    assert client.post("/api/ai/chat", json={"message": "hi"}).status_code == 401
    r = user.post("/api/ai/chat", json={"message": "hi"})  # no GEMINI_API_KEY in tests
    assert r.status_code == 503 and r.json()["error"]["code"] == "not_configured"


def test_sensitive_tool_only_prepares_nothing_created(user, llm):
    llm(*prepare_turns())
    r = user.post("/api/ai/chat", json={"message": "Create a $500 invoice for Sarah Johnson for website development"}).json()
    roles = [m["role"] for m in r["messages"]]
    assert roles == ["user", "tool", "assistant"]
    action = r["messages"][1]
    assert action["tool_name"] == "create_invoice" and action["tool_result"]["status"] == "prepared"
    prev = action["tool_result"]["preview"]
    assert prev["total"] == 500 and prev["customer"]["existing"] is False and prev["provider"] == "paypal"
    assert user.get("/api/invoices").json()["total"] == 0 and user.get("/api/customers").json()["total"] == 0  # nothing executed
    assert user.get("/api/transactions").json()["total"] == 0


def test_approval_creates_invoice_customer_and_paypal_link(user, llm, providers):
    llm(*prepare_turns())
    msgs = user.post("/api/ai/chat", json={"message": "invoice Sarah"}).json()["messages"]
    action_id = msgs[1]["id"]
    done = user.post(f"/api/ai/actions/{action_id}/confirm").json()
    res = done["tool_result"]
    assert res["status"] == "executed"
    assert res["result"]["payment_link"]["provider"] == "paypal" and res["result"]["payment_link"]["url"].startswith("https://www.sandbox.paypal.com")
    assert res["result"]["invoice"]["status"] == "pending" and res["result"]["invoice"]["total_amount"] == 500
    assert user.get("/api/customers").json()["total"] == 1 and user.get("/api/invoices").json()["total"] == 1
    again = user.post(f"/api/ai/actions/{action_id}/confirm").json()  # double click is harmless
    assert again["tool_result"]["status"] == "executed" and user.get("/api/invoices").json()["total"] == 1
    assert user.post(f"/api/ai/actions/{action_id}/cancel").status_code == 409


def test_cancel_prevents_execution(user, llm):
    llm(*prepare_turns())
    aid = user.post("/api/ai/chat", json={"message": "x"}).json()["messages"][1]["id"]
    assert user.post(f"/api/ai/actions/{aid}/cancel").json()["tool_result"]["status"] == "cancelled"
    assert user.post(f"/api/ai/actions/{aid}/confirm").status_code == 409
    assert user.get("/api/invoices").json()["total"] == 0


def test_cannot_confirm_another_users_action(user, other, llm):
    llm(*prepare_turns())
    aid = user.post("/api/ai/chat", json={"message": "x"}).json()["messages"][1]["id"]
    assert other.post(f"/api/ai/actions/{aid}/confirm").status_code == 404
    assert other.post(f"/api/ai/actions/{aid}/cancel").status_code == 404
    assert user.get("/api/invoices").json()["total"] == 0


def test_provider_failure_keeps_invoice_draft_and_reports(user, llm, providers):
    llm(*prepare_turns())
    aid = user.post("/api/ai/chat", json={"message": "x"}).json()["messages"][1]["id"]
    providers.orders.clear()
    from app.api import deps
    import httpx
    from app.payments.paypal.client import PayPalClient
    from app.core.config import get_settings
    deps._paypal = PayPalClient(get_settings(), http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500, json={}))))
    res = user.post(f"/api/ai/actions/{aid}/confirm").json()["tool_result"]
    assert res["status"] == "executed" and res["result"]["payment_link"] is None and "failed" in res["result"]["warning"]
    assert res["result"]["invoice"]["status"] == "draft"


def test_invalid_tool_args_are_returned_to_model_not_executed(user, llm):
    bad = {**INVOICE_ARGS, "amount": -5}
    fake = llm(LLMResult(tool_calls=[ToolCall("1", "create_invoice", bad)]), LLMResult(text="What amount should I use?"))
    r = user.post("/api/ai/chat", json={"message": "x"}).json()
    assert [m["role"] for m in r["messages"]] == ["user", "assistant"]
    tool_msg = fake.seen[1][1][-1]
    assert tool_msg["role"] == "tool" and "Invalid arguments" in tool_msg["content"]


def test_unknown_tool_and_unsupported_provider_combo(user, llm):
    fake = llm(LLMResult(tool_calls=[ToolCall("1", "drop_database", {}), ToolCall("2", "create_invoice", {**INVOICE_ARGS, "provider": "razorpay"})]),
               LLMResult(text="ok"))
    r = user.post("/api/ai/chat", json={"message": "x"}).json()
    assert [m["role"] for m in r["messages"]] == ["user", "assistant"]
    contents = [m["content"] for m in fake.seen[1][1] if m["role"] == "tool"]
    assert "Unknown tool" in contents[0] and "INR" in contents[1]


def seed_payments(user, providers, n=2):
    c = user.post("/api/customers", json={"name": "Marcus Vance"}).json()
    for i in range(n):
        inv = user.post("/api/invoices", json={"customer_id": c["id"], "items": [{"description": "x", "unit_price": 100 * (i + 1)}]}).json()
        o = user.post("/api/payments/paypal/create-order", json={"invoice_id": inv["id"]}).json()
        user.post("/api/payments/paypal/capture-order", json={"order_id": o["order_id"]})


def test_read_tools_return_real_data_and_are_user_scoped(user, other, llm, providers):
    seed_payments(user, providers)
    llm(LLMResult(tool_calls=[ToolCall("1", "get_revenue_summary", {"period": "last_30_days"}), ToolCall("2", "list_transactions", {"status": "completed"})]),
        LLMResult(text="You made $300."))
    r = user.post("/api/ai/chat", json={"message": "revenue?"}).json()
    reads = [m for m in r["messages"] if m["role"] == "tool"]
    rev = next(m for m in reads if m["tool_name"] == "get_revenue_summary")["tool_result"]["result"]
    assert rev["revenue"] == 300 and rev["payments"] == 2 and rev["by_provider"] == {"paypal": 300}
    assert next(m for m in reads if m["tool_name"] == "list_transactions")["tool_result"]["result"]["total"] == 2
    llm(LLMResult(tool_calls=[ToolCall("1", "get_revenue_summary", {"period": "last_30_days"})]), LLMResult(text="none"))
    theirs = other.post("/api/ai/chat", json={"message": "revenue?"}).json()["messages"]
    assert theirs[1]["tool_result"]["result"]["revenue"] == 0


def test_pending_and_reminder_tools(user, llm):
    from datetime import date, timedelta
    c = user.post("/api/customers", json={"name": "Late Payer", "email": "l@x.io"}).json()
    inv = user.post("/api/invoices", json={"customer_id": c["id"], "due_date": str(date.today() - timedelta(days=5)), "items": [{"description": "x", "unit_price": 80}]}).json()
    user.patch(f"/api/invoices/{inv['id']}", json={"status": "pending"})
    llm(LLMResult(tool_calls=[ToolCall("1", "get_pending_payments", {}), ToolCall("2", "suggest_payment_reminder", {})]), LLMResult(text="1 overdue"))
    msgs = user.post("/api/ai/chat", json={"message": "overdue?"}).json()["messages"]
    pend = next(m for m in msgs if m["tool_name"] == "get_pending_payments")["tool_result"]["result"]
    assert pend["overdue_count"] == 1 and pend["invoices"][0]["days_overdue"] == 5
    drafts = next(m for m in msgs if m["tool_name"] == "suggest_payment_reminder")["tool_result"]["result"]["drafts"]
    assert drafts[0]["customer"] == "Late Payer" and "reminder" in drafts[0]["message"]


def test_conversation_history_persisted_and_scoped(user, other, llm):
    llm(LLMResult(text="Hello!"), LLMResult(text="Again!"))
    r1 = user.post("/api/ai/chat", json={"message": "hi"}).json()
    cid = r1["conversation_id"]
    fake = llm(LLMResult(text="Again!"))
    user.post("/api/ai/chat", json={"message": "second", "conversation_id": cid})
    assert [m["content"] for m in fake.seen[0][1]] == ["hi", "Hello!", "second"]  # earlier turns sent as context
    assert [m["role"] for m in user.get(f"/api/ai/conversations/{cid}").json()] == ["user", "assistant", "user", "assistant"]
    assert len(user.get("/api/ai/conversations").json()) == 1
    assert other.get(f"/api/ai/conversations/{cid}").status_code == 404
    assert other.post("/api/ai/chat", json={"message": "x", "conversation_id": cid}).status_code == 404
    assert user.delete(f"/api/ai/conversations/{cid}").status_code == 204


def test_recent_actions_endpoint(user, other, llm):
    llm(*prepare_turns())
    user.post("/api/ai/chat", json={"message": "x"})
    acts = user.get("/api/ai/actions").json()
    assert len(acts) == 1 and acts[0]["tool_name"] == "create_invoice" and acts[0]["tool_result"]["status"] == "prepared"
    assert other.get("/api/ai/actions").json() == []


# --- message ordering: created_at alone is not a total order -------------------------------------------------------
# Messages of one turn are inserted back to back; on a coarse clock (Windows ~0.4-15 ms) or a very fast machine they
# share a timestamp, and `ORDER BY created_at` then returns them in an undefined order.

@pytest.fixture
def clock(monkeypatch):
    """Control the clock used for message timestamps (both the agent and the column default)."""
    from datetime import datetime, timedelta, timezone

    from app.models import AIMessage

    state = {"now": datetime(2026, 1, 1, tzinfo=timezone.utc), "step": timedelta(0)}

    def now(_ctx=None):
        state["now"] += state["step"]
        return state["now"]

    monkeypatch.setattr("app.ai.agent.utcnow", now)
    monkeypatch.setattr(AIMessage.__table__.c.created_at.default, "arg", now)
    return state


def _chat_turns(user, llm, clock, rounds=6):
    """Alternate plain replies and tool-using turns so a conversation has many messages."""
    expected, cid = [], None
    for i in range(rounds):
        if i % 2:
            llm(*prepare_turns())
            expected += ["user", "tool", "assistant"]
        else:
            llm(LLMResult(text=f"reply {i}"))
            expected += ["user", "assistant"]
        body = {"message": f"message {i}"} | ({"conversation_id": cid} if cid else {})
        cid = user.post("/api/ai/chat", json=body).json()["conversation_id"]
    return cid, expected


@pytest.mark.parametrize("step_us", [0, -1000])  # clock frozen, and clock jumping backwards
def test_messages_keep_insertion_order_on_a_stalled_or_backwards_clock(user, llm, clock, step_us):
    from datetime import timedelta

    clock["step"] = timedelta(microseconds=step_us)
    for _ in range(3):  # several conversations (stays under the 20/min chat rate limit)
        cid, expected = _chat_turns(user, llm, clock, rounds=5)
        msgs = user.get(f"/api/ai/conversations/{cid}").json()
        assert [m["role"] for m in msgs] == expected
        # compare real datetimes, not strings: a timestamp with zero microseconds prints without a fractional part and mis-sorts as text
        stamps = [datetime.fromisoformat(m["created_at"].replace("Z", "+00:00")) for m in msgs]
        assert stamps == sorted(set(stamps)), "created_at must be strictly increasing within a conversation"


def test_llm_history_is_in_conversation_order_on_a_stalled_clock(user, llm, clock):
    llm(LLMResult(text="one"), LLMResult(text="two"))
    cid = user.post("/api/ai/chat", json={"message": "a"}).json()["conversation_id"]
    user.post("/api/ai/chat", json={"message": "b", "conversation_id": cid})
    fake = llm(LLMResult(text="three"))
    user.post("/api/ai/chat", json={"message": "c", "conversation_id": cid})
    assert [m["content"] for m in fake.seen[0][1]] == ["a", "one", "b", "two", "c"]
