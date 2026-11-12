-- SUPABASE COMPATIBILITY SHIM for running supabase/migrations/*.sql on a VANILLA PostgreSQL database (tests only).
--
-- This is an APPROXIMATION of the parts of Supabase the migrations depend on. It is NOT Supabase and must never be run against a Supabase
-- project (Supabase already has all of this). A pass on a database built with this shim proves the SQL is valid PostgreSQL and the
-- constraints/triggers/RLS policies behave as written; it does NOT prove anything about Supabase-managed behaviour (PostgREST, GoTrue,
-- the real auth.* implementation, role grants, the pooler, extensions installed by default, etc.).
--
-- Provided here:
--   * schema `auth` with a minimal `auth.users` table (the init schema references auth.users and installs a trigger on it)
--   * auth.uid() / auth.role() reading the request claim GUCs, like Supabase's PostgREST integration does
--   * the roles `anon`, `authenticated`, `service_role`, and default privileges like Supabase's (so RLS, not GRANTs, is what stops access)

create schema if not exists auth;

create table if not exists auth.users (
  id uuid primary key,
  email text,
  raw_user_meta_data jsonb not null default '{}'::jsonb
);

-- Supabase derives these from the request JWT; here tests set `request.jwt.claim.sub` / `request.jwt.claim.role` with set_config().
create or replace function auth.uid() returns uuid language sql stable as
$$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;

create or replace function auth.role() returns text language sql stable as
$$ select nullif(current_setting('request.jwt.claim.role', true), '') $$;

do $$ begin create role anon nologin; exception when duplicate_object then null; end $$;
do $$ begin create role authenticated nologin; exception when duplicate_object then null; end $$;
do $$ begin create role service_role nologin bypassrls; exception when duplicate_object then null; end $$;

grant usage on schema public, auth to anon, authenticated, service_role;
grant select on auth.users to authenticated, service_role;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
alter default privileges in schema public grant execute on functions to anon, authenticated, service_role;
