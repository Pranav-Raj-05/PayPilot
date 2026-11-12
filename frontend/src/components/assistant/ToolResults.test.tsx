import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ReadResult } from "./ToolResults";
import { msg } from "@/test/fixtures";

const show = (tool: string, result: Record<string, unknown>) =>
  render(<ReadResult msg={msg({ role: "tool", tool_name: tool, tool_result: { status: "read", result } })} />);

describe("ReadResult", () => {
  it("revenue summary: shows the period, totals and per-provider split", () => {
    show("get_revenue_summary", { period: "this month", revenue: 12500, payments: 14, average_payment: 892.86, currency: "USD", by_provider: { paypal: 10000, razorpay: 2500 } });
    expect(screen.getByText("Revenue this month")).toBeInTheDocument();
    expect(screen.getByText("$12,500.00")).toBeInTheDocument();
    expect(screen.getByText("14")).toBeInTheDocument();
    expect(screen.getByText("$892.86")).toBeInTheDocument();
    expect(screen.getByText("paypal: $10,000.00")).toBeInTheDocument();
    expect(screen.getByText("razorpay: $2,500.00")).toBeInTheDocument();
  });

  it("revenue summary: omits the provider split when there is none", () => {
    show("get_revenue_summary", { period: "today", revenue: 0, payments: 0, average_payment: 0, currency: "USD", by_provider: {} });
    expect(screen.queryByText(/paypal:/)).not.toBeInTheDocument();
  });

  it("pending payments: lists open invoices and flags overdue ones", () => {
    show("get_pending_payments", { count: 2, overdue_count: 1, invoices: [
      { invoice_number: "INV-0001", customer: "Apex Cloud", amount: 480, currency: "USD", days_overdue: 9, status: "overdue" },
      { invoice_number: "INV-0002", customer: null, amount: 120, currency: "USD", days_overdue: 0, status: "pending" },
    ] });
    expect(screen.getByText("2 open invoice(s) · 1 overdue")).toBeInTheDocument();
    expect(screen.getByText("9d overdue")).toBeInTheDocument();
    expect(screen.getByText("INV-0001")).toBeInTheDocument();
    expect(screen.getByText(/\$480\.00/)).toBeInTheDocument();
    expect(screen.queryByText("0d overdue")).not.toBeInTheDocument(); // the on-time invoice is not flagged
  });

  it("pending payments: celebrates nothing outstanding", () => {
    show("get_pending_payments", { count: 0, overdue_count: 0, invoices: [] });
    expect(screen.getByText(/Nothing outstanding/)).toBeInTheDocument();
  });

  it("transactions: shows each payment, unassigned customers and failure reasons", () => {
    show("list_transactions", { total: 2, period: "last 7 days", transactions: [
      { id: "t1", customer: "Sarah Johnson", provider: "paypal", amount: 250, currency: "USD", status: "completed", failure_reason: null },
      { id: "t2", customer: null, provider: "razorpay", amount: 40, currency: "INR", status: "failed", failure_reason: "Card declined" },
    ] });
    expect(screen.getByText("2 transaction(s) last 7 days")).toBeInTheDocument();
    expect(screen.getByText("Sarah Johnson")).toBeInTheDocument();
    expect(screen.getByText("Unassigned")).toBeInTheDocument();
    expect(screen.getByText("Card declined")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
  });

  it("transactions: says so when nothing matches", () => {
    show("list_transactions", { total: 0, period: "today", transactions: [] });
    expect(screen.getByText("No matching transactions.")).toBeInTheDocument();
  });

  it("reminder drafts: shows each draft and states that nothing was sent", () => {
    show("suggest_payment_reminder", { drafts: [{ invoice_number: "INV-0003", customer: "Apex Cloud", days_overdue: 12, message: "Hi Apex, a friendly reminder…" }] });
    expect(screen.getByText("INV-0003 · Apex Cloud")).toBeInTheDocument();
    expect(screen.getByText("12d overdue")).toBeInTheDocument();
    expect(screen.getByText("Hi Apex, a friendly reminder…")).toBeInTheDocument();
    expect(screen.getByText(/Drafts only — nothing was sent/)).toBeInTheDocument();
  });

  it("reminder drafts: handles no overdue invoices", () => {
    show("suggest_payment_reminder", { drafts: [] });
    expect(screen.getByText("No overdue invoices to remind.")).toBeInTheDocument();
  });

  it("business performance: shows headline figures and top customers (max three)", () => {
    show("analyse_business_performance", {
      days: 30, currency: "USD", revenue: { total: 9000 }, success_rate: { value: 95 }, average_transaction_value: 300,
      top_customers: [{ customer: "A", revenue: 4000 }, { customer: "B", revenue: 3000 }, { customer: "C", revenue: 1500 }, { customer: "D", revenue: 500 }],
    });
    expect(screen.getByText("Revenue · 30d")).toBeInTheDocument();
    expect(screen.getByText("$9,000.00")).toBeInTheDocument();
    expect(screen.getByText("95%")).toBeInTheDocument();
    expect(screen.getByText("Top: A ($4,000.00) · B ($3,000.00) · C ($1,500.00)")).toBeInTheDocument();
  });

  it("business performance: shows a dash when the success rate is unknown", () => {
    show("analyse_business_performance", { days: 7, currency: "USD", revenue: { total: 0 }, success_rate: { value: null }, average_transaction_value: 0, top_customers: [] });
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText(/^Top:/)).not.toBeInTheDocument();
  });

  it("customers, invoice and transaction lookups", () => {
    const { unmount } = show("list_customers", { total: 1, customers: [{ id: "c1", name: "Sarah Johnson", email: "sarah@example.com" }] });
    expect(screen.getByText("1 customer(s)")).toBeInTheDocument();
    expect(screen.getByText("sarah@example.com")).toBeInTheDocument();
    unmount();
    const second = show("get_invoice", { invoice: { invoice_number: "INV-0009", status: "paid", total_amount: 500, currency: "USD", customer: { name: "Sarah Johnson" } } });
    expect(screen.getByText("INV-0009")).toBeInTheDocument();
    expect(screen.getByText("Sarah Johnson · $500.00")).toBeInTheDocument();
    expect(screen.getByText("paid")).toBeInTheDocument();
    second.unmount();
    show("get_transaction", { transaction: { amount: 75, currency: "USD", status: "refunded", provider: "paypal", provider_order_id: "ORD-7" } });
    expect(screen.getByText("$75.00")).toBeInTheDocument();
    expect(screen.getByText("paypal · ORD-7")).toBeInTheDocument();
    expect(screen.getByText("refunded")).toBeInTheDocument();
  });

  it("an invoice lookup with no invoice found renders no card (only the tool indicator)", () => {
    show("get_invoice", {});
    expect(screen.queryByText(/INV-/)).not.toBeInTheDocument();
  });

  it("unknown tools render a safe indicator rather than crashing", () => {
    const { container } = show("some_future_tool", { anything: 1 });
    expect(container).not.toBeEmptyDOMElement();
    expect(screen.queryByText(/anything/)).not.toBeInTheDocument();
  });

  it("tolerates a result with missing fields", () => {
    expect(() => render(<ReadResult msg={msg({ role: "tool", tool_name: "get_revenue_summary", tool_result: { status: "read" } })} />)).not.toThrow();
  });
});

