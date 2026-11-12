-- Money integrity (Stage A): invoice versioning, partial refunds, refund history.
-- Run after 20260101000000_init_schema.sql and 20260101000100_rls_policies.sql.
-- Safe to run more than once (idempotent). Additive, except that the transactions.status CHECK constraint is replaced (see below).
-- It does not rewrite history: transactions.updated_at is preserved by the backfill.
--
-- OPERATING NOTES (read before running)
--  * Privileges: must be run by the OWNER of public.transactions / public.invoices / public.payment_links / public.refunds
--    (on Supabase: the `postgres` role via the SQL editor, `supabase db push` or the CLI). ALTER TABLE, DISABLE/ENABLE TRIGGER and
--    CREATE TRIGGER all require table ownership.
--  * Locks: ALTER TABLE takes ACCESS EXCLUSIVE on invoices, transactions and payment_links (briefly, no table rewrite: the new columns are
--    nullable or have a constant default). The backfill UPDATEs take row locks. Replacing the status CHECK scans transactions once while
--    holding ACCESS EXCLUSIVE. Run it in a quiet moment on a large table.
--  * lock_timeout: so the script fails fast instead of queueing behind a long-running transaction (and blocking every other query on the
--    table behind its own lock request), a session-level lock_timeout is set first. Default 10s; override BEFORE running the script with
--    `set money_integrity.lock_timeout = '30s';` (any value accepted by lock_timeout, '0' disables). It is reset at the very end.
--    Works both in autocommit (each statement is its own transaction; the setting is session-level) and inside one explicit transaction
--    (the setting is also rolled back with that transaction if it aborts, which is harmless).
--    LIMIT: if a tool runs the file statement by statement and aborts on the first error, the session keeps the lock_timeout until the
--    connection closes (or `reset lock_timeout;` is run). It also means partial progress is NOT rolled back in that mode; the migration is
--    idempotent, so simply fix the cause and rerun. Wrap the file in begin; ... commit; if you want all-or-nothing.
--  * Atomic units: each DO block below is a single statement and therefore atomic by itself (all-or-nothing, including the trigger
--    changes made inside it), whatever tool runs the file.
--  * Fail closed: if the schema is not what this migration expects (unknown status CHECK), it raises instead of guessing. Nothing is
--    dropped in that case.

select set_config('lock_timeout', coalesce(nullif(current_setting('money_integrity.lock_timeout', true), ''), '10s'), false);

-- ---------------------------------------------------------------- schema-compatibility guards
-- init_schema.sql gained fee_amount / net_amount after its first commit. A project that applied the first version lacks them.
-- Definitions are identical to init_schema.sql (these are TRANSACTIONS columns). Existing rows are untouched (NULL).
alter table public.transactions add column if not exists fee_amount numeric(14,2);
alter table public.transactions add column if not exists net_amount numeric(14,2);

-- ---------------------------------------------------------------- invoice versioning
-- An order remembers the invoice version it was created for; a payment for an older version can never mark the invoice paid.
alter table public.invoices add column if not exists version int not null default 1;
alter table public.transactions add column if not exists invoice_version int;
alter table public.payment_links add column if not exists invoice_version int;

-- payment_links has no updated_at trigger, so a plain backfill is fine. Only rows still NULL are set, so a rerun never resets anything.
-- (transactions is backfilled further down, with its touch trigger handled.)
update public.payment_links l set invoice_version = i.version from public.invoices i where l.invoice_id = i.id and l.invoice_version is null;

