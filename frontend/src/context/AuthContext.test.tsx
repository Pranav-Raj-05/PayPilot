import type { Session } from "@supabase/supabase-js";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const sb = vi.hoisted(() => ({
  getSession: vi.fn(), onAuthStateChange: vi.fn(), signInWithPassword: vi.fn(), signUp: vi.fn(), signOut: vi.fn(),
  resetPasswordForEmail: vi.fn(), updateUser: vi.fn(),
}));
vi.mock("@/lib/supabase", () => ({ supabase: { auth: sb }, isSupabaseConfigured: true }));

import { AuthProvider, useAuth } from "./AuthContext";

const session = { access_token: "t", user: { id: "u1", email: "me@example.com" } } as unknown as Session;
const wrapper = ({ children }: { children: ReactNode }) => <AuthProvider>{children}</AuthProvider>;
let emit: (event: string, s: Session | null) => void;
const unsubscribe = vi.fn();

beforeEach(() => {
  sb.getSession.mockResolvedValue({ data: { session: null } });
  sb.onAuthStateChange.mockImplementation((cb) => { emit = cb; return { data: { subscription: { unsubscribe } } }; });
  sb.signInWithPassword.mockResolvedValue({ error: null });
  sb.signUp.mockResolvedValue({ data: { session: null }, error: null });
  sb.signOut.mockResolvedValue({ error: null });
  sb.resetPasswordForEmail.mockResolvedValue({ error: null });
  sb.updateUser.mockResolvedValue({ error: null });
});

describe("AuthProvider session state", () => {
  it("is loading until the stored session has been read, then exposes it", async () => {
    sb.getSession.mockResolvedValue({ data: { session } });
    const { result } = renderHook(() => useAuth(), { wrapper });
    expect(result.current.loading).toBe(true);
    expect(result.current.session).toBeNull();
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.session).toBe(session);
    expect(result.current.user?.email).toBe("me@example.com");
  });

  it("finishes loading with no user when signed out", async () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.session).toBeNull();
    expect(result.current.user).toBeNull();
  });

  it("follows sign-in and sign-out events from Supabase", async () => {
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.loading).toBe(false));
    act(() => emit("SIGNED_IN", session));
    expect(result.current.user?.id).toBe("u1");
    act(() => emit("SIGNED_OUT", null));
    expect(result.current.session).toBeNull();
  });

  it("stops listening when unmounted", async () => {
    const { unmount } = renderHook(() => useAuth(), { wrapper });
    unmount();
    expect(unsubscribe).toHaveBeenCalledTimes(1);
  });

  it("throws when used outside the provider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => renderHook(() => useAuth())).toThrow("useAuth outside AuthProvider");
    spy.mockRestore();
  });
});

describe("AuthProvider actions", () => {
  const setup = async () => {
    const hook = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(hook.result.current.loading).toBe(false));
    return hook.result;
  };

  it("signIn forwards the credentials", async () => {
    const auth = await setup();
    await auth.current.signIn("a@b.co", "pw12345678");
    expect(sb.signInWithPassword).toHaveBeenCalledWith({ email: "a@b.co", password: "pw12345678" });
  });

  it("signIn rejects with the provider's message so the form can show it", async () => {
    sb.signInWithPassword.mockResolvedValue({ error: { message: "Invalid login credentials" } });
    const auth = await setup();
    await expect(auth.current.signIn("a@b.co", "bad")).rejects.toThrow("Invalid login credentials");
  });

  it("signUp sends profile metadata and a redirect back to /login, and asks for verification when no session comes back", async () => {
    const auth = await setup();
    await expect(auth.current.signUp("a@b.co", "pw12345678", { full_name: "Ann", business_name: "Acme" })).resolves.toEqual({ needsVerification: true });
    expect(sb.signUp).toHaveBeenCalledWith({
      email: "a@b.co", password: "pw12345678", options: { data: { full_name: "Ann", business_name: "Acme" }, emailRedirectTo: `${window.location.origin}/login` },
    });
  });

  it("signUp does not need verification when Supabase signs the user in straight away", async () => {
    sb.signUp.mockResolvedValue({ data: { session }, error: null });
    const auth = await setup();
    await expect(auth.current.signUp("a@b.co", "pw12345678", { full_name: "Ann" })).resolves.toEqual({ needsVerification: false });
  });

  it("signUp surfaces provider errors", async () => {
    sb.signUp.mockResolvedValue({ data: { session: null }, error: { message: "User already registered" } });
    const auth = await setup();
    await expect(auth.current.signUp("a@b.co", "pw12345678", { full_name: "Ann" })).rejects.toThrow("User already registered");
  });

  it("signOut, requestReset and updatePassword call through; reset links point back at /reset-password", async () => {
    const auth = await setup();
    await auth.current.signOut();
    await auth.current.requestReset("a@b.co");
    await auth.current.updatePassword("newpassword1");
    expect(sb.signOut).toHaveBeenCalled();
    expect(sb.resetPasswordForEmail).toHaveBeenCalledWith("a@b.co", { redirectTo: `${window.location.origin}/reset-password` });
    expect(sb.updateUser).toHaveBeenCalledWith({ password: "newpassword1" });
  });

  it("requestReset and updatePassword reject on provider errors", async () => {
    sb.resetPasswordForEmail.mockResolvedValue({ error: { message: "Rate limited" } });
    sb.updateUser.mockResolvedValue({ error: { message: "Password too weak" } });
    const auth = await setup();
    await expect(auth.current.requestReset("a@b.co")).rejects.toThrow("Rate limited");
    await expect(auth.current.updatePassword("x")).rejects.toThrow("Password too weak");
  });
});
