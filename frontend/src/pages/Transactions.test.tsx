import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async () => await import("@/test/api"));
vi.mock("@/utils/csv", () => ({ downloadCsv: vi.fn() }));

import Transactions from "./Transactions";
import { ApiError, deferred, get, post, requests, serve } from "@/test/api";
import { page, paypal, tx } from "@/test/fixtures";
import { renderPage } from "@/test/render";
import { downloadCsv } from "@/utils/csv";
import type { Transaction } from "@/types";

const summary = {
  currency: "USD", total: { volume: 1234, count: 20 }, settled: { total: 800, count: 15, rate: 75, }, pending: { total: 111, count: 2 },
  failed: { total: 40, count: 2 }, refunded: { total: 60, count: 1 },
};
const captured = (o: Partial<Transaction> = {}) => tx({
  id: "aaaaa-0001", provider_capture_id: "CAP-1", fee_amount: 7.5, net_amount: 242.5, invoice_id: "inv9", invoice_number: "INV-0009",
  events: [{ type: "order_created", at: "2026-01-01T10:00:00Z", detail: null }, { type: "captured", at: "2026-01-01T10:05:00Z", detail: "Capture verified" }], ...o,
});
const failed = tx({ id: "bbbbb-0002", status: "failed", failure_reason: "Card declined", amount: 40, customer: null, provider_order_id: "ORD-F" });

let list: Transaction[];
let detail: Record<string, Transaction>;
let refundBehaviour: (id: string) => unknown;

const routes = () => ({
  "GET /api/transactions/summary": () => summary,
  "GET /api/transactions": () => ({ ...page(list), total: list.length }),
  "GET /api/payments/paypal/status": () => paypal(),
  "GET /api/transactions/aaaaa-0001": () => detail["aaaaa-0001"],
  "GET /api/transactions/bbbbb-0002": () => detail["bbbbb-0002"],
  "POST /api/transactions/aaaaa-0001/refund": () => refundBehaviour("aaaaa-0001"),
});
const open = (route = "/") => renderPage(<Transactions />, { route, path: "/" });
const lastList = () => requests(get, "/api/transactions").at(-1)![1];
const table = async () => within(await screen.findByRole("table"));
const refundButton = () => screen.findByRole("button", { name: /Issue Full Refund/ });

beforeEach(() => {
  const t = captured();
  list = [t, failed];
  detail = { "aaaaa-0001": t, "bbbbb-0002": failed };
  refundBehaviour = () => { detail["aaaaa-0001"] = { ...detail["aaaaa-0001"]!, status: "refunded" }; list = [detail["aaaaa-0001"]!, failed]; return detail["aaaaa-0001"]; };
  serve(routes());
});

