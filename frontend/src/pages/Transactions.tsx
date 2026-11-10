import clsx from "clsx";
import { ArrowDown, ArrowUp, ArrowLeftRight, CheckCircle2, Circle, ExternalLink, Search } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Avatar, Card, CopyButton, Drawer, EmptyState, ErrorState, Pagination, PageHeader, Select, Skeleton, StatusBadge } from "@/components/ui";
import { useTransactions } from "@/hooks/queries";
import { get } from "@/lib/api";
import type { Transaction } from "@/types";
import { fmtDate, fmtTime, money, providerLabel } from "@/utils/format";
import { useQuery } from "@tanstack/react-query";

const EVENT_LABEL: Record<string, string> = {
  order_created: "Order created", approved: "Customer approved at PayPal", captured: "Payment captured & verified", invoice_paid: "Invoice marked paid", pending: "Payment pending",
  failed: "Payment failed", cancelled: "Cancelled by customer", refunded: "Refunded", amount_mismatch: "Amount mismatch — flagged", signature_invalid: "Invalid signature rejected",
};

function Timeline({ t }: { t: Transaction }) {
  return (
    <ol className="relative ml-2 space-y-4 border-l-2 border-surface-high pl-5">
      {t.events.map((e, i) => {
        const bad = ["failed", "amount_mismatch", "signature_invalid", "cancelled"].includes(e.type);
        return (
          <li key={i} className="relative">
            <span className="absolute -left-[29px] top-0.5 rounded-full bg-white">{bad ? <Circle className="h-4 w-4 fill-danger text-danger" /> : <CheckCircle2 className="h-4 w-4 text-primary" />}</span>
            <div className="text-sm font-medium">{EVENT_LABEL[e.type] ?? e.type}</div>
            <div className="text-xs text-muted">{fmtDate(e.at)} · {fmtTime(e.at)}</div>
            {e.detail && <div className="mt-0.5 break-all font-label text-[11px] text-muted">{e.detail}</div>}
          </li>
        );
      })}
      {!t.events.length && <li className="text-sm text-muted">No lifecycle events recorded.</li>}
    </ol>
  );
}

