import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ signOut: vi.fn() }));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/lib/api", async () => await import("@/test/api"));

import AppLayout from "./AppLayout";
import { RangeProvider } from "@/context/RangeContext";
import { get, post, requests, serve } from "@/test/api";
import { paypal, profile } from "@/test/fixtures";
import { LocationProbe } from "@/test/render";
import type { Notification } from "@/types";

const note = (o: Partial<Notification> = {}): Notification =>
  ({ id: "n1", title: "Payment received", message: "Sarah paid INV-0001", notification_type: "payment", is_read: false, data: null, created_at: new Date().toISOString(), ...o });

let notifications: Notification[];
let pending: number, overdue: number;
let pp: ReturnType<typeof paypal>;
let me: ReturnType<typeof profile>;

const routes = () => ({
  "GET /api/auth/me": () => me,
  "GET /api/payments/paypal/status": () => pp,
  "GET /api/invoices": (_body: unknown, p: { status?: string }) => ({ items: [], total: p.status === "pending" ? pending : overdue, page: 1, page_size: 1 }),
  "GET /api/notifications": () => ({ unread: notifications.filter((n) => !n.is_read).length, items: notifications }),
  "POST /api/notifications/read-all": () => ({}),
});

function open(route = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const user = userEvent.setup();
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <RangeProvider>
          <Routes>
            <Route element={<AppLayout />}>
              <Route index element={<h1>Home content</h1>} />
              <Route path="*" element={<LocationProbe />} />
            </Route>
          </Routes>
        </RangeProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { user, client, ...view };
}
const sidebar = () => within(screen.getByRole("navigation", { name: "Main" }));
const search = () => screen.getByLabelText("Search transactions");

beforeEach(() => {
  notifications = []; pending = 0; overdue = 0; pp = paypal(); me = profile();
  serve(routes());
});

describe("navigation", () => {
  it("links every section with the right destination", async () => {
    open();
    await screen.findByRole("heading", { name: "Home content" });
    const hrefs = Object.fromEntries(["Overview", "AI Assistant", "Invoices", "Transactions", "Payment Links", "Customers"].map((n) => [n, sidebar().getByRole("link", { name: new RegExp(n) }).getAttribute("href")]));
    expect(hrefs).toEqual({ Overview: "/", "AI Assistant": "/assistant", Invoices: "/invoices", Transactions: "/transactions", "Payment Links": "/payment-links", Customers: "/customers" });
    const mgmt = within(screen.getByRole("navigation", { name: "Management" }));
    expect(mgmt.getByRole("link", { name: /Analytics/ })).toHaveAttribute("href", "/analytics");
    expect(mgmt.getByRole("link", { name: /Automations/ })).toHaveAttribute("href", "/automations");
    expect(screen.getByRole("link", { name: /Settings/ })).toHaveAttribute("href", "/settings");
  });

  it("marks only the current section as active (Overview is not active on other pages)", async () => {
    open("/transactions");
    expect(await screen.findByTestId("location")).toHaveTextContent("/transactions");
    expect(sidebar().getByRole("link", { name: /Transactions/ })).toHaveAttribute("aria-current", "page");
    expect(sidebar().getByRole("link", { name: /Overview/ })).not.toHaveAttribute("aria-current");
  });

  it("navigates when a link is clicked", async () => {
    const { user } = open();
    await user.click(sidebar().getByRole("link", { name: /Customers/ }));
    expect(await screen.findByTestId("location")).toHaveTextContent("/customers");
    expect(sidebar().getByRole("link", { name: /Customers/ })).toHaveAttribute("aria-current", "page");
  });

  it("shows the number of open invoices (pending + overdue) next to Invoices, and nothing when there are none", async () => {
    pending = 3; overdue = 2;
    const view = open();
    expect(await within(await screen.findByRole("link", { name: /Invoices/ })).findByText("5")).toBeInTheDocument();
    view.unmount();
    pending = 0; overdue = 0;
    open();
    const link = await screen.findByRole("link", { name: /Invoices/ });
    await waitFor(() => expect(requests(get, "/api/invoices").length).toBeGreaterThan(2));
    expect(within(link).queryByText(/^\d+$/)).not.toBeInTheDocument();
  });

  it("names the current page in the breadcrumb", async () => {
    open("/transactions");
    expect(await screen.findByText("Transactions", { selector: "span.font-medium" })).toBeInTheDocument();
  });

  it("falls back to 'Operations' for the home page", async () => {
    open("/");
    expect(await screen.findByText("Operations")).toBeInTheDocument();
  });

  it("the avatar button opens account settings", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Account settings" }));
    expect(await screen.findByTestId("location")).toHaveTextContent("/settings");
  });
});

