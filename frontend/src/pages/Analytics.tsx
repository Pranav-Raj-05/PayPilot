import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Sparkles } from "lucide-react";
import { useNavigate } from "react-router-dom";
import RevenueChart, { buildPoints } from "@/components/RevenueChart";
import { Button, Card, ErrorState, PageHeader, Segmented, Skeleton } from "@/components/ui";
import { RANGES, useRange } from "@/context/RangeContext";
import { useAnalytics } from "@/hooks/queries";
import { money, pct, providerLabel } from "@/utils/format";

const Stat = ({ label, value, sub }: { label: string; value: string; sub?: string }) => <Card className="p-5"><div className="label-caps">{label}</div><div className="mt-2 font-display text-2xl font-semibold">{value}</div>{sub && <div className="mt-1 text-xs text-muted">{sub}</div>}</Card>;

export default function Analytics() {
  const { days, setDays } = useRange();
  const { data, isLoading, error, refetch } = useAnalytics(days);
  const nav = useNavigate();
  if (error) return <Card><ErrorState error={error} onRetry={() => refetch()} /></Card>;
  const cur = data?.currency ?? "USD";
  const vol = data ? buildPoints({ series: data.volume.series, previous: [], start: data.revenue.start, granularity: data.revenue.granularity }) : [];
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Analytics" subtitle="Computed live from your transactions and invoices." actions={<Segmented value={days} onChange={setDays} options={RANGES.map((r) => ({ value: r.value, label: r.label }))} />} />
      {isLoading || !data ? <Skeleton className="h-96" /> : <>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Stat label="Revenue" value={money(data.revenue.total, cur)} sub={`${pct(data.revenue.change_pct)} vs previous period`} />
          <Stat label="Transactions" value={String(data.volume.total)} sub={Object.entries(data.volume.by_status).map(([k, v]) => `${v} ${k}`).join(" · ") || "none yet"} />
          <Stat label="Payment success rate" value={data.success_rate.value == null ? "—" : `${data.success_rate.value}%`} sub={`Failed ${data.success_rate.failed_pct}% · Refunded ${data.success_rate.refunded_pct}%`} />
          <Stat label="Avg transaction value" value={money(data.average_transaction_value, cur)} sub="completed payments only" />
        </div>
        <Card className="p-6"><h2 className="mb-4 text-lg font-semibold">Revenue trend</h2><RevenueChart series={data.revenue.series} previous={data.revenue.previous_series} start={data.revenue.start} granularity={data.revenue.granularity} currency={cur} height={300} /></Card>
        <div className="grid gap-6 lg:grid-cols-2">
          <Card className="p-6"><h2 className="mb-4 text-lg font-semibold">Transaction volume</h2>
            <div className="h-60"><ResponsiveContainer width="100%" height="100%"><BarChart data={vol}><CartesianGrid stroke="#dae2fd" strokeDasharray="3 3" vertical={false} /><XAxis dataKey="label" tickLine={false} axisLine={false} interval={Math.max(0, Math.floor(vol.length / 6))} tick={{ fontSize: 11 }} /><YAxis allowDecimals={false} width={28} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} /><Tooltip /><Bar dataKey="value" name="Transactions" fill="#005ab6" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer></div></Card>
          <Card className="p-6"><h2 className="mb-4 text-lg font-semibold">Customer payment analysis</h2>
            {data.top_customers.length ? <table className="w-full text-sm"><thead><tr className="text-left text-muted"><th className="py-1 font-medium">Customer</th><th className="py-1 text-right font-medium">Payments</th><th className="py-1 text-right font-medium">Revenue</th></tr></thead>
              <tbody>{data.top_customers.map((c) => <tr key={c.customer} className="border-t border-surface-low"><td className="py-2">{c.customer}</td><td className="py-2 text-right">{c.payments}</td><td className="py-2 text-right font-semibold">{money(c.revenue, cur)}</td></tr>)}</tbody></table> : <p className="text-sm text-muted">No completed payments yet.</p>}
            {!!Object.keys(data.revenue_by_provider).length && <div className="mt-4 flex flex-wrap gap-2 text-xs">{Object.entries(data.revenue_by_provider).map(([k, v]) => <span key={k} className="rounded-full bg-surface px-3 py-1">{providerLabel(k)}: {money(v, cur)}</span>)}</div>}</Card>
        </div>
        <Card className="bg-gradient-to-b from-surface-low to-white p-6">
          <div className="flex items-center justify-between"><h2 className="flex items-center gap-2 text-lg font-semibold"><Sparkles className="h-5 w-5 text-primary" />Business insights</h2><Button variant="secondary" size="sm" onClick={() => nav("/assistant", { state: { prompt: `Analyse my last ${days} days of transactions` } })}>Ask Copilot for a deeper analysis</Button></div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">{data.insights.length ? data.insights.map((i) => <div key={i.title} className="rounded-xl bg-white p-4 shadow-card"><div className="font-semibold">{i.title}</div><p className="mt-1 text-sm text-muted">{i.text}</p></div>) : <p className="text-sm text-muted">Insights appear once you have payment activity.</p>}</div>
        </Card>
      </>}
    </div>
  );
}
