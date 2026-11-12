import clsx from "clsx";
import { ArrowDown, ArrowLeftRight, ArrowUp, CheckCircle2, Circle, Download, FileText, Hourglass, RefreshCcw, Search, ShieldAlert, Wallet, Zap, BadgeCheck, Globe } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { StatCard } from "@/components/StatCard";
import { Avatar, Button, Card, CopyButton, EmptyState, ErrorState, Pagination, Segmented, Select, Skeleton, StatusBadge } from "@/components/ui";
import { useToast } from "@/context/ToastContext";
import { usePayPalStatus, useRefund, useTransactions, useTxSummary } from "@/hooks/queries";
import { get } from "@/lib/api";
import type { Transaction } from "@/types";
import { downloadCsv } from "@/utils/csv";
import { fmtDate, fmtTime, money, providerLabel, timeAgo } from "@/utils/format";
import { useQuery } from "@tanstack/react-query";

const EVENT: Record<string, string> = {
  order_created: "Order Created", approved: "Buyer Approved", captured: "Payment Captured & Verified", invoice_paid: "Invoice Marked Paid", pending: "Payment Pending", failed: "Payment Failed", refund_completed: "Refund Completed", refund_failed: "Refund Failed", payment_unmatched: "Payment Needs Review", refund_currency_mismatch: "Refund Flagged (currency)", refund_exceeds_payment: "Refund Flagged (too large)",
  cancelled: "Cancelled by Buyer", refunded: "Refunded", refund_pending: "Refund Pending", amount_mismatch: "Amount Mismatch Flagged", signature_invalid: "Invalid Signature Rejected",
};
const BAD = new Set(["failed", "amount_mismatch", "signature_invalid", "cancelled"]);

