import { FileText, Plus, Search, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Button, Card, EmptyState, ErrorState, PageHeader, Pagination, Segmented, Skeleton, StatusBadge } from "@/components/ui";
import { useToast } from "@/context/ToastContext";
import { useDeleteInvoice, useInvoices } from "@/hooks/queries";
import { fmtDate, money } from "@/utils/format";

const TABS = [{ value: "", label: "All" }, { value: "draft", label: "Draft" }, { value: "pending", label: "Pending" }, { value: "overdue", label: "Overdue" }, { value: "paid", label: "Paid" }];

export default function Invoices() {
  const nav = useNavigate();
  const { toast } = useToast();
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const { data, isLoading, error, refetch } = useInvoices({ status: status || undefined, search: search || undefined, page, page_size: 10 });
  const del = useDeleteInvoice();
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Invoices" subtitle="Create invoices and collect payment through PayPal." actions={<Button icon={<Plus className="h-4 w-4" />} onClick={() => nav("/invoices/new")}>Create Invoice</Button>} />
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 p-4">
          <Segmented value={status} onChange={(v) => { setStatus(v); setPage(1); }} options={TABS} />
          <div className="relative w-full sm:w-72"><Search className="absolute left-3 top-3 h-4 w-4 text-muted" /><input aria-label="Search invoices" className="input pl-9" placeholder="Search number or customer…" value={search} onChange={(e) => { setSearch(e.target.value); setPage(1); }} /></div>
        </div>
        {error ? <ErrorState error={error} onRetry={() => refetch()} /> : isLoading ? <div className="space-y-2 p-6"><Skeleton className="h-12" /><Skeleton className="h-12" /></div> : !data?.items.length ? (
          <EmptyState icon={<FileText className="h-6 w-6" />} title="No invoices" text="Create your first invoice, or ask Copilot to do it for you." action={<Button onClick={() => nav("/invoices/new")}>Create invoice</Button>} />
        ) : (
          <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left">
            <thead className="bg-surface-low"><tr>{["Invoice", "Customer", "Due", "Amount", "Status", ""].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{data.items.map((i) => (
              <tr key={i.id} className="border-t border-surface-low hover:bg-surface-low/60">
                <td className="td"><Link to={`/invoices/${i.id}`} className="font-medium text-primary hover:underline">{i.invoice_number}</Link><div className="text-xs text-muted">{i.description?.slice(0, 40)}</div></td>
                <td className="td">{i.customer?.name ?? <span className="text-muted">—</span>}</td>
                <td className="td text-muted">{fmtDate(i.due_date)}</td>
                <td className="td font-semibold">{money(i.total_amount, i.currency)}</td>
                <td className="td"><StatusBadge status={i.status} /></td>
                <td className="td text-right">{i.status === "draft" && <button aria-label={`Delete ${i.invoice_number}`} className="rounded-lg p-1.5 text-muted hover:bg-danger-container hover:text-danger-dark"
                  onClick={async () => { if (!confirm(`Delete draft ${i.invoice_number}?`)) return; try { await del.mutateAsync(i.id); toast("Draft deleted"); } catch (e) { toast((e as Error).message, "error"); } }}><Trash2 className="h-4 w-4" /></button>}</td>
              </tr>))}</tbody>
          </table></div>
        )}
        {data && <Pagination page={page} pageSize={10} total={data.total} onPage={setPage} />}
      </Card>
    </div>
  );
}
