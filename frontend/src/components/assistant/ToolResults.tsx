import clsx from "clsx";
import { Check } from "lucide-react";
import type { ChatMessage } from "@/types";
import { money } from "@/utils/format";
import { StatusBadge } from "@/components/ui";

const NAMES: Record<string, string> = {
  get_revenue_summary: "revenue summary", list_transactions: "transactions", get_pending_payments: "pending payments", analyse_business_performance: "business performance",
  suggest_payment_reminder: "reminder drafts", list_customers: "customers", get_invoice: "invoice", get_transaction: "transaction",
};

/** Compact "tool executed" indicator + a structured card for the read-only tools. */
export function ReadResult({ msg }: { msg: ChatMessage }) {
  const r = msg.tool_result?.result ?? {};
  const name = NAMES[msg.tool_name ?? ""] ?? msg.tool_name;
  return (
    <div className="space-y-2">
      <div className="inline-flex items-center gap-1.5 rounded-full bg-primary-container/60 px-2.5 py-1 font-label text-[11px] font-semibold text-primary-on"><Check className="h-3 w-3" />Tool: checked {name}</div>
      <Body name={msg.tool_name ?? ""} r={r} />
    </div>
  );
}

const Box = ({ children }: { children: React.ReactNode }) => <div className="overflow-hidden rounded-2xl bg-white p-4 shadow-card ring-1 ring-surface-high">{children}</div>;
const Stat = ({ label, value, tone }: { label: string; value: string; tone?: string }) => <div className="rounded-xl bg-surface-low p-3"><div className="label-caps">{label}</div><div className={clsx("mt-1 font-display text-xl font-semibold", tone)}>{value}</div></div>;

function Body({ name, r }: { name: string; r: any }) {
  if (name === "get_revenue_summary") return (
    <Box><div className="mb-3 text-sm text-muted">Revenue {r.period}</div><div className="grid gap-3 sm:grid-cols-3">
      <Stat label="Revenue" value={money(r.revenue, r.currency)} tone="text-primary" /><Stat label="Payments" value={String(r.payments)} /><Stat label="Average" value={money(r.average_payment, r.currency)} /></div>
      {!!Object.keys(r.by_provider ?? {}).length && <div className="mt-3 flex gap-2 text-xs text-muted">{Object.entries(r.by_provider).map(([k, v]) => <span key={k} className="rounded bg-surface px-2 py-1 capitalize">{k}: {money(v as number, r.currency)}</span>)}</div>}</Box>);
  if (name === "get_pending_payments") return (
    <Box><div className="mb-2 text-sm text-muted">{r.count} open invoice(s) · {r.overdue_count} overdue</div>
      {r.invoices?.length ? <ul className="divide-y divide-surface-low">{r.invoices.map((i: any) => <li key={i.invoice_number} className="flex items-center justify-between gap-2 py-2 text-sm"><span><b>{i.invoice_number}</b> · {i.customer ?? "—"}{i.days_overdue > 0 && <span className="ml-2 text-xs text-danger">{i.days_overdue}d overdue</span>}</span><span className="flex items-center gap-2">{money(i.amount, i.currency)}<StatusBadge status={i.status} /></span></li>)}</ul> : <p className="text-sm">Nothing outstanding 🎉</p>}</Box>);
  if (name === "list_transactions") return (
    <Box><div className="mb-2 text-sm text-muted">{r.total} transaction(s) {r.period}</div>
      {r.transactions?.length ? <ul className="divide-y divide-surface-low">{r.transactions.map((t: any) => <li key={t.id} className="flex items-center justify-between gap-2 py-2 text-sm"><span>{t.customer ?? "Unassigned"} <span className="text-xs text-muted capitalize">· {t.provider}</span>{t.failure_reason && <span className="block text-xs text-danger">{t.failure_reason}</span>}</span><span className="flex items-center gap-2">{money(t.amount, t.currency)}<StatusBadge status={t.status} /></span></li>)}</ul> : <p className="text-sm">No matching transactions.</p>}</Box>);
  if (name === "suggest_payment_reminder") return (
    <Box>{r.drafts?.length ? r.drafts.map((d: any) => <div key={d.invoice_number} className="mb-3 rounded-xl bg-surface-low p-3 text-sm last:mb-0"><div className="mb-1 flex justify-between"><b>{d.invoice_number} · {d.customer}</b><span className="text-xs text-danger">{d.days_overdue}d overdue</span></div><p className="text-muted">{d.message}</p></div>) : <p className="text-sm">No overdue invoices to remind.</p>}
      <p className="mt-2 text-xs text-muted">Drafts only — nothing was sent. Ask me to "queue a reminder" to record one after approval. Email/SMS delivery isn't configured.</p></Box>);
  if (name === "analyse_business_performance") return (
    <Box><div className="grid gap-3 sm:grid-cols-3"><Stat label={`Revenue · ${r.days}d`} value={money(r.revenue?.total, r.currency)} tone="text-primary" /><Stat label="Success rate" value={r.success_rate?.value == null ? "—" : `${r.success_rate.value}%`} /><Stat label="Avg payment" value={money(r.average_transaction_value, r.currency)} /></div>
      {!!r.top_customers?.length && <div className="mt-3 text-xs text-muted">Top: {r.top_customers.slice(0, 3).map((c: any) => `${c.customer} (${money(c.revenue, r.currency)})`).join(" · ")}</div>}</Box>);
  if (name === "list_customers") return <Box><div className="mb-1 text-sm text-muted">{r.total} customer(s)</div><ul className="text-sm">{r.customers?.map((c: any) => <li key={c.id}><b>{c.name}</b> <span className="text-muted">{c.email}</span></li>)}</ul></Box>;
  if (name === "get_invoice" && r.invoice) return <Box><div className="flex items-center justify-between"><b>{r.invoice.invoice_number}</b><StatusBadge status={r.invoice.status} /></div><div className="mt-1 text-sm text-muted">{r.invoice.customer?.name ?? "No customer"} · {money(r.invoice.total_amount, r.invoice.currency)}</div></Box>;
  if (name === "get_transaction" && r.transaction) return <Box><div className="flex items-center justify-between"><b>{money(r.transaction.amount, r.transaction.currency)}</b><StatusBadge status={r.transaction.status} /></div><div className="mt-1 text-sm text-muted capitalize">{r.transaction.provider} · {r.transaction.provider_order_id}</div></Box>;
  return null;
}
