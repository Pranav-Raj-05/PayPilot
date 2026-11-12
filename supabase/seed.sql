-- Development seed data. Replace the UUID with a real auth user id (Supabase Dashboard > Authentication > Users),
-- then run in the SQL editor:   select seed_paypilot_demo('<your-user-uuid>');
-- All records are fictitious sandbox-style demo data; no payment provider is contacted.
create or replace function public.seed_paypilot_demo(p_user uuid) returns void language plpgsql as $$
declare c1 uuid; c2 uuid; c3 uuid; i1 uuid; i2 uuid; i3 uuid;
begin
  insert into public.profiles (id, email, full_name, business_name) values (p_user, 'demo@example.com', 'Alex Vance', 'Vance Studio')
    on conflict (id) do update set business_name = excluded.business_name;
  insert into public.customers (user_id, name, email, country) values (p_user, 'Sarah Johnson', 'sarah.j@acme.io', 'US') returning id into c1;
  insert into public.customers (user_id, name, email, country) values (p_user, 'Marcus Vance', 'm.vance@studio.co', 'GB') returning id into c2;
  insert into public.customers (user_id, name, email, country) values (p_user, 'Nova Tech Global', 'billing@novatech.com', 'IN') returning id into c3;

  insert into public.invoices (user_id, customer_id, invoice_number, description, subtotal, total_amount, currency, due_date, status)
    values (p_user, c1, 'INV-2026-0001', 'Website development', 500, 500, 'USD', current_date - 10, 'paid') returning id into i1;
  insert into public.invoices (user_id, customer_id, invoice_number, description, subtotal, total_amount, currency, due_date, status)
    values (p_user, c2, 'INV-2026-0002', 'Advisory session', 320, 320, 'USD', current_date - 4, 'overdue') returning id into i2;
  insert into public.invoices (user_id, customer_id, invoice_number, description, subtotal, total_amount, currency, due_date, status)
    values (p_user, c3, 'INV-2026-0003', 'Consulting (INR)', 50000, 50000, 'INR', current_date + 7, 'pending') returning id into i3;
  insert into public.invoice_items (invoice_id, description, quantity, unit_price, total) values
    (i1, 'Website development', 1, 500, 500), (i2, 'Advisory session', 1, 320, 320), (i3, 'Consulting', 1, 50000, 50000);

  insert into public.transactions (user_id, customer_id, invoice_id, provider, provider_order_id, provider_capture_id, amount, currency, status, payment_method, created_at, events) values
    (p_user, c1, i1, 'paypal', 'SEED-ORDER-001', 'SEED-CAP-001', 500, 'USD', 'completed', 'paypal', now() - interval '10 days', '[{"type":"order_created"},{"type":"captured"},{"type":"invoice_paid"}]'),
    (p_user, c2, null, 'paypal', 'SEED-ORDER-002', 'SEED-CAP-002', 250, 'USD', 'completed', 'paypal', now() - interval '3 days', '[{"type":"order_created"},{"type":"captured"}]'),
    (p_user, c1, null, 'paypal', 'SEED-ORDER-003', null, 120, 'USD', 'failed', 'paypal', now() - interval '2 days', '[{"type":"order_created"},{"type":"failed"}]'),
    (p_user, c3, null, 'razorpay', 'SEED-RZP-001', 'SEED-RZP-PAY-001', 5000, 'INR', 'completed', 'upi', now() - interval '1 days', '[{"type":"order_created"},{"type":"captured"}]');
end $$;

-- This is an operator tool, run by the project owner in the SQL editor; no client workflow calls it. Without this, PUBLIC (PostgreSQL's default) and,
-- on Supabase, anon and authenticated (through its default privileges for functions) could execute it through the Data API as
-- /rest/v1/rpc/seed_paypilot_demo. The owner keeps EXECUTE. service_role is deliberately left untouched: whether it can execute depends on the project's
-- default privileges (confirm on the real project if you rely on it). Rerunning this file is safe: create or replace keeps the privileges and the revoke is idempotent.
revoke execute on function public.seed_paypilot_demo(uuid) from public, anon, authenticated;
