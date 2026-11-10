import { zodResolver } from "@hookform/resolvers/zod";
import { Sparkles, ShieldCheck, Zap } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { z } from "zod";
import { Button, Field, Input } from "@/components/ui";
import { useAuth } from "@/context/AuthContext";

function Shell({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <div className="relative hidden flex-col justify-between overflow-hidden bg-nav p-12 text-white lg:flex">
        <div className="flex items-center gap-2"><img src="/favicon.svg" alt="" className="h-9 w-9" /><div><div className="font-semibold leading-4">PayPilot</div><div className="label-caps !text-primary-container">AI Payment Ops</div></div></div>
        <div>
          <h2 className="font-display text-4xl font-semibold leading-tight tracking-tight">Run your payments<br />in plain English.</h2>
          <ul className="mt-8 space-y-4 text-sm text-white/80">
            <li className="flex gap-3"><Sparkles className="h-5 w-5 shrink-0 text-primary-container" />Tell Copilot "Create a $500 invoice for Sarah" and approve the preview.</li>
            <li className="flex gap-3"><Zap className="h-5 w-5 shrink-0 text-primary-container" />Real PayPal checkout, verified server-side before anything is marked paid.</li>
            <li className="flex gap-3"><ShieldCheck className="h-5 w-5 shrink-0 text-primary-container" />Every AI action needs your explicit approval and is audit-logged.</li>
          </ul>
        </div>
        <p className="text-xs text-white/50">Runs against PayPal Sandbox by default. No real money moves in test mode.</p>
        <div className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full bg-primary/30 blur-3xl" />
      </div>
      <div className="flex items-center justify-center p-6">
        <div className="w-full max-w-sm">
          <h1 className="text-3xl font-semibold tracking-tight">{title}</h1>
          <p className="mb-6 mt-1 text-sm text-muted">{subtitle}</p>
          {children}
        </div>
      </div>
    </div>
  );
}

const loginSchema = z.object({ email: z.string().email("Enter a valid email"), password: z.string().min(1, "Enter your password") });
export function Login() {
  const { signIn, session } = useAuth();
  const nav = useNavigate();
  const loc = useLocation() as { state?: { from?: string } };
  const [err, setErr] = useState("");
  const f = useForm<z.infer<typeof loginSchema>>({ resolver: zodResolver(loginSchema) });
  if (session) return <Navigate to={loc.state?.from || "/"} replace />;
  return (
    <Shell title="Welcome back" subtitle="Sign in to your PayPilot workspace.">
      <form className="space-y-4" onSubmit={f.handleSubmit(async (v) => { setErr(""); try { await signIn(v.email, v.password); nav(loc.state?.from || "/", { replace: true }); } catch (e) { setErr((e as Error).message); } })}>
        <Field label="Email" error={f.formState.errors.email?.message}><Input type="email" autoComplete="email" {...f.register("email")} /></Field>
        <Field label="Password" error={f.formState.errors.password?.message}><Input type="password" autoComplete="current-password" {...f.register("password")} /></Field>
        {err && <p role="alert" className="rounded-xl bg-danger-container px-3 py-2 text-sm text-danger-dark">{err}</p>}
        <Button type="submit" className="w-full" loading={f.formState.isSubmitting}>Sign in</Button>
      </form>
      <div className="mt-4 flex justify-between text-sm"><Link className="text-primary" to="/forgot-password">Forgot password?</Link><Link className="text-primary" to="/register">Create account</Link></div>
    </Shell>
  );
}

