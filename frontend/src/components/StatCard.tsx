import clsx from "clsx";
import type { ReactNode } from "react";
import { Card, Skeleton } from "@/components/ui";

/** KPI tile used on the Invoices / Transactions / Payment Links screens. */
export function StatCard({ label, value, unit, icon, tone = "primary", badge, note, progress, loading }: {
  label: string; value: string; unit?: string; icon: ReactNode; tone?: "primary" | "amber" | "danger" | "neutral"; badge?: string; note?: string; progress?: number; loading?: boolean;
}) {
  const t = { primary: ["bg-surface text-primary", "bg-primary", "bg-primary-container text-primary-on"], amber: ["bg-amber-100 text-amber-ink", "bg-amber-glow", "bg-amber-100 text-amber-ink"],
    danger: ["bg-danger-container text-danger", "bg-danger", "bg-danger-container text-danger-dark"], neutral: ["bg-surface text-muted", "bg-outline", "bg-surface text-muted"] }[tone];
  return (
    <Card className="flex flex-col gap-3 p-5">
      <div className="flex items-start justify-between gap-2"><span className="label-caps">{label}</span><span className={clsx("rounded-lg p-1.5", t[0])}>{icon}</span></div>
      {loading ? <Skeleton className="h-9 w-32" /> : <div className="font-display text-2xl font-semibold tracking-tight">{value}{unit && <span className="ml-1 text-xs font-medium text-muted">{unit}</span>}</div>}
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">{badge && <span className={clsx("rounded px-1.5 py-0.5 font-label font-semibold", t[2])}>{badge}</span>}{note && <span>{note}</span>}</div>
      {progress != null && <div className="h-1 overflow-hidden rounded-full bg-surface"><div className={clsx("h-full rounded-full", t[1])} style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} /></div>}
    </Card>
  );
}