-- ---------------------------------------------------------------- partial refunds
-- Replace the status CHECK so it accepts 'partially_refunded', FAILING CLOSED.
-- PostgreSQL auto-names an inline column check "<table>_<column>_check", but a project can differ (renamed, recreated by a tool), so the
-- constraint is found by what it constrains, not by name: a CHECK (contype 'c') whose conkey is exactly {status}. Then its literal list
-- is validated as a SET: it must be exactly the original six statuses or the new seven, in any order (so a rerun accepts its own
-- previous output, and the result does not depend on the database collation).
--  * CHECKs that involve other columns (conkey has more than status, e.g. status <> 'failed' or failure_reason is not null) or only other
--    columns (amount > 0, ...) are never dropped and never inspected.
--  * A single-column status CHECK with any other list is NOT dropped; the migration raises so a human can review.
--  * Exactly one valid candidate is replaced. Zero candidates, or more than one, raises.
-- LIMIT: the literals are compared as a set of quoted strings in pg_get_constraintdef(); a CHECK that is semantically the same but written
-- differently (e.g. status ~ '^(created|...)$') is treated as unexpected and raises (fail closed, never a silent drop).
do $$
declare
  status_attnum smallint;
  c record;
  literals text[];
  orig_six text[] := array['cancelled','completed','created','failed','pending','refunded'];
  new_seven text[] := array['cancelled','completed','created','failed','partially_refunded','pending','refunded'];
  valid_names text[] := '{}';
  other_single text;
  other_def text;
begin
  select a.attnum into status_attnum from pg_attribute a
    where a.attrelid = 'public.transactions'::regclass and a.attname = 'status' and not a.attisdropped;
  if status_attnum is null then
    raise exception 'money_integrity: no CHECK constraint on public.transactions(status) matched the expected status list; review the schema before migrating';
  end if;

  for c in
    select oid, conname, pg_get_constraintdef(oid) as def from pg_constraint
    where conrelid = 'public.transactions'::regclass and contype = 'c' and conkey = array[status_attnum]::smallint[]
    order by conname
  loop
    select coalesce(array_agg(distinct m[1]), '{}') into literals
      from regexp_matches(c.def, '''([^'']*)''', 'g') as m;
    -- set equality (each side contains the other): independent of the order the literals are written in and of the database collation
    if (literals @> orig_six and literals <@ orig_six) or (literals @> new_seven and literals <@ new_seven) then
      valid_names := valid_names || c.conname::text;
    elsif other_single is null then
      other_single := c.conname; other_def := c.def;
    end if;
  end loop;

  -- A single-column status CHECK we do not recognise: refuse (and drop nothing), even if another candidate is valid.
  if other_single is not null then
    raise exception 'money_integrity: unexpected single-column CHECK on public.transactions(status) (%): %; refusing to drop it', other_single, other_def;
  end if;
  if coalesce(array_length(valid_names, 1), 0) = 0 then
    raise exception 'money_integrity: no CHECK constraint on public.transactions(status) matched the expected status list; review the schema before migrating';
  end if;
  if array_length(valid_names, 1) > 1 then
    raise exception 'money_integrity: more than one CHECK constraint on public.transactions(status) matched the expected status list: %', array_to_string(valid_names, ', ');
  end if;

  execute format('alter table public.transactions drop constraint %I', valid_names[1]);
  raise notice 'money_integrity: dropped status CHECK %', valid_names[1];
  alter table public.transactions add constraint transactions_status_check
    check (status in ('created','pending','completed','partially_refunded','refunded','failed','cancelled'));
  raise notice 'money_integrity: added status CHECK transactions_status_check';
end $$;

alter table public.transactions add column if not exists refunded_amount numeric(14,2) not null default 0;

-- Added only if missing (a rerun does not drop it, so there is no window without it and no needless re-scan of the table).
do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'public.transactions'::regclass and conname = 'transactions_refunded_amount_check') then
    alter table public.transactions add constraint transactions_refunded_amount_check check (refunded_amount >= 0 and refunded_amount <= amount);
  end if;
end $$;

create table if not exists public.refunds (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  transaction_id uuid not null references public.transactions(id) on delete cascade,
  provider text not null check (provider in ('paypal','razorpay')),
  provider_refund_id text not null,
  amount numeric(14,2) not null check (amount > 0),
  currency char(3) not null,
  status text not null default 'completed' check (status in ('pending','completed','failed')),
  source text not null default 'api' check (source in ('api','webhook','legacy')),
  reason text,
  idempotency_key text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint uq_refund_provider_id unique (provider, provider_refund_id),
  constraint uq_refund_idempotency unique (transaction_id, idempotency_key)  -- NULL keys never collide
);
create index if not exists ix_refunds_txn on public.refunds(transaction_id);
create index if not exists ix_refunds_user on public.refunds(user_id, created_at desc);

