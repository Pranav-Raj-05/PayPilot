import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async () => await import("@/test/api"));
vi.mock("@/utils/csv", () => ({ downloadCsv: vi.fn() }));
vi.mock("qrcode.react", () => ({ QRCodeSVG: ({ value }: { value: string }) => <svg data-testid="qr" data-value={value} /> }));

import Invoices from "./Invoices";
import { ApiError, deferred, del, get, post, requests, serve } from "@/test/api";
import { customer, invoice } from "@/test/fixtures";
import { renderPage } from "@/test/render";
import { downloadCsv } from "@/utils/csv";
import type { Invoice, InvoiceSummary } from "@/types";

const summary = (o: Partial<InvoiceSummary> = {}): InvoiceSummary => ({
  currency: "USD", counts: { all: 10, paid: 3, pending: 4, overdue: 2, draft: 1 }, total_invoiced: 5000,
  paid: { total: 2000, count: 3 }, pending: { total: 1500, count: 4 }, overdue: { total: 600, count: 2 }, ...o,
});
const draft = invoice({ id: "d1", invoice_number: "INV-0001", status: "draft" });
const pending = invoice({ id: "p1", invoice_number: "INV-0002", status: "pending", total_amount: 480, customer: { id: "c2", name: "Apex Cloud", email: "ap@x.io" }, payment_link: "https://pay.test/p1", payment_provider: "paypal" });
const overdue = invoice({ id: "o1", invoice_number: "INV-0003", status: "overdue", total_amount: 300 });
const paid = invoice({ id: "pd1", invoice_number: "INV-0004", status: "paid" });

let items: Invoice[];
let sum: InvoiceSummary;
const byId = (id: string) => [draft, pending, overdue, paid].find((i) => i.id === id)!;
const routes = () => ({
  "GET /api/invoices/summary": () => sum,
  "GET /api/invoices": () => ({ items, total: items.length, page: 1, page_size: 8 }),
  ...Object.fromEntries(["d1", "p1", "o1", "pd1"].flatMap((id) => [
    [`GET /api/invoices/${id}`, () => byId(id)],
    [`GET /api/invoices/${id}/activity`, () => [{ title: "Invoice created", detail: null, at: "2026-01-01T10:00:00Z", done: true }, { title: "Payment received", detail: null, at: null, done: false }]],
  ])),
  "POST /api/invoices/d1/payment-link": () => ({}),
  "DELETE /api/invoices/d1": () => undefined,
});
const open = () => renderPage(<Invoices />, { path: "/" });
const lastList = () => requests(get, "/api/invoices").at(-1)![1];
const ledger = async () => within(await screen.findByRole("table"));

beforeEach(() => { items = [pending, overdue, draft, paid]; sum = summary(); serve(routes()); });

describe("invoice ledger states", () => {
  it("shows skeletons while loading", async () => {
    const d = deferred();
    serve({ ...routes(), "GET /api/invoices": () => d.promise });
    open();
    expect(document.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
    expect(screen.queryByText("No invoices")).not.toBeInTheDocument();
    d.resolve({ items, total: 4, page: 1, page_size: 8 });
    expect(await ledger()).toBeTruthy();
  });

  it("lists invoices with client, amount, currency and status", async () => {
    open();
    const t = await ledger();
    expect(await t.findByText("INV-0002")).toBeInTheDocument();
    expect(t.getByText("Apex Cloud")).toBeInTheDocument();
    expect(t.getByText("$480.00")).toBeInTheDocument();
    expect(t.getByText("overdue")).toBeInTheDocument();
    expect(screen.getByText("Showing 4 of 4")).toBeInTheDocument();
  });

  it("shows summary cards and per-status counts on the tabs", async () => {
    open();
    expect(await screen.findByText("$5,000.00")).toBeInTheDocument();
    expect(screen.getByText("3 settled")).toBeInTheDocument();
    expect(screen.getByText("2 invoices")).toBeInTheDocument();
    expect(within(screen.getByRole("tab", { name: /Overdue/ })).getByText("2")).toBeInTheDocument();
    expect(within(screen.getByRole("tab", { name: /Drafts/ })).getByText("1")).toBeInTheDocument();
  });

  it("explains an empty ledger and offers to create an invoice", async () => {
    items = [];
    const { user } = open();
    expect(await screen.findByText("No invoices")).toBeInTheDocument();
    expect(screen.getByText("Select an invoice")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Create invoice" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/invoices/new");
  });

  it("shows the API error and recovers on Retry", async () => {
    let healthy = false;
    serve({ ...routes(), "GET /api/invoices": () => { if (!healthy) throw new ApiError("Invoices unavailable", 500); return { items, total: 4, page: 1, page_size: 8 }; } });
    const { user } = open();
    expect(await screen.findByText("Invoices unavailable")).toBeInTheDocument();
    healthy = true;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await (await ledger()).findByText("INV-0002")).toBeInTheDocument();
  });
});

