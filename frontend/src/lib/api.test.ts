import { AxiosError, type AxiosAdapter, type InternalAxiosRequestConfig } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ getSession: vi.fn(), signOut: vi.fn() }));
vi.mock("./supabase", () => ({ supabase: { auth }, isSupabaseConfigured: true }));

import { api, ApiError, del, get, patch, post, publicApi } from "./api";

type Reply = { status: number; data?: unknown } | "network-error";
let seen: InternalAxiosRequestConfig[];
const originalAdapter = api.defaults.adapter;

/** Replace the HTTP layer so the real interceptors run but nothing leaves the process. */
function respondWith(reply: Reply): void {
  const adapter: AxiosAdapter = async (config) => {
    seen.push(config);
    if (reply === "network-error") throw new AxiosError("Network Error", "ERR_NETWORK", config);
    const response = { data: reply.data, status: reply.status, statusText: "", headers: {}, config };
    if (reply.status >= 400) throw new AxiosError(`HTTP ${reply.status}`, "ERR_BAD_REQUEST", config, null, response);
    return response;
  };
  api.defaults.adapter = adapter;
}

/** Await a request that must fail and return its error (fails the test if it succeeds). */
async function rejection(request: Promise<unknown>): Promise<ApiError> {
  try { await request; } catch (e) { return e as ApiError; }
  throw new Error("expected the request to be rejected");
}

beforeEach(() => {
  seen = [];
  auth.getSession.mockResolvedValue({ data: { session: { access_token: "jwt-123" } } });
  auth.signOut.mockResolvedValue({ error: null });
});
afterEach(() => { api.defaults.adapter = originalAdapter; });

describe("request interceptor", () => {
  it("sends the Supabase access token as a bearer header", async () => {
    respondWith({ status: 200, data: {} });
    await get("/api/dashboard");
    expect(seen[0]!.headers.Authorization).toBe("Bearer jwt-123");
  });

  it("omits the Authorization header when signed out", async () => {
    auth.getSession.mockResolvedValue({ data: { session: null } });
    respondWith({ status: 200, data: {} });
    await get("/api/dashboard");
    expect(seen[0]!.headers.Authorization).toBeUndefined();
  });
});

describe("request helpers", () => {
  it("get returns the response body and forwards query params", async () => {
    respondWith({ status: 200, data: { items: [1, 2] } });
    await expect(get("/api/invoices", { page: 2, status: "paid" })).resolves.toEqual({ items: [1, 2] });
    expect(seen[0]).toMatchObject({ method: "get", url: "/api/invoices", params: { page: 2, status: "paid" } });
  });

  it("post sends the JSON body, defaulting to an empty object", async () => {
    respondWith({ status: 200, data: { ok: true } });
    await post("/api/invoices", { total: 5 });
    await post("/api/payment-links/1/cancel");
    expect(JSON.parse(seen[0]!.data)).toEqual({ total: 5 });
    expect(JSON.parse(seen[1]!.data)).toEqual({});
  });

  it("post passes query params separately from the body", async () => {
    respondWith({ status: 200, data: {} });
    await post("/api/invoices/1/payment-link", {}, { provider: "razorpay" });
    expect(seen[0]!.params).toEqual({ provider: "razorpay" });
  });

  it("patch and del use the right verbs; del resolves to nothing", async () => {
    respondWith({ status: 200, data: { id: "1" } });
    await expect(patch("/api/customers/1", { name: "A" })).resolves.toEqual({ id: "1" });
    await expect(del("/api/customers/1")).resolves.toBeUndefined();
    expect(seen.map((c) => c.method)).toEqual(["patch", "delete"]);
  });
});

describe("response interceptor: error handling", () => {
  it("turns a backend error envelope into an ApiError with message, status and code", async () => {
    respondWith({ status: 422, data: { error: { message: "Amount must be positive", code: "validation_error" } } });
    const err = await rejection(get("/api/invoices"));
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ message: "Amount must be positive", status: 422, code: "validation_error" });
  });

  it("signs the user out on 401 but not on other errors", async () => {
    respondWith({ status: 401, data: { error: { message: "Invalid or expired session", code: "unauthorized" } } });
    await expect(get("/api/auth/me")).rejects.toMatchObject({ status: 401, message: "Invalid or expired session" });
    expect(auth.signOut).toHaveBeenCalledTimes(1);

    respondWith({ status: 403, data: { error: { message: "Forbidden", code: "forbidden" } } });
    await expect(get("/api/auth/me")).rejects.toMatchObject({ status: 403 });
    expect(auth.signOut).toHaveBeenCalledTimes(1);
  });

  it("reports an unreachable API (no response) with a connection hint and status 0", async () => {
    respondWith("network-error");
    const err = await rejection(get("/api/dashboard"));
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(0);
    expect(err.message).toMatch(/cannot reach the paypilot api/i);
  });

  it("falls back to a generic message when the error body has none", async () => {
    respondWith({ status: 500, data: "<html>boom</html>" });
    await expect(get("/api/dashboard")).rejects.toMatchObject({ message: "Something went wrong", status: 500, code: "error" });
  });

  it("does not leak raw server text into the message", async () => {
    respondWith({ status: 500, data: { detail: "Traceback: secret stuff" } });
    const err = await rejection(get("/api/dashboard"));
    expect(err.message).toBe("Something went wrong");
  });
});

describe("publicApi (customer checkout)", () => {
  it("targets the same API without attaching or looking up credentials", async () => {
    expect(publicApi.defaults.baseURL).toBe(api.defaults.baseURL);
    let headers: unknown;
    publicApi.defaults.adapter = async (config) => { headers = config.headers; return { data: {}, status: 200, statusText: "", headers: {}, config }; };
    await publicApi.get("/api/public/pay/link/1");
    expect((headers as Record<string, unknown>).Authorization).toBeUndefined();
    expect(auth.getSession).not.toHaveBeenCalled();
  });
});
