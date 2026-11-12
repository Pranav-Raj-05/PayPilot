import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async () => await import("@/test/api"));
// Charts need real layout to render; the data they are given is what matters here.
vi.mock("recharts", () => {
  const Stub = () => null;
  return { ResponsiveContainer: Stub, AreaChart: Stub, BarChart: Stub, Area: Stub, Bar: Stub, Line: Stub, CartesianGrid: Stub, Tooltip: Stub, XAxis: Stub, YAxis: Stub };
});

import Analytics from "./Analytics";
import Overview from "./Overview";
import { buildPoints } from "@/components/RevenueChart";
import { RangeProvider } from "@/context/RangeContext";
import { ApiError, deferred, get, requests, serve } from "@/test/api";
import { analytics, dashboard, paypal, profile, tx } from "@/test/fixtures";
import { renderPage } from "@/test/render";

const here = () => screen.getByTestId("location").textContent;
const overview = () => renderPage(<RangeProvider><Overview /></RangeProvider>, { path: "/" });
const analyticsPage = () => renderPage(<RangeProvider><Analytics /></RangeProvider>, { path: "/" });
const lastParams = (url: string) => requests(get, url).at(-1)![1];

beforeEach(() => {
  serve({
    "GET /api/dashboard": () => dashboard(),
    "GET /api/analytics": () => analytics(),
    "GET /api/auth/me": () => profile(),
    "GET /api/payments/paypal/status": () => paypal(),
  });
});

