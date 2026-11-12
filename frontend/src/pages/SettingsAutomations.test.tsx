import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ signOut: vi.fn(), user: { email: "me@example.com" } }));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/lib/api", async () => await import("@/test/api"));

import Automations from "./Automations";
import Settings from "./Settings";
import { ApiError, deferred, patch, post, serve } from "@/test/api";
import { automation, paypal, settings } from "@/test/fixtures";
import { renderPage } from "@/test/render";
import type { Notification, Settings as SettingsType } from "@/types";

describe("Settings page", () => {
  let current: SettingsType;
  const routes = () => ({
    "GET /api/settings": () => current,
    "PATCH /api/settings/profile": (body: object) => ({ ...current.profile, ...body }),
  });
  const open = () => renderPage(<Settings />, { path: "/" });

  beforeEach(() => { current = settings(); serve(routes()); });

  it("shows a placeholder while loading", async () => {
    const d = deferred();
    serve({ "GET /api/settings": () => d.promise });
    open();
    expect(document.querySelector(".animate-pulse")).toBeInTheDocument();
    expect(screen.queryByText("Profile")).not.toBeInTheDocument();
    d.resolve(current);
    expect(await screen.findByRole("heading", { name: "Profile" })).toBeInTheDocument();
  });

  it("shows the API error and recovers on Retry", async () => {
    let healthy = false;
    serve({ "GET /api/settings": () => { if (!healthy) throw new ApiError("Settings unavailable", 500); return current; } });
    const { user } = open();
    expect(await screen.findByRole("alert")).toHaveTextContent("Settings unavailable");
    healthy = true;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("heading", { name: "Profile" })).toBeInTheDocument();
  });

  it("prefills the profile form and shows the signed-in email", async () => {
    open();
    await waitFor(() => expect(screen.getByLabelText("Full name")).toHaveValue("Pranav Raj"));
    expect(screen.getByLabelText("Business name")).toHaveValue("Acme");
    expect(screen.getByLabelText("Default currency")).toHaveValue("USD");
    expect(screen.getByText("me@example.com")).toBeInTheDocument();
  });

  it("saves edited profile values", async () => {
    const { user } = open();
    await waitFor(() => expect(screen.getByLabelText("Full name")).toHaveValue("Pranav Raj"));
    await user.clear(screen.getByLabelText("Business name"));
    await user.type(screen.getByLabelText("Business name"), "Globex");
    await user.selectOptions(screen.getByLabelText("Default currency"), "INR");
    await user.click(screen.getByRole("button", { name: "Save profile" }));
    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(patch).toHaveBeenCalledWith("/api/settings/profile", { full_name: "Pranav Raj", business_name: "Globex", default_currency: "INR" });
    expect(await screen.findByText("Profile saved")).toBeInTheDocument();
  });

  it("reports a failed save", async () => {
    serve({ ...routes(), "PATCH /api/settings/profile": () => { throw new ApiError("Unsupported currency", 422); } });
    const { user } = open();
    await waitFor(() => expect(screen.getByLabelText("Full name")).toHaveValue("Pranav Raj"));
    await user.click(screen.getByRole("button", { name: "Save profile" }));
    expect(await screen.findByText("Unsupported currency")).toBeInTheDocument();
    expect(screen.queryByText("Profile saved")).not.toBeInTheDocument();
  });

  it("reports integration status honestly, including what is not configured", async () => {
    current = settings({ configured: false });
    current.integrations.paypal = paypal({ configured: false, connected: false, webhook_configured: false });
    open();
    expect(await screen.findByText("Set PAYPAL_CLIENT_ID / SECRET on the backend")).toBeInTheDocument();
    expect(screen.getByText("Set PAYPAL_WEBHOOK_ID")).toBeInTheDocument();
    expect(screen.getByText("Optional · not configured")).toBeInTheDocument();
    expect(screen.getByText("Set GEMINI_API_KEY or OPENAI_API_KEY")).toBeInTheDocument();
  });

  it("shows a connected PayPal with latency and only a hint of the client id", async () => {
    open();
    expect(await screen.findByText("Connected · 120ms · AbC…xyz")).toBeInTheDocument();
    expect(screen.getByText("PayPal REST API (sandbox)")).toBeInTheDocument();
    expect(screen.getByText(/API secrets live only on the server and are never shown here/)).toBeInTheDocument();
  });

  it("shows the PayPal error when the credentials do not authenticate", async () => {
    current.integrations.paypal = paypal({ connected: false, error: "invalid_client" });
    open();
    expect(await screen.findByText("invalid_client")).toBeInTheDocument();
  });

  it("shows the webhook URL to register with PayPal", async () => {
    open();
    expect(await screen.findByText("http://api.test/api/webhooks/paypal")).toBeInTheDocument();
  });

  it("signs out", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(auth.signOut).toHaveBeenCalledTimes(1);
  });
});

