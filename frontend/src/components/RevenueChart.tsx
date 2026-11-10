import { Area, AreaChart, CartesianGrid, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { money } from "@/utils/format";

interface Props { series: number[]; previous: number[]; start: string; granularity: "day" | "month"; currency: string; height?: number }

export function buildPoints({ series, previous, start, granularity }: Pick<Props, "series" | "previous" | "start" | "granularity">) {
  const base = new Date(start + "T00:00:00");
  return series.map((v, i) => {
    const d = new Date(base);
    if (granularity === "month") d.setMonth(d.getMonth() + i); else d.setDate(d.getDate() + i);
    return { i, value: v, previous: previous[i] ?? 0, date: d,
      label: d.toLocaleDateString("en-US", granularity === "month" ? { month: "short" } : { month: "short", day: "2-digit" }) };
  });
}

export default function RevenueChart(p: Props) {
  const data = buildPoints(p);
  const hasData = data.some((d) => d.value > 0 || d.previous > 0);
  const tick = Math.max(1, Math.floor(data.length / 5));
  return (
    <div className="relative" style={{ height: p.height ?? 280 }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <defs><linearGradient id="rev" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#005ab6" stopOpacity={0.18} /><stop offset="100%" stopColor="#005ab6" stopOpacity={0} /></linearGradient></defs>
          <CartesianGrid stroke="#dae2fd" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" padding={{ left: 16, right: 16 }} tickLine={false} axisLine={false} interval={tick - 1} tick={{ fontSize: 11, fill: "#45474c", fontFamily: "Geist" }} />
          <YAxis hide domain={[0, "auto"]} />
          <Tooltip cursor={{ stroke: "#005ab6", strokeDasharray: "3 3" }} content={({ active, payload }) => {
            if (!active || !payload?.length) return null;
            const pt = payload[0]!.payload as (typeof data)[number];
            return (
              <div className="rounded-xl bg-nav px-3 py-2 text-white shadow-float">
                <div className="flex justify-between gap-4 font-label text-[11px] text-white/60"><span>{pt.date.toLocaleDateString("en-US", { month: "short", day: "2-digit", year: "numeric" })}</span><span>PayPilot</span></div>
                <div className="text-lg font-semibold">{money(pt.value, p.currency)}</div>
                <div className="font-label text-[11px] text-white/60">prev: {money(pt.previous, p.currency)}</div>
              </div>
            );
          }} />
          <Line type="monotone" dataKey="previous" stroke="#9aa3b8" strokeDasharray="4 4" strokeWidth={1.5} dot={false} isAnimationActive={false} />
          <Area type="monotone" dataKey="value" stroke="#005ab6" strokeWidth={2.5} fill="url(#rev)" dot={false} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>
      {!hasData && <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-muted">No completed payments in this period yet</div>}
    </div>
  );
}
