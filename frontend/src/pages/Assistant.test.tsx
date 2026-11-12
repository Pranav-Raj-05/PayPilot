import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async () => await import("@/test/api"));
vi.mock("qrcode.react", () => ({ QRCodeSVG: ({ value }: { value: string }) => <svg data-testid="qr" data-value={value} /> }));

import Assistant, { orderForDisplay } from "./Assistant";
import { buildSteps } from "@/components/assistant/ExecutionPlan";
import { ApiError, deferred, post, requests, serve, type Routes } from "@/test/api";
import { automation, dashboard, invoice, msg, paypal, preparedInvoice, profile, settings } from "@/test/fixtures";
import { renderPage, type InitialEntry } from "@/test/render";
import type { ChatMessage } from "@/types";

const reply = (text: string, extra: ChatMessage[] = []) => (body: { message: string }) => ({
  conversation_id: "conv-1",
  messages: [msg({ id: `u-${Math.random()}`, role: "user", content: body.message }), msg({ id: `a-${Math.random()}`, content: text }), ...extra],
});

const executed = (): ChatMessage => {
  const prepared = preparedInvoice();
  return { ...prepared, tool_result: { ...prepared.tool_result!, status: "executed", result: {
    invoice: { id: "inv-9", invoice_number: "INV-0009" }, payment_link: { url: "https://paypal.test/checkout/ORD9", provider: "paypal", reference: "ORD9" },
  } } };
};

const base = (extra: Routes = {}): Routes => ({
  "GET /api/auth/me": () => profile(),
  "GET /api/payments/paypal/status": () => paypal(),
  "GET /api/dashboard": () => dashboard(),
  "GET /api/automations": () => [automation()],
  "GET /api/ai/actions": () => [],
  "GET /api/settings": () => settings(),
  "GET /api/ai/conversations": () => [],
  "GET /api/invoices/inv-9": () => invoice({ id: "inv-9", status: "pending" }),
  "POST /api/ai/chat": reply("Here is your answer"),
  ...extra,
});
const open = (route: InitialEntry = "/") => renderPage(<Assistant />, { route });
const box = () => screen.getByLabelText("Message Copilot");
const chatCalls = () => requests(post, "/api/ai/chat");

beforeEach(() => serve(base()));

