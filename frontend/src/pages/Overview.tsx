import clsx from "clsx";
import { ArrowRight, ArrowUp, CalendarClock, CheckCircle2, Clock, Filter, Link2, Plus, Sparkles, TrendingUp, Wallet, ArrowLeftRight, BadgeCheck, AlertCircle, Activity, Webhook, Gauge, Network } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import RevenueChart from "@/components/RevenueChart";
import { Avatar, Button, Card, EmptyState, ErrorState, Segmented, Skeleton, StatusBadge } from "@/components/ui";
import { RANGES, useRange } from "@/context/RangeContext";
import { useDashboard, useProfile, usePayPalStatus } from "@/hooks/queries";
import type { Dashboard, Insight } from "@/types";
import { fmtTime, greeting, money, pct, providerLabel, relativeDay, timeAgo } from "@/utils/format";

function Banner({ d, updatedAt }: { d?: Dashboard; updatedAt: number }) {
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 1000); return () => clearInterval(t); }, []);
  const pp = d?.paypal;
  const ok = pp?.configured;
  return (
    <div className={clsx("flex flex-wrap items-center justify-between gap-2 rounded-xl px-4 py-1", ok ? "bg-amber-glow/10" : "bg-danger-container")}>
      <div className="flex flex-wrap items-center gap-2 font-label text-[11px]">
        <span className="relative flex h-2 w-2"><span className={clsx("absolute inset-0 animate-ping2 rounded-full opacity-75", ok ? "bg-amber-glow" : "bg-danger")} /><span className={clsx("relative h-2 w-2 rounded-full", ok ? "bg-amber-dot" : "bg-danger")} /></span>
        <span className="font-semibold uppercase tracking-[0.025em] text-amber-ink">{!pp ? "Checking PayPal…" : !ok ? "PayPal credentials not configured" : pp.mode === "sandbox" ? "PayPal developer sandbox active" : "PayPal live mode"}</span>
        {ok && <span className="text-amber-ink/60">| API credentials {pp?.client_id_hint}</span>}
      </div>
      <div className="flex items-center gap-2 font-label text-[11px] font-semibold text-amber-ink/80">
        <span>Synced {updatedAt ? timeAgo(updatedAt) : "…"}</span>
        <Link to="/settings" className="text-amber-ink hover:underline">{pp?.mode === "live" ? "Environment" : "Go-live checklist"}</Link>
      </div>
    </div>
  );
}

function Kpi({ label, icon, value, children, loading }: { label: string; icon: React.ReactNode; value: string; children: React.ReactNode; loading: boolean }) {
  return (
    <Card className="flex flex-col gap-3 p-6">
      <div className="flex items-center justify-between"><span className="label-caps">{label}</span><span className="text-primary">{icon}</span></div>
      {loading ? <Skeleton className="h-10 w-40" /> : <div className="font-display text-3xl font-semibold tracking-tight">{value}</div>}
      {children}
    </Card>
  );
}
const Delta = ({ v, suffix }: { v: number | null | undefined; suffix: string }) => (
  <div className="flex items-center justify-between text-xs text-muted">
    <span className={clsx("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-label font-semibold", v == null ? "bg-surface-low text-muted" : v >= 0 ? "bg-emerald-50 text-success" : "bg-danger-container text-danger-dark")}><TrendingUp className={clsx("h-3 w-3", v != null && v < 0 && "rotate-180")} />{pct(v)}</span>
    <span>{suffix}</span>
  </div>
);

const TONE: Record<Insight["tone"], string> = { positive: "bg-surface-low text-primary", negative: "bg-danger-container/50 text-danger-dark", warning: "bg-amber-50 text-amber-ink", neutral: "bg-surface-low text-primary" };
const TONE_ICON = { momentum: TrendingUp, action: AlertCircle, pattern: CalendarClock, risk: AlertCircle } as const;