describe("ledger states", () => {
  it("shows skeletons, not an empty state, while loading", async () => {
    const d = deferred();
    serve({ ...routes(), "GET /api/transactions": () => d.promise });
    open();
    expect(document.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
    expect(screen.queryByText("No transactions found")).not.toBeInTheDocument();
    d.resolve({ ...page(list), total: 2 });
    expect(await (await table()).findByText("#TX-AAAAA")).toBeInTheDocument();
  });

  it("lists transactions with id, customer, signed amount and status", async () => {
    open();
    const t = await table();
    expect(await t.findByText("#TX-AAAAA")).toBeInTheDocument();
    expect(t.getByText("Sarah Johnson")).toBeInTheDocument();
    expect(t.getByText("+$250.00")).toBeInTheDocument();
    expect(t.getByText("completed")).toBeInTheDocument();
    expect(t.getByText("Unassigned")).toBeInTheDocument();
    expect(t.getByText("failed")).toBeInTheDocument();
  });

  it("shows the summary totals from the API", async () => {
    open();
    expect(await screen.findByText("$1,234.00")).toBeInTheDocument();
    expect(screen.getByText("$800.00")).toBeInTheDocument();
    expect(screen.getByText("75% rate")).toBeInTheDocument();
    expect(screen.getByText("$111.00")).toBeInTheDocument();
    expect(screen.getByText("$100.00")).toBeInTheDocument(); // failed + refunded
  });

  it("explains an empty ledger and points to creating an invoice", async () => {
    list = [];
    const { user } = open();
    expect(await screen.findByText("No transactions found")).toBeInTheDocument();
    expect(screen.getByText(/payments appear here once a customer pays/i)).toBeInTheDocument();
    expect(screen.getByText("Select a transaction")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Download Statement/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Create invoice" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/invoices/new");
  });

  it("suggests clearing filters when a filtered search is empty", async () => {
    list = [];
    open("/?search=nothing");
    expect(await screen.findByText("Try clearing your filters.")).toBeInTheDocument();
  });

  it("shows the API error and recovers on Retry", async () => {
    let healthy = false;
    serve({ ...routes(), "GET /api/transactions": () => { if (!healthy) throw new ApiError("Ledger unavailable", 503); return { ...page(list), total: 2 }; } });
    const { user } = open();
    expect(await screen.findByText("Ledger unavailable")).toBeInTheDocument();
    healthy = true;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await (await table()).findByText("#TX-AAAAA")).toBeInTheDocument();
  });

  it("Refresh re-queries the ledger", async () => {
    const { user } = open();
    await table();
    const before = requests(get, "/api/transactions").length;
    await user.click(screen.getByRole("button", { name: /Refresh/ }));
    await waitFor(() => expect(requests(get, "/api/transactions").length).toBeGreaterThan(before));
  });

  it("reports webhook health in the header", async () => {
    serve({ ...routes(), "GET /api/payments/paypal/status": () => paypal({ webhook_configured: false }) });
    open();
    expect(await screen.findByText("No webhook events received yet", { exact: false })).toBeInTheDocument();
  });
});

describe("search, filters, sorting and paging", () => {
  it("sends the search text to the API and keeps it in the URL", async () => {
    const { user } = open();
    await table();
    await user.type(screen.getByLabelText("Search transactions"), "ORD-9");
    await waitFor(() => expect(lastList()).toMatchObject({ search: "ORD-9", page: 1 }));
  });

  it("starts from a search deep-linked in the URL (e.g. from the global search box)", async () => {
    open("/?search=sarah");
    expect(screen.getByLabelText("Search transactions")).toHaveValue("sarah");
    await waitFor(() => expect(lastList()).toMatchObject({ search: "sarah" }));
  });

  it("filters by status tab and by payment method", async () => {
    const { user } = open();
    await table();
    await user.click(screen.getByRole("tab", { name: "Failed" }));
    await waitFor(() => expect(lastList()).toMatchObject({ status: "failed" }));
    await user.selectOptions(screen.getByLabelText("Filter by method"), "razorpay");
    await waitFor(() => expect(lastList()).toMatchObject({ status: "failed", provider: "razorpay" }));
    await user.click(screen.getByRole("tab", { name: "All" }));
    await waitFor(() => expect(lastList().status).toBeUndefined());
  });

  it("sorts by amount, then reverses the direction on a second click", async () => {
    const { user } = open();
    await table();
    expect(lastList()).toMatchObject({ sort: "created_at", order: "desc" });
    await user.click(screen.getByRole("button", { name: "Sort by Amount" }));
    await waitFor(() => expect(lastList()).toMatchObject({ sort: "amount", order: "desc" }));
    await user.click(screen.getByRole("button", { name: "Sort by Amount" }));
    await waitFor(() => expect(lastList()).toMatchObject({ sort: "amount", order: "asc" }));
  });

  it("pages through results and returns to page 1 when a filter changes", async () => {
    serve({ ...routes(), "GET /api/transactions": () => ({ items: list, total: 20, page: 1, page_size: 8 }) });
    const { user } = open();
    expect(await screen.findByText("Showing 1–8 of 20")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(lastList()).toMatchObject({ page: 2, page_size: 8 }));
    await user.click(screen.getByRole("tab", { name: "Completed" }));
    await waitFor(() => expect(lastList()).toMatchObject({ page: 1, status: "completed" }));
  });

  it("exports the visible rows as CSV", async () => {
    const { user } = open();
    await table();
    await user.click(screen.getByRole("button", { name: /Download Statement/ }));
    expect(downloadCsv).toHaveBeenCalledTimes(1);
    const [name, rows] = vi.mocked(downloadCsv).mock.calls[0]!;
    expect(name).toBe("transactions.csv");
    expect(rows).toHaveLength(3); // header + 2 rows
    expect(rows[1]).toEqual(expect.arrayContaining(["aaaaa-0001", "Sarah Johnson", "paypal", 250, "USD", "completed"]));
  });
});