describe("Overview dashboard", () => {
  it("shows skeletons instead of numbers while loading", async () => {
    const d = deferred();
    serve({ "GET /api/dashboard": () => d.promise, "GET /api/auth/me": () => profile(), "GET /api/payments/paypal/status": () => paypal() });
    overview();
    expect(document.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
    expect(screen.queryByText("No transactions yet")).not.toBeInTheDocument();
    d.resolve(dashboard());
    expect(await screen.findByText("42")).toBeInTheDocument();
  });

  it("renders the headline figures from the API", async () => {
    overview();
    expect((await screen.findAllByText("$12,345.00")).length).toBeGreaterThan(0);
    expect(screen.getByText("42")).toBeInTheDocument(); // total transactions
    expect(screen.getByText("$800.00")).toBeInTheDocument(); // pending
    expect(screen.getByText("96.5%")).toBeInTheDocument(); // success rate
    expect(screen.getByText("+23.5%")).toBeInTheDocument();
    expect(screen.getByText("Requires Attention")).toBeInTheDocument(); // there is an overdue invoice
    expect(screen.getByText("Failed: 2%")).toBeInTheDocument();
    expect(screen.getByText("Refunds: 1.5%")).toBeInTheDocument();
  });

  it("greets the user by first name", async () => {
    overview();
    expect(await screen.findByRole("heading", { level: 1, name: /Pranav/ })).toBeInTheDocument();
  });

  it("says 'On track' when nothing is overdue", async () => {
    serve({ "GET /api/dashboard": () => dashboard({ pending: { total: 0, count: 0, overdue_count: 0, overdue_total: 0, earliest_aging_days: null } }), "GET /api/auth/me": () => profile(), "GET /api/payments/paypal/status": () => paypal() });
    overview();
    expect(await screen.findByText("On track")).toBeInTheDocument();
  });

  it("shows em-dashes rather than fake numbers when there is no history yet", async () => {
    serve({
      "GET /api/dashboard": () => dashboard({
        success_rate: { value: null, previous: null, change_pct: null, failed_pct: 0, refunded_pct: 0 }, revenue: { ...dashboard().revenue, change_pct: null },
      }), "GET /api/auth/me": () => profile(), "GET /api/payments/paypal/status": () => paypal(),
    });
    overview();
    expect(await screen.findAllByText("—")).not.toHaveLength(0);
    expect(screen.getByText("n/a")).toBeInTheDocument();
  });

  it("lists recent transactions with a short id, signed amount and status", async () => {
    serve({
      "GET /api/dashboard": () => dashboard({ recent_transactions: [tx(), tx({ id: "t2", customer: null, amount: 40, status: "failed", provider: "razorpay", provider_order_id: null })] }),
      "GET /api/auth/me": () => profile(), "GET /api/payments/paypal/status": () => paypal(),
    });
    overview();
    const table = within(await screen.findByRole("table"));
    expect(table.getByText("#ABC12345")).toBeInTheDocument();
    expect(table.getByText("+$250.00")).toBeInTheDocument();
    expect(table.getByText("Sarah Johnson")).toBeInTheDocument();
    expect(table.getByText("Unassigned")).toBeInTheDocument();
    expect(table.getByText("failed")).toBeInTheDocument();
    expect(table.getByText("Razorpay", { exact: false })).toBeInTheDocument();
  });

  it("guides a brand-new account instead of showing an empty table", async () => {
    serve({
      "GET /api/dashboard": () => dashboard({ transactions: { total: 0, previous: 0, change_pct: null, processed_today: 0 }, pending: { total: 0, count: 0, overdue_count: 0, overdue_total: 0, earliest_aging_days: null }, recent_transactions: [] }),
      "GET /api/auth/me": () => profile(), "GET /api/payments/paypal/status": () => paypal(),
    });
    const { user } = overview();
    expect(await screen.findByText("No transactions yet")).toBeInTheDocument();
    expect(screen.getByText("Ready for your first sandbox payment.")).toBeInTheDocument();
    expect(screen.getByText(/insights appear once you have a few payments/i)).toBeInTheDocument();
    await user.click(within(screen.getByText("No transactions yet").closest("div")!).getByRole("button", { name: "Create invoice" }));
    expect(here()).toBe("/invoices/new");
  });

  it("shows the API error and refetches on Retry", async () => {
    let healthy = false;
    serve({ "GET /api/dashboard": () => { if (!healthy) throw new ApiError("Database unavailable", 500); return dashboard(); }, "GET /api/auth/me": () => profile(), "GET /api/payments/paypal/status": () => paypal() });
    const { user } = overview();
    expect(await screen.findByRole("alert")).toHaveTextContent("Database unavailable");
    healthy = true;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("42")).toBeInTheDocument();
  });

  it("re-queries for the chosen period", async () => {
    const { user } = overview();
    await screen.findByText("42");
    expect(lastParams("/api/dashboard")).toMatchObject({ days: 30 });
    await user.click(screen.getByRole("tab", { name: "7D" }));
    await waitFor(() => expect(lastParams("/api/dashboard")).toMatchObject({ days: 7 }));
    expect(screen.getByRole("tab", { name: "7D" })).toHaveAttribute("aria-selected", "true");
  });

  describe("PayPal status banner", () => {
    const withPaypal = (pp: ReturnType<typeof paypal>) => serve({ "GET /api/dashboard": () => dashboard({ paypal: pp }), "GET /api/auth/me": () => profile(), "GET /api/payments/paypal/status": () => pp });
    it("warns when credentials are missing", async () => { withPaypal(paypal({ configured: false, connected: false })); overview(); expect(await screen.findByText("PayPal credentials not configured")).toBeInTheDocument(); });
    it("labels sandbox mode", async () => { overview(); expect(await screen.findByText("PayPal developer sandbox active")).toBeInTheDocument(); });
    it("labels live mode distinctly", async () => { withPaypal(paypal({ mode: "live" })); overview(); expect(await screen.findByText("PayPal live mode")).toBeInTheDocument(); });
    it("flags an unreachable PayPal API", async () => { withPaypal(paypal({ connected: false })); overview(); expect(await screen.findByText("Unreachable")).toBeInTheDocument(); });
  });

  describe("AI prompt box", () => {
    it("hands the typed prompt to the assistant page", async () => {
      const { user } = overview();
      await user.type(await screen.findByLabelText("Ask PayPilot"), "Create a $250 invoice for John");
      await user.click(screen.getByRole("button", { name: "Send to Copilot" }));
      expect(here()).toBe("/assistant");
    });

    it("ignores an empty prompt", async () => {
      const { user } = overview();
      await screen.findByText("42");
      await user.type(screen.getByLabelText("Ask PayPilot"), "   {Enter}");
      expect(screen.queryByTestId("location")).not.toBeInTheDocument(); // still on the dashboard
    });

    it("suggested prompts open the assistant", async () => {
      const { user } = overview();
      await user.click(await screen.findByRole("button", { name: /Quick Link/ }));
      expect(here()).toBe("/assistant");
    });

    it("Ctrl+J focuses the prompt box", async () => {
      const { user } = overview();
      await screen.findByText("42");
      await user.keyboard("{Control>}j{/Control}");
      expect(screen.getByLabelText("Ask PayPilot")).toHaveFocus();
    });

    it("an insight's action opens the assistant with a reminder request", async () => {
      serve({
        "GET /api/dashboard": () => dashboard({ insights: [{ kind: "action", title: "Overdue invoices", tone: "warning", text: "1 invoice is overdue", action: "Send reminders" }] }),
        "GET /api/auth/me": () => profile(), "GET /api/payments/paypal/status": () => paypal(),
      });
      const { user } = overview();
      expect(await screen.findByText("1 invoice is overdue")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: /Send reminders/ }));
      expect(here()).toBe("/assistant");
    });
  });
});

