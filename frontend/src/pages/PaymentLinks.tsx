import { zodResolver } from "@hookform/resolvers/zod";
import clsx from "clsx";
import { Download, Link2, Mail, MessageCircle, MessageSquare, Plus, QrCode, Search, ShieldCheck, Sparkles, TrendingUp, Radio, CheckCircle2, XCircle, Zap } from "lucide-react";
import { QRCodeCanvas, QRCodeSVG } from "qrcode.react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { z } from "zod";
import { StatCard } from "@/components/StatCard";
import { Button, Card, CopyButton, EmptyState, ErrorState, Field, Input, Modal, Pagination, Select, Skeleton, StatusBadge, Textarea } from "@/components/ui";
import { useToast } from "@/context/ToastContext";
import { useCancelLink, useCreateLink, useCustomers, useLinkSummary, usePayPalStatus, usePaymentLinks } from "@/hooks/queries";
import type { PaymentLink } from "@/types";
import { downloadCsv } from "@/utils/csv";
import { fmtDate, fmtTime, money, providerLabel, relativeDay } from "@/utils/format";

const schema = z.object({
  provider: z.enum(["paypal", "razorpay"]), title: z.string().min(1, "Enter a title").max(120), amount: z.coerce.number().gt(0, "Enter an amount"), currency: z.string(),
  details: z.string().max(300).optional(), customer_id: z.string().optional(), expiry_days: z.coerce.number().int().min(0).max(365),
}).refine((v) => v.provider !== "razorpay" || v.currency === "INR", { path: ["currency"], message: "Razorpay links must be in INR" });
type Form = z.infer<typeof schema>;
const CURRENCIES = ["USD", "EUR", "GBP", "INR", "AUD", "CAD", "SGD", "JPY"];

function Inspector({ link }: { link: PaymentLink }) {
  const text = encodeURIComponent(`${link.description ?? "Payment"} — ${money(link.amount, link.currency)}: ${link.url}`);
  const png = () => { const c = document.getElementById("qr-canvas") as HTMLCanvasElement | null; if (!c) return; const a = document.createElement("a"); a.href = c.toDataURL("image/png"); a.download = "payment-link-qr.png"; a.click(); };
  return (
    <Card className="p-6">
      <div className="flex items-center justify-between"><span className="label-caps">Active inspector</span><StatusBadge status={link.status} label={link.status === "active" ? "Live · accepting payments" : link.status} /></div>
      <h2 className="mt-2 font-display text-2xl font-semibold leading-tight">{link.description}</h2>
      <div className="mt-1 font-display text-3xl font-semibold text-primary">{money(link.amount, link.currency)} <span className="text-sm font-medium text-muted">{link.currency} / payment</span></div>
      <div className="label-caps mt-5">{providerLabel(link.provider)} direct checkout URL</div>
      <div className="mt-1 flex items-center gap-2 rounded-xl bg-surface-low px-3 py-2 text-xs"><Link2 className="h-3.5 w-3.5 shrink-0 text-muted" /><span className="min-w-0 flex-1 truncate">{link.url}</span><CopyButton text={link.url} label="Copy Link" className="!bg-primary !text-white" /></div>
      <div className="mt-4 flex items-center gap-4 rounded-2xl bg-surface-low p-4">
        <div className="rounded-xl bg-white p-3 shadow-card"><QRCodeCanvas id="qr-canvas" value={link.url} size={112} /><div className="mt-1 text-center font-label text-[10px] text-muted">ID: #{link.id.slice(0, 5).toUpperCase()}</div></div>
        <div className="min-w-0"><div className="font-display text-lg font-semibold leading-tight">Scan &amp; test on mobile</div><p className="mt-1 text-xs text-muted">Open your phone camera to run the buyer checkout in the provider's {link.provider === "paypal" ? "sandbox" : "test mode"}.</p>
          <div className="mt-2 flex gap-2"><Button size="sm" variant="secondary" icon={<Download className="h-3 w-3" />} onClick={png}>PNG</Button></div></div>
      </div>
      <div className="label-caps mt-5">Quick dispatch link</div>
      <div className="mt-2 grid grid-cols-3 gap-2">{[["WhatsApp", MessageCircle, `https://wa.me/?text=${text}`], ["Email", Mail, `mailto:?subject=${encodeURIComponent(link.description ?? "Payment link")}&body=${text}`], ["SMS", MessageSquare, `sms:?&body=${text}`]].map(([l, Icon, href]) => {
        const I = Icon as typeof Mail; return <a key={l as string} href={href as string} target="_blank" rel="noreferrer" className="flex flex-col items-center gap-1 rounded-xl bg-surface-low py-3 text-xs font-medium hover:bg-surface"><I className="h-4 w-4 text-primary" />{l as string}</a>; })}</div>
      <p className="mt-2 text-[11px] text-muted">Opens your own app with the message pre-filled; PayPilot does not send messages for you.</p>
      <div className="mt-4 flex items-center justify-between border-t border-surface-low pt-3 text-xs text-muted"><span>{link.status === "paid" ? "Paid" : `Created ${relativeDay(link.created_at)}`}</span><span>{link.expires_at ? `Expires ${fmtDate(link.expires_at)}` : "No expiry"}</span></div>
    </Card>
  );
}