export default function Overview() {
  const { days, setDays } = useRange();
  const { data, isLoading, error, refetch, dataUpdatedAt } = useDashboard(days);
  const { data: profile } = useProfile();
  const { data: pp } = usePayPalStatus();
  const nav = useNavigate();
  const [prompt, setPrompt] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.ctrlKey && e.key.toLowerCase() === "j") { e.preventDefault(); inputRef.current?.focus(); } };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  const ask = (text: string) => nav("/assistant", { state: { prompt: text } });
  const cur = data?.currency ?? "USD";
  const first = (profile?.full_name || "there").split(" ")[0];
  const month = new Date().toLocaleDateString("en-US", { month: "long" });
  const bars = (data?.revenue.series ?? []).slice(-6);
  const barMax = Math.max(1, ...bars);
  const emptyAll = data && data.transactions.total === 0 && data.pending.count === 0;

  if (error) return <Card><ErrorState error={error} onRetry={() => refetch()} /></Card>;
  return (
    <div className="flex flex-col gap-6">
      <Banner d={data} updatedAt={dataUpdatedAt} />
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-[-0.025em] sm:text-4xl">{greeting()}, {first} <span aria-hidden>👋</span></h1>
          <p className="mt-1 text-sm text-muted">Here's what's happening across your payment pipelines in the selected period.</p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" icon={<Link2 className="h-4 w-4" />} onClick={() => nav("/payment-links")}>Generate Link</Button>
          <Button icon={<Plus className="h-4 w-4" />} onClick={() => nav("/invoices/new")}>Create Invoice</Button>
        </div>
      </div>

      <Card className="p-4">
        <form onSubmit={(e) => { e.preventDefault(); if (prompt.trim()) ask(prompt.trim()); }} className="flex items-center gap-2 rounded-xl bg-surface-low px-4 py-2.5 shadow-[inset_0_2px_4px_rgba(0,0,0,0.05)]">
          <Sparkles className="h-5 w-5 shrink-0 text-primary" />
          <input ref={inputRef} value={prompt} onChange={(e) => setPrompt(e.target.value)} aria-label="Ask PayPilot" className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted/60" placeholder="Ask PayPilot anything... e.g. Create a $250 invoice for John or Check pending payouts" />
          <kbd className="hidden rounded bg-surface px-2 py-0.5 font-label text-[11px] font-semibold text-muted sm:block">Ctrl+J</kbd>
          <button type="submit" aria-label="Send to Copilot" className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary text-white shadow-card hover:bg-primary-dark"><ArrowUp className="h-4 w-4" /></button>
        </form>
        <div className="flex flex-wrap items-center gap-1 pt-2">
          <span className="label-caps pl-1">Suggested prompts:</span>
          {[["⚡", "Create Invoice", "Create a $250 invoice for John for consulting", "bg-primary-container/50 text-primary-on"], ["🔗", "Quick Link", "Generate a payment link for $120", "bg-surface text-ink"],
            ["📊", `Summarize ${month} Revenue`, "Show my revenue this month", "bg-surface text-ink"], ["📩", "Send Overdue Reminders", "Which customers have overdue invoices?", "bg-surface text-ink"]].map(([i, l, t, c]) => (
            <button key={l} onClick={() => ask(t!)} className={clsx("flex items-center gap-1.5 rounded-full px-2 py-1 font-label text-[11px] font-semibold hover:brightness-95", c)}><span aria-hidden>{i}</span>{l}</button>
          ))}
        </div>
      </Card>

      <div className="grid gap-6 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label="Total revenue" icon={<Wallet className="h-4 w-4" />} loading={isLoading} value={money(data?.revenue.total, cur)}>
          <Delta v={data?.revenue.change_pct} suffix="vs previous period" />
          <div className="flex h-6 items-end gap-1" aria-hidden>{bars.map((b, i) => <span key={i} className={clsx("flex-1 rounded-sm", i === bars.length - 1 ? "bg-primary" : i === bars.length - 3 ? "bg-primary-dark" : "bg-surface-highest")} style={{ height: `${Math.max(12, (b / barMax) * 100)}%` }} />)}</div>
        </Kpi>
        <Kpi label="Total transactions" icon={<ArrowLeftRight className="h-4 w-4" />} loading={isLoading} value={(data?.transactions.total ?? 0).toLocaleString()}>
          <Delta v={data?.transactions.change_pct} suffix={`${data?.transactions.processed_today ?? 0} processed today`} />
          <div className="h-1.5 overflow-hidden rounded-full bg-surface"><div className="h-full rounded-full bg-primary" style={{ width: `${data?.transactions.total ? Math.min(100, ((data?.transactions.processed_today ?? 0) / data.transactions.total) * 100) : 0}%` }} /></div>
        </Kpi>
        <Kpi label="Pending payments" icon={<Clock className="h-4 w-4 text-amber-dot" />} loading={isLoading} value={money(data?.pending.total, cur)}>
          <div className="flex items-center justify-between text-xs text-muted"><span className="rounded bg-amber-100 px-1.5 py-0.5 font-label font-semibold text-amber-ink">{data?.pending.overdue_count ? "Requires Attention" : "On track"}</span><span>{data?.pending.count ?? 0} invoices</span></div>
          <div className="flex items-center gap-1 font-label text-[11px] font-semibold text-amber-ink"><span className="h-1.5 w-1.5 rounded-full bg-amber-glow" />{data?.pending.earliest_aging_days != null ? `Earliest aging: ${data.pending.earliest_aging_days} days` : "Nothing outstanding"}</div>
        </Kpi>
        <Kpi label="Payment success rate" icon={<BadgeCheck className="h-4 w-4" />} loading={isLoading} value={data?.success_rate.value == null ? "—" : `${data.success_rate.value}%`}>
          <div className="flex items-center justify-between text-xs text-muted"><span className="rounded-md bg-primary-container/60 px-1.5 py-0.5 font-label font-semibold text-primary">{data?.success_rate.change_pct == null ? "n/a" : `${data.success_rate.change_pct > 0 ? "↑" : "↓"}${pct(data.success_rate.change_pct)}`}</span><span>completed ÷ (completed + failed)</span></div>
          <div className="flex justify-between text-xs text-muted"><span>Failed: {data?.success_rate.failed_pct ?? 0}%</span><span>Refunds: {data?.success_rate.refunded_pct ?? 0}%</span></div>
        </Kpi>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-6">
          <Card className="p-6">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold">Revenue &amp; Cashflow Overview</h2>
                <div className="mt-1 flex flex-wrap items-baseline gap-2"><span className="font-display text-2xl font-semibold">{money(data?.revenue.total, cur)}</span><span className="text-xs text-muted">Net Volume</span>
                  <span className="rounded-md bg-emerald-50 px-1.5 py-0.5 font-label text-[11px] font-semibold text-success">vs {money(data?.revenue.previous, cur)} prev</span></div>
              </div>
              <Segmented value={days} onChange={setDays} options={RANGES.map((r) => ({ value: r.value, label: r.label }))} />
            </div>
            <div className="mt-4">{data ? <RevenueChart series={data.revenue.series} previous={data.revenue.previous_series} start={data.revenue.start} granularity={data.revenue.granularity} currency={cur} /> : <Skeleton className="h-[280px]" />}</div>
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
              <div className="flex gap-4"><span className="flex items-center gap-1.5 font-medium text-ink"><span className="h-0.5 w-3 rounded bg-primary" />Current period</span><span className="flex items-center gap-1.5"><span className="h-0.5 w-3 border-t border-dashed border-outline" />Previous period</span></div>
              <span className="flex items-center gap-1"><Activity className="h-3.5 w-3.5" />Average {data?.revenue.granularity === "month" ? "monthly" : "daily"} run rate: {money(data?.revenue.daily_average, cur)}/day</span>
            </div>
          </Card>

          <Card className="overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-3 p-6">
              <div><h2 className="text-lg font-semibold">Recent Transaction Activity</h2><p className="text-sm text-muted">Live from PayPal &amp; Razorpay captures and verified webhooks</p></div>
              <div className="flex items-center gap-3 text-sm"><Link to="/transactions" className="inline-flex items-center gap-1 rounded-lg bg-surface-low px-2 py-1 text-muted"><Filter className="h-3.5 w-3.5" />Filter</Link><Link to="/transactions" className="inline-flex items-center gap-1 font-medium text-primary">View All Activity <ArrowRight className="h-3.5 w-3.5" /></Link></div>
            </div>
            {isLoading ? <div className="space-y-2 p-6"><Skeleton className="h-12" /><Skeleton className="h-12" /></div> : !data?.recent_transactions.length ? (
              <EmptyState icon={<ArrowLeftRight className="h-6 w-6" />} title="No transactions yet" text="Create an invoice and share its PayPal link. Verified payments appear here instantly." action={<Button onClick={() => nav("/invoices/new")}>Create invoice</Button>} />
            ) : (
              <div className="overflow-x-auto"><table className="w-full min-w-[720px] text-left">
                <thead className="bg-surface-low"><tr>{["Customer", "TX ID", "Date", "Amount", "Method", "Status"].map((h) => <th key={h} className="th !px-4">{h}</th>)}</tr></thead>
                <tbody>{data.recent_transactions.map((t) => (
                  <tr key={t.id} className="border-t border-surface-low hover:bg-surface-low/50">
                    <td className="td !px-4"><Link to={`/transactions?open=${t.id}`} className="flex items-center gap-3"><Avatar name={t.customer?.name ?? "?"} /><span><span className="block font-medium">{t.customer?.name ?? "Unassigned"}</span><span className="block text-xs text-muted">{t.customer?.email ?? t.invoice_number ?? "—"}</span></span></Link></td>
                    <td className="td !px-4 font-label text-xs text-muted">#{(t.provider_order_id ?? t.id).slice(-8).toUpperCase()}</td>
                    <td className="td !px-4 whitespace-nowrap text-xs text-muted">{relativeDay(t.created_at)},<br />{fmtTime(t.created_at)}</td>
                    <td className={clsx("td !px-4 whitespace-nowrap font-semibold", t.status === "failed" && "text-danger")}>+{money(t.amount, t.currency)}</td>
                    <td className="td !px-4 text-xs">{providerLabel(t.provider)}<br /><span className="text-muted capitalize">{t.payment_method ?? "—"}</span></td>
                    <td className="td !px-4"><StatusBadge status={t.status} /></td>
                  </tr>))}</tbody>
              </table></div>
            )}
            <div className="px-6 py-4 text-sm text-muted">Showing {data?.recent_transactions.length ?? 0} of {(data?.transactions.total ?? 0).toLocaleString()} transactions in this period</div>
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-6">
          <Card className="bg-gradient-to-b from-surface-low to-white p-6">
            <div className="flex items-start justify-between"><div className="flex items-center gap-2"><Sparkles className="h-5 w-5 text-primary" /><h2 className="text-lg font-semibold leading-tight">PayPilot<br />Intelligence</h2></div><span className="inline-flex items-center gap-1 rounded-full bg-primary-container px-2 py-1 font-label text-[11px] font-semibold text-primary-on"><span className="h-1.5 w-1.5 rounded-full bg-primary" />Live</span></div>
            <p className="mt-3 text-sm text-muted">Insights computed from your actual transactions and invoices.</p>
            <div className="mt-4 flex flex-col gap-3">
              {isLoading ? <Skeleton className="h-28" /> : data?.insights.length ? data.insights.map((i) => {
                const Icon = TONE_ICON[i.kind as keyof typeof TONE_ICON] ?? Sparkles;
                return (
                  <div key={i.title} className={clsx("rounded-xl p-4", TONE[i.tone])}>
                    <div className="flex items-center gap-2 font-semibold"><Icon className="h-4 w-4" />{i.title}</div>
                    <p className="mt-1 text-sm text-ink">{i.text}</p>
                    {i.action && <button onClick={() => ask("Draft payment reminders for my overdue invoices")} className="mt-2 inline-flex items-center gap-1 text-sm font-semibold text-amber-ink">{i.action} <ArrowRight className="h-3.5 w-3.5" /></button>}
                  </div>);
              }) : <p className="rounded-xl bg-surface-low p-4 text-sm text-muted">Insights appear once you have a few payments.</p>}
              <Link to="/analytics" className="flex items-center justify-center gap-2 rounded-xl bg-surface-low px-4 py-3 text-sm font-semibold hover:bg-surface">Open Full Analytics <ArrowRight className="h-4 w-4" /></Link>
            </div>
          </Card>

          <Card className="p-6">
            <div className="flex items-center justify-between"><div className="flex items-center gap-2"><Network className="h-5 w-5 text-primary" /><h2 className="text-lg font-semibold">Sandbox Status</h2></div>
              <span className={clsx("rounded-full px-2 py-1 font-label text-[11px] font-semibold", pp?.connected ? "bg-emerald-100 text-success" : "bg-danger-container text-danger-dark")}>{pp?.connected ? "Connected" : pp?.configured ? "Unreachable" : "Not configured"}</span></div>
            <div className="mt-4 rounded-xl bg-surface-low p-4">
              <div className="label-caps">Collected this period</div>
              <div className="mt-1 font-display text-2xl font-semibold">{money(data?.revenue.total, cur)} <span className="text-base text-muted">{cur}</span></div>
              <div className="mt-1 text-xs text-muted">Mode: {pp?.mode ?? "—"} · Client {pp?.client_id_hint ?? "—"}</div>
            </div>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <div className="rounded-xl bg-surface-low p-3"><div className="label-caps">API latency</div><div className="mt-1 flex items-center gap-1 font-label text-sm font-semibold text-success"><Gauge className="h-3.5 w-3.5" />{pp?.latency_ms != null ? `${pp.latency_ms}ms` : "—"}</div></div>
              <div className="rounded-xl bg-surface-low p-3"><div className="label-caps">Webhooks</div><div className={clsx("mt-1 flex items-center gap-1 font-label text-sm font-semibold", pp?.webhook_configured ? "text-primary" : "text-amber-ink")}><Webhook className="h-3.5 w-3.5" />{pp?.webhook_configured ? (pp.last_webhook_at ? "Receiving" : "Configured") : "Not set"}</div></div>
            </div>
            {pp?.last_webhook_at && <p className="mt-3 text-xs text-muted">Last webhook {timeAgo(pp.last_webhook_at)}</p>}
            {emptyAll && <p className="mt-3 flex items-center gap-1 text-xs text-muted"><CheckCircle2 className="h-3.5 w-3.5" />Ready for your first sandbox payment.</p>}
          </Card>
        </div>
      </div>
    </div>
  );
}
