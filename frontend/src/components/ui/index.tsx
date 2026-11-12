import clsx from "clsx";
import { AlertTriangle, Check, ChevronLeft, ChevronRight, Copy, Loader2, X } from "lucide-react";
import { forwardRef, useEffect, useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { useToast } from "@/context/ToastContext";
import { initials } from "@/utils/format";

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger" | "dark"; size?: "sm" | "md"; loading?: boolean; icon?: ReactNode };
export function Button({ variant = "primary", size = "md", loading, icon, className, children, disabled, ...rest }: BtnProps) {
  const styles = {
    primary: "bg-primary text-white hover:bg-primary-dark",
    secondary: "bg-surface text-ink hover:bg-surface-high",
    ghost: "bg-transparent text-muted hover:bg-surface-low",
    danger: "bg-danger-container text-danger-dark hover:bg-red-200",
    dark: "bg-nav text-white hover:bg-ink",
  }[variant];
  return (
    <button {...rest} disabled={disabled || loading}
      className={clsx("inline-flex items-center justify-center gap-1.5 rounded-xl font-medium shadow-card transition disabled:cursor-not-allowed disabled:opacity-60",
        size === "sm" ? "px-3 py-1.5 text-xs" : "px-4 py-2.5 text-sm", styles, className)}>
      {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
      {children}
    </button>
  );
}

export const Card = ({ className, children }: { className?: string; children: ReactNode }) => <section className={clsx("card", className)}>{children}</section>;

const STATUS: Record<string, string> = {
  completed: "bg-emerald-100 text-success", paid: "bg-emerald-100 text-success", active: "bg-emerald-100 text-success", executed: "bg-emerald-100 text-success",
  pending: "bg-amber-100 text-amber-ink", created: "bg-slate-100 text-muted", draft: "bg-slate-100 text-muted", prepared: "bg-primary-container text-primary-on",
  approved: "bg-primary-container text-primary-on", overdue: "bg-danger-container text-danger-dark", failed: "bg-danger-container text-danger-dark",
  refunded: "bg-slate-200 text-muted", partially_refunded: "bg-amber-100 text-amber-ink", cancelled: "bg-slate-200 text-muted", expired: "bg-slate-200 text-muted",
};
const DOT: Record<string, string> = { completed: "bg-success-bright", paid: "bg-success-bright", active: "bg-success-bright", pending: "bg-amber-glow", overdue: "bg-danger", failed: "bg-danger", refunded: "bg-outline" };
const STATUS_LABEL: Record<string, string> = { partially_refunded: "partially refunded" };
export const StatusBadge = ({ status, label }: { status: string; label?: string }) => (
  <span className={clsx("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-label text-[11px] font-semibold capitalize", STATUS[status] ?? "bg-slate-100 text-muted")}>
    <span className={clsx("h-1.5 w-1.5 rounded-full", DOT[status] ?? "bg-outline")} />
    {label ?? STATUS_LABEL[status] ?? status}
  </span>
);

export const Avatar = ({ name, size = 40 }: { name?: string | null; size?: number }) => (
  <span style={{ width: size, height: size }} className="inline-flex shrink-0 items-center justify-center rounded-full bg-surface-high font-label text-xs font-semibold text-primary-on">{initials(name)}</span>
);

export function Field({ label, error, children, hint }: { label: string; error?: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="label-caps mb-1.5 block">{label}</span>
      {children}
      {hint && !error && <span className="mt-1 block text-xs text-muted">{hint}</span>}
      {error && <span role="alert" className="mt-1 block text-xs text-danger">{error}</span>}
    </label>
  );
}
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(({ className, ...p }, ref) => <input ref={ref} {...p} className={clsx("input", className)} />);
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...p }, ref) => <textarea ref={ref} {...p} className={clsx("input min-h-[84px]", className)} />);
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(({ className, ...p }, ref) => <select ref={ref} {...p} className={clsx("input", className)} />);

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-nav/50 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label={title} className={clsx("max-h-[90vh] w-full overflow-y-auto rounded-2xl bg-white p-6 shadow-float", wide ? "max-w-3xl" : "max-w-lg")}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-xl font-semibold">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="rounded-lg p-1 text-muted hover:bg-surface-low"><X className="h-5 w-5" /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Drawer({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-nav/40" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside role="dialog" aria-modal="true" aria-label={title} className="h-full w-full max-w-md overflow-y-auto bg-white p-6 shadow-float">
        <div className="mb-5 flex items-center justify-between">
          <h2 className="text-xl font-semibold">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="rounded-lg p-1 text-muted hover:bg-surface-low"><X className="h-5 w-5" /></button>
        </div>
        {children}
      </aside>
    </div>
  );
}

export const Skeleton = ({ className }: { className?: string }) => <div className={clsx("animate-pulse rounded-xl bg-surface", className)} />;

export function EmptyState({ icon, title, text, action }: { icon?: ReactNode; title: string; text?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center">
      {icon && <div className="mb-3 rounded-2xl bg-surface-low p-3 text-primary">{icon}</div>}
      <h3 className="text-lg font-semibold">{title}</h3>
      {text && <p className="mt-1 max-w-sm text-sm text-muted">{text}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-10 text-center" role="alert">
      <AlertTriangle className="h-8 w-8 text-danger" />
      <p className="text-sm text-muted">{(error as Error)?.message || "Something went wrong."}</p>
      {onRetry && <Button variant="secondary" size="sm" onClick={onRetry}>Retry</Button>}
    </div>
  );
}

export function CopyButton({ text, label = "Copy", className }: { text: string; label?: string; className?: string }) {
  const [done, setDone] = useState(false);
  const { toast } = useToast();
  return (
    <button type="button" className={clsx("inline-flex items-center gap-1 rounded-lg bg-white px-2 py-1 text-xs font-medium shadow-card hover:bg-surface-low", className)}
      onClick={async () => { try { await navigator.clipboard.writeText(text); setDone(true); toast("Link copied"); setTimeout(() => setDone(false), 1500); } catch { toast("Copy failed", "error"); } }}>
      {done ? <Check className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />} {done ? "Copied" : label}
    </button>
  );
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  return (
    <div className="flex items-center justify-between px-6 py-4 text-sm text-muted">
      <span>Showing {from}–{Math.min(page * pageSize, total)} of {total.toLocaleString()}</span>
      <div className="flex items-center gap-1">
        <button aria-label="Previous page" disabled={page <= 1} onClick={() => onPage(page - 1)} className="rounded-lg p-1.5 hover:bg-surface-low disabled:opacity-40"><ChevronLeft className="h-4 w-4" /></button>
        <span className="px-2 font-label text-xs">{page} / {pages}</span>
        <button aria-label="Next page" disabled={page >= pages} onClick={() => onPage(page + 1)} className="rounded-lg p-1.5 hover:bg-surface-low disabled:opacity-40"><ChevronRight className="h-4 w-4" /></button>
      </div>
    </div>
  );
}

export function Segmented<T extends string | number>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div role="tablist" className="inline-flex rounded-xl bg-surface-low p-1">
      {options.map((o) => (
        <button key={String(o.value)} role="tab" aria-selected={o.value === value} onClick={() => onChange(o.value)}
          className={clsx("rounded-lg px-3 py-1 font-label text-xs font-semibold transition", o.value === value ? "bg-white text-ink shadow-card" : "text-muted hover:text-ink")}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export const PageHeader = ({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) => (
  <div className="flex flex-wrap items-end justify-between gap-4">
    <div>
      <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">{title}</h1>
      {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
    </div>
    {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
  </div>
);
