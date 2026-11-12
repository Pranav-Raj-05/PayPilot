import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Button, CopyButton, EmptyState, ErrorState, Field, Input, Modal, Pagination, Segmented, StatusBadge } from "./index";
import { ToastProvider } from "@/context/ToastContext";

describe("Button", () => {
  it("is disabled and does not fire while loading", async () => {
    const onClick = vi.fn();
    render(<Button loading onClick={onClick}>Save</Button>);
    const btn = screen.getByRole("button", { name: "Save" });
    expect(btn).toBeDisabled();
    await userEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("fires when enabled", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Save</Button>);
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

describe("Field", () => {
  it("associates the label with its input and announces errors", () => {
    render(<Field label="Email" error="Enter a valid email"><Input type="email" /></Field>);
    expect(screen.getByLabelText(/^Email/)).toBeInstanceOf(HTMLInputElement); // the error text is inside the label too
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a valid email");
  });

  it("shows the hint only when there is no error", () => {
    const { rerender } = render(<Field label="Amount" hint="In dollars"><Input /></Field>);
    expect(screen.getByText("In dollars")).toBeInTheDocument();
    rerender(<Field label="Amount" hint="In dollars" error="Required"><Input /></Field>);
    expect(screen.queryByText("In dollars")).not.toBeInTheDocument();
  });
});

describe("Modal", () => {
  const Harness = () => {
    const [open, setOpen] = useState(true);
    return <><button onClick={() => setOpen(true)}>reopen</button><Modal open={open} onClose={() => setOpen(false)} title="Edit thing"><input aria-label="inside" /></Modal></>;
  };

  it("is an accessible dialog and renders nothing when closed", async () => {
    render(<Harness />);
    expect(screen.getByRole("dialog", { name: "Edit thing" })).toHaveAttribute("aria-modal", "true");
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes on Escape and on a backdrop click, but not on a click inside", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByLabelText("inside"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "reopen" }));
    fireEvent.mouseDown(screen.getByRole("dialog").parentElement!);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("stops listening for Escape once closed", () => {
    const onClose = vi.fn();
    const { rerender } = render(<Modal open onClose={onClose} title="T">x</Modal>);
    rerender(<Modal open={false} onClose={onClose} title="T">x</Modal>);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("Pagination", () => {
  it("reports the visible range and disables the ends", () => {
    const { rerender } = render(<Pagination page={1} pageSize={20} total={45} onPage={() => {}} />);
    expect(screen.getByText("Showing 1–20 of 45")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous page" })).toBeDisabled();
    rerender(<Pagination page={3} pageSize={20} total={45} onPage={() => {}} />);
    expect(screen.getByText("Showing 41–45 of 45")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBeDisabled();
  });

  it("handles zero results without a negative range", () => {
    render(<Pagination page={1} pageSize={20} total={0} onPage={() => {}} />);
    expect(screen.getByText("Showing 0–0 of 0")).toBeInTheDocument();
    expect(screen.getByText("1 / 1")).toBeInTheDocument();
  });

  it("asks for the neighbouring page", async () => {
    const onPage = vi.fn();
    render(<Pagination page={2} pageSize={10} total={50} onPage={onPage} />);
    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    await userEvent.click(screen.getByRole("button", { name: "Previous page" }));
    expect(onPage.mock.calls).toEqual([[3], [1]]);
  });
});

describe("Segmented", () => {
  it("marks the active option and reports changes", async () => {
    const onChange = vi.fn();
    render(<Segmented value={30} onChange={onChange} options={[{ value: 7, label: "7D" }, { value: 30, label: "30D" }]} />);
    expect(screen.getByRole("tab", { name: "30D" })).toHaveAttribute("aria-selected", "true");
    await userEvent.click(screen.getByRole("tab", { name: "7D" }));
    expect(onChange).toHaveBeenCalledWith(7);
  });
});

describe("EmptyState and ErrorState", () => {
  it("EmptyState shows its guidance and call to action", () => {
    render(<EmptyState title="No invoices" text="Create one to get paid." action={<button>Create</button>} />);
    expect(screen.getByRole("heading", { name: "No invoices" })).toBeInTheDocument();
    expect(screen.getByText("Create one to get paid.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create" })).toBeInTheDocument();
  });

  it("ErrorState shows the error message and offers Retry only when it can", async () => {
    const onRetry = vi.fn();
    const { rerender } = render(<ErrorState error={new Error("Boom")} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Boom");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    rerender(<ErrorState error={new Error("Boom")} />);
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("ErrorState falls back to a generic message for non-Error values", () => {
    render(<ErrorState error={undefined} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong.");
  });
});

describe("StatusBadge", () => {
  it("shows the status or a custom label, and tolerates unknown statuses", () => {
    const { rerender } = render(<StatusBadge status="paid" />);
    expect(screen.getByText("paid")).toBeInTheDocument();
    rerender(<StatusBadge status="paid" label="Settled" />);
    expect(screen.getByText("Settled")).toBeInTheDocument();
    rerender(<StatusBadge status="some_new_status" />);
    expect(screen.getByText("some_new_status")).toBeInTheDocument();
  });
});

describe("CopyButton", () => {
  const setup = (writeText: () => Promise<void>) => {
    const user = userEvent.setup();
    // userEvent.setup() installs its own clipboard stub; replace it after setup so our spy is the one called.
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<ToastProvider><CopyButton text="https://pay.example/l/1" /></ToastProvider>);
    return user;
  };

  it("copies the text and confirms with a toast", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const user = setup(writeText);
    await user.click(screen.getByRole("button", { name: /copy/i }));
    expect(writeText).toHaveBeenCalledWith("https://pay.example/l/1");
    expect(await screen.findByText("Link copied")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /copied/i })).toBeInTheDocument();
  });

  it("tells the user when copying is blocked", async () => {
    const user = setup(vi.fn().mockRejectedValue(new Error("denied")));
    await user.click(screen.getByRole("button", { name: /copy/i }));
    expect(await screen.findByText("Copy failed")).toBeInTheDocument();
    expect(screen.queryByText("Link copied")).not.toBeInTheDocument();
  });
});
