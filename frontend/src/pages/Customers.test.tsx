import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async () => await import("@/test/api"));

import Customers from "./Customers";
import { ApiError, deferred, del, get, patch, post, requests, serve } from "@/test/api";
import { customer, page } from "@/test/fixtures";
import { renderPage } from "@/test/render";

const sarah = customer();
const noEmail = customer({ id: "c2", name: "Apex Cloud", email: null, country: null });
const open = () => renderPage(<Customers />);
const dialog = () => within(screen.getByRole("dialog"));

beforeEach(() => {
  serve({
    "GET /api/customers": () => page([sarah, noEmail]),
    "POST /api/customers": (body) => ({ ...sarah, id: "new", ...body }),
    "PATCH /api/customers/c1": (body) => ({ ...sarah, ...body }),
    "DELETE /api/customers/c1": () => undefined,
  });
});

describe("customer list states", () => {
  it("shows a loading placeholder, not the empty state, while fetching", async () => {
    const d = deferred();
    get.mockReturnValue(d.promise);
    open();
    expect(document.querySelector(".animate-pulse")).toBeInTheDocument();
    expect(screen.queryByText("No customers")).not.toBeInTheDocument();
    d.resolve(page([sarah]));
    expect(await screen.findByText("Sarah Johnson")).toBeInTheDocument();
  });

  it("lists customers, falling back to 'no email' and showing the country when known", async () => {
    open();
    expect(await screen.findByText("sarah@example.com · US")).toBeInTheDocument();
    expect(screen.getByText("Apex Cloud")).toBeInTheDocument();
    expect(screen.getByText("no email")).toBeInTheDocument();
  });

  it("shows an empty state when there are no customers", async () => {
    serve({ "GET /api/customers": () => page([]) });
    open();
    expect(await screen.findByText("No customers")).toBeInTheDocument();
  });

  it("shows the API error and recovers on Retry", async () => {
    let healthy = false;
    serve({ "GET /api/customers": () => { if (!healthy) throw new ApiError("Cannot reach the PayPilot API. Check your connection."); return page([sarah]); } });
    const { user } = open();
    expect(await screen.findByRole("alert")).toHaveTextContent("Cannot reach the PayPilot API");
    healthy = true;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Sarah Johnson")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("sends the search text to the API", async () => {
    const { user } = open();
    await screen.findByText("Sarah Johnson");
    await user.type(screen.getByLabelText("Search customers"), "ann");
    await waitFor(() => expect(requests(get, "/api/customers").at(-1)![1]).toEqual({ search: "ann", page_size: 100 }));
  });
});

describe("adding a customer", () => {
  it("cannot be saved without a name", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Add customer" }));
    expect(dialog().getByRole("button", { name: "Save" })).toBeDisabled();
    await user.type(dialog().getByLabelText("Name"), "   ");
    expect(dialog().getByRole("button", { name: "Save" })).toBeDisabled();
    expect(post).not.toHaveBeenCalled();
  });

  it("trims the name, upper-cases the country and nulls empty fields", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Add customer" }));
    await user.type(dialog().getByLabelText("Name"), "  Acme Ltd  ");
    await user.type(dialog().getByLabelText("Email"), "billing@acme.test");
    await user.type(dialog().getByLabelText("Country (2-letter)"), "in");
    await user.click(dialog().getByRole("button", { name: "Save" }));
    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0]).toEqual(["/api/customers", { name: "Acme Ltd", email: "billing@acme.test", phone: null, address: null, country: "IN" }]);
    expect(await screen.findByText("Customer saved")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("limits the country to two characters", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Add customer" }));
    await user.type(dialog().getByLabelText("Country (2-letter)"), "usa");
    expect(dialog().getByLabelText("Country (2-letter)")).toHaveValue("us");
  });

  it("keeps the dialog open and reports the error when the API rejects the customer", async () => {
    serve({ "GET /api/customers": () => page([sarah]), "POST /api/customers": () => { throw new ApiError("Email already in use", 409, "conflict"); } });
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Add customer" }));
    await user.type(dialog().getByLabelText("Name"), "Dup");
    await user.click(dialog().getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Email already in use")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("closes without saving on Cancel or Escape", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Add customer" }));
    await user.click(dialog().getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add customer" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });
});

describe("editing and deleting", () => {
  it("edits an existing customer through PATCH with the current values prefilled", async () => {
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Edit Sarah Johnson" }));
    expect(dialog().getByLabelText("Name")).toHaveValue("Sarah Johnson");
    expect(dialog().getByLabelText("Email")).toHaveValue("sarah@example.com");
    await user.clear(dialog().getByLabelText("Name"));
    await user.type(dialog().getByLabelText("Name"), "Sarah J. Johnson");
    await user.click(dialog().getByRole("button", { name: "Save" }));
    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(patch.mock.calls[0]![0]).toBe("/api/customers/c1");
    expect(patch.mock.calls[0]![1]).toMatchObject({ name: "Sarah J. Johnson", email: "sarah@example.com", country: "US" });
    expect(post).not.toHaveBeenCalled();
  });

  it("does not delete unless the user confirms", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Delete Sarah Johnson" }));
    expect(confirm).toHaveBeenCalledWith("Delete Sarah Johnson?");
    expect(del).not.toHaveBeenCalled();
  });

  it("deletes after confirmation and refreshes the list", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Delete Sarah Johnson" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("/api/customers/c1"));
    expect(await screen.findByText("Customer deleted")).toBeInTheDocument();
    await waitFor(() => expect(requests(get, "/api/customers").length).toBeGreaterThan(1));
  });

  it("reports a failed delete (e.g. the customer still has invoices)", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    serve({ "GET /api/customers": () => page([sarah]), "DELETE /api/customers/c1": () => { throw new ApiError("Customer has invoices", 409, "conflict"); } });
    const { user } = open();
    await user.click(await screen.findByRole("button", { name: "Delete Sarah Johnson" }));
    expect(await screen.findByText("Customer has invoices")).toBeInTheDocument();
  });
});
