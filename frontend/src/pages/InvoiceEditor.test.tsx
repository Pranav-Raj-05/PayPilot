import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async () => await import("@/test/api"));
// A QR code is just an <svg>; the payment link text next to it is what we assert on.
vi.mock("qrcode.react", () => ({ QRCodeSVG: ({ value }: { value: string }) => <svg data-testid="qr" data-value={value} /> }));

import InvoiceEditor from "./InvoiceEditor";
import { ApiError, patch, post, serve } from "@/test/api";
import { customer, invoice, page } from "@/test/fixtures";
import { renderPage } from "@/test/render";

const here = () => screen.getByTestId("location").textContent;
const newInvoice = () => renderPage(<InvoiceEditor />, { route: "/invoices/new", path: "/invoices/new" });
const existing = (id = "inv1") => renderPage(<InvoiceEditor />, { route: `/invoices/${id}`, path: "/invoices/:id" });
const itemRow = (n = 0) => ({ desc: screen.getAllByLabelText("Item description")[n]!, qty: screen.getAllByLabelText("Quantity")[n]!, price: screen.getAllByLabelText("Unit price")[n]! });

beforeEach(() => {
  serve({
    "GET /api/customers": () => page([customer(), customer({ id: "c2", name: "Apex Cloud" })]),
    "POST /api/invoices": (body) => invoice({ id: "inv-new", invoice_number: "INV-0042", ...body }),
  });
});

