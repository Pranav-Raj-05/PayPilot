import clsx from "clsx";
import { AlertTriangle, CheckCircle2, ExternalLink, FileText, Link2, Pencil, ShieldCheck, X } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { Button, CopyButton, StatusBadge } from "@/components/ui";
import type { ChatMessage } from "@/types";
import { fmtDate, money } from "@/utils/format";

const LABEL: Record<string, string> = { prepared: "Prepared operation · awaiting approval", approved: "User approved · executing", executed: "Executed", failed: "Failed", cancelled: "Cancelled" };

export function StageBadge({ status }: { status: string }) {
  return <StatusBadge status={status} label={LABEL[status] ?? status} />;
}

interface Props { msg: ChatMessage; busy: boolean; onConfirm: () => void; onCancel: () => void; onEdit: (text: string) => void }

/** Rich staged-action card for create_invoice (matches the Figma "Staged Action" card). */
function InvoicePreview({ msg, busy, onConfirm, onCancel, onEdit }: Props) {
  const r = msg.tool_result!, p = r.preview;
  const status = r.status;
  const provider = p.provider === "paypal" ? "PayPal" : "Razorpay";
  return (
    <div className="overflow-hidden rounded-2xl bg-white shadow-card ring-1 ring-surface-high">
      <div className="flex flex-wrap items-center gap-2 bg-primary px-4 py-3 text-white">
        <FileText className="h-4 w-4" /><span className="text-sm font-semibold">Staged Action: {p.title}</span>
        <span className="rounded bg-white/20 px-2 py-0.5 font-label text-[10px] font-semibold uppercase">{p.provider === "paypal" ? "PayPal Orders v2" : "Razorpay Orders"}</span>
        <span className="rounded bg-amber-400 px-2 py-0.5 font-label text-[10px] font-semibold uppercase text-amber-text">{p.mode}</span>
        <span className="ml-auto"><StageBadge status={status} /></span>
      </div>
      <div className="space-y-4 p-4">
        <div className="grid gap-4 rounded-xl bg-surface-low p-4 sm:grid-cols-3">
          <div><div className="label-caps">Recipient customer</div><div className="mt-1 font-semibold">{p.customer.name}</div><div className="text-xs text-primary">{p.customer.email ?? (p.customer.existing ? "no email on file" : "new customer")}</div></div>
          <div><div className="label-caps">Payment terms</div><div className="mt-1 font-semibold">{p.terms}</div><div className="text-xs text-muted">Due: {fmtDate(p.due_date)}</div></div>
          <div><div className="label-caps">Routing endpoint</div><div className="mt-1 break-all text-sm font-semibold">{p.provider === "paypal" ? `api.${p.mode === "sandbox" ? "sandbox." : ""}paypal.com` : "api.razorpay.com"}</div><div className="text-xs text-muted">Currency: {p.currency}</div></div>
        </div>
        <div className="overflow-x-auto"><table className="w-full min-w-[420px] text-sm">
          <thead className="bg-surface-low"><tr><th className="th !py-2">Description</th><th className="th !py-2">Qty</th><th className="th !py-2 text-right">Unit price</th><th className="th !py-2 text-right">Amount</th></tr></thead>
          <tbody>{p.items.map((it: any, i: number) => <tr key={i}><td className="px-6 py-3 font-medium">{it.description}</td><td className="px-6 py-3">{it.quantity}</td><td className="px-6 py-3 text-right">{money(it.unit_price, p.currency)}</td><td className="px-6 py-3 text-right">{money(it.amount, p.currency)}</td></tr>)}</tbody>
        </table></div>
        <div className="ml-auto w-full max-w-xs space-y-1 text-sm">
          <div className="flex justify-between text-muted"><span>Subtotal:</span><span>{money(p.subtotal, p.currency)} {p.currency}</span></div>
          <div className="flex justify-between text-muted"><span>Estimated Tax ({p.tax_rate}%):</span><span>{money(p.tax, p.currency)} {p.currency}</span></div>
          <div className="flex items-baseline justify-between pt-1"><span className="font-semibold">Total Amount:</span><span className="font-display text-xl font-semibold text-primary">{money(p.total, p.currency)} {p.currency}</span></div>
        </div>
        <ul className="space-y-1 rounded-xl bg-surface-low p-3 text-xs text-muted">
          {p.checks.map((c: any, i: number) => <li key={i} className="flex items-center gap-2">{c.ok ? <CheckCircle2 className="h-4 w-4 text-primary" /> : <AlertTriangle className="h-4 w-4 text-amber-dot" />}{c.text}</li>)}
          <li className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-primary" />Nothing is created until you approve. Payment is verified server-side before an invoice is marked paid.</li>
        </ul>
        {status === "prepared" && (
          <div className="flex flex-wrap items-center gap-2">
            <Button loading={busy} onClick={onConfirm} icon={<CheckCircle2 className="h-4 w-4" />}>Confirm &amp; Create on {provider}</Button>
            <Button variant="secondary" disabled={busy} icon={<Pencil className="h-4 w-4" />} onClick={() => onEdit(`Change the prepared invoice for ${p.customer.name}: `)}>Edit Details</Button>
            <Button variant="ghost" disabled={busy} icon={<X className="h-4 w-4" />} onClick={onCancel}>Cancel Action</Button>
          </div>
        )}
        {status === "failed" && <p role="alert" className="rounded-xl bg-danger-container p-3 text-sm text-danger-dark">{r.error}</p>}
      </div>
    </div>
  );
}