describe("account and sign-out", () => {
  it("shows the signed-in user's name and business", async () => {
    open();
    expect(await screen.findByText("Pranav Raj")).toBeInTheDocument();
    expect(screen.getByText("Acme")).toBeInTheDocument();
  });

  it("falls back to email, then to a generic label, when there is no name", async () => {
    me = profile({ full_name: null, business_name: null });
    const view = open();
    expect(await screen.findByText("me@example.com")).toBeInTheDocument();
    expect(screen.getByText("Founder")).toBeInTheDocument();
    view.unmount();
    me = profile({ full_name: null, email: "" });
    open();
    expect(await screen.findByText("Account")).toBeInTheDocument();
  });

  it("signs the user out", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(auth.signOut).toHaveBeenCalledTimes(1);
  });
});

describe("global search", () => {
  it("sends the query to the transactions ledger on Enter", async () => {
    const { user } = open();
    await user.type(search(), "ORD-123{Enter}");
    expect(await screen.findByTestId("location")).toHaveTextContent("/transactions?search=ORD-123");
  });

  it("trims the query and URL-encodes special characters", async () => {
    const { user } = open();
    await user.type(search(), "  acme & sons/ltd  {Enter}");
    expect(await screen.findByTestId("location")).toHaveTextContent("/transactions?search=acme%20%26%20sons%2Fltd");
  });

  it("does nothing for an empty or blank query", async () => {
    const { user } = open();
    await screen.findByRole("heading", { name: "Home content" });
    await user.type(search(), "   {Enter}");
    expect(screen.getByRole("heading", { name: "Home content" })).toBeInTheDocument();
  });

  it("is focused by Ctrl+K and Cmd+K", async () => {
    const { user } = open();
    await screen.findByRole("heading", { name: "Home content" });
    await user.keyboard("{Control>}k{/Control}");
    expect(search()).toHaveFocus();
    (document.activeElement as HTMLElement).blur();
    await user.keyboard("{Meta>}k{/Meta}");
    expect(search()).toHaveFocus();
  });
});

describe("notifications", () => {
  it("has no unread dot when everything is read", async () => {
    notifications = [note({ is_read: true })];
    open();
    const bell = await screen.findByRole("button", { name: "Notifications" });
    await waitFor(() => expect(requests(get, "/api/notifications").length).toBeGreaterThan(0));
    expect(bell.querySelector(".bg-danger")).toBeNull();
  });

  it("flags unread notifications and lists them when opened", async () => {
    notifications = [note(), note({ id: "n2", title: "Invoice overdue", message: "INV-0003 is overdue", is_read: true })];
    const { user } = open();
    const bell = await screen.findByRole("button", { name: "Notifications" });
    await waitFor(() => expect(bell.querySelector(".bg-danger")).not.toBeNull());
    await user.click(bell);
    expect(await screen.findByText("Payment received")).toBeInTheDocument();
    expect(screen.getByText("Sarah paid INV-0001")).toBeInTheDocument();
    expect(screen.getByText("Invoice overdue")).toBeInTheDocument();
  });

  it("says when there is nothing new", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Notifications" }));
    expect(await screen.findByText("You're all caught up.")).toBeInTheDocument();
  });

  it("marks all as read and refreshes the list", async () => {
    notifications = [note()];
    const { user, client } = open();
    await user.click(await screen.findByRole("button", { name: "Notifications" }));
    const spy = vi.spyOn(client, "invalidateQueries");
    await user.click(await screen.findByRole("button", { name: "Mark all read" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/notifications/read-all"));
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: ["notifications"] }));
  });

  it("closes when the user clicks elsewhere, and toggles on the bell", async () => {
    notifications = [note()];
    const { user } = open();
    const bell = await screen.findByRole("button", { name: "Notifications" });
    await user.click(bell);
    await screen.findByText("Sarah paid INV-0001");
    await user.click(bell);
    expect(screen.queryByText("Sarah paid INV-0001")).not.toBeInTheDocument();
    await user.click(bell);
    await screen.findByText("Sarah paid INV-0001");
    await user.click(screen.getByRole("heading", { name: "Home content" }));
    expect(screen.queryByText("Sarah paid INV-0001")).not.toBeInTheDocument();
  });
});

