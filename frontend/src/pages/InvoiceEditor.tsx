import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowLeft, ExternalLink, Link2, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useFieldArray, useForm, useWatch } from "react-hook-form";
import { useNavigate, useParams } from "react-router-dom";
import { z } from "zod";
import { Button, Card, CopyButton, ErrorState, Field, Input, Modal, PageHeader, Select, Skeleton, StatusBadge, Textarea } from "@/components/ui";
import { QRCodeSVG } from "qrcode.react";
import { useToast } from "@/context/ToastContext";
import { useCreateCustomer, useCreateInvoice, useCustomers, useInvoice, useInvoiceLink, useUpdateInvoice } from "@/hooks/queries";
import type { Invoice } from "@/types";
import { fmtDate, money } from "@/utils/format";

const schema = z.object({
  customer_id: z.string().optional(), description: z.string().max(2000).optional(), currency: z.enum(["USD", "EUR", "GBP", "INR", "AUD", "CAD", "SGD", "JPY"]),
  due_date: z.string().optional(), tax_rate: z.coerce.number().min(0).max(100),
  items: z.array(z.object({ description: z.string().min(1, "Required"), quantity: z.coerce.number().gt(0, "> 0"), unit_price: z.coerce.number().min(0, "≥ 0") })).min(1),
});
type Form = z.infer<typeof schema>;
const CURRENCIES = ["USD", "EUR", "GBP", "INR", "AUD", "CAD", "SGD", "JPY"];

function Preview({ v, customer }: { v: Form; customer?: string }) {
  const items = (v.items ?? []).map((i) => ({ ...i, total: (Number(i.quantity) || 0) * (Number(i.unit_price) || 0) }));
  const sub = items.reduce((s, i) => s + i.total, 0);
  const tax = sub * ((Number(v.tax_rate) || 0) / 100);
  return (
    <Card className="p-6">
      <div className="flex items-start justify-between"><div><div className="label-caps">Invoice preview</div><div className="mt-1 text-lg font-semibold">{customer ?? "Select a customer"}</div></div><div className="text-right text-xs text-muted">Due {fmtDate(v.due_date)}</div></div>
      <table className="mt-4 w-full text-sm"><thead><tr className="text-left text-muted"><th className="py-1 font-medium">Item</th><th className="py-1 text-right font-medium">Qty</th><th className="py-1 text-right font-medium">Amount</th></tr></thead>
        <tbody>{items.map((i, n) => <tr key={n} className="border-t border-surface-low"><td className="py-2">{i.description || "—"}</td><td className="py-2 text-right">{i.quantity}</td><td className="py-2 text-right">{money(i.total, v.currency)}</td></tr>)}</tbody></table>
      <div className="ml-auto mt-4 w-full max-w-[220px] space-y-1 text-sm"><div className="flex justify-between text-muted"><span>Subtotal</span><span>{money(sub, v.currency)}</span></div><div className="flex justify-between text-muted"><span>Tax ({v.tax_rate || 0}%)</span><span>{money(tax, v.currency)}</span></div>
        <div className="flex justify-between border-t border-surface-low pt-2 text-base font-semibold"><span>Total</span><span className="text-primary">{money(sub + tax, v.currency)}</span></div></div>
    </Card>
  );
}

