import { screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async () => await import("@/test/api"));

import { PayCancelled, PayRazorpay, PayReturn } from "./Pay";
import { deferred, publicApi } from "@/test/api";
import { renderPage } from "@/test/render";

/** The shape axios gives us when the backend returns {error:{message}}. */
const backendError = (message?: string) => Object.assign(new Error("Request failed"), { response: message ? { data: { error: { message } } } : undefined });
const outcome = (o: Record<string, unknown> = {}) => ({ data: { status: "completed", amount: 50, currency: "USD", invoice_number: "INV-0007", ...o } });

describe("PayReturn (PayPal redirect back to the site)", () => {
  const open = (token = "ORD123") => renderPage(<StrictMode><PayReturn /></StrictMode>, { route: token ? `/pay/return?token=${token}` : "/pay/return", path: "/pay/return" });

  it("verifies the order server-side, exactly once, and shows the receipt", async () => {
    publicApi.post.mockResolvedValue(outcome());
    open();
    expect(await screen.findByRole("heading", { name: "Payment received" })).toBeInTheDocument();
    expect(screen.getByText("$50.00 · INV-0007")).toBeInTheDocument();
    expect(publicApi.post).toHaveBeenCalledTimes(1); // StrictMode double-invokes effects; the capture must not repeat
    expect(publicApi.post).toHaveBeenCalledWith("/api/public/pay/paypal/capture", { order_id: "ORD123" });
  });

  it("shows a spinner message while the capture is in flight", async () => {
    const d = deferred();
    publicApi.post.mockReturnValue(d.promise);
    open();
    expect(screen.getByText("Confirming your PayPal payment…")).toBeInTheDocument();
    d.resolve(outcome());
    expect(await screen.findByRole("heading", { name: "Payment received" })).toBeInTheDocument();
  });

  it("does not claim success for a pending payment", async () => {
    publicApi.post.mockResolvedValue(outcome({ status: "pending" }));
    open();
    expect(await screen.findByRole("heading", { name: "Payment pending" })).toBeInTheDocument();
    expect(screen.queryByText(/payment received/i)).not.toBeInTheDocument();
  });

  it.each(["failed", "cancelled", "created"])("does not claim success when the status is %s", async (status) => {
    publicApi.post.mockResolvedValue(outcome({ status }));
    open();
    expect(await screen.findByRole("heading", { name: "Payment not completed" })).toBeInTheDocument();
    expect(screen.getByText(/no payment was recorded/i)).toBeInTheDocument();
  });

  it("omits the invoice number when the payment is not tied to an invoice", async () => {
    publicApi.post.mockResolvedValue(outcome({ invoice_number: null }));
    open();
    expect(await screen.findByText("$50.00")).toBeInTheDocument();
  });

  it("shows the backend's reason when verification fails", async () => {
    publicApi.post.mockRejectedValue(backendError("Order was not approved by the payer"));
    open();
    expect(await screen.findByText("Order was not approved by the payer")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Payment received" })).not.toBeInTheDocument();
  });

  it("falls back to safe guidance when the server cannot be reached", async () => {
    publicApi.post.mockRejectedValue(backendError());
    open();
    expect(await screen.findByText(/couldn't verify this payment\. if you were charged, contact the merchant/i)).toBeInTheDocument();
  });

  it("never calls the API when the return URL has no token", () => {
    open("");
    expect(screen.getByText("Missing payment reference.")).toBeInTheDocument();
    expect(publicApi.post).not.toHaveBeenCalled();
  });
});

describe("PayCancelled", () => {
  const open = (route: string) => renderPage(<PayCancelled />, { route, path: "/pay/cancelled" });

  it("tells the customer they were not charged and records the cancellation", async () => {
    publicApi.post.mockResolvedValue({ data: {} });
    open("/pay/cancelled?token=ORD9");
    expect(screen.getByRole("heading", { name: "Payment cancelled" })).toBeInTheDocument();
    expect(screen.getByText(/you weren't charged/i)).toBeInTheDocument();
    await waitFor(() => expect(publicApi.post).toHaveBeenCalledWith("/api/public/pay/paypal/cancel", { order_id: "ORD9" }));
  });

  it("still shows the page if recording the cancellation fails", async () => {
    publicApi.post.mockRejectedValue(backendError("boom"));
    open("/pay/cancelled?token=ORD9");
    await waitFor(() => expect(publicApi.post).toHaveBeenCalled());
    expect(screen.getByRole("heading", { name: "Payment cancelled" })).toBeInTheDocument();
  });

  it("does not call the API without a token", () => {
    open("/pay/cancelled");
    expect(publicApi.post).not.toHaveBeenCalled();
  });
});

describe("PayRazorpay (public checkout page)", () => {
  const linkInfo = (o: Record<string, unknown> = {}) => ({ data: { status: "active", amount: 499.5, currency: "INR", description: "Course fee", merchant: "Acme Academy", key_id: "rzp_test_abc", order_id: "order_X1", ...o } });
  let razorpayOptions: Record<string, any>;
  const open = () => renderPage(<PayRazorpay />, { route: "/pay/razorpay/link1", path: "/pay/razorpay/:linkId" });

  beforeEach(() => {
    window.Razorpay = vi.fn(function (this: unknown, opts: Record<string, unknown>) { razorpayOptions = opts; return { open: vi.fn() }; }) as unknown as typeof window.Razorpay;
    publicApi.get.mockResolvedValue(linkInfo());
  });
  afterEach(() => { delete window.Razorpay; document.querySelectorAll("script[src*=razorpay]").forEach((s) => s.remove()); });

  it("shows the merchant, description and amount for an active link", async () => {
    open();
    expect(await screen.findByText("Acme Academy")).toBeInTheDocument();
    expect(screen.getByText("Course fee")).toBeInTheDocument();
    expect(screen.getByText("₹499.50")).toBeInTheDocument();
    expect(publicApi.get).toHaveBeenCalledWith("/api/public/pay/link/link1");
  });

  it.each(["paid", "expired", "cancelled"])("does not offer payment for a %s link", async (status) => {
    publicApi.get.mockResolvedValue(linkInfo({ status }));
    open();
    expect(await screen.findByText(`This payment link is ${status}.`)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /pay with razorpay/i })).not.toBeInTheDocument();
  });

  it("shows the backend error when the link cannot be loaded", async () => {
    publicApi.get.mockRejectedValue(backendError("Payment link not found"));
    open();
    expect(await screen.findByText("Payment link not found")).toBeInTheDocument();
  });

  it("opens Razorpay checkout with the amount in paise and the server-created order", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: /pay with razorpay/i }));
    await waitFor(() => expect(window.Razorpay).toHaveBeenCalled());
    expect(razorpayOptions).toMatchObject({ key: "rzp_test_abc", order_id: "order_X1", amount: 49950, currency: "INR", name: "Acme Academy", description: "Course fee" });
  });

  it("sends the signed payment details to the server for verification and shows the receipt", async () => {
    publicApi.post.mockResolvedValue(outcome({ amount: 499.5, currency: "INR", invoice_number: null }));
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: /pay with razorpay/i }));
    await waitFor(() => expect(razorpayOptions).toBeDefined());
    await razorpayOptions.handler({ razorpay_order_id: "order_X1", razorpay_payment_id: "pay_1", razorpay_signature: "sig", extra: "ignored" });
    expect(await screen.findByRole("heading", { name: "Payment received" })).toBeInTheDocument();
    expect(publicApi.post).toHaveBeenCalledWith("/api/public/pay/razorpay/verify", { razorpay_order_id: "order_X1", razorpay_payment_id: "pay_1", razorpay_signature: "sig" });
  });

  it("does not show a receipt when server-side verification rejects the signature", async () => {
    publicApi.post.mockRejectedValue(backendError("Signature verification failed"));
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: /pay with razorpay/i }));
    await waitFor(() => expect(razorpayOptions).toBeDefined());
    await razorpayOptions.handler({ razorpay_order_id: "order_X1", razorpay_payment_id: "pay_1", razorpay_signature: "forged" });
    expect(await screen.findByText("Signature verification failed")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Payment received" })).not.toBeInTheDocument();
  });

  it("explains when the Razorpay checkout script cannot be loaded", async () => {
    delete window.Razorpay;
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: /pay with razorpay/i }));
    const script = await waitFor(() => { const s = document.querySelector("script[src*=razorpay]"); expect(s).not.toBeNull(); return s!; });
    expect(script).toHaveAttribute("src", "https://checkout.razorpay.com/v1/checkout.js");
    script.dispatchEvent(new Event("error"));
    expect(await screen.findByText("Could not load Razorpay checkout.")).toBeInTheDocument();
  });
});
