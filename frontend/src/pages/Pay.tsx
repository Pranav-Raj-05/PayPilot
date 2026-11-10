import { CheckCircle2, Clock, Loader2, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { publicApi } from "@/lib/api";
import { money } from "@/utils/format";

interface Result { status: string; amount: number; currency: string; invoice_number: string | null }
const Shell = ({ children }: { children: React.ReactNode }) => (
  <div className="flex min-h-screen items-center justify-center bg-canvas p-6"><div className="card w-full max-w-md p-8 text-center"><div className="mb-6 flex items-center justify-center gap-2"><img src="/favicon.svg" alt="" className="h-8 w-8" /><span className="font-semibold">PayPilot</span></div>{children}</div></div>
);
const msg = (e: any) => e?.response?.data?.error?.message || "We couldn't verify this payment. If you were charged, contact the merchant.";

function Outcome({ r }: { r: Result }) {
  const ok = r.status === "completed";
  return (
    <>
      {ok ? <CheckCircle2 className="mx-auto h-14 w-14 text-success-bright" /> : r.status === "pending" ? <Clock className="mx-auto h-14 w-14 text-amber-dot" /> : <XCircle className="mx-auto h-14 w-14 text-danger" />}
      <h1 className="mt-4 text-2xl font-semibold">{ok ? "Payment received" : r.status === "pending" ? "Payment pending" : "Payment not completed"}</h1>
      <p className="mt-2 text-sm text-muted">{money(r.amount, r.currency)}{r.invoice_number ? ` · ${r.invoice_number}` : ""}</p>
      <p className="mt-4 text-xs text-muted">{ok ? "Verified with the payment provider. You can close this page." : "No payment was recorded. You can retry from the original link."}</p>
    </>
  );
}

export function PayReturn() {
  const [sp] = useSearchParams();
  const token = sp.get("token");
  const [res, setRes] = useState<Result | null>(null);
  const [err, setErr] = useState("");
  const started = useRef(false);
  useEffect(() => {
    if (!token || started.current) return;
    started.current = true;
    publicApi.post<Result>("/api/public/pay/paypal/capture", { order_id: token }).then((r) => setRes(r.data)).catch((e) => setErr(msg(e)));
  }, [token]);
  return (
    <Shell>
      {!token ? <p className="text-sm text-muted">Missing payment reference.</p> : err ? <><XCircle className="mx-auto h-14 w-14 text-danger" /><p className="mt-4 text-sm">{err}</p></> : res ? <Outcome r={res} /> :
        <><Loader2 className="mx-auto h-10 w-10 animate-spin text-primary" /><p className="mt-4 text-sm text-muted">Confirming your PayPal payment…</p></>}
    </Shell>
  );
}

export function PayCancelled() {
  const [sp] = useSearchParams();
  const token = sp.get("token");
  useEffect(() => { if (token) publicApi.post("/api/public/pay/paypal/cancel", { order_id: token }).catch(() => {}); }, [token]);
  return <Shell><XCircle className="mx-auto h-14 w-14 text-muted" /><h1 className="mt-4 text-2xl font-semibold">Payment cancelled</h1><p className="mt-2 text-sm text-muted">You weren't charged. Use the original link if you want to try again.</p></Shell>;
}

declare global { interface Window { Razorpay?: new (o: Record<string, unknown>) => { open: () => void } } }

export function PayRazorpay() {
  const { linkId } = useParams();
  const [info, setInfo] = useState<any>(null);
  const [res, setRes] = useState<Result | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { publicApi.get(`/api/public/pay/link/${linkId}`).then((r) => setInfo(r.data)).catch((e) => setErr(msg(e))); }, [linkId]);
  const pay = async () => {
    if (!window.Razorpay) await new Promise<void>((ok, bad) => { const s = document.createElement("script"); s.src = "https://checkout.razorpay.com/v1/checkout.js"; s.onload = () => ok(); s.onerror = () => bad(); document.body.appendChild(s); }).catch(() => setErr("Could not load Razorpay checkout."));
    if (!window.Razorpay || !info) return;
    new window.Razorpay({
      key: info.key_id, order_id: info.order_id, amount: Math.round(info.amount * 100), currency: info.currency, name: info.merchant || "PayPilot", description: info.description,
      handler: async (r: Record<string, string>) => {
        try { setRes((await publicApi.post<Result>("/api/public/pay/razorpay/verify", { razorpay_order_id: r.razorpay_order_id, razorpay_payment_id: r.razorpay_payment_id, razorpay_signature: r.razorpay_signature })).data); }
        catch (e) { setErr(msg(e)); }
      },
    }).open();
  };
  return (
    <Shell>
      {err ? <><XCircle className="mx-auto h-12 w-12 text-danger" /><p className="mt-3 text-sm">{err}</p></> : res ? <Outcome r={res} /> : !info ? <Loader2 className="mx-auto h-8 w-8 animate-spin text-primary" /> : info.status !== "active" ? <p className="text-sm text-muted">This payment link is {info.status}.</p> : (
        <><p className="text-sm text-muted">{info.merchant ?? "Payment request"}</p><div className="mt-2 font-display text-4xl font-semibold">{money(info.amount, info.currency)}</div><p className="mt-1 text-sm text-muted">{info.description}</p>
          <button onClick={pay} className="mt-6 w-full rounded-xl bg-primary px-4 py-3 font-medium text-white hover:bg-primary-dark">Pay with Razorpay (UPI / card)</button><p className="mt-3 text-xs text-muted">Test mode checkout</p></>
      )}
    </Shell>
  );
}
