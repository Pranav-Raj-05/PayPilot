-- Row Level Security. The FastAPI backend connects with the database owner role (bypasses RLS) and enforces ownership
-- itself; these policies protect every path that goes through PostgREST / the anon + authenticated keys.
alter table public.profiles enable row level security;
alter table public.customers enable row level security;
alter table public.invoices enable row level security;
alter table public.invoice_items enable row level security;
alter table public.payment_links enable row level security;
alter table public.transactions enable row level security;
alter table public.ai_conversations enable row level security;
alter table public.ai_messages enable row level security;
alter table public.automations enable row level security;
alter table public.notifications enable row level security;
alter table public.audit_logs enable row level security;
alter table public.webhook_events enable row level security;  -- no policies: service/backend only

-- profiles
create policy profiles_select on public.profiles for select to authenticated using (id = auth.uid());
create policy profiles_update on public.profiles for update to authenticated using (id = auth.uid()) with check (id = auth.uid());

-- user-owned, fully editable by the owner
create policy customers_all on public.customers for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy invoices_all on public.invoices for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy invoice_items_all on public.invoice_items for all to authenticated
  using (exists (select 1 from public.invoices i where i.id = invoice_id and i.user_id = auth.uid()))
  with check (exists (select 1 from public.invoices i where i.id = invoice_id and i.user_id = auth.uid()));
create policy automations_all on public.automations for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy notifications_select on public.notifications for select to authenticated using (user_id = auth.uid());
create policy notifications_update on public.notifications for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy conversations_all on public.ai_conversations for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy ai_messages_select on public.ai_messages for select to authenticated
  using (exists (select 1 from public.ai_conversations c where c.id = conversation_id and c.user_id = auth.uid()));

-- financial records are READ-ONLY for clients: only the backend (verified provider events) may write them
create policy transactions_select on public.transactions for select to authenticated using (user_id = auth.uid());
create policy payment_links_select on public.payment_links for select to authenticated using (user_id = auth.uid());
create policy audit_select on public.audit_logs for select to authenticated using (user_id = auth.uid());

-- A client must never be able to mark an invoice paid; only backend roles (postgres / service_role) can.
create or replace function public.guard_invoice_paid() returns trigger language plpgsql as $$
begin
  if coalesce(auth.role(), '') = 'authenticated' and new.status = 'paid'
     and (tg_op = 'INSERT' or old.status is distinct from 'paid') then
    raise exception 'invoices can only be marked paid by a verified payment';
  end if;
  return new;
end $$;
create trigger trg_guard_invoice_paid before insert or update on public.invoices for each row execute function public.guard_invoice_paid();

revoke all on public.webhook_events from anon, authenticated;
revoke all on all tables in schema public from anon;
