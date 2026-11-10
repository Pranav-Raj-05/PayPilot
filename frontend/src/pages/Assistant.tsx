import { useMutation, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ArrowUp, BarChart3, Bell, Bot, CheckCircle2, FileText, History, Link2, Lock, Mic, Paperclip, Plus, ShieldCheck, Sparkles, XCircle, Zap } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { ActionCard } from "@/components/assistant/ActionCards";
import { ReadResult } from "@/components/assistant/ToolResults";
import { Avatar, Button, Card, ErrorState } from "@/components/ui";
import { useAutomations, useConversations, useDashboard, usePayPalStatus, useProfile, useRecentActions, useSettings } from "@/hooks/queries";
import { get, post } from "@/lib/api";
import type { ChatMessage } from "@/types";
import { fmtTime, money, relativeDay } from "@/utils/format";

const QUICK = [
  { icon: FileText, label: "Create $500 invoice for Sarah", text: "Create a $500 invoice for Sarah Johnson for website development, due in 14 days" },
  { icon: Link2, label: "Generate $120 consulting link", text: "Generate a $120 payment link for consulting" },
  { icon: BarChart3, label: "Analyze monthly success rate", text: "Analyse my last 30 days of transactions" },
  { icon: Bell, label: "Nudge overdue clients", text: "Which customers have overdue invoices? Draft reminders." },
];

/** Within one turn show the assistant's explanation before the tool/approval cards it refers to. */
export function orderForDisplay(msgs: ChatMessage[]): ChatMessage[] {
  const out: ChatMessage[] = [];
  let tools: ChatMessage[] = [];
  const flush = () => { out.push(...tools); tools = []; };
  for (const m of msgs) {
    if (m.role === "user") { flush(); out.push(m); }
    else if (m.role === "tool") tools.push(m);
    else out.push(m);
  }
  flush();
  return out;
}

