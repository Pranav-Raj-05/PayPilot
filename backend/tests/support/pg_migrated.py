"""Opt-in pytest plugin: run the (SQLite-based) application suite against the REAL migrations on PostgreSQL.

    TEST_POSTGRES_URL=<admin url of a disposable local server> ALLOW_DESTRUCTIVE_PG_TESTS=1 \
        python -m pytest tests -p tests.support.pg_migrated -p no:logging -q

WHAT THIS IS (and is not)
  * It builds ONE throw-away database per pytest session (``pg_safety.temp_database("pp_mig")``, always dropped at the end), applies
    ``tests/support/supabase_shim.sql`` and the four files in ``supabase/migrations`` to it, and then makes the suite's ``client``
    fixture (which asks for ``init_engine("sqlite://")``) use that database instead of in-memory SQLite.
  * It exercises VANILLA PostgreSQL + a Supabase compatibility shim. It is NOT Supabase: no GoTrue, no PostgREST, no pooler, no real
    ``auth`` schema, no Supabase role grants.
  * The application connects as the database owner (a superuser on a disposable server), which bypasses Row Level Security. RLS is
    therefore NOT exercised by the app-level suite; it is covered separately by tests/test_migrations_postgres.py.
  * The schema comes from the SQL migrations, NOT from the SQLAlchemy models: ``Base.metadata.create_all`` and the SQLite dev-schema
    upgrader are turned into no-ops for PostgreSQL engines, so a model/migration mismatch surfaces as a test failure (and as a warning at
    session start if a model column is missing from the migrated schema).
  * Every data table is TRUNCATEd before each test (``TRUNCATE ... RESTART IDENTITY CASCADE``), so tests stay independent.

AUTH SCAFFOLDING (only inside the throw-away database)
  The suite creates ``profiles`` rows directly (the app auto-creates them from the JWT) without ever inserting into ``auth.users``, but
  ``profiles.id`` references ``auth.users(id)``. After the migrations are applied, the plugin therefore installs, in the throw-away
  database only:
    * ``alter table auth.users disable trigger on_auth_user_created`` - otherwise the migration's signup trigger would pre-create the
      profile and the app's own insert would collide / see a profile it did not expect;
    * a BEFORE INSERT trigger on ``public.profiles`` that inserts the matching ``auth.users`` row (``raw_user_meta_data`` built from the
      profile's full_name / business_name).
  Nothing of this exists in the migrations or on any real project.

EXCLUDED TESTS (documented, simplest robust choice)
  Tests that bring their own database or write to ``TEST_POSTGRES_URL`` directly would bypass this plugin's isolation, so they are
  DESELECTED while the plugin is active: everything in ``tests/test_migrations_postgres.py`` (builds its own databases), and any test that
  uses the ``postgres_client`` fixture or has "postgres" in its name/id (the profile-concurrency ``[postgres]`` variants and the
  cancel-vs-capture race test ``..._on_postgresql``). Run those
  without the plugin. The SQLite-only variants in test_profile_concurrency.py keep running (they use their own file database).

If TEST_POSTGRES_URL / ALLOW_DESTRUCTIVE_PG_TESTS are not set the plugin SKIPS every test with an explicit reason (it never silently falls
back to SQLite, which would give false confidence) and never connects anywhere.
"""
from __future__ import annotations

import pathlib
import warnings

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
MIGRATIONS = ROOT / "supabase" / "migrations"
SHIM = pathlib.Path(__file__).with_name("supabase_shim.sql")
MIGRATION_FILES = ("20260101000000_init_schema.sql", "20260101000100_rls_policies.sql", "20260102000000_money_integrity.sql",
                   "20260102000100_client_write_hardening.sql")
EXCLUDED_MODULES = ("test_migrations_postgres.py",)
EXCLUDED_FIXTURES = ("postgres_client",)
_STATE: dict = {}  # engine / admin connection of the running session, shared by the fixtures below

SCAFFOLD_SQL = """
alter table auth.users disable trigger on_auth_user_created;
create or replace function public.pg_migrated_scaffold_auth_user() returns trigger language plpgsql as $f$
begin
  insert into auth.users (id, email, raw_user_meta_data)
  values (new.id, new.email, jsonb_strip_nulls(jsonb_build_object('full_name', new.full_name, 'business_name', new.business_name)))
  on conflict (id) do nothing;
  return new;
end $f$;
drop trigger if exists pg_migrated_scaffold_auth_user on public.profiles;
create trigger pg_migrated_scaffold_auth_user before insert on public.profiles for each row execute function public.pg_migrated_scaffold_auth_user();
"""


def _read_migration(name: str, has_pgcrypto: bool) -> str:
    sql = (MIGRATIONS / name).read_text(encoding="utf-8")
    if not has_pgcrypto:  # gen_random_uuid() is built in on PostgreSQL 13+, so only the CREATE EXTENSION line is dropped
        sql = sql.replace('create extension if not exists "pgcrypto";', "-- pgcrypto skipped (not available in this build)")
    return sql