describe("environment badge and date range", () => {
  it.each([
    ["sandbox", { mode: "sandbox" as const }, "PayPal Sandbox"],
    ["live", { mode: "live" as const }, "PayPal Live"],
    ["unconfigured", { configured: false }, "PayPal not configured"],
  ])("labels the %s PayPal environment", async (_n, change, label) => {
    pp = paypal(change);
    open();
    expect(await screen.findByText(label)).toBeInTheDocument();
  });

  it("changes the global date range", async () => {
    const { user } = open();
    const select = await screen.findByLabelText("Date range");
    expect(select).toHaveValue("30");
    await user.selectOptions(select, "7");
    expect(select).toHaveValue("7");
  });
});

describe("mobile menu", () => {
  it("opens a drawer with the same navigation and closes it again", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Open menu" }));
    expect(screen.getAllByRole("navigation", { name: "Main" })).toHaveLength(2); // desktop + drawer
    await user.click(screen.getByRole("button", { name: "Close menu" }));
    expect(screen.getAllByRole("navigation", { name: "Main" })).toHaveLength(1);
  });

  it("closes the drawer after choosing a destination", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Open menu" }));
    const drawerNav = within(screen.getAllByRole("navigation", { name: "Main" })[1]!);
    await user.click(drawerNav.getByRole("link", { name: /Customers/ }));
    expect(await screen.findByTestId("location")).toHaveTextContent("/customers");
    await waitFor(() => expect(screen.getAllByRole("navigation", { name: "Main" })).toHaveLength(1));
  });
});

describe("additional layout behaviour", () => {
  it.each([
    ["/assistant", "AI Assistant"], ["/invoices", "Invoices"], ["/invoices/new", "Invoices"], ["/transactions", "Transactions"], ["/payment-links", "Payment Links"],
    ["/customers", "Customers"], ["/analytics", "Analytics"], ["/automations", "Automations"], ["/settings", "Settings"], ["/not-a-real-page", "Operations"],
  ])("breadcrumb for %s reads %s", async (route, label) => {
    open(route);
    expect(await screen.findByText(label, { selector: "span.font-medium" })).toBeInTheDocument();
  });

  it("the help link opens the project README in a new tab safely", async () => {
    open();
    const help = await screen.findByRole("link", { name: /Help & Support/ });
    expect(help).toHaveAttribute("href", "https://github.com/Pranav-Raj-05/PayPilot#readme");
    expect(help).toHaveAttribute("target", "_blank");
    expect(help.getAttribute("rel")).toContain("noreferrer");
  });

  it("can sign out from the mobile drawer too", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Open menu" }));
    const signOuts = screen.getAllByRole("button", { name: "Sign out" });
    expect(signOuts).toHaveLength(2);
    await user.click(signOuts[1]!);
    expect(auth.signOut).toHaveBeenCalledTimes(1);
  });

  it("keeps the typed query in the box after searching", async () => {
    const { user } = open();
    await user.type(search(), "ORD-1{Enter}");
    await screen.findByTestId("location");
    expect(search()).toHaveValue("ORD-1");
  });

  it("shows how long ago each notification arrived", async () => {
    notifications = [note({ created_at: new Date(Date.now() - 5 * 60_000).toISOString() })];
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Notifications" }));
    expect(await screen.findByText("5m ago")).toBeInTheDocument();
  });
});
