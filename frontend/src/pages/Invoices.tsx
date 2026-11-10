import clsx from "clsx";
import { AlertTriangle, CheckCircle2, Circle, Clock, Download, FileText, Link2, Plus, Receipt, Search, Send, ShoppingCart, Sparkles, Trash2, X } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { StatCard } from "@/components/StatCard";
import { Avatar, Button, Card, CopyButton, EmptyState, ErrorState, Pagination, Skeleton, StatusBadge } from "@/components/ui";
import { useToast } from "@/context/ToastContext";
import { useDeleteInvoice, useInvoice, useInvoiceActivity, useInvoiceLink, useInvoiceSummary, useInvoices } from "@/hooks/queries";
import type { Invoice } from "@/types";
import { downloadCsv } from "@/utils/csv";
import { fmtDate, fmtTime, money, providerLabel } from "@/utils/format";

const TABS: [string, string][] = [["", "all"], ["paid", "paid"], ["pending", "pending"], ["overdue", "overdue"], ["draft", "draft"]];
const TAB_LABEL: Record<string, string> = { all: "All", paid: "Paid", pending: "Pending", overdue: "Overdue", draft: "Drafts" };

function LivePreview({ id, onClose }: { id: string; onClose: () => void }) {
  const nav = useNavigate();
  const { toast } = useToast();
  const { data: inv } = useInvoice(id);
  const { data: activity } = useInvoiceActivity(id);
  const link = useInvoiceLink();
  if (!inv) return <Card className="p-6"><Skeleton className="h-96" /></Card>;
  const make = async () => { try { await link.mutateAsync({ id: inv.id, provider: "paypal" }); toast("PayPal link created"); } catch (e) { toast((e as Error).message, "error"); } };
  const canRemind = inv.status === "pending" || inv.status === "overdue";
  return (
    <Card className="flex flex-col gap-5 p-5">
      <div className="flex items-start justify-between gap-2">
        <div><div className="label-caps">Live preview</div><button onClick={() => nav(`/invoices/${inv.id}`)} className="font-display text-xl font-semibold hover:text-primary">#{inv.invoice_number}</button></div>
        <div className="flex items-center gap-2">
          {canRemind && <Button size="sm" icon={<Send className="h-3.5 w-3.5" />} onClick={() => nav("/assistant", { state: { prompt: `Draft a payment reminder for invoice ${inv.invoice_number}` } })}>Send Reminder</Button>}
          <button aria-label="Close preview" onClick={onClose} className="rounded-lg p-1.5 text-muted hover:bg-surface-low"><X className="h-4 w-4" /></button>
        </div>
      </div>
      {inv.payment_link && inv.status !== "paid" && (
        <div className="flex items-center gap-3 rounded-xl bg-surface-low p-3"><span className="rounded-lg bg-primary p-2 text-white"><ShoppingCart className="h-4 w-4" /></span><div className="min-w-0 text-sm"><div className="font-semibold">Pending customer checkout</div><div className="truncate text-xs text-muted">{providerLabel(inv.payment_provider ?? "")} link active</div></div></div>
      )}
      <div className="rounded-xl bg-surface-low p-4">
        <div className="flex items-center justify-between"><span className="label-caps">Billed to</span>{inv.customer?.email && <span className="rounded bg-primary-container px-2 py-0.5 font-label text-[11px] font-semibold text-primary-on">On file</span>}</div>
        <div className="mt-2 flex items-center gap-3"><Avatar name={inv.customer?.name ?? "?"} /><div><div className="font-display text-lg font-semibold">{inv.customer?.name ?? "No customer"}</div><div className="text-xs text-muted">{inv.customer?.email ?? "—"}</div></div></div>
      </div>
      <div>
        <div className="flex justify-between bg-surface-low px-3 py-2 label-caps"><span>Item &amp; description</span><span>Amount</span></div>
        {inv.items.map((i) => <div key={i.id} className="flex justify-between gap-3 px-3 py-3 text-sm"><div><div className="font-semibold">{i.description}</div><div className="text-xs text-muted">{i.quantity} unit × {money(i.unit_price, inv.currency)}</div></div><div className="font-display text-lg font-semibold">{money(i.total, inv.currency)}</div></div>)}
        <div className="space-y-1 border-t border-surface-low px-3 pt-3 text-sm"><div className="flex justify-between text-muted"><span>Subtotal</span><span>{money(inv.subtotal, inv.currency)}</span></div><div className="flex justify-between text-muted"><span>Tax</span><span>{money(inv.tax, inv.currency)}</span></div>
          <div className="flex items-baseline justify-between pt-1"><span className="font-semibold">Total Due</span><span className="font-display text-2xl font-semibold text-primary">{money(inv.total_amount, inv.currency)} <span className="text-xs text-muted">{inv.currency}</span></span></div></div>
      </div>
      <div className="rounded-xl bg-surface-low p-4">
        <div className="flex justify-between"><span className="label-caps">Hosted {providerLabel(inv.payment_provider ?? "PayPal")} checkout</span><span className="text-xs text-muted">Verified on capture</span></div>
        {inv.payment_link ? <>
          <div className="mt-2 flex items-center gap-2 rounded-lg bg-white px-3 py-2 text-xs shadow-card"><Link2 className="h-3.5 w-3.5 shrink-0 text-muted" /><span className="min-w-0 flex-1 break-all">{inv.payment_link}</span><CopyButton text={inv.payment_link} label="Copy" className="!bg-primary !text-white" /></div>
          <div className="mt-3 flex items-center gap-3"><div className="rounded-lg bg-white p-2"><QRCodeSVG value={inv.payment_link} size={72} /></div><div className="text-sm"><div className="font-semibold">Scan to pay</div><div className="text-xs text-muted">Opens the provider's checkout on mobile.</div></div></div>
        </> : <div className="mt-2 text-sm text-muted">No payment link yet.{["draft", "pending", "overdue"].includes(inv.status) && <Button size="sm" className="ml-3" loading={link.isPending} icon={<Link2 className="h-3.5 w-3.5" />} onClick={make}>Create PayPal link</Button>}</div>}
      </div>
      <div><div className="label-caps mb-3">Activity audit trail</div>
        <ol className="space-y-3">{activity?.map((a, i) => (
          <li key={i} className="flex gap-3">{a.done ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" /> : <Circle className="mt-0.5 h-4 w-4 shrink-0 text-outline" />}
            <div className="min-w-0"><div className="text-sm font-medium">{a.title}</div><div className="break-words text-xs text-muted">{a.at ? `${fmtDate(a.at)} at ${fmtTime(a.at)}` : ""}{a.detail ? ` · ${a.detail}` : ""}</div></div></li>))}</ol>
      </div>
    </Card>
  );
}

export default function Invoices() {
  const nav = useNavigate();
  const { toast } = useToast();
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<string | null>(null);
  const [banner, setBanner] = useState(true);
  const { data: sum, isLoading: sl } = useInvoiceSummary();
  const { data, isLoading, error, refetch } = useInvoices({ status: status || undefined, search: search || undefined, page, page_size: 8 });
  const del = useDeleteInvoice();
  const cur = sum?.currency ?? "USD";
  const exportCsv = () => data && downloadCsv("invoices.csv", [["Invoice", "Customer", "Due", "Amount", "Currency", "Status"], ...data.items.map((i) => [i.invoice_number, i.customer?.name, i.due_date, i.total_amount, i.currency, i.status])]);
  const selected = sel ?? data?.items[0]?.id ?? null;
  return (
    <div className="flex flex-col gap-6">
      {banner && sum && sum.overdue.count > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-surface-high/60 px-5 py-3">
          <div className="flex items-start gap-3"><Sparkles className="mt-0.5 h-5 w-5 text-primary" /><div className="text-sm"><div className="flex items-center gap-2 font-semibold">PayPilot Copilot Active <span className="rounded bg-primary px-2 py-0.5 font-label text-[10px] font-semibold uppercase text-white">Smart suggestion</span></div>
            <p className="text-muted">{sum.overdue.count} invoice{sum.overdue.count > 1 ? "s are" : " is"} overdue totalling {money(sum.overdue.total, cur)}. Want reminder drafts to review?</p></div></div>
          <div className="flex items-center gap-2"><Button variant="secondary" size="sm" icon={<Sparkles className="h-3.5 w-3.5" />} onClick={() => nav("/assistant", { state: { prompt: "Draft payment reminders for my overdue invoices" } })}>Review AI Draft</Button><button aria-label="Dismiss" onClick={() => setBanner(false)} className="rounded p-1 text-muted hover:bg-white"><X className="h-4 w-4" /></button></div>
        </div>
      )}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div><div className="flex items-center gap-3"><h1 className="text-4xl font-semibold tracking-tight">Invoices</h1><span className="rounded bg-primary-container px-2 py-0.5 font-label text-[11px] font-semibold uppercase text-primary-on">PayPal Orders v2</span></div>
          <p className="mt-2 max-w-xl text-sm text-muted">Create, track and reconcile client billing powered by PayPal and verified-capture reconciliation.</p></div>
        <div className="flex flex-col items-end gap-2 sm:flex-row"><Button variant="secondary" icon={<Sparkles className="h-4 w-4 text-primary" />} onClick={() => nav("/assistant", { state: { prompt: "Create an invoice" } })}>AI Invoice Generator</Button><Button icon={<Plus className="h-4 w-4" />} onClick={() => nav("/invoices/new")}>Create Invoice</Button></div>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard loading={sl} label="Total invoiced" value={money(sum?.total_invoiced, cur)} unit={cur} icon={<Receipt className="h-4 w-4" />} note={`${sum?.counts.all ?? 0} total invoices`} progress={sum?.total_invoiced ? (sum.paid.total / sum.total_invoiced) * 100 : 0} />
        <StatCard loading={sl} label="Paid volume" value={money(sum?.paid.total, cur)} unit={cur} icon={<CheckCircle2 className="h-4 w-4" />} badge={`${sum?.paid.count ?? 0} settled`} progress={sum?.total_invoiced ? (sum.paid.total / sum.total_invoiced) * 100 : 0} />
        <StatCard loading={sl} tone="neutral" label="Pending capture" value={money(sum?.pending.total, cur)} unit={cur} icon={<Clock className="h-4 w-4" />} badge={`${sum?.pending.count ?? 0} awaiting`} progress={sum?.total_invoiced ? (sum.pending.total / sum.total_invoiced) * 100 : 0} />
        <StatCard loading={sl} tone="danger" label="Overdue balance" value={money(sum?.overdue.total, cur)} unit={cur} icon={<AlertTriangle className="h-4 w-4" />} badge={`${sum?.overdue.count ?? 0} invoices`} progress={sum?.total_invoiced ? (sum.overdue.total / sum.total_invoiced) * 100 : 0} />
      </div>
      <Card className="flex flex-wrap items-center gap-3 p-4">
        <div className="relative min-w-[220px] flex-1"><Search className="absolute left-3 top-3 h-4 w-4 text-muted" /><input aria-label="Search invoices" className="input pl-9" placeholder="Search client name or invoice…" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} /></div>
        <div role="tablist" className="flex flex-wrap items-center gap-1">{TABS.map(([v, k]) => (
          <button key={k} role="tab" aria-selected={status === v} onClick={() => { setStatus(v); setPage(1); }} className={clsx("flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium", status === v ? "bg-nav text-white" : "text-ink hover:bg-surface-low")}>
            {TAB_LABEL[k]}<span className={clsx("rounded-full px-1.5 py-0.5 font-label text-[11px]", status === v ? "bg-white/20" : k === "overdue" ? "bg-danger-container text-danger-dark" : "bg-surface")}>{sum?.counts[k] ?? 0}</span></button>))}</div>
        <Button variant="secondary" size="sm" icon={<Download className="h-3.5 w-3.5" />} onClick={exportCsv} disabled={!data?.items.length}>Export CSV</Button>
      </Card>
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Card className="min-w-0 overflow-hidden">
          <div className="flex items-baseline gap-3 p-5"><h2 className="text-lg font-semibold">Invoice Ledger</h2><span className="text-xs text-muted">Showing {data?.items.length ?? 0} of {data?.total ?? 0}</span></div>
          {error ? <ErrorState error={error} onRetry={() => refetch()} /> : isLoading ? <div className="space-y-2 p-6"><Skeleton className="h-12" /><Skeleton className="h-12" /></div> : !data?.items.length ? (
            <EmptyState icon={<FileText className="h-6 w-6" />} title="No invoices" text="Create your first invoice, or ask Copilot to do it for you." action={<Button onClick={() => nav("/invoices/new")}>Create invoice</Button>} />
          ) : (
            <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left">
              <thead className="bg-surface-low"><tr>{["Invoice", "Client", "Due", "Amount", "Status", ""].map((h) => <th key={h} className="th !px-4">{h}</th>)}</tr></thead>
              <tbody>{data.items.map((i: Invoice) => (
                <tr key={i.id} onClick={() => setSel(i.id)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setSel(i.id)} className={clsx("cursor-pointer border-t border-surface-low hover:bg-surface-low/60", selected === i.id && "bg-surface-low")}>
                  <td className="td !px-4 whitespace-nowrap font-label text-xs font-semibold">{i.invoice_number}</td>
                  <td className="td !px-4"><div className="flex items-center gap-2"><Avatar name={i.customer?.name ?? "?"} size={32} /><div className="min-w-0"><div className="truncate text-sm font-medium">{i.customer?.name ?? "—"}</div><div className="truncate text-xs text-muted">{i.customer?.email ?? ""}</div></div></div></td>
                  <td className="td !px-4 text-xs text-muted"><span className={clsx("whitespace-nowrap", i.status === "overdue" && "font-semibold text-danger")}>{fmtDate(i.due_date, { month: "short", day: "numeric" })}</span></td>
                  <td className={clsx("td !px-4 font-semibold", i.status === "overdue" && "text-danger")}>{money(i.total_amount, i.currency)}<div className="text-[11px] font-normal text-muted">{i.currency} · {providerLabel(i.payment_provider ?? "—")}</div></td>
                  <td className="td !px-4"><StatusBadge status={i.status} /></td>
                  <td className="td !px-4 text-right">{i.status === "draft" && <button aria-label={`Delete ${i.invoice_number}`} className="rounded-lg p-1.5 text-muted hover:bg-danger-container hover:text-danger-dark" onClick={async (e) => { e.stopPropagation(); if (!confirm(`Delete draft ${i.invoice_number}?`)) return; try { await del.mutateAsync(i.id); setSel(null); toast("Draft deleted"); } catch (x) { toast((x as Error).message, "error"); } }}><Trash2 className="h-4 w-4" /></button>}</td>
                </tr>))}</tbody>
            </table></div>
          )}
          {data && <Pagination page={page} pageSize={8} total={data.total} onPage={setPage} />}
        </Card>
        {selected ? <LivePreview id={selected} onClose={() => setSel("")} /> : <Card className="p-8"><EmptyState title="Select an invoice" text="Its live preview, checkout link and audit trail appear here." /></Card>}
      </div>
    </div>
  );
}
