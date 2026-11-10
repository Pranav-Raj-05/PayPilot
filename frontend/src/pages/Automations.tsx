import clsx from "clsx";
import { Bot, Play } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button, Card, ErrorState, PageHeader, Skeleton } from "@/components/ui";
import { useToast } from "@/context/ToastContext";
import { useAutomations, useNotifications, useRunAutomation, useUpdateAutomation } from "@/hooks/queries";
import { fmtDate, fmtTime, timeAgo } from "@/utils/format";

const EVENT_DRIVEN = new Set(["payment_notifications"]);

export default function Automations() {
  const { toast } = useToast();
  const nav = useNavigate();
  const { data, isLoading, error, refetch } = useAutomations();
  const { data: notes } = useNotifications();
  const update = useUpdateAutomation(), run = useRunAutomation();
  const suggestions = (notes?.items ?? []).filter((n) => n.notification_type === "reminder_suggestion").slice(0, 5);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Automations" subtitle="Background jobs run on the server. Reminders are drafted for your approval — nothing is sent automatically." />
      {error ? <Card><ErrorState error={error} onRetry={() => refetch()} /></Card> : isLoading ? <Skeleton className="h-64" /> : (
        <div className="grid gap-4 md:grid-cols-2">{data?.map((a) => (
          <Card key={a.id} className="flex flex-col gap-3 p-6">
            <div className="flex items-start justify-between gap-3">
              <div className="flex items-start gap-3"><span className="rounded-xl bg-surface-low p-2 text-primary"><Bot className="h-5 w-5" /></span><div><h2 className="font-semibold">{a.name}</h2><p className="text-sm text-muted">{a.description}</p></div></div>
              <button role="switch" aria-checked={a.is_enabled} aria-label={`${a.is_enabled ? "Disable" : "Enable"} ${a.name}`} onClick={async () => { try { await update.mutateAsync({ id: a.id, is_enabled: !a.is_enabled }); toast(`${a.name} ${a.is_enabled ? "disabled" : "enabled"}`); } catch (e) { toast((e as Error).message, "error"); } }}
                className={clsx("relative h-6 w-11 shrink-0 rounded-full transition", a.is_enabled ? "bg-primary" : "bg-surface-highest")}><span className={clsx("absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all", a.is_enabled ? "left-[22px]" : "left-0.5")} /></button>
            </div>
            <div className="mt-auto flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
              <span>{EVENT_DRIVEN.has(a.automation_type) ? "Event-driven (webhooks & captures)" : a.last_executed_at ? `Last run ${timeAgo(a.last_executed_at)} · ${fmtDate(a.last_executed_at)} ${fmtTime(a.last_executed_at)}` : "Has not run yet"}</span>
              {!EVENT_DRIVEN.has(a.automation_type) && <Button size="sm" variant="secondary" icon={<Play className="h-3 w-3" />} loading={run.isPending && run.variables === a.id} onClick={async () => { try { const r = await run.mutateAsync(a.id); toast(`Ran: ${r.result}`); } catch (e) { toast((e as Error).message, "error"); } }}>Run now</Button>}
            </div>
          </Card>))}</div>
      )}
      <Card className="p-6">
        <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-lg font-semibold">Payment reminder suggestions</h2><Button size="sm" variant="secondary" onClick={() => nav("/assistant", { state: { prompt: "Draft payment reminders for my overdue invoices" } })}>Draft with Copilot</Button></div>
        <p className="mt-1 text-sm text-muted">Scheduled checks surface overdue invoices here. Approve a reminder in Copilot to record it. Email/SMS delivery is not configured in this deployment.</p>
        <ul className="mt-4 divide-y divide-surface-low">{suggestions.length ? suggestions.map((s) => <li key={s.id} className="py-3 text-sm"><div className="font-medium">{s.title}</div><div className="text-muted">{s.message}</div><div className="mt-1 text-xs text-muted">{timeAgo(s.created_at)}</div></li>) : <li className="py-3 text-sm text-muted">No suggestions yet. Enable “Payment reminder suggestions” and run it.</li>}</ul>
      </Card>
    </div>
  );
}
