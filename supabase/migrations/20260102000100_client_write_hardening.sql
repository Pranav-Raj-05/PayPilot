-- Client write hardening. Run after 20260102000000_money_integrity.sql. Idempotent (safe to run more than once).
--
-- WHY: invoices and invoice_items were editable by any signed-in user straight through the Supabase Data API (policies FOR ALL). A client could
-- change amounts, currency, status or version, edit items, move a paid invoice back to pending, delete a paid invoice (orphaning its
-- transactions) or attach another tenant's customer id, all of which bypass the backend rules that make payments safe (edit lock while a link is
-- live, version bump, cancel cascade). The frontend never reads or writes data through Supabase (it uses Supabase for sign-in only) and the
-- FastAPI backend connects as the table owner, so making these tables READ-ONLY for clients removes the bypass without affecting the app.
--
-- 1) Invoices and their items: read-only for clients (own rows only).
drop policy if exists invoices_all on public.invoices;
drop policy if exists invoice_items_all on public.invoice_items;
drop policy if exists invoices_select on public.invoices;
drop policy if exists invoice_items_select on public.invoice_items;
create policy invoices_select on public.invoices for select to authenticated using (user_id = auth.uid());
create policy invoice_items_select on public.invoice_items for select to authenticated
  using (exists (select 1 from public.invoices i where i.id = invoice_id and i.user_id = auth.uid()));

-- 2) Defense in depth: financial tables are read-only at the GRANT level as well, not only because no write policy exists.
--    (revoke-all then grant-select is version-proof: PostgreSQL 17 added a MAINTAIN privilege to ALL.)
revoke all on public.invoices, public.invoice_items, public.payment_links, public.transactions, public.refunds, public.audit_logs, public.ai_messages from authenticated;
grant select on public.invoices, public.invoice_items, public.payment_links, public.transactions, public.refunds, public.audit_logs, public.ai_messages to authenticated;

-- 3) The anonymous role gets nothing in public (covers refunds and any table created after the RLS migration).
revoke all on all tables in schema public from anon;

-- 4) The signup trigger function. It only ever runs as the trigger on auth.users and calling it directly already fails ("trigger functions can
--    only be called as triggers"), but PUBLIC has EXECUTE on new functions by default and Supabase's default privileges also grant it to anon and
--    authenticated, which exposes a SECURITY DEFINER function through the Data API (/rest/v1/rpc/handle_new_user) and trips Supabase's security
--    advisor. EXECUTE on a trigger function is checked when the trigger is CREATED, not when it fires, so signup keeps working (tested). The function
--    is created by 20260101000000_init_schema.sql, which always runs before this file. service_role is deliberately left untouched.
revoke execute on function public.handle_new_user() from public, anon, authenticated;
