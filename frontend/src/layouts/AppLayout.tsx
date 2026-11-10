import clsx from "clsx";
import { Bell, Bot, Calendar, ChevronDown, FileText, HelpCircle, LayoutGrid, Link2, LogOut, Menu, MoreVertical, Search, Settings as SettingsIcon, Sparkles, SquareTerminal, ArrowLeftRight, Users, BarChart3, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Avatar } from "@/components/ui";
import { useAuth } from "@/context/AuthContext";
import { useInvoices, useNotifications, useProfile, usePayPalStatus } from "@/hooks/queries";
import { useQueryClient } from "@tanstack/react-query";
import { post } from "@/lib/api";
import { timeAgo } from "@/utils/format";
import { RANGES, useRange } from "@/context/RangeContext";

type Item = { to: string; label: string; icon: typeof LayoutGrid; badge?: string; end?: boolean };

function NavItem({ item, onNavigate }: { item: Item; onNavigate: () => void }) {
  const Icon = item.icon;
  return (
    <NavLink to={item.to} end={item.end} onClick={onNavigate}
      className={({ isActive }) => clsx("flex items-center justify-between rounded-lg px-2 py-1.5 transition", isActive ? "bg-nav font-semibold text-nav-text" : "text-muted hover:bg-surface-low")}>
      {({ isActive }) => (
        <>
          <span className="flex items-center gap-2"><Icon className={clsx("h-4 w-4", isActive && "text-white")} /><span className={clsx(isActive ? "text-base text-white" : "text-sm")}>{item.label}</span></span>
          {item.badge && <span className={clsx("rounded px-1 py-0.5 font-label text-[11px] font-semibold", item.badge === "Copilot" ? "bg-primary-container text-primary-on" : "rounded-full bg-surface-high text-ink")}>{item.badge}</span>}
        </>
      )}
    </NavLink>
  );
}

function Sidebar({ onNavigate }: { onNavigate: () => void }) {
  const { signOut } = useAuth();
  const { data: profile } = useProfile();
  const { data: open } = useInvoices({ status: "pending", page_size: 1 });
  const { data: overdue } = useInvoices({ status: "overdue", page_size: 1 });
  const openCount = (open?.total ?? 0) + (overdue?.total ?? 0);
  const main: Item[] = [
    { to: "/", label: "Overview", icon: LayoutGrid, end: true },
    { to: "/assistant", label: "AI Assistant", icon: Sparkles, badge: "Copilot" },
    { to: "/invoices", label: "Invoices", icon: FileText, badge: openCount ? String(openCount) : undefined },
    { to: "/transactions", label: "Transactions", icon: ArrowLeftRight },
    { to: "/payment-links", label: "Payment Links", icon: Link2 },
    { to: "/customers", label: "Customers", icon: Users },
  ];
  const mgmt: Item[] = [{ to: "/analytics", label: "Analytics", icon: BarChart3 }, { to: "/automations", label: "Automations", icon: Bot }];
  const name = profile?.full_name || profile?.email || "Account";
  return (
    <div className="flex h-full flex-col justify-between bg-white shadow-aside">
      <div>
        <div className="flex h-16 items-center gap-2 px-6">
          <img src="/favicon.svg" alt="" className="h-8 w-8" />
          <div><div className="text-base font-semibold leading-4 tracking-tight">PayPilot</div><div className="label-caps mt-1 !text-primary">AI Payment Ops</div></div>
        </div>
        <nav className="flex flex-col gap-1 px-4 py-2" aria-label="Main">
          <div className="label-caps px-2 py-1">Main</div>
          {main.map((i) => <NavItem key={i.to} item={i} onNavigate={onNavigate} />)}
        </nav>
        <nav className="flex flex-col gap-1 px-4 py-2" aria-label="Management">
          <div className="label-caps px-2 py-1">Management</div>
          {mgmt.map((i) => <NavItem key={i.to} item={i} onNavigate={onNavigate} />)}
        </nav>
      </div>
      <div className="flex flex-col gap-2 p-4">
        <NavItem item={{ to: "/settings", label: "Settings", icon: SettingsIcon }} onNavigate={onNavigate} />
        <a href="https://github.com/Pranav-Raj-05/PayPilot#readme" target="_blank" rel="noreferrer" className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-muted hover:bg-surface-low"><HelpCircle className="h-4 w-4" />Help &amp; Support</a>
        <div className="flex items-center justify-between rounded-xl bg-surface-low p-2">
          <div className="flex min-w-0 items-center gap-2">
            <span className="relative"><Avatar name={name} size={32} /><span className="absolute bottom-0 right-0 h-2 w-2 rounded-full bg-primary ring-2 ring-white" /></span>
            <div className="min-w-0"><div className="truncate font-label text-[13px] font-semibold">{name}</div><div className="truncate font-label text-[11px] font-semibold text-muted">{profile?.business_name || "Founder"}</div></div>
          </div>
          <button onClick={() => signOut()} aria-label="Sign out" title="Sign out" className="rounded p-1 text-muted hover:bg-white"><LogOut className="h-4 w-4" /></button>
        </div>
      </div>
    </div>
  );
}