describe("ReadResult: missing or malformed data", () => {
  const TOOLS = ["get_revenue_summary", "get_pending_payments", "list_transactions", "suggest_payment_reminder", "analyse_business_performance", "list_customers", "get_invoice", "get_transaction"];

  it.each(TOOLS)("%s renders without crashing when the result is empty", (tool) => {
    expect(() => show(tool, {})).not.toThrow();
  });

  it.each(TOOLS)("%s renders without crashing when the tool result is absent entirely", (tool) => {
    expect(() => render(<ReadResult msg={msg({ role: "tool", tool_name: tool, tool_result: { status: "read" } })} />)).not.toThrow();
  });

  it.each(TOOLS)("%s renders without crashing when lists are null", (tool) => {
    expect(() => show(tool, { invoices: null, transactions: null, drafts: null, customers: null, by_provider: null, top_customers: null })).not.toThrow();
  });

  it("does not crash when a tool name is missing", () => {
    expect(() => render(<ReadResult msg={msg({ role: "tool", tool_name: null, tool_result: { status: "read", result: { a: 1 } } })} />)).not.toThrow();
  });

  it("shows zero amounts rather than NaN for a revenue result with missing numbers", () => {
    const { container } = show("get_revenue_summary", { period: "today", currency: "USD" });
    expect(container.textContent).not.toMatch(/NaN/);
    expect(screen.getAllByText("$0.00").length).toBeGreaterThan(0);
  });

  // KNOWN APP BUG (minor): a missing count is rendered via String(undefined), so the Payments tile literally says "undefined".
  it.fails("BUG: a revenue result with a missing payment count should not print 'undefined'", () => {
    const { container } = show("get_revenue_summary", { period: "today", currency: "USD" });
    expect(container.textContent).not.toMatch(/undefined/);
  });

  it("falls back to dashes for an invoice in the pending list that has no customer", () => {
    show("get_pending_payments", { count: 1, overdue_count: 0, invoices: [{ invoice_number: "INV-9", customer: null, amount: 10, currency: "USD", days_overdue: 0, status: "pending" }] });
    expect(screen.getByText(/INV-9/)).toBeInTheDocument();
    expect(screen.getByText(/—/)).toBeInTheDocument();
  });

  it("does not render raw HTML from tool data (draft text is plain text)", () => {
    const { container } = show("suggest_payment_reminder", { drafts: [{ invoice_number: "INV-1", customer: "A", days_overdue: 1, message: "<img src=x onerror=alert(1)>" }] });
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
  });
});