describe("Automations page", () => {
  const note = (o: Partial<Notification> = {}): Notification =>
    ({ id: "n1", title: "Reminder suggested", message: "INV-0003 is 9 days overdue", notification_type: "reminder_suggestion", is_read: false, data: null, created_at: new Date().toISOString(), ...o });
  const reminders = automation();
  const events = automation({ id: "a2", name: "Payment notifications", description: "On every capture", automation_type: "payment_notifications", is_enabled: true });
  const idle = automation({ id: "a3", name: "Weekly summary", description: "Mondays", automation_type: "weekly_summary", is_enabled: false, last_executed_at: null });
  let list = [reminders, events, idle];
  let notes: Notification[] = [];
  const routes = () => ({
    "GET /api/automations": () => list,
    "GET /api/notifications": () => ({ unread: notes.length, items: notes }),
    "PATCH /api/automations/a1": (b: { is_enabled: boolean }) => ({ ...reminders, ...b }),
    "PATCH /api/automations/a3": (b: { is_enabled: boolean }) => ({ ...idle, ...b }),
    "POST /api/automations/a1/run": () => ({ result: "Drafted 2 reminders" }),
    "POST /api/automations/a3/run": () => ({ result: "Summary built" }),
  });
  const open = () => renderPage(<Automations />, { path: "/" });

  beforeEach(() => { list = [reminders, events, idle]; notes = []; serve(routes()); });

  it("shows a placeholder while loading, then each automation with its state", async () => {
    const d = deferred();
    serve({ ...routes(), "GET /api/automations": () => d.promise });
    open();
    expect(document.querySelector(".animate-pulse")).toBeInTheDocument();
    d.resolve(list);
    expect(await screen.findByText("Overdue reminders")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Disable Overdue reminders" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("switch", { name: "Enable Weekly summary" })).toHaveAttribute("aria-checked", "false");
  });

  it("shows the API error and recovers on Retry", async () => {
    let healthy = false;
    serve({ ...routes(), "GET /api/automations": () => { if (!healthy) throw new ApiError("Automations unavailable", 500); return list; } });
    const { user } = open();
    expect(await screen.findByRole("alert")).toHaveTextContent("Automations unavailable");
    healthy = true;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Overdue reminders")).toBeInTheDocument();
  });

  it("says when an automation has never run, and marks event-driven ones as not runnable", async () => {
    open();
    expect(await screen.findAllByText("Has not run yet")).toHaveLength(2); // the two schedulable automations
    expect(screen.getByText("Event-driven (webhooks & captures)")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Run now" })).toHaveLength(2); // not for the event-driven one
  });

  it("toggles an automation on and tells the user", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("switch", { name: "Enable Weekly summary" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith("/api/automations/a3", { is_enabled: true }));
    expect(await screen.findByText("Weekly summary enabled")).toBeInTheDocument();
  });

  it("toggles an automation off", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("switch", { name: "Disable Overdue reminders" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith("/api/automations/a1", { is_enabled: false }));
    expect(await screen.findByText("Overdue reminders disabled")).toBeInTheDocument();
  });

  it("reports a failed toggle", async () => {
    serve({ ...routes(), "PATCH /api/automations/a3": () => { throw new ApiError("Not allowed", 403); } });
    const { user } = open();
    await user.click(await screen.findByRole("switch", { name: "Enable Weekly summary" }));
    expect(await screen.findByText("Not allowed")).toBeInTheDocument();
  });

  it("runs an automation on demand and shows its result", async () => {
    const { user } = open();
    const card = (await screen.findByText("Overdue reminders")).closest("section")!;
    await user.click(within(card).getByRole("button", { name: "Run now" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/automations/a1/run"));
    expect(await screen.findByText("Ran: Drafted 2 reminders")).toBeInTheDocument();
  });

  it("only the automation being run shows a busy button", async () => {
    const d = deferred();
    serve({ ...routes(), "POST /api/automations/a1/run": () => d.promise });
    const { user } = open();
    const first = (await screen.findByText("Overdue reminders")).closest("section")!;
    const third = screen.getByText("Weekly summary").closest("section")!;
    await user.click(within(first).getByRole("button", { name: "Run now" }));
    await waitFor(() => expect(within(first).getByRole("button", { name: "Run now" })).toBeDisabled());
    expect(within(third).getByRole("button", { name: "Run now" })).toBeEnabled();
    d.resolve({ result: "done" });
    expect(await screen.findByText("Ran: done")).toBeInTheDocument();
  });

  it("reports a failed run", async () => {
    serve({ ...routes(), "POST /api/automations/a1/run": () => { throw new ApiError("Scheduler error", 500); } });
    const { user } = open();
    const card = (await screen.findByText("Overdue reminders")).closest("section")!;
    await user.click(within(card).getByRole("button", { name: "Run now" }));
    expect(await screen.findByText("Scheduler error")).toBeInTheDocument();
  });

  it("lists up to five reminder suggestions and ignores other notifications", async () => {
    notes = [...Array.from({ length: 7 }, (_, i) => note({ id: `r${i}`, title: `Suggestion ${i}` })), note({ id: "x", title: "Payment received", notification_type: "payment" })];
    open();
    expect(await screen.findByText("Suggestion 0")).toBeInTheDocument();
    expect(screen.getByText("Suggestion 4")).toBeInTheDocument();
    expect(screen.queryByText("Suggestion 5")).not.toBeInTheDocument();
    expect(screen.queryByText("Payment received")).not.toBeInTheDocument();
  });

  it("explains when there are no suggestions and can hand over to the assistant", async () => {
    const { user } = open();
    expect(await screen.findByText(/No suggestions yet/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Draft with Copilot/ }));
    expect(screen.getByTestId("location")).toHaveTextContent("/assistant");
  });
});

describe("Settings: remaining behaviour", () => {
  const open = () => renderPage(<Settings />, { path: "/" });

  it("disables the save button while the request is in flight, then confirms", async () => {
    const d = deferred();
    serve({ "GET /api/settings": () => settings(), "PATCH /api/settings/profile": () => d.promise });
    const { user } = open();
    await waitFor(() => expect(screen.getByLabelText("Full name")).toHaveValue("Pranav Raj"));
    await user.click(screen.getByRole("button", { name: "Save profile" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save profile" })).toBeDisabled());
    d.resolve({});
    expect(await screen.findByText("Profile saved")).toBeInTheDocument();
  });

  // KNOWN APP BUG (minor): a fast double-click sends two PATCH requests because the button only disables after the first
  // submit has started. The request is idempotent so the impact is a redundant call. `it.fails` documents it.
  it.fails("BUG: a double-click on Save should send exactly one request", async () => {
    const d = deferred();
    serve({ "GET /api/settings": () => settings(), "PATCH /api/settings/profile": () => d.promise });
    const { user } = open();
    await waitFor(() => expect(screen.getByLabelText("Full name")).toHaveValue("Pranav Raj"));
    await user.dblClick(screen.getByRole("button", { name: "Save profile" }));
    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(patch).toHaveBeenCalledTimes(1);
    d.resolve({});
  });

  // Known gap: the schema caps names at 200 characters but renders no message, so the form just refuses to submit.
  it("refuses to save a name longer than 200 characters (silently)", async () => {
    serve({ "GET /api/settings": () => settings(), "PATCH /api/settings/profile": (b) => b });
    const { user } = open();
    await waitFor(() => expect(screen.getByLabelText("Full name")).toHaveValue("Pranav Raj"));
    const name = screen.getByLabelText("Full name");
    await user.clear(name);
    await user.click(name);
    await user.paste("x".repeat(201));
    await user.click(screen.getByRole("button", { name: "Save profile" }));
    await new Promise((r) => setTimeout(r, 50));
    expect(patch).not.toHaveBeenCalled();
    expect(screen.queryByText("Profile saved")).not.toBeInTheDocument();
  });

  it("allows clearing optional fields", async () => {
    serve({ "GET /api/settings": () => settings(), "PATCH /api/settings/profile": (b) => b });
    const { user } = open();
    await waitFor(() => expect(screen.getByLabelText("Business name")).toHaveValue("Acme"));
    await user.clear(screen.getByLabelText("Business name"));
    await user.click(screen.getByRole("button", { name: "Save profile" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith("/api/settings/profile", expect.objectContaining({ business_name: "" })));
  });

  it("distinguishes webhooks that are receiving events from ones with no events yet", async () => {
    serve({ "GET /api/settings": () => { const s = settings(); s.integrations.paypal = paypal({ last_webhook_at: "2026-01-01T00:00:00Z" }); return s; } });
    const view = open();
    expect(await screen.findByText("Receiving events")).toBeInTheDocument();
    view.unmount();
    serve({ "GET /api/settings": () => settings() });
    open();
    expect(await screen.findByText("Configured · no events yet")).toBeInTheDocument();
  });

  it("copies the webhook URL", async () => {
    serve({ "GET /api/settings": () => settings() });
    const { user } = open();
    const row = (await screen.findByText("http://api.test/api/webhooks/paypal")).parentElement!;
    await user.click(within(row).getByRole("button"));
    await expect(navigator.clipboard.readText()).resolves.toBe("http://api.test/api/webhooks/paypal");
  });
});

describe("Automations: remaining behaviour", () => {
  const reminders = automation({ last_executed_at: new Date(Date.now() - 3 * 3600_000).toISOString() });
  const open = () => renderPage(<Automations />, { path: "/" });
  beforeEach(() => serve({
    "GET /api/automations": () => [reminders],
    "GET /api/notifications": () => ({ unread: 0, items: [] }),
    "PATCH /api/automations/a1": (b: object) => ({ ...reminders, ...b }),
  }));

  it("shows when an automation last ran", async () => {
    open();
    expect(await screen.findByText(/Last run 3h ago/)).toBeInTheDocument();
  });

  it("offers only enable/disable and run: there is no create or delete flow in this UI", async () => {
    open();
    await screen.findByText("Overdue reminders");
    expect(screen.queryByRole("button", { name: /add|create|new|delete|remove/i })).not.toBeInTheDocument();
  });

  it("each toggle sends the opposite of the state the user saw", async () => {
    const { user } = open();
    const sw = await screen.findByRole("switch", { name: "Disable Overdue reminders" });
    await user.click(sw);
    await waitFor(() => expect(patch).toHaveBeenCalledWith("/api/automations/a1", { is_enabled: false }));
  });

  // KNOWN APP BUG (minor): the switch has no pending guard, so a quick double-click sends two toggle requests.
  it.fails("BUG: a rapid double-click on the switch should send the toggle request once", async () => {
    const d = deferred();
    serve({ "GET /api/automations": () => [reminders], "GET /api/notifications": () => ({ unread: 0, items: [] }), "PATCH /api/automations/a1": () => d.promise });
    const { user } = open();
    await user.dblClick(await screen.findByRole("switch", { name: "Disable Overdue reminders" }));
    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(patch.mock.calls.length).toBe(1);
    d.resolve({});
  });
});
