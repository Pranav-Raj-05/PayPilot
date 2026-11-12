import type { Analytics, Automation, ChatMessage, Customer, Dashboard, Invoice, Page, PayPalStatus, PaymentLink, Profile, Settings, Transaction } from "@/types";

export const page = <T,>(items: T[]): Page<T> => ({ items, total: items.length, page: 1, page_size: 20 });

export const customer = (o: Partial<Customer> = {}): Customer =>
  ({ id: "c1", name: "Sarah Johnson", email: "sarah@example.com", phone: null, address: null, country: "US", created_at: "2026-01-01T00:00:00Z", ...o });

export const invoice = (o: Partial<Invoice> = {}): Invoice => ({
  id: "inv1", invoice_number: "INV-0001", customer_id: "c1", customer: { id: "c1", name: "Sarah Johnson", email: "sarah@example.com" }, description: "Website",
  subtotal: 100, tax: 10, total_amount: 110, currency: "USD", due_date: "2026-02-01", status: "draft", payment_provider: null, payment_link: null,
  items: [{ id: "i1", description: "Design", quantity: 2, unit_price: 50, total: 100 }], created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", ...o,
});

export const link = (o: Partial<PaymentLink> = {}): PaymentLink => ({
  id: "link-0001-aaaa", customer_id: null, customer: null, invoice_id: null, provider: "paypal", provider_reference: "ORD1", url: "https://pay.example/l/1", amount: 120,
  currency: "USD", description: "Consulting", status: "active", expires_at: null, created_at: "2026-01-01T10:00:00Z", ...o,
});

export const tx = (o: Partial<Transaction> = {}): Transaction => ({
  id: "t1", customer_id: "c1", customer: { id: "c1", name: "Sarah Johnson", email: null }, invoice_id: null, invoice_number: null, provider: "paypal", provider_order_id: "ORDERABC12345",
  provider_capture_id: null, amount: 250, currency: "USD", status: "completed", payment_method: "card", failure_reason: null, events: [], created_at: "2026-01-01T10:00:00Z", updated_at: "2026-01-01T10:00:00Z", ...o,
});

export const paypal = (o: Partial<PayPalStatus> = {}): PayPalStatus =>
  ({ configured: true, mode: "sandbox", webhook_configured: true, client_id_hint: "AbC…xyz", connected: true, latency_ms: 120, error: null, last_webhook_at: null, ...o });

export const profile = (o: Partial<Profile> = {}): Profile =>
  ({ id: "u1", email: "me@example.com", full_name: "Pranav Raj", business_name: "Acme", avatar_url: null, default_currency: "USD", created_at: "2026-01-01T00:00:00Z", ...o });

const revenue = (o: Partial<Dashboard["revenue"]> = {}): Dashboard["revenue"] =>
  ({ total: 12345, previous: 10000, change_pct: 23.5, series: [100, 200, 300], previous_series: [50, 100, 150], granularity: "day", start: "2026-01-01", daily_average: 411.5, ...o });

export const dashboard = (o: Partial<Dashboard> = {}): Dashboard => ({
  currency: "USD", currencies: ["USD"], days: 30, revenue: revenue(), transactions: { total: 42, previous: 30, change_pct: 40, processed_today: 3 },
  pending: { total: 800, count: 2, overdue_count: 1, overdue_total: 300, earliest_aging_days: 9 }, success_rate: { value: 96.5, previous: 90, change_pct: 6.5, failed_pct: 2, refunded_pct: 1.5 },
  recent_transactions: [tx()], insights: [], paypal: paypal(), ...o,
});

export const analytics = (o: Partial<Analytics> = {}): Analytics => ({
  currency: "USD", days: 30, revenue: revenue(), success_rate: { value: 96.5, previous: null, change_pct: null, failed_pct: 2, refunded_pct: 1.5 },
  volume: { total: 42, by_status: { completed: 40, failed: 2 }, series: [1, 2, 3] }, average_transaction_value: 293.93,
  top_customers: [{ customer: "Apex Cloud", revenue: 5000, payments: 4 }], revenue_by_provider: { paypal: 9000, razorpay: 3345 }, insights: [], ...o,
});

export const automation = (o: Partial<Automation> = {}): Automation =>
  ({ id: "a1", name: "Overdue reminders", description: "Remind late payers", automation_type: "overdue_reminders", is_enabled: true, configuration: {}, last_executed_at: null, ...o });

export const settings = (ai: Partial<Settings["integrations"]["ai"]> = {}): Settings => ({
  profile: profile(), integrations: { paypal: paypal(), razorpay: { configured: false, mode: "test" }, ai: { provider: "gemini", configured: true, ...ai } },
});

export const msg = (o: Partial<ChatMessage> = {}): ChatMessage =>
  ({ id: "m1", role: "assistant", content: "Hello!", tool_name: null, tool_result: null, created_at: "2026-01-01T10:00:00Z", ...o });

export const preparedInvoice = (o: Partial<ChatMessage> = {}): ChatMessage => msg({
  id: "tool1", role: "tool", content: "", tool_name: "create_invoice",
  tool_result: {
    status: "prepared", args: { customer_name: "Sarah Johnson", amount: 500, currency: "USD" },
    preview: {
      title: "Create invoice for Sarah Johnson", provider: "paypal", mode: "sandbox", customer: { name: "Sarah Johnson", email: "sarah@example.com", existing: true }, terms: "Net 14",
      due_date: "2026-02-01", items: [{ description: "Website development", quantity: 1, unit_price: 500, total: 500 }], currency: "USD", subtotal: 500, tax_rate: 0, tax: 0, total: 500,
      checks: [{ ok: true, text: "Customer matched" }],
    },
  }, ...o,
});
