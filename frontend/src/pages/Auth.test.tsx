import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({
  session: null as unknown, signIn: vi.fn(), signUp: vi.fn(), requestReset: vi.fn(), updatePassword: vi.fn(),
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => auth }));

import { ForgotPassword, Login, Register, ResetPassword } from "./Auth";
import { renderPage, type InitialEntry } from "@/test/render";

const here = () => screen.getByTestId("location").textContent;

beforeEach(() => {
  auth.session = null;
  auth.signIn.mockResolvedValue(undefined);
  auth.signUp.mockResolvedValue({ needsVerification: false });
  auth.requestReset.mockResolvedValue(undefined);
  auth.updatePassword.mockResolvedValue(undefined);
});

describe("Login", () => {
  const open = (route: InitialEntry = "/login") => renderPage(<Login />, { route, path: "/login" });

  it("blocks submission and explains both fields when empty", async () => {
    const { user } = open();
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter a valid email")).toBeInTheDocument();
    expect(screen.getByText("Enter your password")).toBeInTheDocument();
    expect(auth.signIn).not.toHaveBeenCalled();
  });

  it("rejects a malformed email without calling Supabase (the browser blocks the submit)", async () => {
    const { user } = open();
    await user.type(screen.getByLabelText("Email"), "not-an-email");
    await user.type(screen.getByLabelText("Password"), "secret");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByLabelText("Email")).toBeInvalid();
    expect(auth.signIn).not.toHaveBeenCalled();
  });

  it("signs in and lands on the dashboard", async () => {
    const { user } = open();
    await user.type(screen.getByLabelText("Email"), "me@example.com");
    await user.type(screen.getByLabelText("Password"), "secret");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(here()).toBe("/"));
    expect(auth.signIn).toHaveBeenCalledWith("me@example.com", "secret");
  });

  it("returns the user to the page they were trying to open", async () => {
    const { user } = open({ pathname: "/login", state: { from: "/invoices" } });
    await user.type(screen.getByLabelText("Email"), "me@example.com");
    await user.type(screen.getByLabelText("Password"), "secret");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(here()).toBe("/invoices"));
  });

  it("shows the provider's error and stays on the page when sign-in fails", async () => {
    auth.signIn.mockRejectedValue(new Error("Invalid login credentials"));
    const { user } = open();
    await user.type(screen.getByLabelText("Email"), "me@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid login credentials");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
    expect(screen.getByRole("heading", { name: "Welcome back" })).toBeInTheDocument();
  });

  it("clears a previous error when the user tries again", async () => {
    auth.signIn.mockRejectedValueOnce(new Error("Invalid login credentials"));
    const { user } = open();
    await user.type(screen.getByLabelText("Email"), "me@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(here()).toBe("/"));
  });

  it("redirects away immediately if there is already a session", () => {
    auth.session = { user: { id: "u1" } };
    open();
    expect(here()).toBe("/");
  });

  it("links to registration and password reset", () => {
    open();
    expect(screen.getByRole("link", { name: "Create account" })).toHaveAttribute("href", "/register");
    expect(screen.getByRole("link", { name: "Forgot password?" })).toHaveAttribute("href", "/forgot-password");
  });
});

