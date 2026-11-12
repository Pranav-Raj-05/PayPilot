import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async () => await import("@/test/api"));
vi.mock("qrcode.react", () => ({ QRCodeSVG: ({ value }: { value: string }) => <svg data-testid="qr" data-value={value} />, QRCodeCanvas: () => <canvas /> }));

import PaymentLinks from "./PaymentLinks";
import { ApiError, deferred, post, serve } from "@/test/api";
import { customer, link, page, paypal } from "@/test/fixtures";
import type { PaymentLink } from "@/types";
import { renderPage } from "@/test/render";

const summary = { currency: "USD", total: 3, active: 2, paid: 1, paid_volume: 450, conversion_rate: 33.3 };
const consulting = link();
const retainer = link({ id: "link-0002-bbbb", description: "Monthly retainer", amount: 900, url: "https://pay.example/l/2", provider: "razorpay", currency: "INR", status: "paid" });
const workshop = link({ id: "link-0003-cccc", description: "Workshop", amount: 75, url: "https://pay.example/l/3", customer: { id: "c1", name: "Sarah Johnson", email: null } });

let links: PaymentLink[];
const routes = () => ({
  "GET /api/payment-links": () => links,
  "GET /api/payment-links/summary": () => summary,
  "GET /api/customers": () => page([customer()]),
  "GET /api/payments/paypal/status": () => paypal(),
  "POST /api/payment-links": (body: any) => {
    const created = link({ id: "link-new-0000", ...body, url: "https://pay.example/l/new" });
    links = [created, ...links];
    return created;
  },
  "POST /api/payment-links/link-0001-aaaa/cancel": () => ({ ...consulting, status: "cancelled" }),
});
const open = () => renderPage(<PaymentLinks />);
const table = () => within(screen.getByRole("table"));
const tableReady = async () => within(await screen.findByRole("table"));
const fillBasics = async (user: ReturnType<typeof open>["user"], title = "Consulting call", amount = "120") => {
  await user.type(screen.getByLabelText("Product or service title"), title);
  await user.type(screen.getByLabelText("Amount"), amount);
};

beforeEach(() => { links = [consulting, retainer, workshop]; serve(routes()); });

describe("link list", () => {
  it("lists links with amount, provider and status, and inspects the first one by default", async () => {
    open();
    expect(await (await tableReady()).findByText("Consulting")).toBeInTheDocument();
    expect(table().getByText("Monthly retainer")).toBeInTheDocument();
    expect(table().getByText("Razorpay")).toBeInTheDocument();
    expect(table().getByText("paid")).toBeInTheDocument();
    expect(screen.getByText("https://pay.example/l/1")).toBeInTheDocument(); // inspector
    expect(screen.getByText("Live · accepting payments")).toBeInTheDocument();
  });

  it("shows summary figures from the API", async () => {
    open();
    expect(await screen.findByText("$450.00")).toBeInTheDocument();
    expect(screen.getByText("2 Live")).toBeInTheDocument();
    expect(screen.getByText("33.3%")).toBeInTheDocument();
  });

  it("switches the inspector when another row is clicked", async () => {
    const { user } = open();
    await user.click(await (await tableReady()).findByText("Workshop"));
    expect(await screen.findByText("https://pay.example/l/3")).toBeInTheDocument();
  });

  it("shows an empty state when there are no links", async () => {
    links = [];
    open();
    expect(await screen.findByText("No payment links")).toBeInTheDocument();
    expect(screen.getByText("No link selected")).toBeInTheDocument();
  });

  it("shows the error and recovers on Retry", async () => {
    let healthy = false;
    serve({ ...routes(), "GET /api/payment-links": () => { if (!healthy) throw new ApiError("Server unavailable", 503); return links; } });
    const { user } = open();
    expect(await screen.findByText("Server unavailable")).toBeInTheDocument();
    healthy = true;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await (await tableReady()).findByText("Consulting")).toBeInTheDocument();
  });

  it("filters by search text (description or customer) and by status", async () => {
    const { user } = open();
    await (await tableReady()).findByText("Consulting");
    await user.type(screen.getByLabelText("Search links"), "sarah");
    await waitFor(() => expect(table().queryByText("Consulting")).not.toBeInTheDocument());
    expect(table().getByText("Workshop")).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Search links"));
    await user.selectOptions(screen.getByLabelText("Filter by status"), "paid");
    await waitFor(() => expect(table().queryByText("Workshop")).not.toBeInTheDocument());
    expect(table().getByText("Monthly retainer")).toBeInTheDocument();
  });

  it("tells the user when filters match nothing", async () => {
    const { user } = open();
    await (await tableReady()).findByText("Consulting");
    await user.type(screen.getByLabelText("Search links"), "zzz-no-match");
    expect(await screen.findByText("No payment links")).toBeInTheDocument();
  });

  it("paginates five links per page", async () => {
    links = Array.from({ length: 6 }, (_, i) => link({ id: `l-${i}`, description: `Link number ${i + 1}`, url: `https://pay.example/${i}` }));
    const { user } = open();
    expect(await screen.findByText("Showing 1–5 of 6")).toBeInTheDocument();
    expect(table().queryByText("Link number 6")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(await (await tableReady()).findByText("Link number 6")).toBeInTheDocument();
    expect(screen.getByText("Showing 6–6 of 6")).toBeInTheDocument();
  });

  it("shows the checkout URL as a QR code in a dialog", async () => {
    const { user } = open();
    await user.click((await screen.findAllByRole("button", { name: "Show QR code" }))[0]!);
    const dlg = within(screen.getByRole("dialog", { name: "Scan to pay" }));
    expect(dlg.getByTestId("qr")).toHaveAttribute("data-value", "https://pay.example/l/1");
    expect(dlg.getByText("$120.00")).toBeInTheDocument();
  });
});