def pytest_collection_modifyitems(config, items):
    kept, dropped = [], []
    for item in items:
        module = pathlib.Path(str(item.fspath)).name
        # parametrised variants load postgres_client lazily (request.getfixturevalue), so the test id / name is checked as well:
        # "...[postgres]" (profile concurrency) and "...on_postgresql" (cancel-vs-capture race)
        own_db = "postgres" in item.name.lower() or any(f in getattr(item, "fixturenames", ()) for f in EXCLUDED_FIXTURES)
        if module in EXCLUDED_MODULES or own_db:
            dropped.append(item)
        else:
            kept.append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept


@pytest.fixture(scope="session")
def pg_migrated_url():
    """Connection URL of the session's migrated throw-away database."""
    from tests import pg_safety

    pg_safety.pg_test_admin_url()  # skips (never connects) unless the opt-in environment is set; raises for unsafe targets
    with pg_safety.temp_database("pp_mig") as url:
        with pg_safety.connect_verified(url) as conn:  # validated target; current_database() and the server are verified before any DDL
            has_pgcrypto = conn.execute("select count(*) from pg_available_extensions where name = 'pgcrypto'").fetchone()[0] > 0
            conn.execute(SHIM.read_text(encoding="utf-8"))
            for name in MIGRATION_FILES:
                conn.execute(_read_migration(name, has_pgcrypto))
            conn.execute(SCAFFOLD_SQL)
        yield url


@pytest.fixture(scope="session", autouse=True)
def _pg_migrated_session(pg_migrated_url):
    """Redirect the suite's ``init_engine("sqlite://")`` to the migrated database and neutralise SQLite-only schema helpers."""
    from sqlalchemy import MetaData
    from tests import pg_safety
    from app.core import db as dbmod
    from app.core.config import get_settings
    import app.main as appmain

    mp = pytest.MonkeyPatch()
    original_init = dbmod.init_engine
    original_create_all = MetaData.create_all
    original_upgrade = dbmod.upgrade_sqlite_dev_schema
    state = _STATE
    state.clear()
    state["engine"] = None

    def init_engine(url=None):
        wanted = url or get_settings().database_url
        if wanted not in ("sqlite://", "sqlite:///"):
            return original_init(url)
        if state["engine"] is not None:
            state["engine"].dispose()
        engine = original_init(pg_migrated_url)
        try:  # the engine is a separate connection path from connect_verified: verify it as well before any test uses it
            with engine.connect() as conn:
                pg_safety.verify_connection(conn, pg_migrated_url)
        except BaseException:
            engine.dispose()
            raise
        state["engine"] = engine
        return engine

    def is_pg(bind) -> bool:
        return getattr(getattr(bind, "dialect", None), "name", "") == "postgresql"

    def create_all(self, bind=None, *args, **kwargs):
        if is_pg(bind):
            return None  # the schema is owned by the SQL migrations
        return original_create_all(self, bind, *args, **kwargs)

    def upgrade(engine):
        return [] if is_pg(engine) else original_upgrade(engine)

    mp.setattr(dbmod, "init_engine", init_engine)
    mp.setattr(MetaData, "create_all", create_all)
    mp.setattr(dbmod, "upgrade_sqlite_dev_schema", upgrade)
    mp.setattr(appmain, "upgrade_sqlite_dev_schema", upgrade)

    admin = pg_safety.connect_verified(pg_migrated_url)
    state["admin"] = admin
    _warn_on_model_drift(admin)
    try:
        yield
    finally:
        if state["engine"] is not None:
            state["engine"].dispose()
        admin.close()
        mp.undo()


def _warn_on_model_drift(conn) -> None:
    from app.models import Base

    have = {(t, c) for t, c in conn.execute("select table_name, column_name from information_schema.columns where table_schema = 'public'").fetchall()}
    missing = sorted(f"{t.name}.{c.name}" for t in Base.metadata.sorted_tables for c in t.columns if (t.name, c.name) not in have)
    if missing:
        warnings.warn(f"pg_migrated: model columns missing from the migrated schema: {', '.join(missing)}", stacklevel=2)


@pytest.fixture(autouse=True)
def _pg_migrated_clean_tables(_pg_migrated_session):
    """Empty every data table before each test so tests are independent (the migrated schema itself is kept)."""
    state = _STATE
    if state["engine"] is not None:
        state["engine"].dispose()  # release pooled connections so TRUNCATE never waits on a leftover one
    admin = state["admin"]
    tables = [r[0] for r in admin.execute(
        "select format('%I.%I', schemaname, tablename) from pg_tables where schemaname = 'public' "
        "union all select 'auth.users' order by 1").fetchall()]
    admin.execute("truncate " + ", ".join(tables) + " restart identity cascade")
    yield