describe("transaction details", () => {
  it("opens the first transaction by default with its settlement breakdown", async () => {
    open();
    expect(await screen.findByRole("heading", { name: "Transaction Details" })).toBeInTheDocument();
    expect(await screen.findByText("-$7.50 USD")).toBeInTheDocument(); // fee
    expect(screen.getByText("$242.50 USD")).toBeInTheDocument(); // net
    expect(screen.getByText("CAP-1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "#INV-0009" })).toHaveAttribute("href", "/invoices/inv9");
    expect(screen.getByText("2 events")).toBeInTheDocument();
    expect(screen.getByText("Payment Captured & Verified")).toBeInTheDocument();
  });

  it("says when the provider did not report a fee, rather than inventing one", async () => {
    detail["aaaaa-0001"] = captured({ fee_amount: null, net_amount: null });
    open();
    expect(await screen.findByText("Not reported")).toBeInTheDocument();
    expect(screen.getByText("—", { selector: ".font-display" })).toBeInTheDocument();
  });

  it("switches the panel when another row is clicked, and supports the keyboard", async () => {
    const { user } = open();
    const t = await table();
    await user.click(await t.findByText("#TX-BBBBB"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Card declined");
    expect(requests(get, "/api/transactions/bbbbb-0002")).toHaveLength(1);
    await screen.findByText("TX-BBBBB");
    t.getByText("#TX-AAAAA").closest("tr")!.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("opens the transaction named in the URL", async () => {
    open("/?open=bbbbb-0002");
    expect(await screen.findByText("Card declined")).toBeInTheDocument();
    expect(requests(get, "/api/transactions/aaaaa-0001")).toHaveLength(0);
  });

  it("shows an error in the panel if the details cannot be loaded", async () => {
    serve({ ...routes(), "GET /api/transactions/aaaaa-0001": () => { throw new ApiError("Transaction not found", 404); } });
    open();
    expect(await screen.findByText("Transaction not found")).toBeInTheDocument();
  });

  it("shows 'No lifecycle events' for a transaction without events", async () => {
    detail["aaaaa-0001"] = captured({ events: [] });
    open();
    expect(await screen.findByText("No lifecycle events recorded.")).toBeInTheDocument();
  });
});

describe("refund workflow", () => {
  describe("who can be refunded", () => {
    it("offers a full refund for a completed PayPal payment with a capture id", async () => {
      open();
      expect(await refundButton()).toHaveTextContent("Issue Full Refund ($250.00)");
    });

    it.each([
      ["a pending payment", { status: "pending" as const }],
      ["an already refunded payment", { status: "refunded" as const }],
      ["a failed payment", { status: "failed" as const }],
      ["a Razorpay payment", { provider: "razorpay" as const }],
      ["a payment with no capture id", { provider_capture_id: null }],
    ])("does not offer a refund for %s", async (_label, change) => {
      detail["aaaaa-0001"] = captured(change);
      open();
      await screen.findByRole("heading", { name: "Transaction Details" });
      await screen.findByText("Settlement breakdown");
      expect(screen.queryByRole("button", { name: /Issue Full Refund/ })).not.toBeInTheDocument();
    });
  });

  describe("confirmation", () => {
    it("asks for confirmation with the amount and a warning that it cannot be undone", async () => {
      const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
      const { user } = open();
      await user.click(await refundButton());
      expect(confirm).toHaveBeenCalledWith("Refund $250.00 to the buyer via PayPal? This cannot be undone.");
    });

    it("does nothing at all if the user declines", async () => {
      vi.spyOn(window, "confirm").mockReturnValue(false);
      const { user } = open();
      await user.click(await refundButton());
      expect(post).not.toHaveBeenCalled();
      expect(screen.queryByText("Refund issued")).not.toBeInTheDocument();
      expect(await refundButton()).toBeEnabled();
    });
  });

  describe("success", () => {
    it("requests the refund for exactly this transaction and confirms with a toast", async () => {
      vi.spyOn(window, "confirm").mockReturnValue(true);
      const { user } = open();
      await user.click(await refundButton());
      await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
      expect(post).toHaveBeenCalledWith("/api/transactions/aaaaa-0001/refund");
      expect(await screen.findByText("Refund issued")).toBeInTheDocument();
    });

    it("invalidates everything a refund changes: ledger, summary, dashboard, invoices, notifications and analytics", async () => {
      vi.spyOn(window, "confirm").mockReturnValue(true);
      const { user, client } = open();
      const spy = vi.spyOn(client, "invalidateQueries");
      await user.click(await refundButton());
      await screen.findByText("Refund issued");
      const keys = spy.mock.calls.map((c) => (c[0]!.queryKey as string[])[0]);
      expect(keys).toEqual(expect.arrayContaining(["transactions", "dashboard", "invoices", "invoice", "notifications", "analytics"]));
    });

    it("re-queries the ledger and summary after the refund", async () => {
      vi.spyOn(window, "confirm").mockReturnValue(true);
      const { user } = open();
      await user.click(await refundButton());
      await screen.findByText("Refund issued");
      await waitFor(() => expect(requests(get, "/api/transactions/summary").length).toBeGreaterThan(1));
      await waitFor(() => expect(requests(get, "/api/transactions").length).toBeGreaterThan(1));
      expect(await (await table()).findByText("refunded")).toBeInTheDocument();
    });

    it("the details panel shows the payment as refunded and drops the refund button", async () => {
      vi.spyOn(window, "confirm").mockReturnValue(true);
      const { user } = open();
      await user.click(await refundButton());
      await screen.findByText("Refund issued");
      await waitFor(() => expect(requests(get, "/api/transactions/aaaaa-0001").length).toBeGreaterThan(1));
      await waitFor(() => expect(screen.queryByRole("button", { name: /Issue Full Refund/ })).not.toBeInTheDocument());
    });
  });

  describe("failure", () => {
    const failWith = (message: string, status = 502) => { refundBehaviour = () => { throw new ApiError(message, status, "refund_failed"); }; };

    it("shows the provider's reason and leaves the payment untouched", async () => {
      failWith("PayPal declined the refund");
      vi.spyOn(window, "confirm").mockReturnValue(true);
      const { user } = open();
      await user.click(await refundButton());
      expect(await screen.findByText("PayPal declined the refund")).toBeInTheDocument();
      expect(screen.queryByText("Refund issued")).not.toBeInTheDocument();
      expect(screen.getAllByText("completed").length).toBeGreaterThan(0);
      expect(screen.queryByText("refunded")).not.toBeInTheDocument();
    });

    it("re-enables the button so the user can retry after a failure", async () => {
      failWith("Temporary error", 503);
      vi.spyOn(window, "confirm").mockReturnValue(true);
      const { user } = open();
      await user.click(await refundButton());
      await screen.findByText("Temporary error");
      expect(await refundButton()).toBeEnabled();
    });

    it("does not refresh any data after a failed refund", async () => {
      failWith("PayPal declined the refund");
      vi.spyOn(window, "confirm").mockReturnValue(true);
      const { user, client } = open();
      await table();
      const spy = vi.spyOn(client, "invalidateQueries");
      await user.click(await refundButton());
      await screen.findByText("PayPal declined the refund");
      expect(spy).not.toHaveBeenCalled();
    });

    it("surfaces a duplicate-refund rejection from the server (already refunded elsewhere)", async () => {
      failWith("This payment has already been refunded", 409);
      vi.spyOn(window, "confirm").mockReturnValue(true);
      const { user } = open();
      await user.click(await refundButton());
      expect(await screen.findByText("This payment has already been refunded")).toBeInTheDocument();
    });
  });

  describe("preventing repeated submissions", () => {
    it("disables the button while the refund is in flight and sends one request even on a double click", async () => {
      const d = deferred();
      refundBehaviour = () => d.promise;
      const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
      const { user } = open();
      const btn = await refundButton();
      await user.dblClick(btn);
      await waitFor(() => expect(refundButtonSync()).toBeDisabled());
      await user.click(refundButtonSync()); // a further click on the disabled button is ignored
      expect(post).toHaveBeenCalledTimes(1);
      expect(confirm).toHaveBeenCalledTimes(1);
      d.resolve({ ...detail["aaaaa-0001"]!, status: "refunded" });
      await screen.findByText("Refund issued");
      expect(post).toHaveBeenCalledTimes(1);
    });

    it("does not show success until the server has answered", async () => {
      const d = deferred();
      refundBehaviour = () => d.promise;
      vi.spyOn(window, "confirm").mockReturnValue(true);
      const { user } = open();
      await user.click(await refundButton());
      await waitFor(() => expect(refundButtonSync()).toBeDisabled());
      expect(screen.queryByText("Refund issued")).not.toBeInTheDocument();
      d.resolve({});
      expect(await screen.findByText("Refund issued")).toBeInTheDocument();
    });

    it("allows exactly one more attempt after a failure, not a burst", async () => {
      let calls = 0;
      refundBehaviour = () => { calls += 1; throw new ApiError("Temporary error", 503); };
      vi.spyOn(window, "confirm").mockReturnValue(true);
      const { user } = open();
      await user.click(await refundButton());
      await screen.findByText("Temporary error");
      await user.click(await refundButton());
      await waitFor(() => expect(calls).toBe(2));
    });
  });
});

const refundButtonSync = () => screen.getByRole("button", { name: /Issue Full Refund/ });

describe("details: lifecycle events, identifiers and provider differences", () => {
  const withEvents = (events: Transaction["events"], o: Partial<Transaction> = {}) => { detail["aaaaa-0001"] = captured({ events, ...o }); };

  it("labels known lifecycle events, appends their detail, and shows unknown event types verbatim", async () => {
    withEvents([
      { type: "order_created", at: "2026-01-01T10:00:00Z", detail: null },
      { type: "captured", at: "2026-01-01T10:05:00Z", detail: "Capture verified" },
      { type: "brand_new_event", at: "2026-01-01T10:06:00Z", detail: null },
    ]);
    open();
    expect(await screen.findByText("Order Created")).toBeInTheDocument();
    expect(screen.getByText("Payment Captured & Verified")).toBeInTheDocument();
    expect(screen.getByText(/· Capture verified/)).toBeInTheDocument();
    expect(screen.getByText("brand_new_event")).toBeInTheDocument();
    expect(screen.getByText("3 events")).toBeInTheDocument();
  });

  it("surfaces security-relevant events (amount mismatch, invalid signature) with their labels", async () => {
    withEvents([
      { type: "amount_mismatch", at: "2026-01-01T10:00:00Z", detail: "expected 250.00, captured 25.00" },
      { type: "signature_invalid", at: "2026-01-01T10:01:00Z", detail: null },
    ]);
    open();
    expect(await screen.findByText("Amount Mismatch Flagged")).toBeInTheDocument();
    expect(screen.getByText(/expected 250\.00, captured 25\.00/)).toBeInTheDocument();
    expect(screen.getByText("Invalid Signature Rejected")).toBeInTheDocument();
  });

  it("copies the capture and order ids", async () => {
    const { user } = open();
    const tile = (await screen.findByText("Capture ID")).closest("div.flex")!;
    await user.click(within(tile as HTMLElement).getByRole("button"));
    await expect(navigator.clipboard.readText()).resolves.toBe("CAP-1");
    const orderTile = screen.getByText("Order ID").closest("div.flex")!;
    await user.click(within(orderTile as HTMLElement).getByRole("button"));
    await expect(navigator.clipboard.readText()).resolves.toBe("ORDERABC12345");
  });

  it("shows a dash and no copy button for an identifier that does not exist yet", async () => {
    withEvents([], { provider_capture_id: null, status: "pending" });
    open();
    const tile = (await screen.findByText("Capture ID")).closest("div.flex")!;
    expect(within(tile as HTMLElement).getByText("—")).toBeInTheDocument();
    expect(within(tile as HTMLElement).queryByRole("button")).not.toBeInTheDocument();
  });

  it("uses Razorpay naming for a Razorpay payment", async () => {
    withEvents([], { provider: "razorpay", payment_method: "upi" });
    open();
    expect(await screen.findByText("Razorpay engine identifiers")).toBeInTheDocument();
    expect(screen.getByText("Razorpay fee")).toBeInTheDocument();
    expect(screen.getByText("upi")).toBeInTheDocument();
  });

  it("copes with a buyer-less payment: Unassigned, no email on file, payment method n/a, no invoice link", async () => {
    withEvents([], { customer: null, payment_method: null, invoice_id: null, invoice_number: null });
    open();
    expect(await screen.findByText("No email on file")).toBeInTheDocument();
    expect(screen.getByText("n/a")).toBeInTheDocument();
    expect(screen.queryByText(/Linked invoice/)).not.toBeInTheDocument();
  });

  it("filters the ledger to refunded payments", async () => {
    const { user } = open();
    await table();
    await user.click(screen.getByRole("tab", { name: "Refunded" }));
    await waitFor(() => expect(lastList()).toMatchObject({ status: "refunded" }));
  });
});

describe("refund: transaction details stay in sync", () => {
  const panel = () => within(screen.getByRole("heading", { name: "Transaction Details" }).closest("section")! as HTMLElement);
  const confirmYes = () => vi.spyOn(window, "confirm").mockReturnValue(true);

  it("the panel's status badge changes from completed to refunded", async () => {
    confirmYes();
    const { user } = open();
    await screen.findByRole("heading", { name: "Transaction Details" });
    expect(panel().getByText("completed")).toBeInTheDocument();
    await user.click(await refundButton());
    await waitFor(() => expect(panel().getByText("refunded")).toBeInTheDocument());
    expect(panel().queryByText("completed")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Issue Full Refund/ })).not.toBeInTheDocument();
  });

  it("refetches exactly this transaction's details after the refund", async () => {
    confirmYes();
    const { user } = open();
    await user.click(await refundButton());
    await screen.findByText("Refund issued");
    expect(requests(get, "/api/transactions/aaaaa-0001")).toHaveLength(2); // initial load + post-refund refresh
    expect(requests(get, "/api/transactions/bbbbb-0002")).toHaveLength(0); // other transactions are not touched
  });

  it("invalidates the details panel's own query key ('transaction', singular), not just the ledger's", async () => {
    confirmYes();
    const { user, client } = open();
    const spy = vi.spyOn(client, "invalidateQueries");
    await user.click(await refundButton());
    await screen.findByText("Refund issued");
    const keys = spy.mock.calls.map((c) => JSON.stringify(c[0]!.queryKey));
    expect(keys).toContain(JSON.stringify(["transaction"]));
    expect(keys).toContain(JSON.stringify(["transactions"]));
    expect((client.getQueryData(["transaction", "aaaaa-0001"]) as Transaction).status).toBe("refunded");
  });

  it("does not refresh unrelated queries such as payment links or customers", async () => {
    confirmYes();
    const { user, client } = open();
    const spy = vi.spyOn(client, "invalidateQueries");
    await user.click(await refundButton());
    await screen.findByText("Refund issued");
    const keys = spy.mock.calls.map((c) => (c[0]!.queryKey as string[])[0]);
    expect(keys).not.toContain("links");
    expect(keys).not.toContain("customers");
  });

  it("keeps the button disabled until the panel has refreshed, so a second refund cannot be started in between", async () => {
    confirmYes();
    const refetch = deferred<Transaction>();
    let detailCalls = 0;
    serve({ ...routes(), "GET /api/transactions/aaaaa-0001": () => (++detailCalls === 1 ? detail["aaaaa-0001"] : refetch.promise) });
    const { user } = open();
    await user.click(await refundButton());
    await waitFor(() => expect(detailCalls).toBe(2)); // POST done, refresh in flight
    expect(screen.getByRole("button", { name: /Issue Full Refund/ })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Issue Full Refund/ }));
    expect(post).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Refund issued")).not.toBeInTheDocument(); // not announced until the panel is accurate
    refetch.resolve({ ...detail["aaaaa-0001"]!, status: "refunded" });
    expect(await screen.findByText("Refund issued")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: /Issue Full Refund/ })).not.toBeInTheDocument());
  });

  it("still succeeds if the follow-up refresh fails (the refund itself went through)", async () => {
    confirmYes();
    let detailCalls = 0;
    serve({ ...routes(), "GET /api/transactions/aaaaa-0001": () => { if (++detailCalls > 1) throw new ApiError("Temporarily unavailable", 503); return detail["aaaaa-0001"]; } });
    const { user } = open();
    await user.click(await refundButton());
    expect(await screen.findByText("Refund issued")).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("a refund that PayPal has not completed yet leaves the payment refundable and shows the pending event", async () => {
    confirmYes();
    refundBehaviour = () => {
      detail["aaaaa-0001"] = { ...detail["aaaaa-0001"]!, events: [...detail["aaaaa-0001"]!.events, { type: "refund_pending", at: "2026-01-01T11:00:00Z", detail: "PayPal refund status PENDING" }] };
      return detail["aaaaa-0001"];
    };
    const { user } = open();
    await user.click(await refundButton());
    expect(await screen.findByText("Refund Pending")).toBeInTheDocument();
    expect(panel().getByText("completed")).toBeInTheDocument();
  });

  it("when the server reports it was already refunded, the panel simply catches up", async () => {
    confirmYes();
    refundBehaviour = () => { detail["aaaaa-0001"] = { ...detail["aaaaa-0001"]!, status: "refunded" }; return detail["aaaaa-0001"]; };
    const { user } = open();
    await user.click(await refundButton());
    await waitFor(() => expect(panel().getByText("refunded")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /Issue Full Refund/ })).not.toBeInTheDocument();
  });

  describe("when the refund API fails", () => {
    const failing = () => { refundBehaviour = () => { throw new ApiError("PayPal declined the refund", 502, "refund_failed"); }; confirmYes(); };

    it("keeps showing the true state: still completed, button still available, details not refetched", async () => {
      failing();
      const { user } = open();
      await user.click(await refundButton());
      expect(await screen.findByText("PayPal declined the refund")).toBeInTheDocument();
      expect(panel().getByText("completed")).toBeInTheDocument();
      expect(panel().queryByText("refunded")).not.toBeInTheDocument();
      expect(await refundButton()).toBeEnabled();
      expect(requests(get, "/api/transactions/aaaaa-0001")).toHaveLength(1);
    });

    it("does not announce success", async () => {
      failing();
      const { user } = open();
      await user.click(await refundButton());
      await screen.findByText("PayPal declined the refund");
      expect(screen.queryByText("Refund issued")).not.toBeInTheDocument();
    });
  });
});

