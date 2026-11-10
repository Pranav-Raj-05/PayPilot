import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { del, get, patch, post } from "@/lib/api";
import type { Analytics, Automation, ChatMessage, Conversation, Customer, Dashboard, Invoice, Notification, Page, PayPalStatus, PaymentLink, Profile, Settings, Transaction } from "@/types";

export const useDashboard = (days: number, currency?: string) =>
  useQuery({ queryKey: ["dashboard", days, currency], queryFn: () => get<Dashboard>("/api/dashboard", { days, currency }), refetchInterval: 30_000 });
export const useAnalytics = (days: number, currency?: string) =>
  useQuery({ queryKey: ["analytics", days, currency], queryFn: () => get<Analytics>("/api/analytics", { days, currency }) });
export const useTransactions = (params: Record<string, unknown>) =>
  useQuery({ queryKey: ["transactions", params], queryFn: () => get<Page<Transaction>>("/api/transactions", params), placeholderData: keepPreviousData });
export const useInvoices = (params: Record<string, unknown>) =>
  useQuery({ queryKey: ["invoices", params], queryFn: () => get<Page<Invoice>>("/api/invoices", params), placeholderData: keepPreviousData });
export const useInvoice = (id?: string) => useQuery({ queryKey: ["invoice", id], queryFn: () => get<Invoice>(`/api/invoices/${id}`), enabled: !!id });
export const useCustomers = (search?: string) =>
  useQuery({ queryKey: ["customers", search], queryFn: () => get<Page<Customer>>("/api/customers", { search, page_size: 100 }) });
export const usePaymentLinks = () => useQuery({ queryKey: ["links"], queryFn: () => get<PaymentLink[]>("/api/payment-links") });
export const useAutomations = () => useQuery({ queryKey: ["automations"], queryFn: () => get<Automation[]>("/api/automations") });
export const useNotifications = () =>
  useQuery({ queryKey: ["notifications"], queryFn: () => get<{ unread: number; items: Notification[] }>("/api/notifications"), refetchInterval: 30_000 });
export const useSettings = () => useQuery({ queryKey: ["settings"], queryFn: () => get<Settings>("/api/settings") });
export const useProfile = () => useQuery({ queryKey: ["me"], queryFn: () => get<Profile>("/api/auth/me"), staleTime: 5 * 60_000 });
export const usePayPalStatus = () => useQuery({ queryKey: ["paypal-status"], queryFn: () => get<PayPalStatus>("/api/payments/paypal/status"), staleTime: 60_000 });
export const useRecentActions = () => useQuery({ queryKey: ["ai-actions"], queryFn: () => get<ChatMessage[]>("/api/ai/actions", { limit: 6 }), refetchInterval: 30_000 });
export const useConversations = () => useQuery({ queryKey: ["conversations"], queryFn: () => get<Conversation[]>("/api/ai/conversations") });

function useInvalidating<T, V>(fn: (v: V) => Promise<T>, keys: string[]) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => keys.forEach((k) => qc.invalidateQueries({ queryKey: [k] })) });
}
const ALL_MONEY = ["dashboard", "analytics", "transactions", "invoices", "invoice", "links", "notifications"];
export const useCreateInvoice = () => useInvalidating((b: object) => post<Invoice>("/api/invoices", b), ALL_MONEY);
export const useUpdateInvoice = () => useInvalidating(({ id, ...b }: { id: string } & Record<string, unknown>) => patch<Invoice>(`/api/invoices/${id}`, b), ALL_MONEY);
export const useDeleteInvoice = () => useInvalidating((id: string) => del(`/api/invoices/${id}`), ALL_MONEY);
export const useInvoiceLink = () => useInvalidating(({ id, provider }: { id: string; provider: string }) => post<PaymentLink>(`/api/invoices/${id}/payment-link`, {}, { provider }), ALL_MONEY);
export const useCreateLink = () => useInvalidating((b: object) => post<PaymentLink>("/api/payment-links", b), ALL_MONEY);
export const useCancelLink = () => useInvalidating((id: string) => post<PaymentLink>(`/api/payment-links/${id}/cancel`), ["links"]);
export const useCreateCustomer = () => useInvalidating((b: object) => post<Customer>("/api/customers", b), ["customers"]);
export const useUpdateCustomer = () => useInvalidating(({ id, ...b }: { id: string } & Record<string, unknown>) => patch<Customer>(`/api/customers/${id}`, b), ["customers"]);
export const useDeleteCustomer = () => useInvalidating((id: string) => del(`/api/customers/${id}`), ["customers"]);
export const useUpdateAutomation = () => useInvalidating(({ id, ...b }: { id: string } & Record<string, unknown>) => patch<Automation>(`/api/automations/${id}`, b), ["automations"]);
export const useRunAutomation = () => useInvalidating((id: string) => post<{ result: string }>(`/api/automations/${id}/run`), ["automations", "notifications"]);
export const useUpdateProfile = () => useInvalidating((b: object) => patch<Profile>("/api/settings/profile", b), ["settings", "me"]);