function NotificationsMenu() {
  const { data } = useNotifications();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  return (
    <div className="relative" ref={ref}>
      <button aria-label="Notifications" onClick={() => setOpen((o) => !o)} className="relative rounded-lg p-1.5 text-muted hover:bg-surface-low">
        <Bell className="h-4 w-4" />
        {!!data?.unread && <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-danger ring-2 ring-white" />}
      </button>
      {open && (
        <div className="absolute right-0 z-40 mt-2 w-80 overflow-hidden rounded-2xl bg-white shadow-float">
          <div className="flex items-center justify-between px-4 py-3"><span className="font-semibold">Notifications</span>
            <button className="text-xs text-primary" onClick={async () => { await post("/api/notifications/read-all"); qc.invalidateQueries({ queryKey: ["notifications"] }); }}>Mark all read</button></div>
          <div className="max-h-80 overflow-y-auto">
            {data?.items.length ? data.items.map((n) => (
              <div key={n.id} className={clsx("border-t border-surface-low px-4 py-3 text-sm", !n.is_read && "bg-surface-low/60")}>
                <div className="font-medium">{n.title}</div><div className="text-xs text-muted">{n.message}</div>
                <div className="mt-1 text-[11px] text-muted">{timeAgo(n.created_at)}</div>
              </div>
            )) : <div className="px-4 py-8 text-center text-sm text-muted">You're all caught up.</div>}
          </div>
        </div>
      )}
    </div>
  );
}

const CRUMBS: Record<string, string> = { "": "Operations", assistant: "AI Assistant", invoices: "Invoices", transactions: "Transactions", "payment-links": "Payment Links", customers: "Customers", analytics: "Analytics", automations: "Automations", settings: "Settings" };

export default function AppLayout() {
  const [drawer, setDrawer] = useState(false);
  const [q, setQ] = useState("");
  const loc = useLocation();
  const nav = useNavigate();
  const { data: pp } = usePayPalStatus();
  const { data: profile } = useProfile();
  const { days, setDays } = useRange();
  const crumb = CRUMBS[loc.pathname.split("/")[1] ?? ""] ?? "Operations";
  useEffect(() => setDrawer(false), [loc.pathname]);
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); document.getElementById("global-search")?.focus(); } };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  const submit = () => { const t = q.trim(); if (t) nav(`/transactions?search=${encodeURIComponent(t)}`); };
  const sandbox = pp?.mode !== "live";
  return (
    <div className="min-h-screen bg-canvas">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 lg:block"><Sidebar onNavigate={() => {}} /></aside>
      {drawer && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-nav/50" onClick={() => setDrawer(false)} />
          <div className="absolute inset-y-0 left-0 w-64"><Sidebar onNavigate={() => setDrawer(false)} /></div>
          <button aria-label="Close menu" className="absolute left-[17rem] top-4 rounded-lg bg-white p-2" onClick={() => setDrawer(false)}><X className="h-4 w-4" /></button>
        </div>
      )}
      <div className="lg:pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 bg-white/90 px-4 shadow-header backdrop-blur-2xl sm:px-6">
          <button className="rounded-lg p-1.5 hover:bg-surface-low lg:hidden" aria-label="Open menu" onClick={() => setDrawer(true)}><Menu className="h-5 w-5" /></button>
          <div className="hidden items-center gap-2 font-label text-xs sm:flex"><SquareTerminal className="h-3.5 w-3.5" /><span className="text-muted">paypilot</span><span className="text-muted">/</span><span className="font-medium">{crumb}</span></div>
          <div className="relative mx-auto min-w-0 max-w-md flex-1 md:px-6">
            <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-muted md:left-9" />
            <input id="global-search" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && submit()} aria-label="Search transactions"
              placeholder="Search transactions, invoices, or prompt AI..." className="h-9 w-full rounded-lg bg-surface-low pl-9 pr-14 text-xs outline-none ring-1 ring-transparent placeholder:text-muted/70 focus:ring-primary" />
            <kbd className="absolute right-2 top-2 hidden rounded bg-surface-high px-1.5 py-0.5 font-label text-[10px] text-muted md:block md:right-8">⌘K</kbd>
          </div>
          <div className="flex items-center gap-3">
            <span className="hidden items-center gap-1 rounded-full bg-primary-container px-2 py-1 font-label text-[11px] font-semibold text-primary-on sm:inline-flex">
              <span className={clsx("h-1.5 w-1.5 rounded-full", pp?.configured ? "bg-primary" : "bg-danger")} />{pp?.configured ? (sandbox ? "PayPal Sandbox" : "PayPal Live") : "PayPal not configured"}
            </span>
            <label className="relative hidden items-center gap-1 rounded-lg bg-surface-low px-2 py-1 text-xs md:inline-flex">
              <Calendar className="h-3.5 w-3.5" />
              <select aria-label="Date range" value={days} onChange={(e) => setDays(Number(e.target.value))} className="cursor-pointer appearance-none bg-transparent pr-4 outline-none">
                {RANGES.map((r) => <option key={r.value} value={r.value}>{r.long}</option>)}
              </select>
              <ChevronDown className="pointer-events-none absolute right-2 h-3 w-3" />
            </label>
            <NotificationsMenu />
            <button onClick={() => nav("/settings")} aria-label="Account settings" className="flex items-center gap-1"><Avatar name={profile?.full_name || profile?.email} size={32} /><MoreVertical className="hidden h-4 w-4 text-muted" /></button>
          </div>
        </header>
        <main className="mx-auto max-w-[1440px] p-4 sm:p-6"><Outlet /></main>
      </div>
    </div>
  );
}
