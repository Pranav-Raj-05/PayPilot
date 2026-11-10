-- PayPilot core schema. Mirrors backend/app/models. Run in the Supabase SQL editor or via `supabase db push`.
create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------- profiles
create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null default '',
  full_name text,
  business_name text,
  avatar_url text,
  default_currency char(3) not null default 'USD',
  created_at timestamptz not null default now()
);

-- Create a profile automatically when someone signs up.
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email, full_name, business_name)
  values (new.id, coalesce(new.email, ''), new.raw_user_meta_data->>'full_name', new.raw_user_meta_data->>'business_name')
  on conflict (id) do nothing;
  return new;
end $$;
create trigger on_auth_user_created after insert on auth.users for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------- customers
create table public.customers (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  name text not null check (length(name) between 1 and 200),
  email text,
  phone text,
  address text,
  country char(2),
  created_at timestamptz not null default now()
);
create index ix_customers_user on public.customers(user_id);

-- ---------------------------------------------------------------- invoices
create table public.invoices (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  customer_id uuid references public.customers(id) on delete set null,
  invoice_number text not null,
  description text,
  subtotal numeric(14,2) not null default 0 check (subtotal >= 0),
  tax numeric(14,2) not null default 0 check (tax >= 0),
  total_amount numeric(14,2) not null default 0 check (total_amount >= 0),
  currency char(3) not null default 'USD',
  due_date date,
  status text not null default 'draft' check (status in ('draft','pending','paid','overdue','cancelled')),
  payment_provider text check (payment_provider in ('paypal','razorpay')),
  payment_link text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint uq_invoice_number unique (user_id, invoice_number)
);
create index ix_invoices_user_status on public.invoices(user_id, status);

create table public.invoice_items (
  id uuid primary key default gen_random_uuid(),
  invoice_id uuid not null references public.invoices(id) on delete cascade,
  position int not null default 0,
  description text not null,
  quantity numeric(10,2) not null default 1 check (quantity > 0),
  unit_price numeric(14,2) not null check (unit_price >= 0),
  total numeric(14,2) not null check (total >= 0)
);
create index ix_invoice_items_invoice on public.invoice_items(invoice_id);

-- ---------------------------------------------------------------- payment links / transactions
create table public.payment_links (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  customer_id uuid references public.customers(id) on delete set null,
  invoice_id uuid references public.invoices(id) on delete set null,
  provider text not null check (provider in ('paypal','razorpay')),
  provider_reference text,
  url text not null,
  amount numeric(14,2) not null check (amount > 0),
  currency char(3) not null,
  description text,
  status text not null default 'active' check (status in ('active','paid','expired','cancelled')),
  expires_at timestamptz,
  created_at timestamptz not null default now()
);
create index ix_links_user on public.payment_links(user_id);

create table public.transactions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  customer_id uuid references public.customers(id) on delete set null,
  invoice_id uuid references public.invoices(id) on delete set null,
  payment_link_id uuid references public.payment_links(id) on delete set null,
  provider text not null check (provider in ('paypal','razorpay')),
  provider_order_id text,
  provider_capture_id text,
  amount numeric(14,2) not null check (amount > 0),
  currency char(3) not null,
  status text not null default 'created' check (status in ('created','pending','completed','failed','refunded','cancelled')),
  payment_method text,
  fee_amount numeric(14,2),
  net_amount numeric(14,2),
  failure_reason text,
  events jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint uq_txn_provider_order unique (provider, provider_order_id)
);
create index ix_txn_user_created on public.transactions(user_id, created_at desc);
create index ix_txn_capture on public.transactions(provider, provider_capture_id);

-- ---------------------------------------------------------------- AI
create table public.ai_conversations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  title text not null default 'New conversation',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index ix_ai_conv_user on public.ai_conversations(user_id);

create table public.ai_messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.ai_conversations(id) on delete cascade,
  role text not null check (role in ('user','assistant','tool')),
  content text not null default '',
  tool_name text,
  tool_result jsonb,
  created_at timestamptz not null default now()
);
create index ix_ai_msg_conv on public.ai_messages(conversation_id, created_at);

-- ---------------------------------------------------------------- automations / notifications / audit / webhooks
create table public.automations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  name text not null,
  description text,
  automation_type text not null,
  is_enabled boolean not null default false,
  configuration jsonb not null default '{}'::jsonb,
  last_executed_at timestamptz,
  created_at timestamptz not null default now(),
  constraint uq_automation_type unique (user_id, automation_type)
);
create index ix_automations_user on public.automations(user_id);

create table public.notifications (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  title text not null,
  message text not null,
  notification_type text not null default 'info',
  is_read boolean not null default false,
  data jsonb,
  created_at timestamptz not null default now()
);
create index ix_notif_user_read on public.notifications(user_id, is_read);

create table public.audit_logs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid,
  action text not null,
  entity_type text,
  entity_id text,
  detail jsonb,
  created_at timestamptz not null default now()
);
create index ix_audit_user on public.audit_logs(user_id, created_at desc);

create table public.webhook_events (
  id uuid primary key default gen_random_uuid(),
  provider text not null,
  event_id text not null,
  event_type text not null,
  payload jsonb,
  created_at timestamptz not null default now(),
  constraint uq_webhook_event unique (provider, event_id)
);

-- updated_at maintenance
create or replace function public.touch_updated_at() returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end $$;
create trigger trg_invoices_touch before update on public.invoices for each row execute function public.touch_updated_at();
create trigger trg_txn_touch before update on public.transactions for each row execute function public.touch_updated_at();
create trigger trg_conv_touch before update on public.ai_conversations for each row execute function public.touch_updated_at();