drop trigger if exists trg_refunds_touch on public.refunds;
create trigger trg_refunds_touch before update on public.refunds for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------- backfill existing transactions
-- transactions has a BEFORE UPDATE trigger (trg_txn_touch in init_schema.sql) that stamps updated_at = now(). A backfill UPDATE would
-- therefore rewrite the last-modified time of every row it touches (and make the failed-payment monitor re-alert old failures). So this
-- block switches off ONLY the trigger(s) on public.transactions that run public.touch_updated_at() (found by FUNCTION, not by name, so a
-- renamed trigger is still handled) while it backfills, and restores each to EXACTLY its previous state before it ends:
--   'O' enabled (origin, default)  -> disabled for the backfill, then `enable trigger`
--   'A' enabled always             -> disabled, then `enable always trigger`
--   'R' enabled replica            -> disabled, then `enable replica trigger`
--   'D' disabled by the operator   -> left untouched (never enabled by this migration)
--   no such trigger                -> nothing to do, the backfill just runs
-- The DO block is a single atomic statement: if anything inside fails, the whole block (including the trigger changes) rolls back, so a
-- trigger can never be left disabled by this migration. No other trigger is touched; session_replication_role is not used.
-- Requires table ownership (ALTER TABLE ... DISABLE TRIGGER) and takes SHARE ROW EXCLUSIVE on transactions while it runs.
-- Concurrent writers are blocked by that lock for the duration of the block, so their updated_at stamps cannot be skipped either.
--
-- Backfill scope (only rows that still need it, so reruns change nothing):
--  * transactions.invoice_version only for OPEN orders (status created/pending) linked to an invoice and still NULL. Completed/failed/
--    refunded/cancelled orders are history and are deliberately left NULL.
--  * transactions.refunded_amount = amount for fully refunded rows still at 0.
do $$
declare
  trg_row record;
  saved jsonb := '[]'::jsonb;
  s jsonb;
begin
  for trg_row in
    select tgname::text as name, tgenabled::text as state from pg_trigger
    where tgrelid = 'public.transactions'::regclass and tgfoid = 'public.touch_updated_at()'::regprocedure and not tgisinternal
    order by tgname
  loop
    saved := saved || jsonb_build_object('name', trg_row.name, 'state', trg_row.state);
    if trg_row.state <> 'D' then
      execute format('alter table public.transactions disable trigger %I', trg_row.name);
    end if;
  end loop;

  update public.transactions t set invoice_version = i.version
    from public.invoices i
    where t.invoice_id = i.id and t.invoice_version is null and t.status in ('created','pending');
  update public.transactions set refunded_amount = amount where status = 'refunded' and refunded_amount = 0;

  for s in select * from jsonb_array_elements(saved)
  loop
    if s->>'state' = 'O' then
      execute format('alter table public.transactions enable trigger %I', s->>'name');
    elsif s->>'state' = 'A' then
      execute format('alter table public.transactions enable always trigger %I', s->>'name');
    elsif s->>'state' = 'R' then
      execute format('alter table public.transactions enable replica trigger %I', s->>'name');
    end if;
  end loop;
end $$;

-- Payments that were fully refunded before this migration get ONE legacy refund row for their full amount. A transaction that already has
-- any refund row (legacy or real) is skipped, so reruns and runs after real refunds never duplicate or edit existing refund rows.
insert into public.refunds (user_id, transaction_id, provider, provider_refund_id, amount, currency, status, source)
select t.user_id, t.id, t.provider, 'legacy-' || t.id::text, t.amount, t.currency, 'completed', 'legacy'
from public.transactions t
where t.status = 'refunded'
  and not exists (select 1 from public.refunds r where r.transaction_id = t.id)
on conflict do nothing;

-- ---------------------------------------------------------------- RLS: refunds are financial records, read-only for clients
alter table public.refunds enable row level security;
drop policy if exists refunds_select on public.refunds;
create policy refunds_select on public.refunds for select to authenticated using (user_id = auth.uid());
revoke all on public.refunds from anon;

reset lock_timeout;
