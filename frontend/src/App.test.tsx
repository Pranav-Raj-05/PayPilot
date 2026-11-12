import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Mutable stand-ins so each test can choose the auth state / configuration.
const state = vi.hoisted(() => ({ configured: true, auth: { session: null as unknown, loading: false, signIn: vi.fn() } }));
vi.mock("@/lib/supabase", () => ({ get isSupabaseConfigured() { return state.configured; }, supabase: { auth: {} } }));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => state.auth }));
vi.mock("@/lib/api", async () => await import("@/test/api"));
// Heavy authenticated screens are replaced by markers: this file tests routing and the guard, not the pages.
vi.mock("@/layouts/AppLayout", async () => {
  const { Outlet } = await import("react-router-dom");
  return { default: () => <div><span>app shell</span><Outlet /></div> };
});
vi.mock("@/pages/Overview", () => ({ default: () => <h1>Overview page</h1> }));
vi.mock("@/pages/Customers", () => ({ default: () => <h1>Customers page</h1> }));
vi.mock("@/pages/Settings", () => ({ default: () => <h1>Settings page</h1> }));

import App from "./App";
import { renderPage } from "@/test/render";

const signedIn = { user: { id: "u1" } };
const open = (route: string) => renderPage(<App />, { route });

beforeEach(() => { state.configured = true; state.auth = { session: null, loading: false, signIn: vi.fn() }; });

describe("protected routes", () => {
  it.each(["/", "/customers", "/settings"])("sends a signed-out visitor from %s to the login page", async (route) => {
    open(route);
    expect(await screen.findByRole("heading", { name: "Welcome back" })).toBeInTheDocument();
    expect(screen.queryByText("app shell")).not.toBeInTheDocument();
  });

  it("does not leak protected content or redirect while the session is still loading", () => {
    state.auth.loading = true;
    open("/customers");
    expect(screen.queryByText("Customers page")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Welcome back" })).not.toBeInTheDocument();
    expect(document.querySelector(".animate-pulse")).toBeInTheDocument();
  });

  it("shows the requested page inside the app shell once signed in", async () => {
    state.auth.session = signedIn;
    open("/customers");
    expect(await screen.findByRole("heading", { name: "Customers page" })).toBeInTheDocument();
    expect(screen.getByText("app shell")).toBeInTheDocument();
  });

  it("renders the dashboard at the index route when signed in", async () => {
    state.auth.session = signedIn;
    open("/");
    expect(await screen.findByRole("heading", { name: "Overview page" })).toBeInTheDocument();
  });

  it("bounces an already signed-in user away from /login", async () => {
    state.auth.session = signedIn;
    open("/login");
    expect(await screen.findByRole("heading", { name: "Overview page" })).toBeInTheDocument();
  });

  it("treats unknown URLs as the index route (and so requires login)", async () => {
    open("/definitely/not/a/page");
    expect(await screen.findByRole("heading", { name: "Welcome back" })).toBeInTheDocument();
  });
});

describe("public routes", () => {
  it.each([["/register", "Create your account"], ["/forgot-password", "Reset your password"]])("%s is reachable without a session", async (route, title) => {
    open(route);
    expect(await screen.findByRole("heading", { name: title })).toBeInTheDocument();
  });

  it("customer checkout pages are reachable without a session", async () => {
    open("/pay/cancelled");
    expect(await screen.findByRole("heading", { name: "Payment cancelled" })).toBeInTheDocument();
  });
});

describe("configuration guard", () => {
  it("explains how to configure Supabase instead of rendering a broken app", () => {
    state.configured = false;
    open("/");
    expect(screen.getByRole("heading", { name: "Supabase is not configured" })).toBeInTheDocument();
    expect(screen.getByText("VITE_SUPABASE_URL")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Welcome back" })).not.toBeInTheDocument();
  });
});
