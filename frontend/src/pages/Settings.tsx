import { zodResolver } from "@hookform/resolvers/zod";
import { CheckCircle2, LogOut, XCircle } from "lucide-react";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button, Card, CopyButton, ErrorState, Field, Input, PageHeader, Select, Skeleton } from "@/components/ui";
import { useAuth } from "@/context/AuthContext";
import { useToast } from "@/context/ToastContext";
import { useSettings, useUpdateProfile } from "@/hooks/queries";
import { api } from "@/lib/api";

const schema = z.object({ full_name: z.string().max(200).optional(), business_name: z.string().max(200).optional(), default_currency: z.string() });

const Row = ({ ok, label, detail }: { ok: boolean; label: string; detail?: string }) => (
  <li className="flex items-center justify-between gap-3 rounded-xl bg-surface-low px-4 py-3 text-sm"><span className="flex items-center gap-2">{ok ? <CheckCircle2 className="h-4 w-4 text-primary" /> : <XCircle className="h-4 w-4 text-muted" />}{label}</span><span className="text-xs text-muted">{detail}</span></li>
);

export default function Settings() {
  const { data, isLoading, error, refetch } = useSettings();
  const { signOut, user } = useAuth();
  const { toast } = useToast();
  const update = useUpdateProfile();
  const f = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema) });
  useEffect(() => { if (data) f.reset({ full_name: data.profile.full_name ?? "", business_name: data.profile.business_name ?? "", default_currency: data.profile.default_currency }); }, [data, f]);
  if (error) return <Card><ErrorState error={error} onRetry={() => refetch()} /></Card>;
  const pp = data?.integrations.paypal;
  const hook = `${api.defaults.baseURL}/api/webhooks/paypal`;
  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <PageHeader title="Settings" subtitle={user?.email ?? undefined} />
      {isLoading || !data ? <Skeleton className="h-64" /> : <>
        <Card className="p-6">
          <h2 className="mb-4 text-lg font-semibold">Profile</h2>
          <form className="space-y-4" onSubmit={f.handleSubmit(async (v) => { try { await update.mutateAsync(v); toast("Profile saved"); } catch (e) { toast((e as Error).message, "error"); } })}>
            <div className="grid gap-4 sm:grid-cols-2"><Field label="Full name"><Input {...f.register("full_name")} /></Field><Field label="Business name"><Input {...f.register("business_name")} /></Field></div>
            <Field label="Default currency"><Select {...f.register("default_currency")}>{["USD", "EUR", "GBP", "INR", "AUD", "CAD", "SGD", "JPY"].map((c) => <option key={c}>{c}</option>)}</Select></Field>
            <Button type="submit" loading={update.isPending}>Save profile</Button>
          </form>
        </Card>
        <Card className="p-6">
          <h2 className="mb-4 text-lg font-semibold">Integrations</h2>
          <ul className="space-y-2">
            <Row ok={!!pp?.configured} label={`PayPal REST API (${pp?.mode ?? "—"})`} detail={pp?.configured ? (pp.connected ? `Connected · ${pp.latency_ms}ms · ${pp.client_id_hint}` : pp.error ?? "Cannot authenticate") : "Set PAYPAL_CLIENT_ID / SECRET on the backend"} />
            <Row ok={!!pp?.webhook_configured} label="PayPal webhooks" detail={pp?.webhook_configured ? (pp.last_webhook_at ? "Receiving events" : "Configured · no events yet") : "Set PAYPAL_WEBHOOK_ID"} />
            <Row ok={data.integrations.razorpay.configured} label={`Razorpay (${data.integrations.razorpay.mode})`} detail={data.integrations.razorpay.configured ? "Configured" : "Optional · not configured"} />
            <Row ok={data.integrations.ai.configured} label={`AI provider: ${data.integrations.ai.provider}`} detail={data.integrations.ai.configured ? "Configured" : "Set GEMINI_API_KEY or OPENAI_API_KEY"} />
          </ul>
          <div className="mt-4"><div className="label-caps mb-1">PayPal webhook URL (add in the PayPal developer dashboard)</div><div className="flex items-center gap-2 rounded-xl bg-surface-low px-3 py-2 font-label text-xs"><span className="min-w-0 flex-1 break-all">{hook}</span><CopyButton text={hook} /></div>
            <p className="mt-2 text-xs text-muted">Subscribe to CHECKOUT.ORDER.APPROVED, PAYMENT.CAPTURE.COMPLETED, PAYMENT.CAPTURE.DENIED, PAYMENT.CAPTURE.REFUNDED. API secrets live only on the server and are never shown here.</p></div>
        </Card>
        <Card className="p-6"><h2 className="mb-2 text-lg font-semibold">Go live checklist</h2><ol className="list-decimal space-y-1 pl-5 text-sm text-muted"><li>Create a PayPal live app and set <code>PAYPAL_BASE_URL=https://api-m.paypal.com</code> with live credentials.</li><li>Register the live webhook and set <code>PAYPAL_WEBHOOK_ID</code>.</li><li>Run a small real payment end-to-end before announcing.</li></ol></Card>
        <Card className="flex items-center justify-between p-6"><div><h2 className="font-semibold">Session</h2><p className="text-sm text-muted">Sign out of this device.</p></div><Button variant="secondary" icon={<LogOut className="h-4 w-4" />} onClick={() => signOut()}>Sign out</Button></Card>
      </>}
    </div>
  );
}