function Detail({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { data: t, error } = useQuery({ queryKey: ["transaction", id], queryFn: () => get<Transaction>(`/api/transactions/${id}`), enabled: !!id });
  return (
    <Drawer open={!!id} onClose={onClose} title="Transaction details">
      {error ? <ErrorState error={error} /> : !t ? <Skeleton className="h-64" /> : (
        <div className="space-y-6">
          <div className="rounded-2xl bg-surface-low p-5">
            <div className="flex items-center justify-between"><StatusBadge status={t.status} /><span className="text-xs text-muted">{providerLabel(t.provider)}</span></div>
            <div className="mt-3 font-display text-3xl font-semibold">{money(t.amount, t.currency)}</div>
            {t.failure_reason && <p className="mt-2 rounded-lg bg-danger-container px-3 py-2 text-sm text-danger-dark">{t.failure_reason}</p>}
          </div>
          <dl className="grid grid-cols-2 gap-4 text-sm">
            <div><dt className="label-caps">Customer</dt><dd className="mt-0.5 flex items-center gap-2 font-medium">{t.customer ? <><Avatar name={t.customer.name} size={24} />{t.customer.name}</> : "Unassigned"}</dd></div>
            <div><dt className="label-caps">Method</dt><dd className="mt-0.5 font-medium capitalize">{t.payment_method ?? "—"}</dd></div>
            <div><dt className="label-caps">Invoice</dt><dd className="mt-0.5 font-medium">{t.invoice_id ? <Link className="inline-flex items-center gap-1 text-primary" to={`/invoices/${t.invoice_id}`}>{t.invoice_number}<ExternalLink className="h-3 w-3" /></Link> : "—"}</dd></div>
            <div><dt className="label-caps">Created</dt><dd className="mt-0.5 font-medium">{fmtDate(t.created_at)} {fmtTime(t.created_at)}</dd></div>
            <div className="col-span-2"><dt className="label-caps">Provider order ID</dt><dd className="mt-0.5 flex items-center gap-2 break-all font-label text-xs">{t.provider_order_id ?? "—"}{t.provider_order_id && <CopyButton text={t.provider_order_id} />}</dd></div>
            <div className="col-span-2"><dt className="label-caps">Capture ID</dt><dd className="mt-0.5 break-all font-label text-xs">{t.provider_capture_id ?? "—"}</dd></div>
          </dl>
          <div><h3 className="mb-3 font-semibold">Payment lifecycle</h3><Timeline t={t} /></div>
        </div>
      )}
    </Drawer>
  );
}

const STATUSES = ["created", "pending", "completed", "failed", "refunded", "cancelled"];
export default function Transactions() {
  const [sp, setSp] = useSearchParams();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("");
  const [provider, setProvider] = useState("");
  const [sort, setSort] = useState<"created_at" | "amount" | "status">("created_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const search = sp.get("search") ?? "";
  const open = sp.get("open");
  const params = { page, page_size: 10, status: status || undefined, provider: provider || undefined, search: search || undefined, sort, order };
  const { data, isLoading, error, refetch } = useTransactions(params);
  const toggle = (c: typeof sort) => { if (sort === c) setOrder(order === "asc" ? "desc" : "asc"); else { setSort(c); setOrder("desc"); } setPage(1); };
  const Sort = ({ c, children }: { c: typeof sort; children: string }) => (
    <button onClick={() => toggle(c)} className="inline-flex items-center gap-1 uppercase" aria-label={`Sort by ${children}`}>{children}{sort === c && (order === "asc" ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />)}</button>
  );
  const setParam = (k: string, v: string) => { const n = new URLSearchParams(sp); v ? n.set(k, v) : n.delete(k); setSp(n, { replace: true }); setPage(1); };
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Transactions" subtitle="Every PayPal and Razorpay payment, verified server-side." />
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 p-4">
          <div className="relative min-w-[220px] flex-1"><Search className="absolute left-3 top-3 h-4 w-4 text-muted" /><input aria-label="Search transactions" value={search} onChange={(e) => setParam("search", e.target.value)} placeholder="Search customer, order ID, invoice…" className="input pl-9" /></div>
          <Select aria-label="Filter by status" className="w-40" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}><option value="">All statuses</option>{STATUSES.map((s) => <option key={s} value={s} className="capitalize">{s}</option>)}</Select>
          <Select aria-label="Filter by provider" className="w-40" value={provider} onChange={(e) => { setProvider(e.target.value); setPage(1); }}><option value="">All providers</option><option value="paypal">PayPal</option><option value="razorpay">Razorpay</option></Select>
        </div>
        {error ? <ErrorState error={error} onRetry={() => refetch()} /> : isLoading ? <div className="space-y-2 p-6"><Skeleton className="h-12" /><Skeleton className="h-12" /><Skeleton className="h-12" /></div> : !data?.items.length ? (
          <EmptyState icon={<ArrowLeftRight className="h-6 w-6" />} title="No transactions found" text={search || status || provider ? "Try clearing your filters." : "Payments appear here once a customer pays an invoice or payment link."} />
        ) : (
          <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left">
            <thead className="bg-surface-low"><tr><th className="th">Customer</th><th className="th">Order ID</th><th className="th"><Sort c="created_at">Date</Sort></th><th className="th"><Sort c="amount">Amount</Sort></th><th className="th">Method</th><th className="th"><Sort c="status">Status</Sort></th></tr></thead>
            <tbody>{data.items.map((t) => (
              <tr key={t.id} tabIndex={0} onClick={() => setParam("open", t.id)} onKeyDown={(e) => e.key === "Enter" && setParam("open", t.id)} className="cursor-pointer border-t border-surface-low hover:bg-surface-low/60">
                <td className="td"><div className="flex items-center gap-3"><Avatar name={t.customer?.name ?? "?"} /><div><div className="font-medium">{t.customer?.name ?? "Unassigned"}</div><div className="text-xs text-muted">{t.invoice_number ?? "Payment link"}</div></div></div></td>
                <td className="td font-label text-xs text-muted">{t.provider_order_id ?? "—"}</td>
                <td className="td text-xs text-muted">{fmtDate(t.created_at)}<br />{fmtTime(t.created_at)}</td>
                <td className={clsx("td font-semibold", t.status === "failed" && "text-danger")}>{money(t.amount, t.currency)}</td>
                <td className="td text-xs">{providerLabel(t.provider)}<br /><span className="capitalize text-muted">{t.payment_method ?? "—"}</span></td>
                <td className="td"><StatusBadge status={t.status} /></td>
              </tr>))}</tbody>
          </table></div>
        )}
        {data && <Pagination page={page} pageSize={10} total={data.total} onPage={setPage} />}
      </Card>
      <Detail id={open} onClose={() => setParam("open", "")} />
    </div>
  );
}