export default function PaymentLinks() {
  const { toast } = useToast();
  const nav = useNavigate();
  const { data, isLoading, error, refetch } = usePaymentLinks();
  const { data: sum, isLoading: sl } = useLinkSummary();
  const { data: customers } = useCustomers();
  const { data: pp } = usePayPalStatus();
  const create = useCreateLink(), cancel = useCancelLink();
  const [sel, setSel] = useState<string | null>(null);
  const [qr, setQr] = useState<PaymentLink | null>(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const f = useForm<Form>({ resolver: zodResolver(schema), defaultValues: { provider: "paypal", currency: "USD", title: "", details: "", expiry_days: 7, amount: undefined as unknown as number } });
  const provider = f.watch("provider");
  const filtered = (data ?? []).filter((l) => (!status || l.status === status) && (!q || `${l.description} ${l.customer?.name ?? ""}`.toLowerCase().includes(q.toLowerCase())));
  const pageItems = filtered.slice((page - 1) * 5, page * 5);
  const selected = (data ?? []).find((l) => l.id === (sel ?? data?.[0]?.id)) ?? null;
  const cur = sum?.currency ?? "USD";
  const submit = f.handleSubmit(async (v) => {
    const description = v.details ? `${v.title} — ${v.details}` : v.title;
    try {
      const l = await create.mutateAsync({ provider: v.provider, amount: v.amount, currency: v.currency, description: description.slice(0, 500), customer_id: v.customer_id || null, expires_at: v.expiry_days ? new Date(Date.now() + v.expiry_days * 864e5).toISOString() : null });
      toast("Payment link created"); setSel(l.id); f.reset();
    } catch (e) { toast((e as Error).message, "error"); }
  });
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div><div className="label-caps flex items-center gap-1 !text-primary"><Link2 className="h-3 w-3" />Self-serve checkout infrastructure</div><h1 className="mt-1 text-4xl font-semibold tracking-tight">Payment Links Hub</h1>
          <p className="mt-2 max-w-xl text-sm text-muted">Instant checkout links and QR codes powered by PayPal. Share over WhatsApp, email or SMS, or embed in client invoices.</p></div>
        <div className="flex flex-col gap-2 sm:items-end"><Button variant="secondary" icon={<Sparkles className="h-4 w-4 text-primary" />} onClick={() => nav("/assistant", { state: { prompt: "Generate a payment link for $" } })}>Generate with AI</Button>
          <Button icon={<Plus className="h-4 w-4" />} onClick={() => document.getElementById("link-title")?.focus()}>Create Payment Link</Button></div>
      </div>
      <Card className="flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="flex items-center gap-3"><span className={clsx("rounded-xl p-2", pp?.connected ? "bg-surface text-primary" : "bg-amber-100 text-amber-ink")}><ShieldCheck className="h-5 w-5" /></span>
          <div className="text-sm"><div className="font-semibold">{pp?.configured ? `PayPal ${pp.mode} gateway ${pp.connected ? "live" : "unreachable"}` : "PayPal not configured"}</div><div className="text-xs text-muted">Links created here call the PayPal Orders API with your server-side credentials.</div></div></div>
        <span className="font-label text-[11px] font-semibold">Credentials: {pp?.client_id_hint ?? "—"}</span>
      </Card>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard loading={sl} label="Paid link volume" value={money(sum?.paid_volume, cur)} icon={<TrendingUp className="h-4 w-4" />} note={`${sum?.paid ?? 0} paid`} progress={sum?.conversion_rate ?? 0} />
        <StatCard loading={sl} label="Active links" value={`${sum?.active ?? 0} Live`} icon={<Radio className="h-4 w-4" />} note="Accepting payments" progress={sum?.total ? ((sum.active) / sum.total) * 100 : 0} />
        <StatCard loading={sl} label="Completed checkouts" value={String(sum?.paid ?? 0)} icon={<CheckCircle2 className="h-4 w-4" />} badge="Verified" note="captured server-side" progress={sum?.total ? (sum.paid / sum.total) * 100 : 0} />
        <StatCard loading={sl} label="Conversion rate" value={sum?.conversion_rate == null ? "—" : `${sum.conversion_rate}%`} icon={<Zap className="h-4 w-4" />} note="paid ÷ created links" progress={sum?.conversion_rate ?? 0} />
      </div>
      <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <Card className="p-6">
          <div className="flex items-center justify-between"><h2 className="flex items-center gap-2 text-lg font-semibold"><span className="h-2 w-2 rounded-full bg-primary" />Create a Payment Link</h2></div>
          <form onSubmit={submit} className="mt-5 space-y-4">
            <Field label="Product or service title" error={f.formState.errors.title?.message}><Input id="link-title" placeholder="1-hour technical advisory consultation" {...f.register("title")} /></Field>
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Provider"><Select {...f.register("provider", { onChange: (e) => { if (e.target.value === "razorpay") f.setValue("currency", "INR"); } })}><option value="paypal">PayPal (primary)</option><option value="razorpay">Razorpay (INR)</option></Select></Field>
              <Field label="Amount" error={f.formState.errors.amount?.message}><Input type="number" step="0.01" inputMode="decimal" placeholder="320.00" {...f.register("amount")} /></Field>
              <Field label="Currency" error={f.formState.errors.currency?.message}><Select {...f.register("currency")} disabled={provider === "razorpay"}>{CURRENCIES.map((c) => <option key={c}>{c}</option>)}</Select></Field>
            </div>
            <Field label="Description & deliverables"><Textarea placeholder="What does the customer get?" {...f.register("details")} /></Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Link expiry"><Select {...f.register("expiry_days")}><option value={1}>Expires in 1 day</option><option value={3}>Expires in 3 days</option><option value={7}>Expires in 7 days</option><option value={30}>Expires in 30 days</option><option value={0}>Never expires</option></Select></Field>
              <Field label="Customer (optional)"><Select {...f.register("customer_id")}><option value="">—</option>{customers?.items.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></Field>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 pt-2"><span className="flex items-center gap-2 text-xs text-muted"><ShieldCheck className="h-4 w-4 text-primary" />Payments are verified server-side before anything is marked paid.</span>
              <Button type="submit" loading={create.isPending} icon={<Zap className="h-4 w-4" />}>Generate {provider === "paypal" ? "PayPal" : "Razorpay"} Link</Button></div>
          </form>
        </Card>
        {selected ? <Inspector link={selected} /> : <Card><EmptyState icon={<Link2 className="h-6 w-6" />} title="No link selected" text="Create a link to inspect its URL and QR code." /></Card>}
      </div>
      <Card className="overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 p-5">
          <div><h2 className="text-lg font-semibold">Generated Payment Links Catalog</h2><p className="text-sm text-muted">Inspect lifecycle and status of every link you've created.</p></div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative"><Search className="absolute left-3 top-3 h-4 w-4 text-muted" /><input aria-label="Search links" className="input w-52 pl-9" placeholder="Search service or client…" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} /></div>
            <Select aria-label="Filter by status" className="w-36" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}><option value="">All statuses</option><option value="active">Active</option><option value="paid">Paid</option><option value="expired">Expired</option><option value="cancelled">Cancelled</option></Select>
            <Button size="sm" variant="secondary" icon={<Download className="h-3.5 w-3.5" />} disabled={!filtered.length} onClick={() => downloadCsv("payment-links.csv", [["Description", "Provider", "Amount", "Currency", "Status", "URL"], ...filtered.map((l) => [l.description, l.provider, l.amount, l.currency, l.status, l.url])])}>Export</Button>
          </div>
        </div>
        {error ? <ErrorState error={error} onRetry={() => refetch()} /> : isLoading ? <div className="space-y-2 p-6"><Skeleton className="h-12" /><Skeleton className="h-12" /></div> : !pageItems.length ? <EmptyState icon={<Link2 className="h-6 w-6" />} title="No payment links" text={data?.length ? "No links match your filters." : "Generate your first link above."} /> : (
          <div className="overflow-x-auto"><table className="w-full min-w-[860px] text-left">
            <thead className="bg-surface-low"><tr>{["Service / title", "Amount", "Created", "Provider", "Expiry", "QR", "Status", "Actions"].map((h) => <th key={h} className="th !px-4">{h}</th>)}</tr></thead>
            <tbody>{pageItems.map((l) => (
              <tr key={l.id} onClick={() => setSel(l.id)} className={clsx("cursor-pointer border-t border-surface-low hover:bg-surface-low/60", selected?.id === l.id && "bg-surface-low")}>
                <td className="td !px-4"><div className="max-w-[260px] truncate font-medium">{l.description}</div><div className="font-label text-[11px] text-muted">{l.customer?.name ?? "No customer"} · ID: {l.id.slice(0, 8)}</div></td>
                <td className="td !px-4 font-semibold">{money(l.amount, l.currency)}<div className="text-[11px] font-normal text-muted">{l.currency}</div></td>
                <td className="td !px-4 whitespace-nowrap text-xs text-muted">{relativeDay(l.created_at)}<br />{fmtTime(l.created_at)}</td>
                <td className="td !px-4 text-xs">{providerLabel(l.provider)}</td>
                <td className="td !px-4 text-xs text-muted">{l.expires_at ? fmtDate(l.expires_at, { month: "short", day: "numeric" }) : "Never"}</td>
                <td className="td !px-4"><button aria-label="Show QR code" className="rounded p-1 text-primary hover:bg-surface" onClick={(e) => { e.stopPropagation(); setQr(l); }}><QrCode className="h-5 w-5" /></button></td>
                <td className="td !px-4"><StatusBadge status={l.status} /></td>
                <td className="td !px-4"><div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}><CopyButton text={l.url} label="" />
                  {l.status === "active" && <button aria-label="Cancel link" className="rounded-lg p-1.5 text-muted hover:bg-danger-container hover:text-danger-dark" onClick={async () => { if (confirm("Cancel this link? Customers will no longer be able to pay it.")) { try { await cancel.mutateAsync(l.id); toast("Link cancelled"); } catch (e) { toast((e as Error).message, "error"); } } }}><XCircle className="h-4 w-4" /></button>}</div></td>
              </tr>))}</tbody>
          </table></div>
        )}
        <Pagination page={page} pageSize={5} total={filtered.length} onPage={setPage} />
      </Card>
      <Modal open={!!qr} onClose={() => setQr(null)} title="Scan to pay">
        {qr && <div className="flex flex-col items-center gap-3"><div className="rounded-xl bg-white p-4 shadow-card"><QRCodeSVG value={qr.url} size={220} /></div><div className="font-semibold">{money(qr.amount, qr.currency)}</div><p className="max-w-full break-all text-center text-xs text-muted">{qr.url}</p><CopyButton text={qr.url} /></div>}
      </Modal>
    </div>
  );
}