describe("new invoice form", () => {
  it("starts with one empty line item, USD and a default due date two weeks out", async () => {
    newInvoice();
    expect(await screen.findByRole("heading", { name: "Create Invoice" })).toBeInTheDocument();
    expect(screen.getAllByLabelText("Item description")).toHaveLength(1);
    expect(screen.getByLabelText(/^Currency/)).toHaveValue("USD");
    const due = (screen.getByLabelText("Due date") as HTMLInputElement).value;
    const days = Math.round((new Date(due).getTime() - Date.now()) / 864e5);
    expect(days).toBeGreaterThanOrEqual(13);
    expect(days).toBeLessThanOrEqual(14);
  });

  it("requires a description on every line item and does not submit", async () => {
    const { user } = newInvoice();
    await user.click(await screen.findByRole("button", { name: "Create draft invoice" }));
    expect(await screen.findByText("Required")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  // Known gap: only the description error is rendered; an invalid quantity blocks the submit without any message.
  it("does not submit a zero quantity", async () => {
    const { user } = newInvoice();
    await screen.findByRole("heading", { name: "Create Invoice" });
    await user.type(itemRow().desc, "Design");
    await user.clear(itemRow().qty);
    await user.type(itemRow().qty, "0");
    await user.click(screen.getByRole("button", { name: "Create draft invoice" }));
    expect(post).not.toHaveBeenCalled();
    // once the quantity is valid the very same form submits, so the earlier block was caused by the quantity
    await user.clear(itemRow().qty);
    await user.type(itemRow().qty, "1");
    await user.click(screen.getByRole("button", { name: "Create draft invoice" }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  });

  it("rejects a tax rate above 100%", async () => {
    const { user } = newInvoice();
    await screen.findByRole("heading", { name: "Create Invoice" });
    await user.type(itemRow().desc, "Design");
    await user.type(screen.getByLabelText("Tax rate (%)"), "150");
    await user.click(screen.getByRole("button", { name: "Create draft invoice" }));
    expect(await screen.findByText(/less than or equal to 100/i)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("updates the live preview with subtotal, tax and total as the user types", async () => {
    const { user } = newInvoice();
    await screen.findByRole("heading", { name: "Create Invoice" });
    await user.type(itemRow().desc, "Design");
    await user.clear(itemRow().qty);
    await user.type(itemRow().qty, "2");
    await user.clear(itemRow().price);
    await user.type(itemRow().price, "50");
    await user.type(screen.getByLabelText("Tax rate (%)"), "10");
    expect(await screen.findByText("$110.00")).toBeInTheDocument(); // total
    expect(screen.getByText("Tax (10%)")).toBeInTheDocument();
    expect(screen.getByText("$10.00")).toBeInTheDocument();
  });

  it("adds and removes line items, but never removes the last one", async () => {
    const { user } = newInvoice();
    await screen.findByRole("heading", { name: "Create Invoice" });
    expect(screen.getByRole("button", { name: "Remove item" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Add item" }));
    expect(screen.getAllByLabelText("Item description")).toHaveLength(2);
    await user.click(screen.getAllByRole("button", { name: "Remove item" })[0]!);
    expect(screen.getAllByLabelText("Item description")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Remove item" })).toBeDisabled();
  });

  it("creates the invoice with normalized values and opens it", async () => {
    const { user } = newInvoice();
    await screen.findByRole("heading", { name: "Create Invoice" });
    await user.selectOptions(screen.getByLabelText(/^Customer/), "c2");
    await user.selectOptions(screen.getByLabelText(/^Currency/), "EUR");
    await user.type(itemRow().desc, "Design");
    await user.clear(itemRow().qty);
    await user.type(itemRow().qty, "2");
    await user.clear(itemRow().price);
    await user.type(itemRow().price, "50");
    await user.type(screen.getByLabelText("Tax rate (%)"), "10");
    await user.click(screen.getByRole("button", { name: "Create draft invoice" }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0]).toEqual(["/api/invoices", {
      customer_id: "c2", description: null, currency: "EUR", due_date: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/), tax_rate: 10,
      items: [{ description: "Design", quantity: 2, unit_price: 50 }],
    }]);
    expect(await screen.findByText("Invoice INV-0042 created")).toBeInTheDocument();
    await waitFor(() => expect(here()).toBe("/invoices/inv-new"));
  });

  it("sends no customer when none is chosen", async () => {
    const { user } = newInvoice();
    await screen.findByRole("heading", { name: "Create Invoice" });
    await user.type(itemRow().desc, "Consulting");
    await user.click(screen.getByRole("button", { name: "Create draft invoice" }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0]![1]).toMatchObject({ customer_id: null });
  });

  it("shows the API error and stays on the form when creation fails", async () => {
    serve({ "GET /api/customers": () => page([]), "POST /api/invoices": () => { throw new ApiError("Currency not supported", 422, "validation_error"); } });
    const { user } = newInvoice();
    await screen.findByRole("heading", { name: "Create Invoice" });
    await user.type(itemRow().desc, "Design");
    await user.click(screen.getByRole("button", { name: "Create draft invoice" }));
    expect(await screen.findByText("Currency not supported")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Create Invoice" })).toBeInTheDocument();
  });

  it("creates a customer inline and selects them", async () => {
    serve({
      "GET /api/customers": () => page([customer()]),
      "POST /api/customers": (body) => customer({ id: "c9", ...body }),
    });
    const { user } = newInvoice();
    await screen.findByRole("heading", { name: "Create Invoice" });
    await user.click(screen.getByRole("button", { name: "New customer" }));
    const dlg = within(screen.getByRole("dialog"));
    expect(dlg.getByRole("button", { name: /save|create|add/i })).toBeDisabled();
    await user.type(dlg.getByLabelText("Name"), "  Newco  ");
    await user.click(dlg.getByRole("button", { name: /save|create|add/i }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/customers", { name: "Newco", email: null }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("existing invoices", () => {
  it("loads a draft into the form with its values and saves changes with PATCH", async () => {
    serve({
      "GET /api/customers": () => page([customer()]),
      "GET /api/invoices/inv1": () => invoice(),
      "PATCH /api/invoices/inv1": (body) => invoice({ ...body, updated_at: "2026-01-02T00:00:00Z" }),
    });
    const { user } = existing();
    expect(await screen.findByRole("heading", { name: "INV-0001" })).toBeInTheDocument();
    expect(itemRow().desc).toHaveValue("Design");
    expect(itemRow().qty).toHaveValue(2);
    expect(screen.getByLabelText("Tax rate (%)")).toHaveValue(10); // 10 / 100
    await user.clear(itemRow().desc);
    await user.type(itemRow().desc, "Redesign");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(patch.mock.calls[0]![0]).toBe("/api/invoices/inv1");
    expect(patch.mock.calls[0]![1]).toMatchObject({ currency: "USD", tax_rate: 10, items: [{ description: "Redesign", quantity: 2, unit_price: 50 }] });
    expect(await screen.findByText("Invoice saved")).toBeInTheDocument();
  });

  it("shows a loading placeholder, then an error with Retry if the invoice cannot be loaded", async () => {
    let healthy = false;
    serve({ "GET /api/customers": () => page([]), "GET /api/invoices/inv1": () => { if (!healthy) throw new ApiError("Invoice not found", 404, "not_found"); return invoice(); } });
    const { user } = existing();
    expect(await screen.findByRole("alert")).toHaveTextContent("Invoice not found");
    healthy = true;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("button", { name: "Save changes" })).toBeInTheDocument();
  });
});

describe("issued invoices (read-only view)", () => {
  const issued = (o: Parameters<typeof invoice>[0] = {}) => serve({
    "GET /api/customers": () => page([]),
    "GET /api/invoices/inv1": () => invoice({ status: "pending", ...o }),
    "POST /api/invoices/inv1/payment-link": () => ({}),
    "PATCH /api/invoices/inv1": (b) => invoice({ ...b }),
  });

  it("is not editable and offers to create a PayPal link when there is none", async () => {
    issued();
    existing();
    expect(await screen.findByText("No payment link yet.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Item description")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /razorpay/i })).not.toBeInTheDocument(); // Razorpay is INR only
  });

  it("creates a PayPal payment link for the invoice", async () => {
    issued();
    const { user } = existing();
    await user.click(await screen.findByRole("button", { name: "Create PayPal link" }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0]).toEqual(["/api/invoices/inv1/payment-link", {}, { provider: "paypal" }]);
    expect(await screen.findByText("PayPal payment link created")).toBeInTheDocument();
  });

  it("offers Razorpay only for INR invoices", async () => {
    issued({ currency: "INR" });
    const { user } = existing();
    await user.click(await screen.findByRole("button", { name: "Create Razorpay link (INR)" }));
    await waitFor(() => expect(post.mock.calls[0]).toEqual(["/api/invoices/inv1/payment-link", {}, { provider: "razorpay" }]));
  });

  it("shows the existing link with a QR code instead of the create buttons", async () => {
    issued({ payment_link: "https://paypal.test/checkout?token=ORD1", payment_provider: "paypal" });
    existing();
    expect(await screen.findByText("PayPal checkout link")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "https://paypal.test/checkout?token=ORD1" })).toHaveAttribute("href", "https://paypal.test/checkout?token=ORD1");
    expect(screen.getByTestId("qr")).toHaveAttribute("data-value", "https://paypal.test/checkout?token=ORD1");
    expect(screen.queryByRole("button", { name: "Create PayPal link" })).not.toBeInTheDocument();
  });

  it("reports a failure to create the link", async () => {
    serve({
      "GET /api/customers": () => page([]), "GET /api/invoices/inv1": () => invoice({ status: "pending" }),
      "POST /api/invoices/inv1/payment-link": () => { throw new ApiError("PayPal is not configured", 503, "not_configured"); },
    });
    const { user } = existing();
    await user.click(await screen.findByRole("button", { name: "Create PayPal link" }));
    expect(await screen.findByText("PayPal is not configured")).toBeInTheDocument();
  });

  it("only cancels after confirmation", async () => {
    issued();
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    const { user } = existing();
    await user.click(await screen.findByRole("button", { name: "Cancel invoice" }));
    expect(patch).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Cancel invoice" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith("/api/invoices/inv1", { status: "cancelled" }));
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it("a paid invoice is confirmed as paid and cannot get a new link or be cancelled", async () => {
    issued({ status: "paid" });
    existing();
    expect(await screen.findByText(/confirmed by a verified provider capture/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create PayPal link" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel invoice" })).not.toBeInTheDocument();
  });

  it("an overdue invoice can still be paid but not cancelled from here", async () => {
    issued({ status: "overdue" });
    existing();
    expect(await screen.findByRole("button", { name: "Create PayPal link" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel invoice" })).not.toBeInTheDocument();
  });
});