function Inspector({ id }: { id: string }) {
  const { toast } = useToast();
  const refund = useRefund();
  const { data: t, error } = useQuery({ queryKey: ["transaction", id], queryFn: () => get<Transaction>(`/api/transactions/${id}`) });
  if (error) return <Card><ErrorState error={error} /></Card>;
  if (!t) return <Card className="p-6"><Skeleton className="h-96" /></Card>;
  const gross = t.amount, fee = t.fee_amount;
  const refunded = t.refunded_amount ?? 0;
  const inFlight = (t.refunds ?? []).filter((r) => r.status === "pending").reduce((sum, r) => sum + r.amount, 0);
  const remaining = Math.round((t.amount - refunded - inFlight) * 100) / 100;
  const canRefund = t.provider === "paypal" && (t.status === "completed" || t.status === "partially_refunded") && !!t.provider_capture_id && remaining > 0;
  return (
    <Card className="flex flex-col gap-5 p-5">
      <div className="flex items-start justify-between gap-3">
        <div><div className="label-caps flex items-center gap-1">Inspection panel <span className="text-primary">● Active session</span></div><h2 className="mt-1 font-display text-2xl font-semibold leading-tight">Transaction Details</h2></div>
        <div className="flex flex-col items-end gap-1"><StatusBadge status={t.status} /><span className="rounded bg-surface px-2 py-0.5 font-label text-[11px] text-muted">TX-{t.id.slice(0, 5).toUpperCase()}</span></div>
      </div>
      {t.failure_reason && <p role="alert" className="rounded-xl bg-danger-container px-3 py-2 text-sm text-danger-dark">{t.failure_reason}</p>}
      <div className="rounded-xl bg-surface-low p-4 text-sm">
        <div className="mb-2 flex justify-between label-caps"><span>Settlement breakdown</span><span className="text-primary">{t.currency} primary</span></div>
        <div className="flex justify-between py-1"><span className="text-muted">Gross authorized</span><span className="font-semibold">{money(gross, t.currency)} {t.currency}</span></div>
        {refunded > 0 && <div className="flex justify-between py-1"><span className="text-muted">Refunded</span><span className="font-semibold text-danger">-{money(refunded, t.currency)} {t.currency}</span></div>}
        <div className="flex justify-between border-b border-surface-high py-1"><span className="text-muted">{providerLabel(t.provider)} fee</span><span className="font-semibold text-danger">{fee != null ? `-${money(fee, t.currency)} ${t.currency}` : "Not reported"}</span></div>
        <div className="flex items-end justify-between pt-2"><div><div className="font-semibold">Net settled</div><div className="text-xs text-muted">Reported by the provider at capture</div></div>
          <span className="font-display text-2xl font-semibold text-primary">{t.net_amount != null ? `${money(t.net_amount, t.currency)} ${t.currency}` : "—"}</span></div>
      </div>
      <div><div className="label-caps mb-2">{providerLabel(t.provider)} engine identifiers</div>
        <div className="grid gap-2 sm:grid-cols-2">{[["Capture ID", t.provider_capture_id], ["Order ID", t.provider_order_id]].map(([l, v]) => (
          <div key={l} className="flex items-start justify-between gap-2 rounded-xl bg-surface-low p-3"><div className="min-w-0"><div className="text-[11px] text-muted">{l}</div><div className="break-all font-label text-xs font-semibold">{v ?? "—"}</div></div>{v && <CopyButton text={v} label="" />}</div>))}</div>
        {t.invoice_id && <div className="mt-2 flex items-center justify-between rounded-xl bg-surface-low px-3 py-2 text-sm"><span className="flex items-center gap-2 text-muted"><FileText className="h-4 w-4" />Linked invoice: <Link to={`/invoices/${t.invoice_id}`} className="font-semibold text-primary">#{t.invoice_number}</Link></span><span className="rounded bg-white px-2 py-0.5 text-[11px]">Direct link</span></div>}
      </div>
      {!!t.refunds?.length && <div><div className="label-caps mb-2">Refunds</div>
        <ul className="space-y-2">{t.refunds.map((r) => (
          <li key={r.id} className="flex items-center justify-between gap-2 rounded-xl bg-surface-low px-3 py-2 text-sm"><span><span className="font-semibold">{money(r.amount, r.currency)}</span><span className="ml-2 text-xs text-muted">{fmtDate(r.created_at)}{r.reason ? ` · ${r.reason}` : ""}</span></span><StatusBadge status={r.status} /></li>))}</ul></div>}
      <div><div className="label-caps mb-2">Buyer context</div>
        <div className="flex items-center gap-3 rounded-xl bg-surface-low p-3"><Avatar name={t.customer?.name ?? "?"} size={44} /><div className="min-w-0 flex-1"><div className="font-semibold">{t.customer?.name ?? "Unassigned"}</div><div className="truncate text-xs text-muted">{t.customer?.email ?? "No email on file"}</div></div>
          <span className="rounded-lg bg-white px-2 py-1 text-xs capitalize shadow-card">{t.payment_method ?? "n/a"}</span></div></div>
      <div><div className="mb-3 flex justify-between label-caps"><span>Lifecycle audit trail</span><span className="text-primary">{t.events.length} events</span></div>
        <ol className="space-y-3 rounded-xl bg-surface-low p-4">{t.events.map((e, i) => (
          <li key={i} className="flex gap-3">{BAD.has(e.type) ? <Circle className="mt-0.5 h-4 w-4 shrink-0 fill-danger text-danger" /> : <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" />}
            <div className="min-w-0 flex-1"><div className="flex justify-between gap-2 text-sm"><span className="font-medium">{EVENT[e.type] ?? e.type}</span><span className="text-xs text-muted">{fmtTime(e.at)}</span></div><div className="break-all text-xs text-muted">{fmtDate(e.at)}{e.detail ? ` · ${e.detail}` : ""}</div></div></li>))}
          {!t.events.length && <li className="text-sm text-muted">No lifecycle events recorded.</li>}</ol></div>
      {canRefund && <Button variant="danger" className="w-full" loading={refund.isPending} icon={<RefreshCcw className="h-4 w-4" />}
        onClick={async () => { if (!confirm(`Refund ${money(remaining, t.currency)} to the buyer via PayPal? This cannot be undone.`)) return; try { await refund.mutateAsync(t.id); toast("Refund issued"); } catch (e) { toast((e as Error).message, "error"); } }}>{refunded > 0 ? "Refund Remaining" : "Issue Full Refund"} ({money(remaining, t.currency)})</Button>}
    </Card>
  );
}

const STATUS_TABS = [{ value: "", label: "All" }, { value: "completed", label: "Completed" }, { value: "pending", label: "Pending" }, { value: "failed", label: "Failed" }, { value: "refunded", label: "Refunded" }];

export default function Transactions() {
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [provider, setProvider] = useState("");
  const [sort, setSort] = useState<"created_at" | "amount" | "status">("created_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const search = sp.get("search") ?? "";
  const open = sp.get("open");
  const { data: sum, isLoading: sl } = useTxSummary();
  const { data: pp } = usePayPalStatus();
  const { data, isLoading, error, refetch, dataUpdatedAt } = useTransactions({ page, page_size: 8, status: status || undefined, provider: provider || undefined, search: search || undefined, sort, order });
  const cur = sum?.currency ?? "USD";
  const toggle = (c: typeof sort) => { if (sort === c) setOrder(order === "asc" ? "desc" : "asc"); else { setSort(c); setOrder("desc"); } setPage(1); };
  const Sort = ({ c, children }: { c: typeof sort; children: string }) => <button onClick={() => toggle(c)} className="inline-flex items-center gap-1 uppercase" aria-label={`Sort by ${children}`}>{children}{sort === c && (order === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}</button>;
  const setParam = (k: string, v: string) => { const n = new URLSearchParams(sp); v ? n.set(k, v) : n.delete(k); setSp(n, { replace: true }); setPage(1); };
  const selected = open ?? data?.items[0]?.id ?? null;
  const exportCsv = () => data && downloadCsv("transactions.csv", [["ID", "Customer", "Provider", "Order ID", "Amount", "Currency", "Status", "Created"], ...data.items.map((t) => [t.id, t.customer?.name, t.provider, t.provider_order_id, t.amount, t.currency, t.status, t.created_at])]);
  return (
    <div className="-mx-4 -mt-4 sm:-mx-6 sm:-mt-6">
      <div className="flex flex-wrap items-center justify-between gap-2 bg-primary-container/70 px-6 py-2.5 text-xs">
        <span className="flex flex-wrap items-center gap-2"><span className={clsx("flex items-center gap-1.5 rounded-full bg-white px-2 py-0.5 font-label font-semibold uppercase", pp?.webhook_configured ? "text-primary" : "text-amber-ink")}><span className={clsx("h-1.5 w-1.5 rounded-full", pp?.webhook_configured ? "bg-primary" : "bg-amber-glow")} />{pp?.webhook_configured ? "Webhook pipe connected" : "Webhook not configured"}</span>
          <span className="text-muted">{pp?.last_webhook_at ? `Last event ${timeAgo(pp.last_webhook_at)}` : "No webhook events received yet"} · endpoint <code className="rounded bg-white px-1">/api/webhooks/paypal</code></span></span>
        <span className="flex items-center gap-3 font-label font-semibold text-primary"><span className="flex items-center gap-1"><Zap className="h-3.5 w-3.5" />Latency: {pp?.latency_ms != null ? `${pp.latency_ms}ms` : "—"}</span><span>Signature verified</span></span>
      </div>
      <div className="flex flex-col gap-6 p-4 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div><div className="flex items-center gap-3"><h1 className="text-3xl font-semibold tracking-tight">Transactions Ledger</h1><span className="rounded bg-surface px-2 py-0.5 font-label text-[11px] font-semibold">Live ingest</span></div>
            <p className="mt-1 max-w-xl text-sm text-muted">Real-time PayPal and Razorpay settlement activity, reconciled from verified captures and webhooks.</p></div>
          <div className="flex gap-2"><Button variant="secondary" icon={<Download className="h-4 w-4" />} onClick={exportCsv} disabled={!data?.items.length}>Download Statement</Button><Button icon={<RefreshCcw className="h-4 w-4" />} onClick={() => refetch()}>Refresh</Button></div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard loading={sl} label="Total volume" value={money(sum?.total.volume, cur)} icon={<Wallet className="h-4 w-4" />} note={`${sum?.total.count ?? 0} transactions`} progress={100} />
          <StatCard loading={sl} label="Successful settlements" value={money(sum?.settled.total, cur)} icon={<BadgeCheck className="h-4 w-4" />} badge={sum?.settled.rate != null ? `${sum.settled.rate}% rate` : "no data"} note={`${sum?.settled.count ?? 0} settled`} progress={sum?.settled.rate ?? 0} />
          <StatCard loading={sl} tone="neutral" label="Pending authorizations" value={money(sum?.pending.total, cur)} icon={<Hourglass className="h-4 w-4" />} badge={`${sum?.pending.count ?? 0} pending capture`} />
          <StatCard loading={sl} tone="danger" label="Failed / refunded" value={money((sum?.failed.total ?? 0) + (sum?.refunded.total ?? 0), cur)} icon={<ShieldAlert className="h-4 w-4" />} badge={`${sum?.failed.count ?? 0} failed`} note={`${sum?.refunded.count ?? 0} refunded`} />
        </div>
        <Card className="flex flex-wrap items-center justify-between gap-3 p-4">
          <div className="relative min-w-[240px] flex-1 lg:max-w-md"><Search className="absolute left-3 top-3 h-4 w-4 text-muted" /><input aria-label="Search transactions" value={search} onChange={(e) => setParam("search", e.target.value)} placeholder="Filter by order ID, customer or invoice…" className="input pl-9" /></div>
          <div className="flex flex-wrap items-center gap-3"><Segmented value={status} onChange={(v) => { setStatus(v); setPage(1); }} options={STATUS_TABS} />
            <Select aria-label="Filter by method" className="w-36" value={provider} onChange={(e) => { setProvider(e.target.value); setPage(1); }}><option value="">Method: All</option><option value="paypal">PayPal</option><option value="razorpay">Razorpay</option></Select></div>
        </Card>
        <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
          <Card className="min-w-0 overflow-hidden">
            <div className="flex items-center justify-between p-5"><h2 className="text-lg font-semibold">Real-time Stream</h2><span className="flex items-center gap-1 text-xs text-muted"><RefreshCcw className="h-3 w-3" />Auto-refresh · updated {dataUpdatedAt ? timeAgo(dataUpdatedAt) : "…"}</span></div>
            {error ? <ErrorState error={error} onRetry={() => refetch()} /> : isLoading ? <div className="space-y-2 p-6"><Skeleton className="h-12" /><Skeleton className="h-12" /><Skeleton className="h-12" /></div> : !data?.items.length ? (
              <EmptyState icon={<ArrowLeftRight className="h-6 w-6" />} title="No transactions found" text={search || status || provider ? "Try clearing your filters." : "Payments appear here once a customer pays an invoice or payment link."} action={<Button onClick={() => nav("/invoices/new")}>Create invoice</Button>} />
            ) : (
              <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left">
                <thead className="bg-surface-low"><tr><th className="th !px-4">TX ID</th><th className="th !px-4">Customer</th><th className="th !px-4"><Sort c="created_at">Timestamp</Sort></th><th className="th !px-4 hidden 2xl:table-cell">Method</th><th className="th !px-4"><Sort c="amount">Amount</Sort></th><th className="th !px-4"><Sort c="status">Status</Sort></th></tr></thead>
                <tbody>{data.items.map((t) => (
                  <tr key={t.id} tabIndex={0} onClick={() => setParam("open", t.id)} onKeyDown={(e) => e.key === "Enter" && setParam("open", t.id)} className={clsx("cursor-pointer border-t border-surface-low hover:bg-surface-low/60", selected === t.id && "bg-surface-low shadow-[inset_3px_0_0_#005ab6]")}>
                    <td className="td !px-4 font-label text-xs font-semibold">#TX-{t.id.slice(0, 5).toUpperCase()}</td>
                    <td className="td !px-4"><div className="flex items-center gap-2"><Avatar name={t.customer?.name ?? "?"} size={32} /><div className="min-w-0"><div className="truncate text-sm font-medium">{t.customer?.name ?? "Unassigned"}</div><div className="truncate text-xs text-muted">{t.customer?.email ?? t.invoice_number ?? ""}</div></div></div></td>
                    <td className="td !px-4 whitespace-nowrap text-xs text-muted">{fmtDate(t.created_at, { month: "short", day: "numeric" })},<br />{fmtTime(t.created_at)}</td>
                    <td className="td !px-4 hidden 2xl:table-cell"><span className="inline-flex items-center gap-1 rounded-full bg-surface px-2 py-1 text-[11px]"><Globe className="h-3 w-3" />{providerLabel(t.provider)}{t.payment_method && t.payment_method !== t.provider ? ` · ${t.payment_method}` : ""}</span></td>
                    <td className={clsx("td !px-4 whitespace-nowrap font-semibold", t.status === "failed" && "text-danger")}>+{money(t.amount, t.currency)}</td>
                    <td className="td !px-4"><StatusBadge status={t.status} /></td>
                  </tr>))}</tbody>
              </table></div>
            )}
            {data && <Pagination page={page} pageSize={8} total={data.total} onPage={setPage} />}
          </Card>
          {selected ? <Inspector id={selected} /> : <Card><EmptyState title="Select a transaction" text="Settlement breakdown and lifecycle appear here." /></Card>}
        </div>
      </div>
    </div>
  );
}
