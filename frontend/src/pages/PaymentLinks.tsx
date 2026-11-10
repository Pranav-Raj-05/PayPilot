import { zodResolver } from "@hookform/resolvers/zod";
import { Link2, Plus, QrCode, XCircle } from "lucide-react";
import { QRCodeSVG } from "qrcode.react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button, Card, CopyButton, EmptyState, ErrorState, Field, Input, Modal, PageHeader, Select, Skeleton, StatusBadge } from "@/components/ui";
import { useToast } from "@/context/ToastContext";
import { useCancelLink, useCreateLink, useCustomers, usePaymentLinks } from "@/hooks/queries";
import type { PaymentLink } from "@/types";
import { fmtDate, money, providerLabel } from "@/utils/format";

const schema = z.object({
  provider: z.enum(["paypal", "razorpay"]), amount: z.coerce.number().gt(0, "Enter an amount"), currency: z.string(), description: z.string().min(1, "Describe what this is for").max(500),
  customer_id: z.string().optional(), expires: z.string().optional(),
}).refine((v) => v.provider !== "razorpay" || v.currency === "INR", { path: ["currency"], message: "Razorpay links must be in INR" });
type Form = z.infer<typeof schema>;

export default function PaymentLinks() {
  const { toast } = useToast();
  const { data, isLoading, error, refetch } = usePaymentLinks();
  const { data: customers } = useCustomers();
  const create = useCreateLink(), cancel = useCancelLink();
  const [open, setOpen] = useState(false);
  const [qr, setQr] = useState<PaymentLink | null>(null);
  const f = useForm<Form>({ resolver: zodResolver(schema), defaultValues: { provider: "paypal", currency: "USD", description: "", amount: undefined as unknown as number } });
  const provider = f.watch("provider");
  const submit = f.handleSubmit(async (v) => {
    try {
      await create.mutateAsync({ provider: v.provider, amount: v.amount, currency: v.currency, description: v.description, customer_id: v.customer_id || null, expires_at: v.expires ? new Date(v.expires + "T23:59:59").toISOString() : null });
      toast("Payment link created"); setOpen(false); f.reset();
    } catch (e) { toast((e as Error).message, "error"); }
  });
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Payment Links" subtitle="Shareable checkout links. PayPal is primary; Razorpay supports INR (UPI/cards)." actions={<Button icon={<Plus className="h-4 w-4" />} onClick={() => setOpen(true)}>Create link</Button>} />
      <Card className="overflow-hidden">
        {error ? <ErrorState error={error} onRetry={() => refetch()} /> : isLoading ? <div className="space-y-2 p-6"><Skeleton className="h-12" /><Skeleton className="h-12" /></div> : !data?.length ? (
          <EmptyState icon={<Link2 className="h-6 w-6" />} title="No payment links yet" text="Create a link and send it to a customer to get paid through PayPal." action={<Button onClick={() => setOpen(true)}>Create link</Button>} />
        ) : (
          <div className="overflow-x-auto"><table className="w-full min-w-[820px] text-left">
            <thead className="bg-surface-low"><tr>{["Description", "Provider", "Amount", "Expires", "Status", ""].map((h) => <th key={h} className="th">{h}</th>)}</tr></thead>
            <tbody>{data.map((l) => (
              <tr key={l.id} className="border-t border-surface-low">
                <td className="td"><div className="font-medium">{l.description}</div><div className="text-xs text-muted">{l.customer?.name ?? "No customer"} · created {fmtDate(l.created_at)}</div></td>
                <td className="td">{providerLabel(l.provider)}</td>
                <td className="td font-semibold">{money(l.amount, l.currency)}</td>
                <td className="td text-muted">{l.expires_at ? fmtDate(l.expires_at) : "Never"}</td>
                <td className="td"><StatusBadge status={l.status} /></td>
                <td className="td"><div className="flex items-center justify-end gap-1">
                  <CopyButton text={l.url} />
                  <button aria-label="Show QR code" className="rounded-lg p-1.5 text-muted hover:bg-surface-low" onClick={() => setQr(l)}><QrCode className="h-4 w-4" /></button>
                  {l.status === "active" && <button aria-label="Cancel link" className="rounded-lg p-1.5 text-muted hover:bg-danger-container hover:text-danger-dark" onClick={async () => { if (confirm("Cancel this link? Customers will no longer be able to pay it.")) { try { await cancel.mutateAsync(l.id); toast("Link cancelled"); } catch (e) { toast((e as Error).message, "error"); } } }}><XCircle className="h-4 w-4" /></button>}
                </div></td>
              </tr>))}</tbody>
          </table></div>
        )}
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Create payment link">
        <form onSubmit={submit} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Provider"><Select {...f.register("provider", { onChange: (e) => { if (e.target.value === "razorpay") f.setValue("currency", "INR"); } })}><option value="paypal">PayPal (primary)</option><option value="razorpay">Razorpay (INR)</option></Select></Field>
            <Field label="Currency" error={f.formState.errors.currency?.message}><Select {...f.register("currency")} disabled={provider === "razorpay"}>{["USD", "EUR", "GBP", "INR", "AUD", "CAD", "SGD", "JPY"].map((c) => <option key={c}>{c}</option>)}</Select></Field>
          </div>
          <Field label="Amount" error={f.formState.errors.amount?.message}><Input type="number" step="0.01" inputMode="decimal" {...f.register("amount")} /></Field>
          <Field label="Description" error={f.formState.errors.description?.message}><Input placeholder="Consulting session" {...f.register("description")} /></Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Customer (optional)"><Select {...f.register("customer_id")}><option value="">—</option>{customers?.items.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></Field>
            <Field label="Expires (optional)" hint="Defaults to 3 days for PayPal"><Input type="date" min={new Date().toISOString().slice(0, 10)} {...f.register("expires")} /></Field>
          </div>
          <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button><Button type="submit" loading={create.isPending}>Create link</Button></div>
        </form>
      </Modal>
      <Modal open={!!qr} onClose={() => setQr(null)} title="Scan to pay">
        {qr && <div className="flex flex-col items-center gap-3"><div className="rounded-xl bg-white p-4 shadow-card"><QRCodeSVG value={qr.url} size={220} /></div><div className="font-semibold">{money(qr.amount, qr.currency)}</div><p className="max-w-full break-all text-center text-xs text-muted">{qr.url}</p><CopyButton text={qr.url} /></div>}
      </Modal>
    </div>
  );
}
