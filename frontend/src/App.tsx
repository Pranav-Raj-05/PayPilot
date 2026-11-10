import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { isSupabaseConfigured } from "@/lib/supabase";
import AppLayout from "@/layouts/AppLayout";
import { Skeleton } from "@/components/ui";
import Overview from "@/pages/Overview";
import { ForgotPassword, Login, Register, ResetPassword } from "@/pages/Auth";

const Assistant = lazy(() => import("@/pages/Assistant"));
const Transactions = lazy(() => import("@/pages/Transactions"));
const Invoices = lazy(() => import("@/pages/Invoices"));
const InvoiceEditor = lazy(() => import("@/pages/InvoiceEditor"));
const PaymentLinks = lazy(() => import("@/pages/PaymentLinks"));
const Customers = lazy(() => import("@/pages/Customers"));
const Analytics = lazy(() => import("@/pages/Analytics"));
const Automations = lazy(() => import("@/pages/Automations"));
const Settings = lazy(() => import("@/pages/Settings"));
const { PayReturn, PayCancelled, PayRazorpay } = { PayReturn: lazy(() => import("@/pages/Pay").then((m) => ({ default: m.PayReturn }))), PayCancelled: lazy(() => import("@/pages/Pay").then((m) => ({ default: m.PayCancelled }))), PayRazorpay: lazy(() => import("@/pages/Pay").then((m) => ({ default: m.PayRazorpay }))) };

function Protected({ children }: { children: ReactNode }) {
  const { session, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="p-8"><Skeleton className="h-10 w-64" /></div>;
  if (!session) return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
  return <>{children}</>;
}

function ConfigMissing() {
  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="card max-w-lg p-8">
        <h1 className="text-2xl font-semibold">Supabase is not configured</h1>
        <p className="mt-2 text-sm text-muted">Copy <code>frontend/.env.example</code> to <code>frontend/.env</code> and set <code>VITE_SUPABASE_URL</code> and <code>VITE_SUPABASE_ANON_KEY</code>, then restart the dev server.</p>
      </div>
    </div>
  );
}

export default function App() {
  if (!isSupabaseConfigured) return <ConfigMissing />;
  return (
    <Suspense fallback={<div className="p-8"><Skeleton className="h-10 w-64" /></div>}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        {/* Customer-facing checkout pages (no login) */}
        <Route path="/pay/return" element={<PayReturn />} />
        <Route path="/pay/cancelled" element={<PayCancelled />} />
        <Route path="/pay/razorpay/:linkId" element={<PayRazorpay />} />
        <Route element={<Protected><AppLayout /></Protected>}>
          <Route index element={<Overview />} />
          <Route path="assistant" element={<Assistant />} />
          <Route path="transactions" element={<Transactions />} />
          <Route path="invoices" element={<Invoices />} />
          <Route path="invoices/new" element={<InvoiceEditor />} />
          <Route path="invoices/:id" element={<InvoiceEditor />} />
          <Route path="payment-links" element={<PaymentLinks />} />
          <Route path="customers" element={<Customers />} />
          <Route path="analytics" element={<Analytics />} />
          <Route path="automations" element={<Automations />} />
          <Route path="settings" element={<Settings />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