describe("search, filters and paging", () => {
  it("filters by status tab and resets to page 1", async () => {
    const { user } = open();
    await ledger();
    await user.click(screen.getByRole("tab", { name: /Overdue/ }));
    await waitFor(() => expect(lastList()).toMatchObject({ status: "overdue", page: 1, page_size: 8 }));
    expect(screen.getByRole("tab", { name: /Overdue/ })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: /All/ }));
    await waitFor(() => expect(lastList().status).toBeUndefined());
  });

  it("searches by client or invoice number", async () => {
    const { user } = open();
    await ledger();
    await user.type(screen.getByLabelText("Search invoices"), "apex");
    await waitFor(() => expect(lastList()).toMatchObject({ search: "apex", page: 1 }));
  });

  it("pages through the ledger", async () => {
    serve({ ...routes(), "GET /api/invoices": () => ({ items, total: 20, page: 1, page_size: 8 }) });
    const { user } = open();
    expect(await screen.findByText("Showing 1–8 of 20")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(lastList()).toMatchObject({ page: 2 }));
  });

  it("exports the visible invoices as CSV", async () => {
    const { user } = open();
    await ledger();
    await user.click(screen.getByRole("button", { name: "Export CSV" }));
    const [name, rows] = vi.mocked(downloadCsv).mock.calls[0]!;
    expect(name).toBe("invoices.csv");
    expect(rows[0]).toEqual(["Invoice", "Customer", "Due", "Amount", "Currency", "Status"]);
    expect(rows).toHaveLength(5);
    expect(rows[1]).toEqual(["INV-0002", "Apex Cloud", "2026-02-01", 480, "USD", "pending"]);
  });
});

describe("overdue banner", () => {
  it("tells the user how much is overdue and offers AI reminder drafts", async () => {
    const { user } = open();
    expect(await screen.findByText(/2 invoices are overdue totalling \$600\.00/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Review AI Draft/ }));
    expect(screen.getByTestId("location")).toHaveTextContent("/assistant");
  });

  it("can be dismissed", async () => {
    const { user } = open();
    await screen.findByText(/overdue totalling/);
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/overdue totalling/)).not.toBeInTheDocument();
  });

  it("uses the singular for one invoice and is absent when nothing is overdue", async () => {
    sum = summary({ overdue: { total: 300, count: 1 } });
    const view = open();
    expect(await screen.findByText(/1 invoice is overdue totalling \$300\.00/)).toBeInTheDocument();
    view.unmount();
    sum = summary({ overdue: { total: 0, count: 0 } });
    open();
    await screen.findByText("Invoice Ledger");
    expect(screen.queryByText(/overdue totalling/)).not.toBeInTheDocument();
  });
});

describe("live preview", () => {
  it("previews the first invoice and switches when another row is clicked", async () => {
    const { user } = open();
    expect(await screen.findByRole("button", { name: "#INV-0002" })).toBeInTheDocument();
    expect(await screen.findByText("Pending customer checkout")).toBeInTheDocument();
    await user.click(await (await ledger()).findByText("INV-0003"));
    expect(await screen.findByRole("button", { name: "#INV-0003" })).toBeInTheDocument();
    expect(requests(get, "/api/invoices/o1")).toHaveLength(1);
  });

  it("shows billed-to, line items, totals and the audit trail", async () => {
    open();
    expect(await screen.findByText("Billed to")).toBeInTheDocument();
    expect(await screen.findByText("Design")).toBeInTheDocument();
    expect(screen.getByText("Total Due")).toBeInTheDocument();
    expect(await screen.findByText("Invoice created")).toBeInTheDocument();
    expect(screen.getByText("Payment received")).toBeInTheDocument();
  });

  it("shows the checkout link with a QR code for an invoice that has one", async () => {
    open();
    expect(await screen.findByTestId("qr")).toHaveAttribute("data-value", "https://pay.test/p1");
    expect(screen.getAllByText("https://pay.test/p1").length).toBeGreaterThan(0);
  });

  it("offers a reminder only for pending or overdue invoices", async () => {
    const { user } = open();
    expect(await screen.findByRole("button", { name: /Send Reminder/ })).toBeInTheDocument();
    await user.click(await (await ledger()).findByText("INV-0004")); // paid
    await screen.findByRole("button", { name: "#INV-0004" });
    expect(screen.queryByRole("button", { name: /Send Reminder/ })).not.toBeInTheDocument();
    await user.click((await ledger()).getByText("INV-0001")); // draft
    await screen.findByRole("button", { name: "#INV-0001" });
    expect(screen.queryByRole("button", { name: /Send Reminder/ })).not.toBeInTheDocument();
  });

  it("opens the assistant with a reminder request for the invoice", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: /Send Reminder/ }));
    expect(screen.getByTestId("location")).toHaveTextContent("/assistant");
  });

  it("creates a PayPal link for an invoice without one", async () => {
    items = [draft, pending];
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Create PayPal link" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/invoices/d1/payment-link", {}, { provider: "paypal" }));
    expect(await screen.findByText("PayPal link created")).toBeInTheDocument();
  });

  it("reports a failure to create the link", async () => {
    items = [draft, pending];
    serve({ ...routes(), "POST /api/invoices/d1/payment-link": () => { throw new ApiError("PayPal is not configured", 503); } });
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Create PayPal link" }));
    expect(await screen.findByText("PayPal is not configured")).toBeInTheDocument();
  });

  it("closes the preview", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Close preview" }));
    expect(await screen.findByText("Select an invoice")).toBeInTheDocument();
  });

  it("opens the full invoice from the preview heading", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "#INV-0002" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/invoices/p1");
  });
});