describe("conversation basics", () => {
  it("greets a new user with quick actions that send a real prompt", async () => {
    const { user } = open();
    expect(await screen.findByRole("heading", { name: "Meet your Financial AI Agent" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Generate $120 consulting link" }));
    await waitFor(() => expect(chatCalls()).toHaveLength(1));
    expect(chatCalls()[0]![1]).toEqual({ message: "Generate a $120 payment link for consulting", conversation_id: null });
    expect(await screen.findByText("Here is your answer")).toBeInTheDocument();
  });

  it("sends on Enter, clears the box, and labels messages with the user's name", async () => {
    const { user } = open();
    await user.type(box(), "Show my revenue{Enter}");
    expect(box()).toHaveValue("");
    expect(await screen.findByText("Here is your answer")).toBeInTheDocument();
    expect(screen.getByText("Show my revenue")).toBeInTheDocument();
    expect(await screen.findByText("Pranav Raj")).toBeInTheDocument();
    expect(screen.getByText("AI-generated suggestion")).toBeInTheDocument();
  });

  it("shows the user's message immediately and a thinking indicator until the reply arrives", async () => {
    const d = deferred();
    serve(base({ "POST /api/ai/chat": () => d.promise }));
    const { user } = open();
    await user.type(await screen.findByLabelText("Message Copilot"), "Analyse my month{Enter}");
    expect(screen.getByText("Analyse my month")).toBeInTheDocument();
    expect(await screen.findByText(/copilot is thinking/i)).toBeInTheDocument();
    d.resolve(reply("Done")({ message: "Analyse my month" }));
    expect(await screen.findByText("Done")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/copilot is thinking/i)).not.toBeInTheDocument());
    expect(screen.getAllByText("Analyse my month")).toHaveLength(1); // optimistic bubble replaced, not duplicated
  });

  it("keeps the conversation id for follow-up messages", async () => {
    const { user } = open();
    await user.type(await screen.findByLabelText("Message Copilot"), "first{Enter}");
    await screen.findByText("Here is your answer");
    await user.type(box(), "second{Enter}");
    await waitFor(() => expect(chatCalls()).toHaveLength(2));
    expect(chatCalls()[1]![1]).toEqual({ message: "second", conversation_id: "conv-1" });
  });

  it("does not send blank input, and Shift+Enter inserts a newline instead of sending", async () => {
    const { user } = open();
    await screen.findByLabelText("Message Copilot");
    expect(screen.getByRole("button", { name: "Run Prompt" })).toBeDisabled();
    await user.type(box(), "   {Enter}");
    expect(chatCalls()).toHaveLength(0);
    await user.clear(box());
    await user.type(box(), "line one{Shift>}{Enter}{/Shift}line two");
    expect(chatCalls()).toHaveLength(0);
    expect(box()).toHaveValue("line one\nline two");
  });

  it("starts a fresh conversation with New chat", async () => {
    const { user } = open();
    await user.type(await screen.findByLabelText("Message Copilot"), "hello{Enter}");
    await screen.findByText("Here is your answer");
    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(await screen.findByRole("heading", { name: "Meet your Financial AI Agent" })).toBeInTheDocument();
    await user.type(box(), "again{Enter}");
    await waitFor(() => expect(chatCalls()).toHaveLength(2));
    expect(chatCalls()[1]![1]).toMatchObject({ conversation_id: null });
  });

  it("sends a prompt handed over by another page exactly once", async () => {
    open({ pathname: "/assistant", state: { prompt: "Generate a payment link for $" } });
    await waitFor(() => expect(chatCalls()).toHaveLength(1));
    expect(chatCalls()[0]![1]).toMatchObject({ message: "Generate a payment link for $" });
    await screen.findByText("Here is your answer");
    expect(chatCalls()).toHaveLength(1);
  });
});

describe("errors and recovery", () => {
  it("shows the error, drops the unsent bubble, and Retry resends the same text", async () => {
    let fail = true;
    serve(base({ "POST /api/ai/chat": (b) => { if (fail) throw new ApiError("The AI provider is unavailable", 503); return reply("Recovered")(b); } }));
    const { user } = open();
    await user.type(await screen.findByLabelText("Message Copilot"), "What's pending?{Enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The AI provider is unavailable");
    expect(screen.queryByText("What's pending?")).not.toBeInTheDocument();
    fail = false;
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Recovered")).toBeInTheDocument();
    expect(chatCalls().map((c) => c[1].message)).toEqual(["What's pending?", "What's pending?"]);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("warns up front when the server has no AI provider configured", async () => {
    serve(base({ "GET /api/settings": () => settings({ configured: false }) }));
    open();
    expect(await screen.findByText(/AI provider isn't configured on the server/i)).toBeInTheDocument();
  });

  it("opens a past conversation from the recent list", async () => {
    serve(base({
      "GET /api/ai/conversations": () => [{ id: "old-1", title: "Overdue invoices", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" }],
      "GET /api/ai/conversations/old-1": () => [msg({ id: "o1", role: "user", content: "Who is overdue?" }), msg({ id: "o2", content: "Apex Cloud is 9 days late." })],
    }));
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Overdue invoices" }));
    expect(await screen.findByText("Apex Cloud is 9 days late.")).toBeInTheDocument();
    expect(screen.getByText("Who is overdue?")).toBeInTheDocument();
    await user.type(box(), "and the others?{Enter}");
    await waitFor(() => expect(chatCalls()[0]![1]).toMatchObject({ conversation_id: "old-1" }));
  });
});

describe("human approval of AI actions", () => {
  const withPrepared = (extra: Routes = {}) => serve(base({
    "POST /api/ai/chat": reply("I've prepared the invoice - please approve it.", [preparedInvoice()]),
    ...extra,
  }));
  const ask = async () => {
    const view = open();
    await view.user.type(await screen.findByLabelText("Message Copilot"), "Invoice Sarah $500{Enter}");
    await screen.findByText(/Staged Action: Create invoice for Sarah Johnson/);
    return view;
  };

  it("shows a preview and creates nothing until the user approves", async () => {
    withPrepared();
    await ask();
    expect(screen.getByText("Website development")).toBeInTheDocument();
    expect(screen.getByText("Prepared operation · awaiting approval")).toBeInTheDocument();
    expect(screen.getByText(/nothing is created until you approve/i)).toBeInTheDocument();
    expect(requests(post, "/api/ai/actions/tool1/confirm")).toHaveLength(0);
  });

  it("explains the assistant's reasoning before the approval card within a turn", async () => {
    withPrepared();
    await ask();
    const text = screen.getByText("I've prepared the invoice - please approve it.");
    const card = screen.getByText(/Staged Action/);
    expect(text.compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("confirming executes the action and shows the invoice and payment link", async () => {
    withPrepared({ "POST /api/ai/actions/tool1/confirm": () => executed() });
    const { user } = await ask();
    await user.click(screen.getByRole("button", { name: /Confirm & Create on PayPal/ }));
    expect(await screen.findByText("Invoice #INV-0009")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "https://paypal.test/checkout/ORD9" })).toHaveAttribute("href", "https://paypal.test/checkout/ORD9");
    expect(screen.getByTestId("qr")).toHaveAttribute("data-value", "https://paypal.test/checkout/ORD9");
    expect(screen.queryByRole("button", { name: /Confirm & Create/ })).not.toBeInTheDocument();
    expect(post).toHaveBeenCalledWith("/api/ai/actions/tool1/confirm");
  });

  it("cancelling discards the action and says nothing was created", async () => {
    const prepared = preparedInvoice();
    withPrepared({ "POST /api/ai/actions/tool1/cancel": () => ({ ...prepared, tool_result: { ...prepared.tool_result!, status: "cancelled" } }) });
    const { user } = await ask();
    await user.click(screen.getByRole("button", { name: /Cancel Action/ }));
    expect(await screen.findByText(/Cancelled: Create invoice for Sarah Johnson\. Nothing was created\./)).toBeInTheDocument();
    expect(requests(post, "/api/ai/actions/tool1/confirm")).toHaveLength(0);
  });

  it("keeps the card actionable and shows the error if confirmation fails", async () => {
    withPrepared({ "POST /api/ai/actions/tool1/confirm": () => { throw new ApiError("Action already processed", 409, "conflict"); } });
    const { user } = await ask();
    await user.click(screen.getByRole("button", { name: /Confirm & Create on PayPal/ }));
    expect(await screen.findByText("Action already processed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Confirm & Create on PayPal/ })).toBeEnabled();
  });

  it("disables the other buttons while a confirmation is in flight (no double-submit)", async () => {
    const d = deferred();
    withPrepared({ "POST /api/ai/actions/tool1/confirm": () => d.promise });
    const { user } = await ask();
    await user.click(screen.getByRole("button", { name: /Confirm & Create on PayPal/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: /Cancel Action/ })).toBeDisabled());
    expect(screen.getByRole("button", { name: /Edit Details/ })).toBeDisabled();
    d.resolve(executed());
    expect(await screen.findByText("Invoice #INV-0009")).toBeInTheDocument();
    expect(requests(post, "/api/ai/actions/tool1/confirm")).toHaveLength(1);
  });

  it("Edit Details puts a change request in the message box instead of executing", async () => {
    withPrepared();
    const { user } = await ask();
    await user.click(screen.getByRole("button", { name: /Edit Details/ }));
    expect(box()).toHaveValue("Change the prepared invoice for Sarah Johnson: ");
    expect(requests(post, "/api/ai/actions/tool1/confirm")).toHaveLength(0);
  });

  it("surfaces a failed execution to the user", async () => {
    const prepared = preparedInvoice();
    withPrepared({ "POST /api/ai/actions/tool1/confirm": () => ({ ...prepared, tool_result: { ...prepared.tool_result!, status: "failed", error: "PayPal rejected the order" } }) });
    const { user } = await ask();
    await user.click(screen.getByRole("button", { name: /Confirm & Create on PayPal/ }));
    const failed = await screen.findAllByText("PayPal rejected the order");
    expect(failed.length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /Confirm & Create/ })).not.toBeInTheDocument();
  });
});

describe("sidebar", () => {
  it("lists enabled automations, recent AI actions and integration status", async () => {
    const act = { ...preparedInvoice({ id: "act1" }), tool_result: { ...preparedInvoice().tool_result!, status: "executed" as const } };
    serve(base({ "GET /api/ai/actions": () => [act] }));
    open();
    expect(await screen.findByText("Overdue reminders")).toBeInTheDocument();
    expect(screen.getByText("1 running")).toBeInTheDocument();
    expect(await screen.findByText("Create invoice for Sarah Johnson")).toBeInTheDocument();
    expect(screen.getByText("LLM: gemini")).toBeInTheDocument();
    expect(screen.getByText("96.5%")).toBeInTheDocument();
  });

  it("says so when there is nothing to show", async () => {
    serve(base({ "GET /api/automations": () => [] }));
    open();
    expect(await screen.findByText("No AI actions yet.")).toBeInTheDocument();
    expect(screen.getByText(/no scheduled automations enabled/i)).toBeInTheDocument();
  });

  it("still lets the user chat if a sidebar request fails", async () => {
    serve(base({ "GET /api/automations": () => { throw new ApiError("down", 500); } }));
    const { user } = open();
    await user.type(await screen.findByLabelText("Message Copilot"), "hi{Enter}");
    expect(await screen.findByText("Here is your answer")).toBeInTheDocument();
  });
});

describe("orderForDisplay", () => {
  const u = (id: string) => msg({ id, role: "user", content: id });
  const a = (id: string) => msg({ id, role: "assistant", content: id });
  const t = (id: string) => msg({ id, role: "tool", content: "" });
  const ids = (m: ChatMessage[]) => m.map((x) => x.id);

  it("moves tool cards after the assistant text of the same turn", () => {
    expect(ids(orderForDisplay([u("u1"), t("t1"), a("a1")]))).toEqual(["u1", "a1", "t1"]);
  });
  it("does not reorder across turns", () => {
    expect(ids(orderForDisplay([u("u1"), t("t1"), a("a1"), u("u2"), t("t2"), a("a2")]))).toEqual(["u1", "a1", "t1", "u2", "a2", "t2"]);
  });
  it("keeps tool cards that end a conversation and preserves their relative order", () => {
    expect(ids(orderForDisplay([u("u1"), t("t1"), t("t2")]))).toEqual(["u1", "t1", "t2"]);
    expect(orderForDisplay([])).toEqual([]);
  });
});

describe("buildSteps (execution timeline)", () => {
  const states = (m: ChatMessage, invoiceStatus?: string) => buildSteps(m, invoiceStatus).map((s) => s.state);

  it("waits for approval while prepared", () => {
    expect(states(preparedInvoice())).toEqual(["done", "done", "done", "action", "queued", "pending", "pending"]);
  });
  it("marks everything after approval as skipped when cancelled", () => {
    const p = preparedInvoice();
    expect(states({ ...p, tool_result: { ...p.tool_result!, status: "cancelled" } })).toEqual(["done", "done", "done", "skipped", "skipped", "skipped", "skipped"]);
  });
  it("shows the failure on the order step and skips the rest", () => {
    const p = preparedInvoice();
    const steps = buildSteps({ ...p, tool_result: { ...p.tool_result!, status: "failed", error: "Provider down" } });
    expect(steps[4]).toMatchObject({ state: "failed", detail: "Provider down" });
    expect(steps[5]!.state).toBe("skipped");
  });
  it("only reports the payment verified once the invoice is really paid", () => {
    expect(states(executed(), "pending").slice(4)).toEqual(["done", "action", "pending"]);
    expect(states(executed(), "paid").slice(4)).toEqual(["done", "done", "done"]);
  });
});