describe("partial refunds in the details panel", () => {
  const panel = () => within(screen.getByRole("heading", { name: "Transaction Details" }).closest("section")! as HTMLElement);
  const completedRefund = { id: "r1", provider_refund_id: "REF1", amount: 100, currency: "USD", status: "completed" as const, source: "api", reason: "goodwill", created_at: "2026-01-02T10:00:00Z" };
  const partial = (o: Partial<Transaction> = {}) => captured({ status: "partially_refunded", refunded_amount: 100, refunds: [completedRefund], ...o });
  const setPartial = (o: Partial<Transaction> = {}) => { const t = partial(o); detail["aaaaa-0001"] = t; list = [t, failed]; return t; };

  it("shows a partially refunded payment with the original amount, the refunded amount and the refund history", async () => {
    setPartial();
    open();
    expect(await screen.findByText("-$100.00 USD")).toBeInTheDocument(); // Refunded row
    expect(panel().getByText("partially refunded")).toBeInTheDocument();
    expect(panel().getByText("$250.00 USD")).toBeInTheDocument(); // the original payment is not rewritten
    const refunds = within(screen.getByText("Refunds").parentElement as HTMLElement);
    expect(refunds.getByText("$100.00")).toBeInTheDocument();
    expect(refunds.getByText(/goodwill/)).toBeInTheDocument();
    expect(refunds.getByText("completed")).toBeInTheDocument();
  });

  it("the ledger row carries the partially refunded status", async () => {
    setPartial();
    open();
    expect(await (await table()).findByText("partially refunded")).toBeInTheDocument();
  });

  it("offers to refund only what is left, and the confirmation names that amount", async () => {
    setPartial();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { user } = open();
    const btn = await screen.findByRole("button", { name: /Refund Remaining/ });
    expect(btn).toHaveTextContent("Refund Remaining ($150.00)");
    await user.click(btn);
    expect(confirm).toHaveBeenCalledWith("Refund $150.00 to the buyer via PayPal? This cannot be undone.");
    expect(post).not.toHaveBeenCalled();
  });

  it("refunding the remainder requests no amount (the server refunds what is left) and ends in refunded", async () => {
    setPartial();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    refundBehaviour = () => {
      detail["aaaaa-0001"] = partial({ status: "refunded", refunded_amount: 250, refunds: [completedRefund, { ...completedRefund, id: "r2", provider_refund_id: "REF2", amount: 150 }] });
      list = [detail["aaaaa-0001"]!, failed];
      return detail["aaaaa-0001"];
    };
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: /Refund Remaining/ }));
    expect(post).toHaveBeenCalledWith("/api/transactions/aaaaa-0001/refund");
    await waitFor(() => expect(panel().getByText("refunded")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /Refund/ })).not.toBeInTheDocument();
    expect(await screen.findByText("-$250.00 USD")).toBeInTheDocument();
  });

  it("offers no refund once nothing is left, even if the status is stale", async () => {
    setPartial({ refunded_amount: 250 });
    open();
    await screen.findByText("-$250.00 USD");
    expect(screen.queryByRole("button", { name: /Refund (Remaining|)/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Issue Full Refund/ })).not.toBeInTheDocument();
  });

  it("a refund that is still pending at the provider is shown, and its amount is not offered for refund again", async () => {
    setPartial({ refunded_amount: 0, status: "completed", refunds: [{ ...completedRefund, status: "pending", amount: 250 }] });
    open();
    expect(await screen.findByText("pending", { selector: "span" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Issue Full Refund|Refund Remaining/ })).not.toBeInTheDocument();
  });

  it("a payment that was never refunded keeps the plain 'Issue Full Refund' wording", async () => {
    open();
    expect(await refundButton()).toHaveTextContent("Issue Full Refund ($250.00)");
    expect(screen.queryByText("Refunds")).not.toBeInTheDocument();
  });

  it("labels the new refund and review events", async () => {
    setPartial({ events: [{ type: "refund_completed", at: "2026-01-02T10:00:00Z", detail: "REF1: 100.00 USD" }, { type: "payment_unmatched", at: "2026-01-02T10:01:00Z", detail: "invoice was cancelled" }] });
    open();
    expect(await screen.findByText("Refund Completed")).toBeInTheDocument();
    expect(screen.getByText("Payment Needs Review")).toBeInTheDocument();
  });
});
