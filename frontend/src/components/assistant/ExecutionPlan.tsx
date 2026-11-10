import clsx from "clsx";
import { Check, Circle, Loader2, ShieldAlert, X } from "lucide-react";
import { useInvoice } from "@/hooks/queries";
import type { ChatMessage } from "@/types";
import { fmtTime, money } from "@/utils/format";

type StepState = "done" | "action" | "queued" | "pending" | "failed" | "skipped";
interface Step { title: string; detail: string; state: StepState; at?: string }

export function buildSteps(msg: ChatMessage, invoiceStatus?: string): Step[] {
  const r = msg.tool_result!, p = r.preview ?? {}, a = (r.args ?? {}) as any;
  const st = r.status, res = r.result ?? {};
  const executed = st === "executed", cancelled = st === "cancelled", failed = st === "failed";
  const approved = executed || st === "approved" || failed;
  const link = res.payment_link;
  const at = (r as any).executed_at as string | undefined;
  return [
    { title: "Understanding user request", state: "done", at: msg.created_at, detail: `Intent: ${msg.tool_name?.toUpperCase()} · client ${a.customer_name ?? "?"} · amount ${a.amount ?? "?"} ${a.currency ?? ""}` },
    { title: "Resolving customer & terms", state: "done", detail: p.customer ? `${p.customer.existing ? "Matched existing customer" : "New customer will be created"} · ${p.terms}` : "—" },
    { title: "Generating invoice draft", state: "done", detail: p.total != null ? `Total ${money(p.total, p.currency)} · tax ${p.tax_rate}% · due ${p.due_date}` : "—" },
    { title: "Waiting for user approval", state: cancelled ? "skipped" : approved ? "done" : "action", at: approved ? (r as any).approved_at : undefined,
      detail: cancelled ? "Rejected by user — nothing was created." : approved ? "Approved by the account owner in the UI." : "Financial operations require explicit human authorization before anything is created." },
    { title: `Creating ${p.provider === "razorpay" ? "Razorpay" : "PayPal"} order & invoice`, state: cancelled ? "skipped" : failed ? "failed" : executed ? (res.warning ? "failed" : "done") : "queued", at,
      detail: failed ? r.error ?? "Execution failed" : executed ? (res.warning ?? `Order ${link?.reference ?? ""} created · invoice ${res.invoice?.invoice_number}`) : "Runs only after approval." },
    { title: "Waiting for customer checkout", state: cancelled || failed ? "skipped" : invoiceStatus === "paid" ? "done" : executed && link ? "action" : "pending",
      detail: executed && link ? "Payment link is live. Share it with the customer." : "Starts once the link exists." },
    { title: "Verifying payment with the provider", state: cancelled || failed ? "skipped" : invoiceStatus === "paid" ? "done" : "pending",
      detail: invoiceStatus === "paid" ? "Capture verified server-side; invoice marked paid." : "Capture is verified with the provider API (and webhook) before the invoice is marked paid." },
  ];
}

const ICON = { done: <Check className="h-3 w-3" />, action: <ShieldAlert className="h-3 w-3" />, queued: <Loader2 className="h-3 w-3" />, pending: <Circle className="h-3 w-3" />, failed: <X className="h-3 w-3" />, skipped: <X className="h-3 w-3" /> };
const BADGE: Record<StepState, string> = { done: "Completed", action: "Action required", queued: "Queued", pending: "Pending", failed: "Failed", skipped: "Skipped" };

export function ExecutionPlan({ msg }: { msg: ChatMessage }) {
  const invId = msg.tool_result?.result?.invoice?.id as string | undefined;
  const { data: inv } = useInvoice(invId, true);
  const steps = buildSteps(msg, inv?.status);
  const done = steps.filter((s) => s.state === "done").length;
  return (
    <div className="rounded-2xl bg-surface-low p-4">
      <div className="flex flex-wrap items-center justify-between gap-2"><div className="font-semibold">Execution plan</div><span className="rounded bg-white px-2 py-0.5 font-label text-[11px] font-semibold text-primary">{done} of {steps.length} steps complete</span></div>
      <ol className="mt-3 space-y-3">{steps.map((s, i) => (
        <li key={i} className={clsx("flex gap-3", (s.state === "pending" || s.state === "skipped") && "opacity-60")}>
          <span className={clsx("mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full", s.state === "done" ? "bg-primary text-white" : s.state === "action" ? "bg-primary-container text-primary ring-2 ring-primary" : s.state === "failed" ? "bg-danger text-white" : "bg-white text-muted ring-1 ring-surface-high")}>{ICON[s.state]}</span>
          <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center justify-between gap-2"><span className="text-sm font-semibold">Step {String(i + 1).padStart(2, "0")} · {s.title}</span>
            <span className={clsx("rounded px-1.5 py-0.5 font-label text-[10px] font-semibold uppercase", s.state === "action" ? "bg-danger-container text-danger-dark" : s.state === "failed" ? "bg-danger-container text-danger-dark" : "bg-white text-muted")}>{BADGE[s.state]}{s.at ? ` · ${fmtTime(s.at)}` : ""}</span></div>
            <p className="break-words text-xs text-muted">{s.detail}</p></div>
        </li>))}</ol>
    </div>
  );
}
