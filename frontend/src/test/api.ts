import { vi } from "vitest";

/**
 * Stand-in for `@/lib/api` so page tests never touch the network. Usage in a test file:
 *   vi.mock("@/lib/api", async () => await import("@/test/api"));
 *   serve({ "GET /api/customers": () => page([customer()]), "POST /api/customers": (body) => ({ ...customer(), ...body }) });
 * A call without a matching route throws, so an unexpected request fails the test instead of passing silently.
 */
export class ApiError extends Error {
  constructor(message: string, public status = 0, public code = "error") { super(message); }
}

export const get = vi.fn(), post = vi.fn(), patch = vi.fn(), del = vi.fn();
export const publicApi = { get: vi.fn(), post: vi.fn() };
export const api = { defaults: { baseURL: "http://api.test" } };

type Handler = (body: any, params: any) => unknown;
export type Routes = Record<string, Handler>;

export function serve(routes: Routes) {
  const call = (method: string) => async (url: string, a?: any, b?: any) => {
    const h = routes[`${method} ${url}`];
    if (!h) throw new Error(`Unmocked API call: ${method} ${url}`);
    return h(method === "GET" ? undefined : a, method === "GET" ? a : b);
  };
  get.mockImplementation(call("GET"));
  post.mockImplementation(call("POST"));
  patch.mockImplementation(call("PATCH"));
  del.mockImplementation(call("DELETE"));
}

/** Every request made with this method + url, as [url, ...args] tuples. */
export const requests = (fn: ReturnType<typeof vi.fn>, url: string) => fn.mock.calls.filter((c) => c[0] === url);

/** A promise that resolves later, to observe loading states. */
export function deferred<T = unknown>() {
  let resolve!: (v: T) => void, reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