describe("deleting drafts", () => {
  it("only drafts have a delete button", async () => {
    open();
    await ledger();
    expect(screen.getAllByRole("button", { name: /^Delete INV/ })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Delete INV-0001" })).toBeInTheDocument();
  });

  it("does nothing unless the user confirms", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Delete INV-0001" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("INV-0001"));
    expect(del).not.toHaveBeenCalled();
  });

  it("deletes after confirmation without selecting the row", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Delete INV-0001" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("/api/invoices/d1"));
    expect(requests(get, "/api/invoices/d1")).toHaveLength(0); // the row click was not triggered
  });
});

describe("additional invoice flows", () => {
  it("opens the new-invoice form from the header", async () => {
    const { user } = open();
    await ledger();
    await user.click(screen.getByRole("button", { name: "Create Invoice" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/invoices/new");
  });

  it("opens the AI generator from the header", async () => {
    const { user } = open();
    await ledger();
    await user.click(screen.getByRole("button", { name: /AI Invoice Generator/ }));
    expect(screen.getByTestId("location")).toHaveTextContent("/assistant");
  });

  it("selects a row with the keyboard (Enter)", async () => {
    const { user } = open();
    const row = (await (await ledger()).findByText("INV-0003")).closest("tr")!;
    row.focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("button", { name: "#INV-0003" })).toBeInTheDocument();
  });

  it("returns to page 1 when the search text changes", async () => {
    serve({ ...routes(), "GET /api/invoices": () => ({ items, total: 30, page: 1, page_size: 8 }) });
    const { user } = open();
    await screen.findByText("Showing 1–8 of 30");
    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(lastList()).toMatchObject({ page: 2 }));
    await user.type(screen.getByLabelText("Search invoices"), "a");
    await waitFor(() => expect(lastList()).toMatchObject({ page: 1, search: "a" }));
  });

  it("confirms a deletion with a toast", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Delete INV-0001" }));
    expect(await screen.findByText("Draft deleted")).toBeInTheDocument();
  });

  it("shows the server's reason when a deletion is refused", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    serve({ ...routes(), "DELETE /api/invoices/d1": () => { throw new ApiError("Only draft invoices can be deleted", 409); } });
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Delete INV-0001" }));
    expect(await screen.findByText("Only draft invoices can be deleted")).toBeInTheDocument();
    expect(screen.queryByText("Draft deleted")).not.toBeInTheDocument();
  });

  it.each([["paid", "INV-0004"], ["cancelled", "INV-0005"]])("a %s invoice offers neither a new link nor a reminder", async (status, number) => {
    const extra = invoice({ id: "x1", invoice_number: number, status: status as Invoice["status"] });
    items = [extra];
    serve({ ...routes(), "GET /api/invoices/x1": () => extra, "GET /api/invoices/x1/activity": () => [] });
    open();
    expect(await screen.findByRole("button", { name: `#${number}` })).toBeInTheDocument();
    expect(await screen.findByText(/No payment link yet\./)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create PayPal link" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Send Reminder/ })).not.toBeInTheDocument();
  });

  it("does not claim a paid invoice is awaiting checkout, even if it has a link", async () => {
    const extra = invoice({ id: "x2", invoice_number: "INV-0006", status: "paid", payment_link: "https://pay.test/x2", payment_provider: "paypal" });
    items = [extra];
    serve({ ...routes(), "GET /api/invoices/x2": () => extra, "GET /api/invoices/x2/activity": () => [] });
    open();
    await screen.findByRole("button", { name: "#INV-0006" });
    expect(screen.queryByText("Pending customer checkout")).not.toBeInTheDocument();
  });

  it("tolerates an invoice without a customer", async () => {
    const orphan = invoice({ id: "x3", invoice_number: "INV-0007", status: "pending", customer: null, customer_id: null });
    items = [orphan];
    serve({ ...routes(), "GET /api/invoices/x3": () => orphan, "GET /api/invoices/x3/activity": () => [] });
    open();
    expect(await screen.findByText("No customer")).toBeInTheDocument();
  });
});