function AiAvatar() { return <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-nav text-white"><Sparkles className="h-4 w-4" /></span>; }

function Bubble({ msg, busyId, onConfirm, onCancel, onEdit, name }: { msg: ChatMessage; busyId: string | null; onConfirm: (id: string) => void; onCancel: (id: string) => void; onEdit: (t: string) => void; name: string }) {
  if (msg.role === "user") return (
    <div className="flex justify-end gap-3">
      <div className="max-w-[85%]">
        <div className="mb-1 flex items-center justify-end gap-2 text-xs"><span className="text-muted">{fmtTime(msg.created_at)}</span><span className="font-semibold">{name}</span></div>
        <div className="whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-nav px-4 py-3 text-sm text-white">{msg.content}</div>
      </div>
      <Avatar name={name} size={40} />
    </div>
  );
  return (
    <div className="flex gap-3">
      <AiAvatar />
      <div className="min-w-0 max-w-[min(100%,48rem)] flex-1">
        <div className="mb-1 flex items-center gap-2 text-xs"><span className="font-semibold">PayPilot Copilot</span><span className="text-muted">{fmtTime(msg.created_at)}</span>
          {msg.role === "assistant" && <span className="rounded bg-surface px-1.5 py-0.5 font-label text-[10px] font-semibold text-muted">AI-generated suggestion</span>}</div>
        {msg.role === "assistant" && <div className="whitespace-pre-wrap rounded-2xl rounded-tl-sm bg-white p-4 text-sm leading-relaxed shadow-card">{msg.content}</div>}
        {msg.role === "tool" && msg.tool_result?.status === "read" && <ReadResult msg={msg} />}
        {msg.role === "tool" && msg.tool_result && msg.tool_result.status !== "read" && (
          <ActionCard msg={msg} busy={busyId === msg.id} onConfirm={() => onConfirm(msg.id)} onCancel={() => onCancel(msg.id)} onEdit={onEdit} />
        )}
      </div>
    </div>
  );
}

export default function Assistant() {
  const qc = useQueryClient();
  const loc = useLocation() as { state?: { prompt?: string } };
  const { data: profile } = useProfile();
  const { data: pp } = usePayPalStatus();
  const { data: dash } = useDashboard(30);
  const { data: autos } = useAutomations();
  const { data: actions } = useRecentActions();
  const { data: settings } = useSettings();
  const { data: convs } = useConversations();
  const [convId, setConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [lastText, setLastText] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const name = profile?.full_name || "You";

  const chat = useMutation({
    mutationFn: (text: string) => post<{ conversation_id: string; messages: ChatMessage[] }>("/api/ai/chat", { message: text, conversation_id: convId }),
    onSuccess: (res, text) => {
      setConvId(res.conversation_id);
      setMessages((m) => [...m.filter((x) => !x.id.startsWith("tmp-")), ...res.messages]);
      qc.invalidateQueries({ queryKey: ["conversations"] });
      qc.invalidateQueries({ queryKey: ["ai-actions"] });
      void text;
    },
    onError: (e: Error) => { setMessages((m) => m.filter((x) => !x.id.startsWith("tmp-"))); setError(e.message); },
  });

  const send = useCallback((text: string) => {
    const t = text.trim();
    if (!t || chat.isPending) return;
    setError(null); setLastText(t); setInput("");
    setMessages((m) => [...m, { id: `tmp-${Date.now()}`, role: "user", content: t, tool_name: null, tool_result: null, created_at: new Date().toISOString() }]);
    chat.mutate(t);
  }, [chat]);

  const sentInitial = useRef(false);
  useEffect(() => { if (loc.state?.prompt && !sentInitial.current) { sentInitial.current = true; send(loc.state.prompt); window.history.replaceState({}, ""); } }, [loc.state, send]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages, chat.isPending]);

  const act = async (id: string, kind: "confirm" | "cancel") => {
    setBusyId(id);
    try {
      const updated = await post<ChatMessage>(`/api/ai/actions/${id}/${kind}`);
      setMessages((m) => m.map((x) => (x.id === id ? updated : x)));
      ["dashboard", "invoices", "transactions", "links", "customers", "ai-actions", "notifications"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    } catch (e) { setError((e as Error).message); } finally { setBusyId(null); }
  };

  const openConversation = async (id: string) => {
    setError(null); setConvId(id);
    setMessages(await get<ChatMessage[]>(`/api/ai/conversations/${id}`));
  };
  const reset = () => { setConvId(null); setMessages([]); setError(null); };

  const running = (autos ?? []).filter((a) => a.is_enabled && a.automation_type !== "payment_notifications");
  const empty = messages.length === 0;
  const aiReady = settings?.integrations.ai.configured !== false;

  return (
    <div className="-m-4 sm:-m-6">
      <div className="flex flex-wrap items-center justify-between gap-2 bg-primary-container/70 px-6 py-3 font-label text-[11px] font-semibold uppercase text-primary-on">
        <span className="flex items-center gap-2"><span className="h-1.5 w-1.5 rounded-full bg-primary" />AI agent connected to PayPal {pp?.mode ?? "sandbox"} • approval guardrails active</span>
        <span className="flex flex-wrap items-center gap-4 normal-case"><span>Latency: {pp?.latency_ms != null ? `${pp.latency_ms}ms` : "—"}</span><span>Session: {convId ? convId.slice(0, 8) : "new"}</span><span className="flex items-center gap-1"><ShieldCheck className="h-3 w-3" />Audit log active</span></span>
      </div>

      <div className="grid gap-6 p-4 sm:p-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex min-h-[calc(100vh-12rem)] min-w-0 flex-col">
          {empty ? (
            <Card className="bg-gradient-to-br from-surface-low to-white p-6 sm:p-8">
              <div className="label-caps flex items-center gap-2 !text-primary"><ShieldCheck className="h-4 w-4" />Autonomous financial copilot</div>
              <h1 className="mt-3 text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">Meet your Financial AI Agent</h1>
              <p className="mt-3 max-w-xl text-sm text-muted">Describe what you need in plain English. I'll prepare payment workflows via PayPal APIs — you approve every financial action with one click, and each step is audit-logged.</p>
              <div className="mt-6"><div className="label-caps mb-2">Quick actions:</div>
                <div className="flex flex-wrap gap-2">{QUICK.map((q) => <button key={q.label} onClick={() => send(q.text)} disabled={chat.isPending} className="flex items-center gap-2 rounded-xl bg-surface px-3 py-2 text-xs font-medium text-ink hover:bg-surface-high disabled:opacity-60"><q.icon className="h-3.5 w-3.5 text-primary" />{q.label}</button>)}</div></div>
            </Card>
          ) : (
            <div className="mb-4 flex items-center justify-between"><h1 className="text-xl font-semibold">PayPilot Copilot</h1><Button variant="secondary" size="sm" icon={<Plus className="h-3.5 w-3.5" />} onClick={reset}>New chat</Button></div>
          )}

          {!aiReady && <p role="alert" className="mt-4 rounded-xl bg-amber-50 p-4 text-sm text-amber-ink">The AI provider isn't configured on the server (set <code>GEMINI_API_KEY</code> or <code>OPENAI_API_KEY</code>). Copilot can't answer until it is.</p>}

          <div className="mt-6 flex flex-1 flex-col gap-6" aria-live="polite">
            {empty && convs && convs.length > 0 && (
              <div className="rounded-2xl bg-white p-4 shadow-card"><div className="label-caps mb-2 flex items-center gap-1"><History className="h-3.5 w-3.5" />Recent conversations</div>
                <ul className="space-y-1">{convs.slice(0, 4).map((c) => <li key={c.id}><button onClick={() => openConversation(c.id)} className="w-full truncate rounded-lg px-2 py-1.5 text-left text-sm hover:bg-surface-low">{c.title}</button></li>)}</ul></div>
            )}
            {orderForDisplay(messages).map((m) => <Bubble key={m.id} msg={m} busyId={busyId} name={name} onConfirm={(id) => act(id, "confirm")} onCancel={(id) => act(id, "cancel")} onEdit={(t) => { setInput(t); inputRef.current?.focus(); }} />)}
            {chat.isPending && (
              <div className="flex gap-3" role="status"><AiAvatar /><div className="rounded-2xl bg-white px-4 py-3 text-sm text-muted shadow-card"><span className="inline-flex items-center gap-2"><Zap className="h-4 w-4 animate-pulse text-primary" />Copilot is thinking and calling tools…</span></div></div>
            )}
            {error && <div className="flex items-center justify-between gap-3 rounded-2xl bg-danger-container p-4 text-sm text-danger-dark" role="alert"><span className="flex items-center gap-2"><XCircle className="h-4 w-4" />{error}</span>{lastText && <Button size="sm" variant="secondary" onClick={() => send(lastText)}>Retry</Button>}</div>}
            <div ref={endRef} />
          </div>

          <div className="sticky bottom-4 mt-6 rounded-2xl bg-white p-4 shadow-float">
            <div className="flex items-end gap-3">
              <button disabled title="Attachments are not available yet" aria-label="Attach (unavailable)" className="rounded-lg p-2 text-muted/50"><Paperclip className="h-4 w-4" /></button>
              <button disabled title="Voice input is not available yet" aria-label="Voice (unavailable)" className="rounded-lg p-2 text-muted/50"><Mic className="h-4 w-4" /></button>
              <textarea ref={inputRef} rows={1} value={input} onChange={(e) => setInput(e.target.value)} aria-label="Message Copilot" maxLength={4000}
                onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey || !e.shiftKey)) { e.preventDefault(); send(input); } }}
                placeholder="Type a command or instruction... (e.g., 'Check if Apex Cloud paid their invoice' or 'Show my revenue this month')"
                className="max-h-40 min-h-[40px] flex-1 resize-none bg-transparent py-2 text-sm outline-none placeholder:text-muted/60" />
              <Button onClick={() => send(input)} disabled={!input.trim()} loading={chat.isPending} icon={<ArrowUp className="h-4 w-4" />} className="shrink-0">Run Prompt</Button>
            </div>
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted"><span className="flex items-center gap-1 font-label font-semibold"><ShieldCheck className="h-3.5 w-3.5 text-primary" />Actions involving financial transfers or live invoices require your explicit one-click confirmation.</span><span className="font-label font-semibold">Enter to send</span></div>
          </div>
        </div>

        <aside className="flex min-w-0 flex-col gap-6">
          <Card className="p-5">
            <div className="flex items-center justify-between"><h2 className="flex items-center gap-2 font-semibold"><Zap className="h-4 w-4 text-primary" />Active AI Automations</h2><span className="rounded bg-surface px-2 py-0.5 font-label text-[11px] font-semibold">{running.length} running</span></div>
            <div className="mt-3 space-y-2">{running.length ? running.map((a) => (
              <div key={a.id} className="rounded-xl bg-surface-low p-3"><div className="flex items-center justify-between text-sm font-medium">{a.name}<span className="h-1.5 w-1.5 rounded-full bg-primary" /></div><div className="mt-1 text-xs text-muted">{a.description}</div><div className="mt-1 font-label text-[11px] text-muted">Last run: {a.last_executed_at ? `${relativeDay(a.last_executed_at)} ${fmtTime(a.last_executed_at)}` : "not yet"}</div></div>
            )) : <p className="rounded-xl bg-surface-low p-3 text-xs text-muted">No scheduled automations enabled. Turn some on in Automations.</p>}</div>
          </Card>
          <Card className="p-5">
            <h2 className="flex items-center gap-2 font-semibold"><History className="h-4 w-4 text-primary" />Recent AI Actions</h2>
            <ol className="mt-3 space-y-3 border-l-2 border-surface-high pl-4">{actions?.length ? actions.map((a) => {
              const st = a.tool_result?.status;
              return (
                <li key={a.id} className="relative"><span className={clsx("absolute -left-[22px] top-1 h-2.5 w-2.5 rounded-full", st === "executed" ? "bg-primary" : st === "failed" ? "bg-danger" : "bg-outline")} />
                  <div className="flex justify-between font-label text-[11px] text-primary"><span>{relativeDay(a.created_at)} · {fmtTime(a.created_at)}</span><span className="capitalize text-muted">{st}</span></div>
                  <div className="text-sm font-medium">{(a.tool_result?.preview?.title as string) ?? a.tool_name}</div>
                  <div className="text-xs text-muted">{a.tool_result?.preview?.customer?.name ?? a.tool_result?.preview?.description ?? ""}{a.tool_result?.preview?.total ? ` · ${money(a.tool_result.preview.total, a.tool_result.preview.currency)}` : ""}</div></li>);
            }) : <li className="text-xs text-muted">No AI actions yet.</li>}</ol>
          </Card>
          <Card className="p-5">
            <div className="flex items-center justify-between"><h2 className="flex items-center gap-2 font-semibold"><Bot className="h-4 w-4 text-primary" />Connected Capabilities</h2><Lock className="h-4 w-4 text-muted" /></div>
            <ul className="mt-3 space-y-1.5 text-xs">{[
              ["PayPal Orders v2 & links", !!settings?.integrations.paypal.configured], ["Razorpay (INR, test)", !!settings?.integrations.razorpay.configured],
              ["Customer & invoice ledger", true], [`LLM: ${settings?.integrations.ai.provider ?? "—"}`, aiReady],
            ].map(([l, ok]) => <li key={String(l)} className="flex items-center justify-between rounded-lg bg-surface-low px-3 py-2"><span className="flex items-center gap-2">{ok ? <CheckCircle2 className="h-3.5 w-3.5 text-primary" /> : <XCircle className="h-3.5 w-3.5 text-muted" />}{l}</span><span className={clsx("font-label font-semibold", ok ? "text-primary" : "text-muted")}>{ok ? "Active" : "Not set"}</span></li>)}</ul>
            <div className="mt-3 rounded-xl bg-surface-low p-3"><div className="label-caps">30-day success rate</div><div className="mt-1 font-display text-2xl font-semibold">{dash?.success_rate.value == null ? "—" : `${dash.success_rate.value}%`}</div></div>
          </Card>
        </aside>
      </div>
    </div>
  );
}
