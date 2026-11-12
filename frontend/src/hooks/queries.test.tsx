import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async () => await import("@/test/api"));

import { useCancelLink, useCreateCustomer, useCreateInvoice, useCustomers, useDashboard, useInvoice, useInvoiceLink, useRefund } from "./queries";
import { ApiError, deferred, get, post, serve } from "@/test/api";
import { customer, dashboard, page } from "@/test/fixtures";

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const invalidated = () => invalidate.mock.calls.map((c) => (c[0]!.queryKey as string[])[0]);
  return { wrapper, invalidated };
}

describe("queries", () => {
  it("useDashboard requests the chosen period and currency", async () => {
    serve({ "GET /api/dashboard": () => dashboard() });
    const { wrapper } = setup();
    const { result } = renderHook(() => useDashboard(90, "EUR"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(get).toHaveBeenCalledWith("/api/dashboard", { days: 90, currency: "EUR" });
  });

  it("useCustomers always asks for a large page and forwards the search", async () => {
    serve({ "GET /api/customers": () => page([customer()]) });
    const { wrapper } = setup();
    const { result } = renderHook(() => useCustomers("sar"), { wrapper });
    await waitFor(() => expect(result.current.data?.items).toHaveLength(1));
    expect(get).toHaveBeenCalledWith("/api/customers", { search: "sar", page_size: 100 });
  });

  it("useInvoice does not fetch without an id", () => {
    serve({});
    const { wrapper } = setup();
    renderHook(() => useInvoice(undefined), { wrapper });
    expect(get).not.toHaveBeenCalled();
  });

  it("exposes API failures as the query error", async () => {
    serve({ "GET /api/dashboard": () => { throw new ApiError("Unavailable", 503); } });
    const { wrapper } = setup();
    const { result } = renderHook(() => useDashboard(30), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect((result.current.error as ApiError).message).toBe("Unavailable");
  });
});

describe("mutations refresh the data they change", () => {
  it("creating an invoice refreshes money views, not customers", async () => {
    serve({ "POST /api/invoices": () => ({ id: "inv1" }) });
    const { wrapper, invalidated } = setup();
    const { result } = renderHook(() => useCreateInvoice(), { wrapper });
    await act(() => result.current.mutateAsync({ total: 1 }));
    expect(invalidated()).toEqual(expect.arrayContaining(["dashboard", "analytics", "transactions", "invoices", "invoice", "links", "notifications"]));
    expect(invalidated()).not.toContain("customers");
    expect(post).toHaveBeenCalledWith("/api/invoices", { total: 1 });
  });

  it("creating a payment link for an invoice passes the provider as a query param", async () => {
    serve({ "POST /api/invoices/inv1/payment-link": () => ({}) });
    const { wrapper, invalidated } = setup();
    const { result } = renderHook(() => useInvoiceLink(), { wrapper });
    await act(() => result.current.mutateAsync({ id: "inv1", provider: "razorpay" }));
    expect(post).toHaveBeenCalledWith("/api/invoices/inv1/payment-link", {}, { provider: "razorpay" });
    expect(invalidated()).toContain("links");
  });

  it("cancelling a link only refreshes links", async () => {
    serve({ "POST /api/payment-links/l1/cancel": () => ({}) });
    const { wrapper, invalidated } = setup();
    const { result } = renderHook(() => useCancelLink(), { wrapper });
    await act(() => result.current.mutateAsync("l1"));
    expect(invalidated()).toEqual(["links"]);
  });

  it("refunding also refreshes the single-transaction key the details panel uses", async () => {
    serve({ "POST /api/transactions/t1/refund": () => ({}) });
    const { wrapper, invalidated } = setup();
    const { result } = renderHook(() => useRefund(), { wrapper });
    await act(() => result.current.mutateAsync("t1"));
    expect(invalidated()).toContain("transaction");
    expect(invalidated()).toContain("transactions");
  });

  it("refunding stays pending until the active queries have refetched", async () => {
    const refetch = deferred();
    let calls = 0;
    serve({ "POST /api/transactions/t1/refund": () => ({}), "GET /api/transactions/t1": () => (++calls === 1 ? { status: "completed" } : refetch.promise) });
    const { wrapper } = setup();
    const { result } = renderHook(() => ({ q: useQuery({ queryKey: ["transaction", "t1"], queryFn: () => get("/api/transactions/t1") }), r: useRefund() }), { wrapper });
    await waitFor(() => expect(result.current.q.isSuccess).toBe(true));
    act(() => { void result.current.r.mutateAsync("t1"); });
    await waitFor(() => expect(calls).toBe(2));
    expect(result.current.r.isPending).toBe(true); // POST finished, refresh still in flight
    await act(async () => { refetch.resolve({ status: "refunded" }); });
    await waitFor(() => expect(result.current.r.isSuccess).toBe(true));
    expect(result.current.q.data).toEqual({ status: "refunded" });
  });

  it("refunding refreshes transactions, invoices and the dashboard figures", async () => {
    serve({ "POST /api/transactions/t1/refund": () => ({}) });
    const { wrapper, invalidated } = setup();
    const { result } = renderHook(() => useRefund(), { wrapper });
    await act(() => result.current.mutateAsync("t1"));
    expect(invalidated()).toEqual(expect.arrayContaining(["transactions", "dashboard", "invoices", "invoice", "analytics"]));
  });

  it("a failed mutation rejects and does not refresh anything", async () => {
    serve({ "POST /api/customers": () => { throw new ApiError("Duplicate", 409); } });
    const { wrapper, invalidated } = setup();
    const { result } = renderHook(() => useCreateCustomer(), { wrapper });
    let caught: unknown;
    await act(async () => { try { await result.current.mutateAsync({ name: "x" }); } catch (e) { caught = e; } });
    expect(post).toHaveBeenCalledTimes(1);
    expect(caught).toBeInstanceOf(ApiError);
    expect(invalidated()).toEqual([]);
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