function ReadOnly({ inv }: { inv: Invoice }) {
  const { toast } = useToast();
  const link = useInvoiceLink();
  const update = useUpdateInvoice();
  const make = async (provider: "paypal" | "razorpay") => { try { await link.mutateAsync({ id: inv.id, provider }); toast(`${provider === "paypal" ? "PayPal" : "Razorpay"} payment link created`); } catch (e) { toast((e as Error).message, "error"); } };
  const canPay = ["draft", "pending", "overdue"].includes(inv.status);
  return (
    <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
      <Card className="p-6">
        <div className="flex items-start justify-between"><div><div className="label-caps">Invoice</div><h2 className="text-2xl font-semibold">{inv.invoice_number}</h2><p className="mt-1 text-sm text-muted">{inv.customer?.name ?? "No customer"} · due {fmtDate(inv.due_date)}</p></div><StatusBadge status={inv.status} /></div>
        <table className="mt-5 w-full text-sm"><thead><tr className="text-left text-muted"><th className="py-1 font-medium">Item</th><th className="py-1 text-right font-medium">Qty</th><th className="py-1 text-right font-medium">Unit</th><th className="py-1 text-right font-medium">Amount</th></tr></thead>
          <tbody>{inv.items.map((i) => <tr key={i.id} className="border-t border-surface-low"><td className="py-2">{i.description}</td><td className="py-2 text-right">{i.quantity}</td><td className="py-2 text-right">{money(i.unit_price, inv.currency)}</td><td className="py-2 text-right">{money(i.total, inv.currency)}</td></tr>)}</tbody></table>
        <div className="ml-auto mt-4 w-full max-w-[240px] space-y-1 text-sm"><div className="flex justify-between text-muted"><span>Subtotal</span><span>{money(inv.subtotal, inv.currency)}</span></div><div className="flex justify-between text-muted"><span>Tax</span><span>{money(inv.tax, inv.currency)}</span></div><div className="flex justify-between border-t pt-2 text-base font-semibold"><span>Total</span><span className="text-primary">{money(inv.total_amount, inv.currency)}</span></div></div>
      </Card>
      <Card className="h-fit p-6">
        <h3 className="font-semibold">Payment</h3>
        {inv.payment_link ? (
          <div className="mt-3 space-y-3">
            <div className="flex items-center gap-2 text-xs text-muted"><Link2 className="h-3.5 w-3.5" />{inv.payment_provider === "paypal" ? "PayPal checkout link" : "Razorpay checkout link"}</div>
            <div className="flex justify-center rounded-xl bg-surface-low p-4"><QRCodeSVG value={inv.payment_link} size={140} /></div>
            <a href={inv.payment_link} target="_blank" rel="noreferrer" className="block break-all text-sm text-primary hover:underline">{inv.payment_link}</a>
            <div className="flex gap-2"><CopyButton text={inv.payment_link} /><a href={inv.payment_link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-lg bg-white px-2 py-1 text-xs font-medium shadow-card"><ExternalLink className="h-3.5 w-3.5" />Open checkout</a></div>
          </div>
        ) : <p className="mt-2 text-sm text-muted">No payment link yet.</p>}
        {canPay && <div className="mt-4 flex flex-col gap-2">
          {!inv.payment_link && <Button loading={link.isPending} icon={<Link2 className="h-4 w-4" />} onClick={() => make("paypal")}>Create PayPal link</Button>}
          {inv.currency === "INR" && inv.payment_provider !== "razorpay" && <Button variant="secondary" loading={link.isPending} onClick={() => make("razorpay")}>Create Razorpay link (INR)</Button>}
          {inv.status !== "overdue" && <Button variant="ghost" onClick={async () => { if (confirm("Cancel this invoice?")) { try { await update.mutateAsync({ id: inv.id, status: "cancelled" }); toast("Invoice cancelled"); } catch (e) { toast((e as Error).message, "error"); } } }}>Cancel invoice</Button>}
        </div>}
        {inv.status === "paid" && <p className="mt-3 rounded-xl bg-emerald-50 p-3 text-sm text-success">Paid — confirmed by a verified provider capture.</p>}
      </Card>
    </div>
  );
}

function EditorForm({ inv }: { inv?: Invoice }) {
  const nav = useNavigate();
  const { toast } = useToast();
  const { data: customers } = useCustomers();
  const create = useCreateInvoice(), update = useUpdateInvoice(), addCustomer = useCreateCustomer();
  const [newCust, setNewCust] = useState(false);
  const [cName, setCName] = useState(""), [cEmail, setCEmail] = useState("");
  const form = useForm<Form>({
    resolver: zodResolver(schema),
    defaultValues: inv ? { customer_id: inv.customer_id ?? "", description: inv.description ?? "", currency: inv.currency as Form["currency"], due_date: inv.due_date ?? "", tax_rate: inv.subtotal ? Math.round((inv.tax / inv.subtotal) * 10000) / 100 : 0,
      items: inv.items.map((i) => ({ description: i.description, quantity: i.quantity, unit_price: i.unit_price })) }
      : { customer_id: "", description: "", currency: "USD", due_date: new Date(Date.now() + 14 * 864e5).toISOString().slice(0, 10), tax_rate: 0, items: [{ description: "", quantity: 1, unit_price: 0 }] },
  });
  const { fields, append, remove } = useFieldArray({ control: form.control, name: "items" });
  const values = useWatch({ control: form.control }) as Form;
  const custName = customers?.items.find((c) => c.id === values.customer_id)?.name;
  const submit = form.handleSubmit(async (v) => {
    const body = { ...v, customer_id: v.customer_id || null, due_date: v.due_date || null, description: v.description || null };
    try {
      if (inv) { await update.mutateAsync({ id: inv.id, ...body }); toast("Invoice saved"); nav(`/invoices/${inv.id}`); }
      else { const created = await create.mutateAsync(body); toast(`Invoice ${created.invoice_number} created`); nav(`/invoices/${created.id}`); }
    } catch (e) { toast((e as Error).message, "error"); }
  });
  const e = form.formState.errors;
  return (
    <form onSubmit={submit} className="grid gap-6 lg:grid-cols-[1.2fr_1fr]">
      <Card className="space-y-4 p-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Customer"><div className="flex gap-2"><Select {...form.register("customer_id")}><option value="">— none —</option>{customers?.items.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select><Button type="button" variant="secondary" aria-label="New customer" onClick={() => setNewCust(true)}><Plus className="h-4 w-4" /></Button></div></Field>
          <Field label="Currency"><Select {...form.register("currency")}>{CURRENCIES.map((c) => <option key={c}>{c}</option>)}</Select></Field>
          <Field label="Due date"><Input type="date" {...form.register("due_date")} /></Field>
          <Field label="Tax rate (%)" error={e.tax_rate?.message}><Input type="number" step="0.01" {...form.register("tax_rate")} /></Field>
        </div>
        <Field label="Description"><Textarea {...form.register("description")} placeholder="What is this invoice for?" /></Field>
        <div>
          <div className="label-caps mb-2">Line items</div>
          <div className="space-y-2">{fields.map((f, n) => (
            <div key={f.id} className="grid grid-cols-[1fr_70px_100px_auto] items-start gap-2">
              <div><Input aria-label="Item description" placeholder="Description" {...form.register(`items.${n}.description`)} />{e.items?.[n]?.description && <span className="text-xs text-danger">{e.items[n]!.description!.message}</span>}</div>
              <Input aria-label="Quantity" type="number" step="0.01" {...form.register(`items.${n}.quantity`)} />
              <Input aria-label="Unit price" type="number" step="0.01" {...form.register(`items.${n}.unit_price`)} />
              <button type="button" aria-label="Remove item" disabled={fields.length === 1} onClick={() => remove(n)} className="mt-2 rounded-lg p-1.5 text-muted hover:bg-danger-container disabled:opacity-30"><Trash2 className="h-4 w-4" /></button>
            </div>))}</div>
          <Button type="button" variant="ghost" size="sm" className="mt-2" icon={<Plus className="h-3.5 w-3.5" />} onClick={() => append({ description: "", quantity: 1, unit_price: 0 })}>Add item</Button>
        </div>
        <div className="flex gap-2 pt-2"><Button type="submit" loading={create.isPending || update.isPending}>{inv ? "Save changes" : "Create draft invoice"}</Button><Button type="button" variant="ghost" onClick={() => nav(-1)}>Cancel</Button></div>
      </Card>
      <Preview v={values} customer={custName} />
      <Modal open={newCust} onClose={() => setNewCust(false)} title="New customer">
        <div className="space-y-3"><Field label="Name"><Input value={cName} onChange={(x) => setCName(x.target.value)} autoFocus /></Field><Field label="Email"><Input type="email" value={cEmail} onChange={(x) => setCEmail(x.target.value)} /></Field>
          <Button type="button" disabled={!cName.trim()} loading={addCustomer.isPending} onClick={async () => { try { const c = await addCustomer.mutateAsync({ name: cName.trim(), email: cEmail || null }); form.setValue("customer_id", c.id); setNewCust(false); setCName(""); setCEmail(""); } catch (x) { toast((x as Error).message, "error"); } }}>Add customer</Button></div>
      </Modal>
    </form>
  );
}

export default function InvoiceEditor() {
  const { id } = useParams();
  const nav = useNavigate();
  const { data: inv, isLoading, error, refetch } = useInvoice(id);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={id ? (inv?.invoice_number ?? "Invoice") : "Create Invoice"} subtitle={id ? undefined : "Drafts can be edited until a payment link is created."} actions={<Button variant="ghost" icon={<ArrowLeft className="h-4 w-4" />} onClick={() => nav("/invoices")}>All invoices</Button>} />
      {id ? (error ? <ErrorState error={error} onRetry={() => refetch()} /> : isLoading || !inv ? <Skeleton className="h-96" /> : inv.status === "draft" ? <EditorForm inv={inv} key={inv.updated_at} /> : <ReadOnly inv={inv} />) : <EditorForm />}
      {id && inv?.status === "draft" && <ReadOnlyLinkHint inv={inv} />}
    </div>
  );
}

function ReadOnlyLinkHint({ inv }: { inv: Invoice }) {
  const { toast } = useToast();
  const link = useInvoiceLink();
  return (
    <Card className="flex flex-wrap items-center justify-between gap-3 p-4">
      <p className="text-sm text-muted">Ready to get paid? Creating a PayPal link moves this invoice to <b>pending</b> and locks editing.</p>
      <Button loading={link.isPending} icon={<Link2 className="h-4 w-4" />} onClick={async () => { try { await link.mutateAsync({ id: inv.id, provider: "paypal" }); toast("PayPal link created"); } catch (e) { toast((e as Error).message, "error"); } }}>Create PayPal link</Button>
    </Card>
  );
}
