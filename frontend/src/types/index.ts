export type Money = number;
export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface CustomerRef { id: string; name: string; email: string | null }
export interface Customer extends CustomerRef { phone: string | null; address: string | null; country: string | null; created_at: string }
export interface InvoiceItem { id: string; description: string; quantity: Money; unit_price: Money; total: Money }
export type InvoiceStatus = "draft" | "pending" | "paid" | "overdue" | "cancelled";
export interface Invoice {
  id: string; invoice_number: string; customer_id: string | null; customer: CustomerRef | null; description: string | null;
  subtotal: Money; tax: Money; total_amount: Money; currency: string; due_date: string | null; status: InvoiceStatus;
  payment_provider: "paypal" | "razorpay" | null; payment_link: string | null; items: InvoiceItem[]; created_at: string; updated_at: string;
}
export type TxStatus = "created" | "pending" | "completed" | "partially_refunded" | "refunded" | "failed" | "cancelled";
export interface Refund { id: string; provider_refund_id: string; amount: Money; currency: string; status: "pending" | "completed" | "failed"; source: string; reason: string | null; created_at: string }
export interface TxEvent { type: string; at: string; detail: string | null }
export interface Transaction {
  id: string; customer_id: string | null; customer: CustomerRef | null; invoice_id: string | null; invoice_number: string | null;
  provider: "paypal" | "razorpay"; provider_order_id: string | null; provider_capture_id: string | null; amount: Money; currency: string;
  status: TxStatus; payment_method: string | null; fee_amount?: Money | null; net_amount?: Money | null; failure_reason: string | null; events: TxEvent[];
  /** Sum of completed refunds; `amount` stays the original payment. */
  refunded_amount?: Money; refunds?: Refund[]; created_at: string; updated_at: string;
}
export interface PaymentLink {
  id: string; customer_id: string | null; customer: CustomerRef | null; invoice_id: string | null; provider: "paypal" | "razorpay";
  provider_reference: string | null; url: string; amount: Money; currency: string; description: string | null;
  status: "active" | "paid" | "expired" | "cancelled"; expires_at: string | null; created_at: string;
}
export interface Profile { id: string; email: string; full_name: string | null; business_name: string | null; avatar_url: string | null; default_currency: string; created_at: string }
export interface PayPalStatus { configured: boolean; mode: "sandbox" | "live"; webhook_configured: boolean; client_id_hint: string | null; connected: boolean; latency_ms: number | null; error: string | null; last_webhook_at: string | null }
export interface Insight { kind: string; title: string; tone: "positive" | "negative" | "warning" | "neutral"; text: string; action?: string }
export interface SuccessRate { value: number | null; previous: number | null; change_pct: number | null; failed_pct: number; refunded_pct: number }
export interface Dashboard {
  currency: string; currencies: string[]; days: number;
  revenue: { total: Money; previous: Money; change_pct: number | null; series: number[]; previous_series: number[]; granularity: "day" | "month"; start: string; daily_average: Money };
  transactions: { total: number; previous: number; change_pct: number | null; processed_today: number };
  pending: { total: Money; count: number; overdue_count: number; overdue_total: Money; earliest_aging_days: number | null };
  success_rate: SuccessRate; recent_transactions: Transaction[]; insights: Insight[]; paypal: PayPalStatus;
}
export interface Analytics {
  currency: string; days: number; revenue: Dashboard["revenue"]; success_rate: SuccessRate;
  volume: { total: number; by_status: Record<string, number>; series: number[] }; average_transaction_value: Money;
  top_customers: { customer: string; revenue: Money; payments: number }[]; revenue_by_provider: Record<string, Money>; insights: Insight[];
}
export interface Automation { id: string; name: string; description: string | null; automation_type: string; is_enabled: boolean; configuration: Record<string, unknown>; last_executed_at: string | null }
export interface Notification { id: string; title: string; message: string; notification_type: string; is_read: boolean; data: Record<string, unknown> | null; created_at: string }
export interface Settings {
  profile: Profile;
  integrations: { paypal: PayPalStatus; razorpay: { configured: boolean; mode: string }; ai: { provider: string; configured: boolean } };
}
export type ActionStatus = "prepared" | "approved" | "executed" | "failed" | "cancelled" | "read";
export interface ChatMessage {
  id: string; role: "user" | "assistant" | "tool"; content: string; tool_name: string | null; created_at: string;
  tool_result: null | { status: ActionStatus; args?: Record<string, unknown>; preview?: any; result?: any; error?: string };
}
export interface Conversation { id: string; title: string; created_at: string; updated_at: string }

export interface Bucket { total: Money; count: number }
export interface InvoiceSummary { currency: string; counts: Record<string, number>; total_invoiced: Money; paid: Bucket; pending: Bucket; overdue: Bucket }
export interface TxSummary { currency: string; total: { volume: Money; count: number }; settled: Bucket & { rate: number | null }; pending: Bucket; failed: Bucket; refunded: Bucket }
export interface LinkSummary { currency: string; total: number; active: number; paid: number; paid_volume: Money; conversion_rate: number | null }
export interface Activity { title: string; detail: string | null; at: string | null; done: boolean }