describe("Register", () => {
  const open = () => renderPage(<Register />, { route: "/register", path: "/register" });
  const fill = async (user: ReturnType<typeof renderPage>["user"], v: { name?: string; email?: string; pw?: string; confirm?: string; business?: string }) => {
    if (v.name) await user.type(screen.getByLabelText("Full name"), v.name);
    if (v.business) await user.type(screen.getByLabelText("Business name (optional)"), v.business);
    if (v.email) await user.type(screen.getByLabelText("Email"), v.email);
    if (v.pw) await user.type(screen.getByLabelText("Password"), v.pw);
    if (v.confirm) await user.type(screen.getByLabelText("Confirm password"), v.confirm);
    await user.click(screen.getByRole("button", { name: "Create account" }));
  };

  it("requires a name and an 8+ character password", async () => {
    const { user } = open();
    await fill(user, { name: "A", email: "jane@example.com", pw: "short", confirm: "short" });
    expect(await screen.findByText("Enter your name")).toBeInTheDocument();
    expect(screen.getByText("At least 8 characters")).toBeInTheDocument();
    expect(auth.signUp).not.toHaveBeenCalled();
  });

  it("rejects mismatched passwords", async () => {
    const { user } = open();
    await fill(user, { name: "Jane Doe", email: "jane@example.com", pw: "longenough1", confirm: "different12" });
    expect(await screen.findByText("Passwords do not match")).toBeInTheDocument();
    expect(auth.signUp).not.toHaveBeenCalled();
  });

  it("creates the account with profile metadata and goes to the dashboard when no verification is needed", async () => {
    const { user } = open();
    await fill(user, { name: "Jane Doe", business: "Acme", email: "jane@example.com", pw: "longenough1", confirm: "longenough1" });
    await waitFor(() => expect(here()).toBe("/"));
    expect(auth.signUp).toHaveBeenCalledWith("jane@example.com", "longenough1", { full_name: "Jane Doe", business_name: "Acme" });
  });

  it("asks the user to verify their email when Supabase requires it", async () => {
    auth.signUp.mockResolvedValue({ needsVerification: true });
    const { user } = open();
    await fill(user, { name: "Jane Doe", email: "jane@example.com", pw: "longenough1", confirm: "longenough1" });
    expect(await screen.findByRole("heading", { name: "Check your inbox" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to sign in" })).toHaveAttribute("href", "/login");
  });

  it("shows a sign-up failure such as a duplicate account", async () => {
    auth.signUp.mockRejectedValue(new Error("User already registered"));
    const { user } = open();
    await fill(user, { name: "Jane Doe", email: "jane@example.com", pw: "longenough1", confirm: "longenough1" });
    expect(await screen.findByRole("alert")).toHaveTextContent("User already registered");
  });
});

describe("ForgotPassword", () => {
  const open = () => renderPage(<ForgotPassword />, { route: "/forgot-password", path: "/forgot-password" });

  it("validates the email before sending", async () => {
    const { user } = open();
    await user.type(screen.getByLabelText("Email"), "nope");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(screen.getByLabelText("Email")).toBeInvalid();
    expect(auth.requestReset).not.toHaveBeenCalled();
  });

  it("confirms without revealing whether the account exists", async () => {
    const { user } = open();
    await user.type(screen.getByLabelText("Email"), "me@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByText(/if an account exists for that email/i)).toBeInTheDocument();
    expect(auth.requestReset).toHaveBeenCalledWith("me@example.com");
  });

  it("shows an error when the request fails", async () => {
    auth.requestReset.mockRejectedValue(new Error("Rate limited"));
    const { user } = open();
    await user.type(screen.getByLabelText("Email"), "me@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Rate limited");
  });
});

describe("ResetPassword", () => {
  const open = () => renderPage(<ResetPassword />, { route: "/reset-password", path: "/reset-password" });

  it("is disabled until the emailed reset link has created a session", () => {
    open();
    expect(screen.getByLabelText("New password")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Update password" })).toBeDisabled();
    expect(screen.getByText(/open the reset link from your email/i)).toBeInTheDocument();
  });

  it("enforces the minimum length", async () => {
    auth.session = { user: { id: "u1" } };
    const { user } = open();
    await user.type(screen.getByLabelText("New password"), "short");
    await user.click(screen.getByRole("button", { name: "Update password" }));
    expect(await screen.findByText("At least 8 characters")).toBeInTheDocument();
    expect(auth.updatePassword).not.toHaveBeenCalled();
  });

  it("updates the password and returns to the dashboard", async () => {
    auth.session = { user: { id: "u1" } };
    const { user } = open();
    await user.type(screen.getByLabelText("New password"), "brandnewpass1");
    await user.click(screen.getByRole("button", { name: "Update password" }));
    await waitFor(() => expect(here()).toBe("/"));
    expect(auth.updatePassword).toHaveBeenCalledWith("brandnewpass1");
  });

  it("shows an error if the update is rejected", async () => {
    auth.session = { user: { id: "u1" } };
    auth.updatePassword.mockRejectedValue(new Error("Password too weak"));
    const { user } = open();
    await user.type(screen.getByLabelText("New password"), "brandnewpass1");
    await user.click(screen.getByRole("button", { name: "Update password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Password too weak");
  });
});