describe("create link form", () => {
  it("requires a title and a positive amount", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: /Generate PayPal Link/ }));
    expect(await screen.findByText("Enter a title")).toBeInTheDocument();
    expect(screen.getByText("Enter an amount")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("rejects a zero or negative amount", async () => {
    const { user } = open();
    await screen.findByRole("table");
    await fillBasics(user, "Free thing", "0");
    await user.click(screen.getByRole("button", { name: /Generate PayPal Link/ }));
    expect(await screen.findByText("Enter an amount")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("switching to Razorpay fixes the currency to INR and relabels the button", async () => {
    const { user } = open();
    await screen.findByRole("table");
    await user.selectOptions(screen.getByLabelText(/^Provider/), "razorpay");
    expect(screen.getByLabelText(/^Currency/)).toHaveValue("INR");
    expect(screen.getByLabelText(/^Currency/)).toBeDisabled();
    expect(screen.getByRole("button", { name: /Generate Razorpay Link/ })).toBeInTheDocument();
  });

  it("creates a PayPal link with a combined description and the chosen expiry", async () => {
    const { user } = open();
    await screen.findByRole("table");
    await fillBasics(user, "Consulting call", "120.5");
    await user.type(screen.getByLabelText("Description & deliverables"), "One hour");
    await user.selectOptions(screen.getByLabelText(/^Link expiry/), "30");
    await user.selectOptions(screen.getByLabelText(/^Customer/), "c1");
    await user.click(screen.getByRole("button", { name: /Generate PayPal Link/ }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    const [url, body] = post.mock.calls[0]!;
    expect(url).toBe("/api/payment-links");
    expect(body).toMatchObject({ provider: "paypal", amount: 120.5, currency: "USD", description: "Consulting call — One hour", customer_id: "c1" });
    const days = (new Date(body.expires_at).getTime() - Date.now()) / 864e5;
    expect(days).toBeGreaterThan(29.9);
    expect(days).toBeLessThan(30.1);
    expect(await screen.findByText("Payment link created")).toBeInTheDocument();
    expect(screen.getByText("https://pay.example/l/new")).toBeInTheDocument(); // new link is inspected
    expect(screen.getByLabelText("Product or service title")).toHaveValue(""); // form reset
  });

  it("sends a null expiry for 'Never expires' and no customer when none is chosen", async () => {
    const { user } = open();
    await screen.findByRole("table");
    await fillBasics(user);
    await user.selectOptions(screen.getByLabelText(/^Link expiry/), "0");
    await user.click(screen.getByRole("button", { name: /Generate PayPal Link/ }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0]![1]).toMatchObject({ expires_at: null, customer_id: null, description: "Consulting call" });
  });

  it("creates an INR Razorpay link", async () => {
    const { user } = open();
    await screen.findByRole("table");
    await user.selectOptions(screen.getByLabelText(/^Provider/), "razorpay");
    await fillBasics(user, "Course fee", "999");
    await user.click(screen.getByRole("button", { name: /Generate Razorpay Link/ }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0]![1]).toMatchObject({ provider: "razorpay", currency: "INR", amount: 999 });
  });

  it("keeps the form and reports the error when the provider call fails", async () => {
    serve({ ...routes(), "POST /api/payment-links": () => { throw new ApiError("PayPal is not configured", 503, "not_configured"); } });
    const { user } = open();
    await screen.findByRole("table");
    await fillBasics(user);
    await user.click(screen.getByRole("button", { name: /Generate PayPal Link/ }));
    expect(await screen.findByText("PayPal is not configured")).toBeInTheDocument();
    expect(screen.getByLabelText("Product or service title")).toHaveValue("Consulting call");
  });
});

describe("cancelling a link", () => {
  it("only active links can be cancelled", async () => {
    open();
    await screen.findByRole("table");
    expect(screen.getAllByRole("button", { name: "Cancel link" })).toHaveLength(2); // consulting + workshop, not the paid one
  });

  it("asks for confirmation first", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { user } = open();
    await user.click((await screen.findAllByRole("button", { name: "Cancel link" }))[0]!);
    expect(confirm).toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  it("cancels the right link once confirmed", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { user } = open();
    await user.click((await screen.findAllByRole("button", { name: "Cancel link" }))[0]!);
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/payment-links/link-0001-aaaa/cancel"));
  });
});

describe("Razorpay link creation (in-app)", () => {
  const pickRazorpay = async (user: ReturnType<typeof open>["user"]) => {
    await screen.findByRole("table");
    await user.selectOptions(screen.getByLabelText(/^Provider/), "razorpay");
  };

  it("switching back to PayPal makes the currency selectable again", async () => {
    const { user } = open();
    await pickRazorpay(user);
    expect(screen.getByLabelText(/^Currency/)).toBeDisabled();
    await user.selectOptions(screen.getByLabelText(/^Provider/), "paypal");
    expect(screen.getByLabelText(/^Currency/)).toBeEnabled();
    expect(screen.getByRole("button", { name: /Generate PayPal Link/ })).toBeInTheDocument();
  });

  it("still validates the title and amount for Razorpay", async () => {
    const { user } = open();
    await pickRazorpay(user);
    await user.click(screen.getByRole("button", { name: /Generate Razorpay Link/ }));
    expect(await screen.findByText("Enter a title")).toBeInTheDocument();
    expect(screen.getByText("Enter an amount")).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("creates the link, shows it in the inspector and lists it as Razorpay", async () => {
    const { user } = open();
    await pickRazorpay(user);
    await fillBasics(user, "Workshop seat", "1500");
    await user.selectOptions(screen.getByLabelText(/^Customer/), "c1");
    await user.click(screen.getByRole("button", { name: /Generate Razorpay Link/ }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0]![1]).toMatchObject({ provider: "razorpay", currency: "INR", amount: 1500, customer_id: "c1", description: "Workshop seat" });
    expect(await screen.findByText("Payment link created")).toBeInTheDocument();
    expect(screen.getByText("https://pay.example/l/new")).toBeInTheDocument();
  });

  it("shows the server's reason when Razorpay is not configured and keeps the form", async () => {
    serve({ ...routes(), "POST /api/payment-links": () => { throw new ApiError("Razorpay is not configured", 503, "not_configured"); } });
    const { user } = open();
    await pickRazorpay(user);
    await fillBasics(user, "Workshop seat", "1500");
    await user.click(screen.getByRole("button", { name: /Generate Razorpay Link/ }));
    expect(await screen.findByText("Razorpay is not configured")).toBeInTheDocument();
    expect(screen.getByLabelText("Product or service title")).toHaveValue("Workshop seat");
    expect(screen.getByLabelText(/^Currency/)).toHaveValue("INR");
  });

  it("does not create the link twice when the button is double-clicked", async () => {
    const d = deferred();
    serve({ ...routes(), "POST /api/payment-links": () => d.promise });
    const { user } = open();
    await pickRazorpay(user);
    await fillBasics(user, "Workshop seat", "1500");
    await user.dblClick(screen.getByRole("button", { name: /Generate Razorpay Link/ }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post).toHaveBeenCalledTimes(1);
    d.resolve(link({ id: "link-new-0000", url: "https://pay.example/l/new" }));
  });
});