const regSchema = z.object({
  full_name: z.string().min(2, "Enter your name"), business_name: z.string().optional(), email: z.string().email("Enter a valid email"),
  password: z.string().min(8, "At least 8 characters"), confirm: z.string(),
}).refine((v) => v.password === v.confirm, { path: ["confirm"], message: "Passwords do not match" });
export function Register() {
  const { signUp } = useAuth();
  const nav = useNavigate();
  const [err, setErr] = useState("");
  const [sent, setSent] = useState(false);
  const f = useForm<z.infer<typeof regSchema>>({ resolver: zodResolver(regSchema) });
  if (sent) return <Shell title="Check your inbox" subtitle="We sent a verification link. Confirm your email, then sign in."><Link to="/login" className="text-primary">Back to sign in</Link></Shell>;
  return (
    <Shell title="Create your account" subtitle="Start collecting payments with an AI copilot.">
      <form className="space-y-4" onSubmit={f.handleSubmit(async (v) => {
        setErr("");
        try { const r = await signUp(v.email, v.password, { full_name: v.full_name, business_name: v.business_name }); r.needsVerification ? setSent(true) : nav("/", { replace: true }); }
        catch (e) { setErr((e as Error).message); }
      })}>
        <Field label="Full name" error={f.formState.errors.full_name?.message}><Input autoComplete="name" {...f.register("full_name")} /></Field>
        <Field label="Business name (optional)"><Input autoComplete="organization" {...f.register("business_name")} /></Field>
        <Field label="Email" error={f.formState.errors.email?.message}><Input type="email" autoComplete="email" {...f.register("email")} /></Field>
        <Field label="Password" error={f.formState.errors.password?.message}><Input type="password" autoComplete="new-password" {...f.register("password")} /></Field>
        <Field label="Confirm password" error={f.formState.errors.confirm?.message}><Input type="password" autoComplete="new-password" {...f.register("confirm")} /></Field>
        {err && <p role="alert" className="rounded-xl bg-danger-container px-3 py-2 text-sm text-danger-dark">{err}</p>}
        <Button type="submit" className="w-full" loading={f.formState.isSubmitting}>Create account</Button>
      </form>
      <p className="mt-4 text-sm text-muted">Already have an account? <Link className="text-primary" to="/login">Sign in</Link></p>
    </Shell>
  );
}

export function ForgotPassword() {
  const { requestReset } = useAuth();
  const [done, setDone] = useState(false);
  const [err, setErr] = useState("");
  const f = useForm<{ email: string }>({ resolver: zodResolver(z.object({ email: z.string().email("Enter a valid email") })) });
  return (
    <Shell title="Reset your password" subtitle="We'll email you a secure reset link.">
      {done ? <p className="rounded-xl bg-surface-low p-4 text-sm">If an account exists for that email, a reset link is on its way.</p> : (
        <form className="space-y-4" onSubmit={f.handleSubmit(async (v) => { setErr(""); try { await requestReset(v.email); setDone(true); } catch (e) { setErr((e as Error).message); } })}>
          <Field label="Email" error={f.formState.errors.email?.message}><Input type="email" {...f.register("email")} /></Field>
          {err && <p role="alert" className="text-sm text-danger">{err}</p>}
          <Button type="submit" className="w-full" loading={f.formState.isSubmitting}>Send reset link</Button>
        </form>
      )}
      <p className="mt-4 text-sm"><Link className="text-primary" to="/login">Back to sign in</Link></p>
    </Shell>
  );
}

export function ResetPassword() {
  const { updatePassword, session } = useAuth();
  const nav = useNavigate();
  const [err, setErr] = useState("");
  const f = useForm<{ password: string }>({ resolver: zodResolver(z.object({ password: z.string().min(8, "At least 8 characters") })) });
  return (
    <Shell title="Choose a new password" subtitle={session ? "Enter your new password below." : "Open the reset link from your email to continue."}>
      <form className="space-y-4" onSubmit={f.handleSubmit(async (v) => { setErr(""); try { await updatePassword(v.password); nav("/", { replace: true }); } catch (e) { setErr((e as Error).message); } })}>
        <Field label="New password" error={f.formState.errors.password?.message}><Input type="password" autoComplete="new-password" disabled={!session} {...f.register("password")} /></Field>
        {err && <p role="alert" className="text-sm text-danger">{err}</p>}
        <Button type="submit" className="w-full" disabled={!session} loading={f.formState.isSubmitting}>Update password</Button>
      </form>
    </Shell>
  );
}