function GenericPreview({ msg, busy, onConfirm, onCancel }: Props) {
  const r = msg.tool_result!, p = r.preview ?? {};
  const { title, ...rest } = p;
  return (
    <div className="overflow-hidden rounded-2xl bg-white shadow-card ring-1 ring-surface-high">
      <div className="flex items-center gap-2 bg-primary px-4 py-3 text-white"><Link2 className="h-4 w-4" /><span className="text-sm font-semibold">Staged Action: {title ?? msg.tool_name}</span><span className="ml-auto"><StageBadge status={r.status} /></span></div>
      <div className="space-y-3 p-4">
        <dl className="grid gap-3 rounded-xl bg-surface-low p-4 sm:grid-cols-2">
          {Object.entries(rest).filter(([, v]) => v != null && typeof v !== "object").map(([k, v]) => <div key={k}><dt className="label-caps">{k.replace(/_/g, " ")}</dt><dd className="mt-0.5 break-words text-sm font-medium">{typeof v === "number" && /amount/.test(k) ? money(v, (p.currency as string) || "USD") : String(v)}</dd></div>)}
        </dl>
        {r.status === "prepared" && <div className="flex flex-wrap gap-2"><Button loading={busy} onClick={onConfirm} icon={<CheckCircle2 className="h-4 w-4" />}>Approve &amp; execute</Button><Button variant="ghost" disabled={busy} onClick={onCancel} icon={<X className="h-4 w-4" />}>Cancel Action</Button></div>}
        {r.status === "failed" && <p role="alert" className="rounded-xl bg-danger-container p-3 text-sm text-danger-dark">{r.error}</p>}
      </div>
    </div>
  );
}

/** Result of an executed operation: invoice + shareable payment link + QR. */
function ResultCard({ msg }: { msg: ChatMessage }) {
  const res = msg.tool_result!.result ?? {};
  const inv = res.invoice, link = res.payment_link;
  return (
    <div className="rounded-2xl bg-white p-4 shadow-card ring-1 ring-surface-high">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-3">
          <span className="rounded-lg bg-surface p-2 text-primary"><FileText className="h-5 w-5" /></span>
          <div><div className="font-semibold">{inv ? `Invoice #${inv.invoice_number}` : "Operation complete"}</div>
            {link?.reference && <div className="font-label text-[11px] text-muted">{link.provider === "paypal" ? "PayPal" : "Razorpay"} order: {link.reference}</div>}</div>
        </div>
        <StatusBadge status={link ? "executed" : "pending"} label={link ? "Created & link ready" : "Created · no link"} />
      </div>
      {res.warning && <p role="alert" className="mt-3 flex gap-2 rounded-xl bg-amber-50 p-3 text-sm text-amber-ink"><AlertTriangle className="h-4 w-4 shrink-0" />{res.warning}</p>}
      {link && (
        <div className="mt-3 grid gap-4 rounded-xl bg-surface-low p-4 sm:grid-cols-[auto_1fr]">
          <div className="rounded-lg bg-white p-2"><QRCodeSVG value={link.url} size={96} aria-label="Payment link QR code" /></div>
          <div className="min-w-0">
            <div className="label-caps">Shareable direct payment link</div>
            <div className="mt-1 flex items-start gap-2"><Link2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" /><a href={link.url} target="_blank" rel="noreferrer" className="break-all text-sm text-primary hover:underline">{link.url}</a></div>
            <div className="mt-2 flex flex-wrap gap-2"><CopyButton text={link.url} /><a href={link.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-lg bg-white px-2 py-1 text-xs font-medium shadow-card hover:bg-surface-low"><ExternalLink className="h-3.5 w-3.5" />Customer View</a></div>
          </div>
        </div>
      )}
      <div className="mt-3 flex flex-wrap justify-between gap-2 font-label text-[11px] text-muted">
        <span>Timestamp: {new Date((msg.tool_result as any).executed_at ?? msg.created_at).toISOString().replace("T", " ").slice(0, 19)} UTC</span>
        {res.idempotency_key && <span>Idempotency key: {String(res.idempotency_key).slice(0, 13)}…</span>}
      </div>
    </div>
  );
}

export function ActionCard(props: Props) {
  const { msg } = props;
  const status = msg.tool_result!.status;
  if (status === "executed") return <div className="space-y-2"><div className="flex items-center gap-2"><StageBadge status="executed" /><span className="text-xs text-muted">User approved → executed on backend</span></div><ResultCard msg={msg} /></div>;
  if (status === "cancelled") return <div className="flex items-center gap-2 rounded-xl bg-surface-low px-4 py-3 text-sm text-muted"><X className="h-4 w-4" />Cancelled: {msg.tool_result!.preview?.title ?? msg.tool_name}. Nothing was created.</div>;
  return msg.tool_name === "create_invoice" ? <InvoicePreview {...props} /> : <GenericPreview {...props} />;
}