describe("Analytics page", () => {
  it("shows a skeleton while loading", async () => {
    const d = deferred();
    serve({ "GET /api/analytics": () => d.promise });
    analyticsPage();
    expect(document.querySelector(".animate-pulse")).toBeInTheDocument();
    expect(screen.queryByText("Revenue trend")).not.toBeInTheDocument();
    d.resolve(analytics());
    expect(await screen.findByText("Revenue trend")).toBeInTheDocument();
  });

  it("computes the headline stats, status breakdown and provider split from the API data", async () => {
    analyticsPage();
    expect(await screen.findByText("$12,345.00")).toBeInTheDocument();
    expect(screen.getByText("+23.5% vs previous period")).toBeInTheDocument();
    expect(screen.getByText("40 completed · 2 failed")).toBeInTheDocument();
    expect(screen.getByText("96.5%")).toBeInTheDocument();
    expect(screen.getByText("Failed 2% · Refunded 1.5%")).toBeInTheDocument();
    expect(screen.getByText("$293.93")).toBeInTheDocument();
    expect(screen.getByText("PayPal: $9,000.00")).toBeInTheDocument();
    expect(screen.getByText("Razorpay: $3,345.00")).toBeInTheDocument();
  });

  it("lists top customers with payments and revenue", async () => {
    analyticsPage();
    const row = within((await screen.findByText("Apex Cloud")).closest("tr")!);
    expect(row.getByText("4")).toBeInTheDocument();
    expect(row.getByText("$5,000.00")).toBeInTheDocument();
  });

  it("handles an account with no payments", async () => {
    serve({ "GET /api/analytics": () => analytics({
      success_rate: { value: null, previous: null, change_pct: null, failed_pct: 0, refunded_pct: 0 }, volume: { total: 0, by_status: {}, series: [] },
      average_transaction_value: 0, top_customers: [], revenue_by_provider: {}, insights: [], revenue: { ...analytics().revenue, change_pct: null, series: [], previous_series: [] },
    }) });
    analyticsPage();
    expect(await screen.findByText("No completed payments yet.")).toBeInTheDocument();
    expect(screen.getByText("none yet")).toBeInTheDocument();
    expect(screen.getByText("Insights appear once you have payment activity.")).toBeInTheDocument();
    expect(screen.getByText("—", { selector: ".font-display" })).toBeInTheDocument(); // success rate
  });

  it("shows insights returned by the backend", async () => {
    serve({ "GET /api/analytics": () => analytics({ insights: [{ kind: "momentum", title: "Revenue is up", tone: "positive", text: "Up 23% on last month" }] }) });
    analyticsPage();
    expect(await screen.findByText("Revenue is up")).toBeInTheDocument();
    expect(screen.getByText("Up 23% on last month")).toBeInTheDocument();
  });

  it("shows the error and refetches on Retry", async () => {
    let healthy = false;
    serve({ "GET /api/analytics": () => { if (!healthy) throw new ApiError("Analytics unavailable", 500); return analytics(); } });
    const { user } = analyticsPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Analytics unavailable");
    healthy = true;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Revenue trend")).toBeInTheDocument();
  });

  it("re-queries when the period changes and passes it to the assistant prompt", async () => {
    const { user } = analyticsPage();
    await screen.findByText("Revenue trend");
    await user.click(screen.getByRole("tab", { name: "90D" }));
    await waitFor(() => expect(lastParams("/api/analytics")).toMatchObject({ days: 90 }));
    await user.click(await screen.findByRole("button", { name: /deeper analysis/i }));
    expect(here()).toBe("/assistant");
  });
});

describe("buildPoints (chart data)", () => {
  it("builds one point per day from the start date, pairing each with the previous period", () => {
    const pts = buildPoints({ series: [5, 0, 7], previous: [1, 2], start: "2026-03-01", granularity: "day" });
    expect(pts.map((p) => p.label)).toEqual(["Mar 01", "Mar 02", "Mar 03"]);
    expect(pts.map((p) => p.value)).toEqual([5, 0, 7]);
    expect(pts.map((p) => p.previous)).toEqual([1, 2, 0]); // missing previous values default to 0
  });

  it("steps by month for yearly ranges", () => {
    const pts = buildPoints({ series: [1, 2, 3], previous: [], start: "2026-01-01", granularity: "month" });
    expect(pts.map((p) => p.label)).toEqual(["Jan", "Feb", "Mar"]);
  });

  it("returns nothing for an empty series", () => {
    expect(buildPoints({ series: [], previous: [], start: "2026-01-01", granularity: "day" })).toEqual([]);
  });
});
