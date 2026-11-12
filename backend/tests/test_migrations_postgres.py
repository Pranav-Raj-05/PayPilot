"""Money-integrity migration checks (supabase/migrations/20260102000000_money_integrity.sql) and the client-write hardening that follows it
(supabase/migrations/20260102000100_client_write_hardening.sql).

* Static checks of the SQL text run everywhere and never connect to anything.
* PostgreSQL checks need the opt-in environment (see tests/pg_safety.py): TEST_POSTGRES_URL = admin URL of a DISPOSABLE local server and
  ALLOW_DESTRUCTIVE_PG_TESTS=1. Without it they SKIP. Every test gets its own uniquely named throw-away database
  (``pg_safety.temp_database("pp_mig")``: shim + the real migration files), which is dropped in a finally block.

LIMITS: the database is vanilla PostgreSQL (version of the local server) plus tests/support/supabase_shim.sql. That proves the SQL and the
constraint / trigger / RLS-policy semantics on PostgreSQL. It is NOT Supabase: no PostgREST / GoTrue claims, real auth schema, role
grants, pooler or Supabase-installed extensions are involved. Each migration file is executed as ONE simple-protocol call, i.e. as a single
implicit transaction: that is what the atomicity tests below rely on (tools that run a file statement by statement behave differently,
as documented at the top of the migration)."""
import json
import pathlib
import re
import subprocess
import time
import uuid

import pytest

from tests import pg_safety

ROOT = pathlib.Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "supabase" / "migrations"
INIT, RLS, MONEY, HARDEN = (MIGRATIONS / n for n in ("20260101000000_init_schema.sql", "20260101000100_rls_policies.sql", "20260102000000_money_integrity.sql",
                                                        "20260102000100_client_write_hardening.sql"))
BACKFILL_MARK = "-- ---------------------------------------------------------------- backfill existing transactions"  # first statement after refunds is created
SEED = ROOT / "supabase" / "seed.sql"  # optional demo data function, run by the project owner; NOT a migration
SHIM = pathlib.Path(__file__).parent / "support" / "supabase_shim.sql"
FIRST_INIT_COMMIT = "963fb99"  # init_schema.sql as first committed: transactions has no fee_amount / net_amount
SIX = ("created", "pending", "completed", "failed", "refunded", "cancelled")
LOCK_ENV = "money_integrity.lock_timeout"


def _norm(sql: str) -> str:
    """Lower-case, comments removed, whitespace collapsed: static checks look at CODE only and are not brittle on formatting."""
    return re.sub(r"\s+", " ", re.sub(r"--[^\n]*", "", sql.lower()))


def _do_blocks(sql: str) -> list[str]:
    return [m.group(0) for m in re.finditer(r"(?is)\bdo\s+(\$\w*\$).*?\1\s*;", sql)]


def _backfill_block(sql: str) -> str:
    found = [b for b in _do_blocks(sql) if "touch_updated_at" in b.lower() and "update public.transactions" in b.lower()]
    assert len(found) == 1, "expected exactly one backfill DO block"
    return found[0]


# ------------------------------------------------------------------ static checks (no database needed)
class TestMigrationText:
    def _sql(self):
        return MONEY.read_text(encoding="utf-8")

    def test_status_constraint_is_found_by_column_not_by_hard_coded_name_or_regex(self):
        sql = _norm(self._sql())
        assert "drop constraint if exists transactions_status_check" not in sql
        assert "conkey" in sql and "contype = 'c'" in sql  # single-column CHECK on status identified through pg_constraint.conkey
        assert "pg_attribute" in sql and "attisdropped" in sql
        assert "\\mstatus\\m" not in sql  # the old fragile text match on the constraint definition is gone

    def test_status_check_handling_fails_closed_with_the_three_documented_messages(self):
        sql = self._sql()
        assert "money_integrity: no CHECK constraint on public.transactions(status)" in sql
        assert "money_integrity: more than one CHECK constraint on public.transactions(status)" in sql
        assert "money_integrity: unexpected single-column CHECK on public.transactions(status)" in sql
        assert len(re.findall(r"raise exception\s+'money_integrity:", sql)) >= 3

    def test_backfill_switches_only_touch_triggers_found_by_function_and_restores_each_state_in_the_same_atomic_block(self):
        sql = self._sql()
        low = _norm(sql)
        assert "disable trigger all" not in low and "session_replication_role" not in low  # nothing broader than the touch trigger(s)
        block = _norm(_backfill_block(sql))
        assert "tgfoid = 'public.touch_updated_at()'::regprocedure" in block and "not tgisinternal" in block  # found by function
        assert "trg_txn_touch" not in block  # not by a hard-coded name
        for fragment in ("disable trigger %i", "enable trigger %i", "enable always trigger %i", "enable replica trigger %i", "tgenabled"):
            assert fragment in block  # every state is restored in the SAME block as the backfill
        assert block.index("disable trigger") < block.index("update public.transactions") < block.index("enable trigger")
        # outside of DO blocks nothing disables a trigger
        outside = _norm(re.sub(r"(?is)\bdo\s+(\$\w*\$).*?\1\s*;", "", sql))
        assert "disable trigger" not in outside and "enable trigger" not in outside

    def test_backfill_scope_open_orders_only_and_no_duplicate_legacy_refunds(self):
        block = _norm(_backfill_block(self._sql()))
        assert "statusin('created','pending')" in block.replace(" ", "")
        low = _norm(self._sql())
        assert "not exists (select 1 from public.refunds r where r.transaction_id = t.id)" in low

    def test_lock_timeout_is_set_from_the_guc_with_a_default_and_reset_at_the_end(self):
        sql = self._sql()
        low = _norm(sql)
        assert LOCK_ENV in low and "set_config('lock_timeout'" in low and "'10s'" in low
        assert low.rstrip().endswith("reset lock_timeout;")
        assert low.index("set_config('lock_timeout'") < low.index("alter table")  # before the first lock-taking statement

    def test_schema_compatibility_guards_for_the_first_init_schema(self):
        low = _norm(self._sql())
        assert "alter table public.transactions add column if not exists fee_amount numeric(14,2);" in low
        assert "alter table public.transactions add column if not exists net_amount numeric(14,2);" in low
        assert "public.invoices add column if not exists fee_amount" not in low

    def test_the_script_has_no_transaction_control_of_its_own(self):
        low = _norm(re.sub(r"(?is)\bdo\s+(\$\w*\$).*?\1\s*;", "", self._sql()))  # DO blocks contain their own BEGIN ... END
        assert not re.search(r"(^|;) ?(begin|commit|rollback)\b", low)  # the caller decides: one simple-query call is atomic already

    def test_refunds_is_locked_down_right_after_it_is_created_and_before_any_row_exists(self):
        low = _norm(self._sql())
        created, first_row = low.index("create table if not exists public.refunds"), low.index("insert into public.refunds")
        for stmt in ("alter table public.refunds enable row level security;", "revoke all on public.refunds from anon;",
                     "revoke all on public.refunds from authenticated;", "grant select on public.refunds to authenticated;"):
            assert created < low.index(stmt) < first_row, stmt  # no window, even when a tool runs the file statement by statement
        assert low.index("revoke all on public.refunds from authenticated;") < low.index("grant select on public.refunds to authenticated;")
        assert "create policy refunds_select on public.refunds for select to authenticated" in low  # the owner can still read their own refunds


# ------------------------------------------------------------------ PostgreSQL helpers
def _script(path: pathlib.Path, strip_pgcrypto: bool) -> str:
    sql = path.read_text(encoding="utf-8")
    if strip_pgcrypto:  # some vanilla builds ship without the pgcrypto extension; gen_random_uuid() is built in on PostgreSQL 13+
        sql = sql.replace('create extension if not exists "pgcrypto";', "-- pgcrypto skipped (not available in this build)")
    return sql


class Db:
    def __init__(self, conn, url, strip_pgcrypto):
        self.conn, self.url, self.strip = conn, url, strip_pgcrypto

    def run(self, sql, params=None):
        return self.conn.execute(sql, params) if params is not None else self.conn.execute(sql)

    def one(self, sql, params=None):
        return self.run(sql, params).fetchone()

    def all(self, sql, params=None):
        return self.run(sql, params).fetchall()

    def apply(self, *paths):
        for p in paths:
            self.conn.execute(_script(p, self.strip))  # no params -> simple protocol -> the whole file is one implicit transaction

    def apply_text(self, sql):
        self.conn.execute(sql.replace('create extension if not exists "pgcrypto";', "-- skipped") if self.strip else sql)

    def base(self):
        """shim + the two original migrations: the schema as it is before this change."""
        self.conn.execute(SHIM.read_text(encoding="utf-8"))
        self.apply(INIT, RLS)


@pytest.fixture
def db():
    from tests import pg_safety
    pg_safety.pg_test_admin_url()  # SKIPs unless the opt-in environment is set; raises for an unsafe target; nothing connected before this
    with pg_safety.temp_database("pp_mig") as url:  # unique database, dropped in its finally block
        conn = pg_safety.connect_verified(url)  # validated target; current_database() and the server are verified before any DDL
        try:
            has_pgcrypto = conn.execute("select count(*) from pg_available_extensions where name = 'pgcrypto'").fetchone()[0] > 0
            yield Db(conn, url, strip_pgcrypto=not has_pgcrypto)
        finally:
            conn.close()


USER_A, USER_B = str(uuid.uuid4()), str(uuid.uuid4())


def seed_users(db: Db):
    for uid, mail in ((USER_A, "a@example.com"), (USER_B, "b@example.com")):
        db.run("insert into auth.users (id, email) values (%s, %s)", (uid, mail))  # the init trigger creates the profile


def seed_legacy_data(db: Db):
    """Rows as they look BEFORE the migration, with deliberately old updated_at values."""
    seed_users(db)
    inv = str(uuid.uuid4())
    db.run("insert into public.invoices (id, user_id, invoice_number, subtotal, total_amount, status) values (%s, %s, 'INV-1', 100, 100, 'pending')", (inv, USER_A))
    ids = {"refunded": str(uuid.uuid4()), "open": str(uuid.uuid4()), "failed": str(uuid.uuid4()), "completed": str(uuid.uuid4())}
    rows = [("refunded", "refunded", None, "2020-01-02"), ("open", "created", inv, "2020-01-03"), ("failed", "failed", None, "2020-01-04"), ("completed", "completed", inv, "2020-01-05")]
    for key, status, invoice_id, stamp in rows:
        db.run("insert into public.transactions (id, user_id, invoice_id, provider, provider_order_id, amount, currency, status, created_at, updated_at) "
               "values (%s, %s, %s, 'paypal', %s, 100, 'USD', %s, %s, %s)", (ids[key], USER_A, invoice_id, f"ORD-{key}", status, stamp, stamp))
    db.run("insert into public.payment_links (user_id, invoice_id, provider, url, amount, currency) values (%s, %s, 'paypal', 'https://x', 100, 'USD')", (USER_A, inv))
    return inv, ids


def add_txn(db: Db, key, status, invoice_id=None, amount=100, stamp="2020-02-01", user=USER_A, **extra):
    tid = str(uuid.uuid4())
    cols = dict(id=tid, user_id=user, invoice_id=invoice_id, provider="paypal", provider_order_id=f"ORD-{key}", amount=amount, currency="USD",
                status=status, created_at=stamp, updated_at=stamp, **extra)
    db.run(f"insert into public.transactions ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))})", tuple(cols.values()))
    return tid


def stamps(db: Db):
    return {str(r[0]): r[1] for r in db.all("select id, updated_at from public.transactions order by id")}


def trigger_rows(db: Db):
    return db.all("select tgname, tgenabled from pg_trigger where tgrelid = 'public.transactions'::regclass and tgfoid = 'public.touch_updated_at()'::regprocedure and not tgisinternal order by 1")


def trigger_state(db: Db, name="trg_txn_touch"):
    return db.one("select tgenabled from pg_trigger where tgrelid = 'public.transactions'::regclass and tgname = %s", (name,))[0]


def set_trigger_state(db: Db, state):
    cmd = {"O": "enable trigger", "A": "enable always trigger", "R": "enable replica trigger", "D": "disable trigger"}[state]
    db.run(f"alter table public.transactions {cmd} trg_txn_touch")
    assert trigger_state(db) == state


def txn_checks(db: Db):
    return {n: d for n, d in db.all("select conname, pg_get_constraintdef(oid) from pg_constraint where conrelid = 'public.transactions'::regclass and contype = 'c'")}


def snapshot(db: Db):
    """Everything this migration could touch: structure AND data of every table in public (+ auth.users)."""
    snap = {
        "columns": db.all("select table_name, column_name, data_type, is_nullable, column_default, numeric_precision, numeric_scale from information_schema.columns where table_schema = 'public' order by 1, 2"),
        "constraints": db.all("select conrelid::regclass::text, conname, contype, convalidated, pg_get_constraintdef(oid) from pg_constraint where connamespace = 'public'::regnamespace order by 1, 2"),
        "indexes": db.all("select tablename, indexname, indexdef from pg_indexes where schemaname = 'public' order by 1, 2"),
        "policies": db.all("select tablename, policyname, cmd, roles::text, qual, with_check from pg_policies where schemaname = 'public' order by 1, 2"),
        "triggers": db.all("select tgrelid::regclass::text, tgname, tgenabled, pg_get_triggerdef(oid) from pg_trigger where not tgisinternal order by 1, 2"),
        "rls": db.all("select relname, relrowsecurity from pg_class where relnamespace = 'public'::regnamespace and relkind = 'r' order by 1"),
    }
    tables = [r[0] for r in db.all("select table_name from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE' order by 1")]
    for t in tables + ["auth.users"]:
        name = t if "." in t else f"public.{t}"
        snap[f"rows:{t}"] = db.one(f"select coalesce(jsonb_agg(to_jsonb(x) order by to_jsonb(x)::text), '[]'::jsonb) from {name} x")[0]
    return json.loads(json.dumps(snap, default=str))


def assert_migration_not_applied(db: Db, before):
    """A failed migration run must leave NO trace: the whole file is one implicit transaction."""
    assert snapshot(db) == before
    assert db.one("select to_regclass('public.refunds') is null")[0] is True


# ------------------------------------------------------------------ 1-4 status constraint
class TestStatusConstraint:
    def test_renamed_status_constraint_is_replaced_and_new_status_is_accepted(self, db):  # scenario 1
        import psycopg
        db.base()
        seed_legacy_data(db)
        db.run("alter table public.transactions rename constraint transactions_status_check to legacy_status_rule")
        db.apply(MONEY)
        checks = txn_checks(db)
        assert "legacy_status_rule" not in checks and "transactions_status_check" in checks
        assert [n for n, d in checks.items() if "partially_refunded" in d] == ["transactions_status_check"]
        db.run("insert into public.transactions (user_id, provider, provider_order_id, amount, currency, status) values (%s, 'paypal', 'OK-1', 10, 'USD', 'partially_refunded')", (USER_A,))
        with pytest.raises(psycopg.errors.CheckViolation):
            db.run("insert into public.transactions (user_id, provider, provider_order_id, amount, currency, status) values (%s, 'paypal', 'BAD-1', 10, 'USD', 'bogus')", (USER_A,))
        assert [n for n, d in checks.items() if re.search(r"\bstatus\b", d)] == ["transactions_status_check"]  # (original assertion kept)
        assert any("amount > 0" in d or "amount > (0" in d for d in checks.values())  # unrelated checks were not dropped
        assert "transactions_refunded_amount_check" in checks

    def test_only_the_status_only_check_is_identified_other_checks_mentioning_status_text_survive(self, db):  # scenario 2
        """The old implementation matched the TEXT 'status' in any definition. Here other CHECKs mention the word status (as a string literal
        or in a pattern) but constrain other columns: they must be neither dropped nor taken for the status constraint."""
        import psycopg
        db.base()
        seed_users(db)
        db.run("alter table public.transactions add constraint ck_method_not_status check (payment_method is null or payment_method <> 'status')")
        db.run("alter table public.transactions add constraint ck_reason_text check (failure_reason is null or failure_reason not like '%status%')")
        before = txn_checks(db)
        db.apply(MONEY)
        after = txn_checks(db)
        for name in ("ck_method_not_status", "ck_reason_text"):
            assert after[name] == before[name]
        status_only = [n for n, d in after.items() if "partially_refunded" in d]
        assert status_only == ["transactions_status_check"]
        with pytest.raises(psycopg.errors.CheckViolation):
            db.run("insert into public.transactions (user_id, provider, provider_order_id, amount, currency, payment_method) values (%s, 'paypal', 'P1', 5, 'USD', 'status')", (USER_A,))

    def test_unrelated_amount_and_provider_constraints_are_preserved(self, db):  # scenario 3
        import psycopg
        db.base()
        seed_legacy_data(db)
        before = txn_checks(db)
        db.apply(MONEY)
        after = txn_checks(db)
        for name, definition in before.items():
            if name == "transactions_status_check":
                continue
            assert after.get(name) == definition, f"constraint {name} changed or disappeared"
        assert sum("amount > 0" in d or "amount > (0" in d for d in after.values()) == 1
        assert sum("provider" in d and "paypal" in d for d in after.values()) == 1
        ins = "insert into public.transactions (user_id, provider, provider_order_id, amount, currency) values (%s, %s, %s, %s, 'USD')"
        with pytest.raises(psycopg.errors.CheckViolation):
            db.run(ins, (USER_A, "paypal", "A0", 0))
        with pytest.raises(psycopg.errors.CheckViolation):
            db.run(ins, (USER_A, "stripe", "A1", 5))

    def test_combined_check_mentioning_status_and_another_column_is_preserved(self, db):  # scenario 4
        import psycopg
        db.base()
        seed_users(db)
        db.run("alter table public.transactions add constraint ck_failed_needs_reason check (status <> 'failed' or failure_reason is not null)")
        before = txn_checks(db)["ck_failed_needs_reason"]
        db.apply(MONEY)
        after = txn_checks(db)
        assert after["ck_failed_needs_reason"] == before
        assert [n for n, d in after.items() if "partially_refunded" in d] == ["transactions_status_check"]
        ins = "insert into public.transactions (user_id, provider, provider_order_id, amount, currency, status, failure_reason) values (%s, 'paypal', %s, 5, 'USD', %s, %s)"
        with pytest.raises(psycopg.errors.CheckViolation):
            db.run(ins, (USER_A, "F1", "failed", None))  # the combined rule is still enforced
        db.run(ins, (USER_A, "F2", "failed", "card declined"))
        db.run(ins, (USER_A, "F3", "partially_refunded", None))


class TestStatusConstraintFailsClosed:
    def test_missing_status_constraint_raises_and_changes_nothing(self, db):  # scenario 5
        import psycopg
        db.base()
        seed_legacy_data(db)
        db.run("alter table public.transactions drop constraint transactions_status_check")
        before = snapshot(db)
        with pytest.raises(psycopg.errors.RaiseException, match=r"money_integrity: no CHECK"):
            db.apply(MONEY)
        assert_migration_not_applied(db, before)

    def test_ambiguous_status_constraints_raise_and_change_nothing(self, db):  # scenario 6
        import psycopg
        db.base()
        seed_legacy_data(db)
        db.run("alter table public.transactions add constraint zz_status_dup check (status in ('created','pending','completed','failed','refunded','cancelled'))")
        before = snapshot(db)
        with pytest.raises(psycopg.errors.RaiseException, match=r"more than one"):
            db.apply(MONEY)
        assert_migration_not_applied(db, before)

    def test_ambiguity_between_an_old_and_a_new_style_list_also_raises(self, db):  # scenario 6 (variant)
        import psycopg
        db.base()
        db.run("alter table public.transactions add constraint zz_status_new check (status in ('created','pending','completed','partially_refunded','refunded','failed','cancelled'))")
        with pytest.raises(psycopg.errors.RaiseException, match=r"more than one"):
            db.apply(MONEY)

    @pytest.mark.parametrize("definition", [
        "status in ('created','pending','completed','failed','refunded')",                          # a status missing
        "status in ('created','pending','completed','failed','refunded','cancelled','weird')",   # a status too many
    ], ids=["missing-value", "extra-value"])
    def test_unexpected_single_column_status_check_raises_and_nothing_is_dropped(self, db, definition):  # scenario 6b
        import psycopg
        db.base()
        seed_users(db)
        db.run("alter table public.transactions drop constraint transactions_status_check")
        db.run(f"alter table public.transactions add constraint custom_status_rule check ({definition})")
        before = snapshot(db)
        defs_before = txn_checks(db)
        with pytest.raises(psycopg.errors.RaiseException, match=r"unexpected single-column CHECK"):
            db.apply(MONEY)
        assert txn_checks(db) == defs_before and "custom_status_rule" in txn_checks(db)  # not dropped
        assert_migration_not_applied(db, before)  # atomic: even the statements that ran before the DO block were rolled back

    def test_an_unrecognised_status_check_next_to_a_valid_one_still_refuses(self, db):  # scenario 6b (variant: never drop silently)
        import psycopg
        db.base()
        db.run("alter table public.transactions add constraint custom_status_rule check (status <> 'weird')")
        before = snapshot(db)
        with pytest.raises(psycopg.errors.RaiseException, match=r"unexpected single-column CHECK"):
            db.apply(MONEY)
        assert_migration_not_applied(db, before)


# ------------------------------------------------------------------ 7-12 trigger state, updated_at
class TestTriggerStatePreserved:
    @pytest.mark.parametrize("state", ["O", "A", "R", "D"])
    def test_trigger_state_and_historical_updated_at_survive(self, db, state):  # scenarios 7, 8, 9, 10, 12
        db.base()
        inv, ids = seed_legacy_data(db)
        if state != "O":
            set_trigger_state(db, state)
        before = stamps(db)
        db.apply(MONEY)
        assert trigger_state(db) == state, "trigger must be restored to EXACTLY its previous state"
        assert stamps(db) == before, "historical transactions.updated_at was rewritten by the backfill"
        # the backfill itself worked whatever the trigger state
        assert db.one("select invoice_version from public.transactions where id = %s", (ids["open"],))[0] == 1
        assert db.one("select refunded_amount from public.transactions where id = %s", (ids["refunded"],))[0] == 100
        # and the trigger is functionally what it was: enabled states still stamp ordinary updates, a disabled one does not
        db.run("update public.transactions set failure_reason = 'x' where id = %s", (ids["failed"],))
        stamped = db.one("select updated_at > '2021-01-01' from public.transactions where id = %s", (ids["failed"],))[0]
        assert stamped is (state in ("O", "A"))  # 'R' only fires under session_replication_role = replica

    def test_normal_trigger_stays_enabled_and_working(self, db):  # scenario 7 (original test kept)
        db.base()
        inv, ids = seed_legacy_data(db)
        before = stamps(db)
        db.apply(MONEY)
        assert stamps(db) == before
        assert db.one("select count(*) from public.refunds where source = 'legacy'")[0] == 1
        assert db.one("select invoice_version from public.payment_links limit 1")[0] == 1
        assert trigger_state(db) == "O"
        db.run("update public.transactions set failure_reason = 'x' where id = %s", (ids["failed"],))
        assert db.one("select updated_at > '2021-01-01' from public.transactions where id = %s", (ids["failed"],))[0] is True

    def test_a_trigger_that_was_already_disabled_stays_disabled(self, db):  # scenario 8 (original test kept)
        db.base()
        seed_legacy_data(db)
        db.run("alter table public.transactions disable trigger trg_txn_touch")
        db.apply(MONEY)
        assert trigger_state(db) == "D"

    def test_other_triggers_are_untouched(self, db):
        db.base()
        seed_legacy_data(db)
        db.run("alter table public.invoices disable trigger trg_invoices_touch")
        db.run("alter table public.ai_conversations enable always trigger trg_conv_touch")
        q = "select tgrelid::regclass::text, tgname, tgenabled from pg_trigger where not tgisinternal and tgname <> 'trg_refunds_touch' order by 1, 2"
        before = db.all(q)
        db.apply(MONEY)
        assert db.all(q) == before
        states = {n: s for _, n, s in before}
        assert states["trg_invoices_touch"] == "D" and states["trg_conv_touch"] == "A"

    def test_missing_touch_trigger_is_handled_and_backfill_still_works(self, db):  # scenario 11
        db.base()
        inv, ids = seed_legacy_data(db)
        db.run("drop trigger trg_txn_touch on public.transactions")
        assert trigger_rows(db) == []
        before = stamps(db)
        db.apply(MONEY)
        assert trigger_rows(db) == []  # the migration does not (re)create it
        assert stamps(db) == before
        assert db.one("select invoice_version from public.transactions where id = %s", (ids["open"],))[0] == 1
        assert db.one("select refunded_amount from public.transactions where id = %s", (ids["refunded"],))[0] == 100
        assert db.one("select count(*) from public.refunds where source = 'legacy'")[0] == 1

    @pytest.mark.parametrize("state", ["O", "A", "R", "D"])
    def test_a_renamed_touch_trigger_is_found_by_function(self, db, state):  # scenario 7-10 (by function, not by name)
        db.base()
        inv, ids = seed_legacy_data(db)
        set_trigger_state(db, state)
        db.run("alter trigger trg_txn_touch on public.transactions rename to zz_renamed_touch")
        before = stamps(db)
        db.apply(MONEY)
        assert trigger_state(db, "zz_renamed_touch") == state
        assert stamps(db) == before

    def test_the_status_of_triggers_not_running_touch_updated_at_is_irrelevant(self, db):
        db.base()
        inv, ids = seed_legacy_data(db)
        db.run("create function public.zz_noop() returns trigger language plpgsql as $$ begin return new; end $$")
        db.run("create trigger zz_other before update on public.transactions for each row execute function public.zz_noop()")
        db.run("alter table public.transactions disable trigger zz_other")
        db.apply(MONEY)
        assert trigger_state(db, "zz_other") == "D" and trigger_state(db) == "O"


# ------------------------------------------------------------------ 13-17 backfill correctness / reruns
def seed_backfill_scenario(db: Db):
    """Pre-migration data covering every backfill rule. invoices.version is pre-added so a version other than 1 can be used."""
    db.base()
    seed_users(db)
    db.run("alter table public.invoices add column version int not null default 1")
    inv3 = str(uuid.uuid4())
    db.run("insert into public.invoices (id, user_id, invoice_number, subtotal, total_amount, status, version) values (%s, %s, 'INV-3', 100, 100, 'pending', 3)", (inv3, USER_A))
    t = {
        "created": add_txn(db, "created", "created", inv3),
        "pending": add_txn(db, "pending", "pending", inv3),
        "completed": add_txn(db, "completed", "completed", inv3),
        "failed": add_txn(db, "failed", "failed", inv3),
        "cancelled": add_txn(db, "cancelled", "cancelled", inv3),
        "refunded": add_txn(db, "refunded", "refunded", inv3, amount=70),
        "refunded_noinv": add_txn(db, "refunded2", "refunded", None, amount=40),
        "open_noinv": add_txn(db, "open2", "created", None),
    }
    db.run("insert into public.payment_links (user_id, invoice_id, provider, url, amount, currency) values (%s, %s, 'paypal', 'https://x', 100, 'USD')", (USER_A, inv3))
    db.run("insert into public.payment_links (user_id, provider, url, amount, currency) values (%s, 'paypal', 'https://y', 100, 'USD')", (USER_A,))
    return inv3, t


class TestBackfill:
    def test_backfill_correctness(self, db):  # scenario 13
        inv3, t = seed_backfill_scenario(db)
        db.apply(MONEY)
        version = lambda k: db.one("select invoice_version from public.transactions where id = %s", (t[k],))[0]
        assert version("created") == 3 and version("pending") == 3  # open orders get the invoice's CURRENT version
        for k in ("completed", "failed", "cancelled", "refunded"):
            assert version(k) is None, f"{k} is history and must keep invoice_version NULL"
        assert version("open_noinv") is None and version("refunded_noinv") is None  # no invoice -> nothing to copy
        refunded = lambda k: db.one("select refunded_amount from public.transactions where id = %s", (t[k],))[0]
        assert refunded("refunded") == 70 and refunded("refunded_noinv") == 40
        assert all(refunded(k) == 0 for k in ("created", "pending", "completed", "failed", "cancelled", "open_noinv"))
        legacy = {str(r[0]): (r[1], r[2], r[3], r[4]) for r in db.all("select transaction_id, provider_refund_id, amount, status, source from public.refunds")}
        assert set(legacy) == {t["refunded"], t["refunded_noinv"]}
        assert legacy[t["refunded"]] == (f"legacy-{t['refunded']}", 70, "completed", "legacy")
        assert legacy[t["refunded_noinv"]][2:] == ("completed", "legacy") and legacy[t["refunded_noinv"]][1] == 40
        assert db.all("select invoice_id is null, invoice_version from public.payment_links order by 1") == [(False, 3), (True, None)]
        assert db.one("select user_id from public.refunds limit 1")[0] == uuid.UUID(USER_A)

    def test_rerun_before_any_real_refund_exists_changes_nothing(self, db):  # scenario 14
        inv3, t = seed_backfill_scenario(db)
        db.apply(MONEY)
        first = snapshot(db)
        db.apply(MONEY)
        db.apply(MONEY)
        assert snapshot(db) == first
        assert db.one("select count(*) from public.refunds")[0] == 2

    def test_rerun_after_real_refunds_exist_changes_nothing(self, db):  # scenario 15
        inv3, t = seed_backfill_scenario(db)
        db.apply(MONEY)
        # life after the migration: a partial refund, a full api refund, an order that moved on, a completed order without a version
        db.run("insert into public.refunds (user_id, transaction_id, provider, provider_refund_id, amount, currency, source, idempotency_key) values (%s, %s, 'paypal', 'REAL-1', 30, 'USD', 'api', 'K1')", (USER_A, t["completed"]))
        db.run("update public.transactions set status = 'partially_refunded', refunded_amount = 30 where id = %s", (t["completed"],))
        full = add_txn(db, "full-api", "refunded", inv3, amount=20, stamp="2021-05-05", refunded_amount=20)
        db.run("insert into public.refunds (user_id, transaction_id, provider, provider_refund_id, amount, currency, source) values (%s, %s, 'paypal', 'REAL-2', 20, 'USD', 'webhook')", (USER_A, full))
        db.run("update public.transactions set invoice_version = 9 where id = %s", (t["created"],))
        add_txn(db, "late-completed", "completed", inv3, stamp="2021-06-06")
        first = snapshot(db)
        db.apply(MONEY)
        assert snapshot(db) == first, "a rerun after refunds exist must not change ANY row, constraint, index, policy or trigger"
        db.apply(MONEY)
        assert snapshot(db) == first
        assert db.one("select invoice_version from public.transactions where id = %s", (t["created"],))[0] == 9  # versions are never reset

    def test_no_duplicate_legacy_refund_is_ever_created(self, db):  # scenario 16
        inv3, t = seed_backfill_scenario(db)
        db.apply(MONEY)
        legacy_before = db.all("select * from public.refunds where source = 'legacy' order by id")
        assert len(legacy_before) == 2
        # a fully refunded payment that already has a REAL refund row gets no legacy row, ever
        real = add_txn(db, "real-refunded", "refunded", None, amount=50, refunded_amount=50)
        db.run("insert into public.refunds (user_id, transaction_id, provider, provider_refund_id, amount, currency, source) values (%s, %s, 'paypal', 'REAL-X', 50, 'USD', 'api')", (USER_A, real))
        real_row = db.all("select * from public.refunds where transaction_id = %s", (real,))
        for _ in range(3):
            db.apply(MONEY)
        assert db.all("select * from public.refunds where source = 'legacy' order by id") == legacy_before
        assert db.all("select * from public.refunds where transaction_id = %s", (real,)) == real_row
        assert db.one("select count(*) from public.refunds")[0] == 3
        assert db.one("select count(*) from public.refunds where transaction_id = %s", (t["refunded"],))[0] == 1

    def test_existing_rows_are_preserved(self, db):  # scenario 17 (original test kept and extended)
        db.base()
        inv, ids = seed_legacy_data(db)
        counts = lambda: {tb: db.one(f"select count(*) from public.{tb}")[0] for tb in ("profiles", "invoices", "transactions", "payment_links", "customers", "invoice_items")}
        before_counts = counts()
        columns = db.all("select id, user_id, invoice_id, provider, provider_order_id, amount, currency, status, created_at from public.transactions order by id")
        whole = lambda tb, drop: db.all(f"select coalesce(jsonb_agg((to_jsonb(x) - %s::text[]) order by to_jsonb(x)::text), '[]') from public.{tb} x", (drop,))
        rows_before = {"profiles": whole("profiles", []), "invoices": whole("invoices", []), "transactions": whole("transactions", ["updated_at"])}
        db.apply(MONEY)
        assert counts() == before_counts
        assert db.all("select id, user_id, invoice_id, provider, provider_order_id, amount, currency, status, created_at from public.transactions order by id") == columns
        assert whole("profiles", []) == rows_before["profiles"]
        added = ["invoice_version", "refunded_amount"]
        assert whole("transactions", ["updated_at"] + added) == rows_before["transactions"]
        assert whole("invoices", ["version"]) == rows_before["invoices"]

    def test_rerunning_the_migration_changes_nothing_and_does_not_fail(self, db):  # original rerun test, now with the full snapshot
        db.base()
        inv, ids = seed_legacy_data(db)
        db.apply(MONEY)
        first = snapshot(db)
        db.apply(MONEY)
        db.apply(MONEY)
        assert snapshot(db) == first


# ------------------------------------------------------------------ 18-21 refund table
class TestRefundsTable:
    def _ready(self, db):
        db.base()
        inv, ids = seed_legacy_data(db)
        db.apply(MONEY)
        return inv, ids

    def test_refund_constraints_indexes_and_uniqueness(self, db):  # scenario 18 (original test kept)
        import psycopg
        inv, ids = self._ready(db)
        t = ids["completed"]
        ins = lambda rid, amount=10, key=None, provider="paypal", status="completed": db.run(
            "insert into public.refunds (user_id, transaction_id, provider, provider_refund_id, amount, currency, status, idempotency_key) values (%s, %s, %s, %s, %s, 'USD', %s, %s)",
            (USER_A, t, provider, rid, amount, status, key))
        ins("R1")
        with pytest.raises(psycopg.errors.UniqueViolation):
            ins("R1")  # a provider refund id can be recorded only once
        ins("R2", key=None)
        ins("R3", key=None)  # several NULL idempotency keys are fine
        ins("R4", key="K-1")
        with pytest.raises(psycopg.errors.UniqueViolation):
            ins("R5", key="K-1")  # the same idempotency key twice for one payment
        for bad in (dict(amount=0), dict(amount=-1), dict(provider="stripe"), dict(status="weird")):
            with pytest.raises(psycopg.errors.CheckViolation):
                ins(f"BAD-{uuid.uuid4().hex[:6]}", **bad)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.run("update public.transactions set refunded_amount = 101 where id = %s", (t,))  # more than the payment
        with pytest.raises(psycopg.errors.CheckViolation):
            db.run("update public.transactions set refunded_amount = -1 where id = %s", (t,))
        assert {r[0] for r in db.all("select indexname from pg_indexes where tablename = 'refunds'")} >= {"ix_refunds_txn", "ix_refunds_user"}
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            db.run("insert into public.refunds (user_id, transaction_id, provider, provider_refund_id, amount, currency) values (%s, %s, 'paypal', 'ORPHAN', 5, 'USD')", (USER_A, str(uuid.uuid4())))
        db.run("delete from public.transactions where id = %s", (t,))
        assert db.one("select count(*) from public.refunds where transaction_id = %s", (t,))[0] == 0

    def test_provider_refund_id_uniqueness_is_per_provider(self, db):  # scenario 19
        import psycopg
        inv, ids = self._ready(db)
        rz = add_txn(db, "rz", "completed", None)
        db.run("update public.transactions set provider = 'razorpay' where id = %s", (rz,))
        ins = lambda txn, provider, rid: db.run(
            "insert into public.refunds (user_id, transaction_id, provider, provider_refund_id, amount, currency) values (%s, %s, %s, %s, 5, 'USD')", (USER_A, txn, provider, rid))
        ins(ids["completed"], "paypal", "SAME-ID")
        with pytest.raises(psycopg.errors.UniqueViolation):
            ins(ids["completed"], "paypal", "SAME-ID")
        with pytest.raises(psycopg.errors.UniqueViolation):
            ins(ids["open"], "paypal", "SAME-ID")  # even for another transaction: provider ids are globally unique per provider
        ins(rz, "razorpay", "SAME-ID")  # a different provider may reuse the string
        assert db.one("select count(*) from public.refunds where provider_refund_id = 'SAME-ID'")[0] == 2

    def test_idempotency_key_uniqueness_allows_nulls_and_is_per_transaction(self, db):  # scenario 20
        import psycopg
        inv, ids = self._ready(db)
        ins = lambda txn, rid, key: db.run(
            "insert into public.refunds (user_id, transaction_id, provider, provider_refund_id, amount, currency, idempotency_key) values (%s, %s, 'paypal', %s, 5, 'USD', %s)", (USER_A, txn, rid, key))
        for i in range(3):
            ins(ids["completed"], f"N{i}", None)  # NULLs never collide
        ins(ids["completed"], "K1", "key-1")
        ins(ids["open"], "K2", "key-1")  # the same key on ANOTHER transaction is fine
        with pytest.raises(psycopg.errors.UniqueViolation):
            ins(ids["completed"], "K3", "key-1")
        assert db.one("select count(*) from public.refunds where idempotency_key is null and transaction_id = %s", (ids["completed"],))[0] == 3

    def test_refunds_rls_isolates_owners_and_blocks_client_writes(self, db):  # scenario 21
        """LIMITS: vanilla PostgreSQL + the shim's roles/auth.uid() (claims set via set_config). Real Supabase RLS (PostgREST, GoTrue claims,
        real grants, service_role handling) is NOT covered; this only proves the policy text behaves as written."""
        import psycopg
        inv, ids = self._ready(db)
        db.run("insert into public.refunds (user_id, transaction_id, provider, provider_refund_id, amount, currency) values (%s, %s, 'paypal', 'RA', 5, 'USD')", (USER_A, ids["completed"]))
        assert db.one("select relrowsecurity from pg_class where oid = 'public.refunds'::regclass")[0] is True
        assert [r[0] for r in db.all("select policyname from pg_policies where tablename = 'refunds'")] == ["refunds_select"]
        assert db.one("select cmd from pg_policies where tablename = 'refunds'")[0] == "SELECT"

        def as_user(uid, sql, params=None):
            db.run("select set_config('request.jwt.claim.sub', %s, false), set_config('request.jwt.claim.role', 'authenticated', false)", (uid,))
            db.run("set role authenticated")
            try:
                return db.run(sql, params).fetchall()
            finally:
                db.run("reset role")

        assert len(as_user(USER_A, "select * from public.refunds")) == 2  # the owner sees their refunds: the one just inserted + the legacy backfill row
        assert as_user(USER_B, "select * from public.refunds") == []  # another user sees nothing
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # clients cannot create, change or delete refunds
            as_user(USER_A, "insert into public.refunds (user_id, transaction_id, provider, provider_refund_id, amount, currency) values (%s, %s, 'paypal', 'FORGED', 1, 'USD')", (USER_A, ids["completed"]))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):  # clients hold SELECT only (revoked at the GRANT level, not just filtered by RLS)
            as_user(USER_A, "update public.refunds set amount = 1 returning id")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            as_user(USER_A, "delete from public.refunds returning id")
        assert db.one("select amount from public.refunds where provider_refund_id = 'RA'")[0] == 5
        db.run("set role anon")  # the anonymous role has no access at all
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.run("select * from public.refunds")
        db.run("reset role")


# ------------------------------------------------------------------ 22 rollback
class TestAtomicity:
    def _poison(self, db):
        """After a first successful run: put a refunded row back to refunded_amount 0 and add a NOT VALID check that makes the backfill UPDATE fail."""
        db.apply(MONEY)
        ids = {"refunded": db.one("select id from public.transactions where status = 'refunded'")[0]}
        db.run("update public.transactions set refunded_amount = 0 where id = %s", (ids["refunded"],))
        db.run("alter table public.transactions add constraint zz_block check (refunded_amount = 0) not valid")
        return ids

    @pytest.mark.parametrize("state", ["O", "A", "R", "D"])
    def test_if_the_backfill_fails_the_whole_migration_rolls_back_exactly(self, db, state):  # scenario 22 (full script)
        import psycopg
        db.base()
        seed_legacy_data(db)
        self._poison(db)
        set_trigger_state(db, state)
        before = snapshot(db)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.apply(MONEY)
        assert trigger_state(db) == state, "trigger state changed by a failed run"
        assert snapshot(db) == before  # trigger, constraints, columns, indexes, policies and ALL data exactly as before the attempt

    @pytest.mark.parametrize("state", ["O", "A", "R"])
    def test_backfill_block_alone_restores_the_trigger_when_it_fails_and_when_it_succeeds(self, db, state):  # scenario 22 (DO block alone)
        import psycopg
        db.base()
        inv, ids = seed_legacy_data(db)
        self._poison(db)
        set_trigger_state(db, state)
        block = _backfill_block(MONEY.read_text(encoding="utf-8"))
        before = snapshot(db)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.run(block)
        assert trigger_state(db) == state  # the DO block rolled back as one unit, including the trigger change
        assert snapshot(db) == before
        db.run("alter table public.transactions drop constraint zz_block")
        stamped = stamps(db)
        db.run(block)  # now it can succeed: trigger restored to the same state, rows backfilled, updated_at untouched
        assert trigger_state(db) == state
        assert stamps(db) == stamped
        assert db.one("select refunded_amount from public.transactions where status = 'refunded'")[0] == 100

    def test_a_failing_run_leaves_no_partial_schema_behind(self, db):
        """Nothing of the migration (new columns, refunds table) survives when a late step fails."""
        import psycopg
        db.base()
        seed_legacy_data(db)
        # make the late refunded_amount CHECK fail: a pre-existing column with data that violates refunded_amount <= amount
        db.run("alter table public.transactions add column refunded_amount numeric(14,2) not null default 0")
        db.run("update public.transactions set refunded_amount = 500 where status = 'failed'")
        before = snapshot(db)
        with pytest.raises(psycopg.errors.CheckViolation):
            db.apply(MONEY)
        assert_migration_not_applied(db, before)


# ------------------------------------------------------------------ 23 fee/net guards
def _first_version_init():
    try:
        out = subprocess.run(["git", "show", f"{FIRST_INIT_COMMIT}:supabase/migrations/20260101000000_init_schema.sql"], cwd=ROOT, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.decode("utf-8") if out.returncode == 0 and out.stdout else None


class TestSchemaCompatibilityGuards:
    def test_first_version_init_schema_gets_fee_and_net_columns_without_touching_data(self, db):  # scenario 23
        old = _first_version_init()
        if old is None:
            pytest.skip(f"git object {FIRST_INIT_COMMIT} (first version of init_schema.sql) is not available")
        col = lambda: db.all("select column_name, data_type, numeric_precision, numeric_scale, is_nullable, column_default from information_schema.columns where table_schema = 'public' and table_name = 'transactions' and column_name in ('fee_amount', 'net_amount') order by 1")
        db.conn.execute(SHIM.read_text(encoding="utf-8"))
        db.apply_text(old)
        db.apply(RLS)
        assert col() == []  # the first version really lacks them
        inv, ids = seed_legacy_data(db)
        rows = lambda: db.one("select coalesce(jsonb_agg(to_jsonb(x) - 'invoice_version' - 'refunded_amount' - 'fee_amount' - 'net_amount' order by to_jsonb(x)::text), '[]') from public.transactions x")[0]
        before_rows = rows()
        db.apply(MONEY)
        assert col() == [("fee_amount", "numeric", 14, 2, "YES", None), ("net_amount", "numeric", 14, 2, "YES", None)]
        assert rows() == before_rows  # existing data untouched (updated_at too: it is part of the row)
        assert db.one("select count(*) from public.transactions where fee_amount is not null or net_amount is not null")[0] == 0
        again = snapshot(db)
        db.apply(MONEY)
        assert snapshot(db) == again

    def test_rerun_on_the_current_schema_is_a_noop_for_fee_and_net(self, db):  # scenario 23 (current schema)
        db.base()
        inv, ids = seed_legacy_data(db)
        db.run("update public.transactions set fee_amount = 1.50, net_amount = 98.50 where id = %s", (ids["completed"],))
        definition = lambda: db.all("select column_name, data_type, numeric_precision, numeric_scale, is_nullable from information_schema.columns where table_name = 'transactions' and column_name in ('fee_amount', 'net_amount') order by 1")
        before_def = definition()
        db.apply(MONEY)
        db.apply(MONEY)
        assert definition() == before_def
        row = db.one("select fee_amount::text, net_amount::text from public.transactions where id = %s", (ids["completed"],))
        assert row == ("1.50", "98.50")


# ------------------------------------------------------------------ 24 lock timeout
class TestLockTimeout:
    def test_blocked_migration_fails_fast_with_lock_not_available_and_changes_nothing(self, db):  # scenario 24
        import psycopg
        db.base()
        seed_legacy_data(db)
        before = snapshot(db)
        locker = pg_safety.connect_verified(db.url, autocommit=False)  # explicit transaction, holds the lock until rolled back
        try:
            locker.execute("lock table public.transactions in access exclusive mode")
            db.run(f"set {LOCK_ENV} = '500ms'")
            start = time.monotonic()
            with pytest.raises(psycopg.errors.LockNotAvailable):
                db.apply(MONEY)
            elapsed = time.monotonic() - start
        finally:
            locker.rollback()
            locker.close()
        assert 0.3 < elapsed < 5, f"lock_timeout was not honoured: {elapsed:.2f}s"
        assert_migration_not_applied(db, before)
        # with the lock gone the very same session now succeeds
        db.apply(MONEY)
        assert db.one("select to_regclass('public.refunds') is not null")[0] is True

    def test_session_lock_timeout_is_not_left_changed_after_a_successful_run(self, db):  # scenario 24
        db.base()
        seed_legacy_data(db)
        default = db.one("show lock_timeout")[0]
        db.run(f"set {LOCK_ENV} = '500ms'")
        db.apply(MONEY)
        assert db.one("show lock_timeout")[0] == default
        db.apply(MONEY)  # a rerun, without the override, uses the 10s default internally and still leaves nothing behind
        assert db.one("show lock_timeout")[0] == default

    def test_the_lock_timeout_is_effective_inside_an_explicit_transaction_too(self, db):
        import psycopg
        db.base()
        seed_legacy_data(db)
        locker = pg_safety.connect_verified(db.url, autocommit=False)
        try:
            locker.execute("lock table public.transactions in access exclusive mode")
            db.run(f"set {LOCK_ENV} = '500ms'")
            start = time.monotonic()
            with pytest.raises(psycopg.errors.LockNotAvailable):
                db.apply_text("begin;\n" + MONEY.read_text(encoding="utf-8") + "\ncommit;")
            assert time.monotonic() - start < 5
        finally:
            locker.rollback()
            locker.close()
        db.run("rollback")  # leave no aborted transaction behind (the script's own BEGIN was not completed)


# ------------------------------------------------------------------ integration correction: the status list is compared as a SET
# The first version sorted the literals and compared arrays. Sorting text depends on the database collation and the comparison on the order the
# literals happen to be written in, so a semantically identical CHECK could be refused (fail closed, but wrongly). It is now an order-independent
# set comparison (each side contains the other).
class TestStatusListComparedAsASet:
    ORIGINAL_SIX_SHUFFLED = "('refunded','pending','failed','cancelled','created','completed')"
    NEW_SEVEN_SHUFFLED = "('refunded','partially_refunded','pending','failed','cancelled','created','completed')"

    def _replace_status_check(self, db: Db, name: str, definition: str):
        db.run("alter table public.transactions drop constraint transactions_status_check")
        db.run(f"alter table public.transactions add constraint {name} check (status in {definition})")

    def test_the_migration_compares_literal_sets_not_sorted_arrays(self):
        sql = _norm(MONEY.read_text(encoding="utf-8"))
        assert "order by m[1]" not in sql and "literals = orig_six" not in sql and "literals = new_seven" not in sql
        for side in ("orig_six", "new_seven"):
            assert f"literals @> {side}" in sql and f"literals <@ {side}" in sql  # both directions = set equality

    def test_the_original_list_written_in_any_order_is_recognised_and_replaced(self, db):
        db.base()
        seed_legacy_data(db)
        self._replace_status_check(db, "shuffled_status_rule", self.ORIGINAL_SIX_SHUFFLED)
        db.apply(MONEY)
        checks = txn_checks(db)
        assert "shuffled_status_rule" not in checks
        assert [n for n, d in checks.items() if "partially_refunded" in d] == ["transactions_status_check"]

    def test_a_previous_output_in_a_different_order_is_accepted_on_rerun(self, db):
        db.base()
        seed_legacy_data(db)
        db.apply(MONEY)
        self._replace_status_check(db, "shuffled_new_rule", self.NEW_SEVEN_SHUFFLED)
        db.apply(MONEY)  # must not raise
        checks = txn_checks(db)
        assert "shuffled_new_rule" not in checks and "transactions_status_check" in checks

    def test_duplicate_literals_do_not_matter(self, db):
        db.base()
        seed_legacy_data(db)
        self._replace_status_check(db, "dup_status_rule", "('created','created','pending','completed','failed','refunded','cancelled','cancelled')")
        db.apply(MONEY)
        assert "dup_status_rule" not in txn_checks(db)

    @pytest.mark.parametrize("definition", [
        "('refunded','pending','failed','created','completed')",  # 'cancelled' missing, shuffled
        "('refunded','pending','failed','cancelled','created','completed','weird')",  # extra value, shuffled
        "('partially_refunded','refunded','pending','failed','created','completed')",  # new status but 'cancelled' missing
    ])
    def test_an_incomplete_or_extended_list_is_still_refused_whatever_its_order(self, db, definition):
        import psycopg
        db.base()
        seed_legacy_data(db)
        self._replace_status_check(db, "odd_status_rule", definition)
        before = snapshot(db)
        with pytest.raises(psycopg.errors.RaiseException, match=r"money_integrity: unexpected single-column CHECK"):
            db.apply(MONEY)
        assert_migration_not_applied(db, before)
        assert "odd_status_rule" in txn_checks(db)  # not dropped


# ================================================================== client write hardening (20260102000100) and the refunds lockdown
READ_ONLY_FOR_CLIENTS = ("invoices", "invoice_items", "payment_links", "transactions", "refunds", "audit_logs", "ai_messages")
CLIENT_WRITABLE = ("customers", "profiles", "notifications", "automations", "ai_conversations")
PRIVS = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


def _privs(db: Db, role: str, table: str) -> set:
    return {p for p in PRIVS if db.one("select has_table_privilege(%s, %s, %s)", (role, f"public.{table}", p))[0]}


def _as(db: Db, role: str, uid, sql: str, params=None):
    """Run one statement as a Data-API role (the shim's claim settings stand in for the JWT). Always resets the role."""
    db.run("select set_config('request.jwt.claim.sub', %s, false), set_config('request.jwt.claim.role', %s, false)", (uid or "", role))
    db.run(f"set role {role}")
    try:
        cur = db.run(sql, params)
        return cur.fetchall() if cur.description else []
    finally:
        db.run("reset role")


class TestClientWriteHardeningText:
    def _sql(self):
        return HARDEN.read_text(encoding="utf-8")

    def test_the_migration_files_are_the_reviewed_set_in_apply_order(self):
        assert sorted(p.name for p in MIGRATIONS.glob("*.sql")) == ["20260101000000_init_schema.sql", "20260101000100_rls_policies.sql",
                                                                      "20260102000000_money_integrity.sql", "20260102000100_client_write_hardening.sql"]

    def test_every_test_utility_lists_the_same_migrations(self):
        from tests.support import pg_migrated

        assert list(pg_migrated.MIGRATION_FILES) == sorted(p.name for p in MIGRATIONS.glob("*.sql"))

    def test_invoices_and_items_become_read_only_for_clients(self):
        low = _norm(self._sql())
        assert "drop policy if exists invoices_all on public.invoices" in low and "drop policy if exists invoice_items_all on public.invoice_items" in low
        assert "create policy invoices_select on public.invoices for select to authenticated" in low
        assert "create policy invoice_items_select on public.invoice_items for select to authenticated" in low
        for forbidden in (" for all", " for insert", " for update", " for delete"):
            assert forbidden not in low

    def test_every_policy_is_dropped_before_it_is_created_so_reruns_work(self):
        low = _norm(self._sql())
        names = re.findall(r"create policy (\w+) on", low)
        assert names
        for name in names:
            assert f"drop policy if exists {name} on" in low and low.index(f"drop policy if exists {name} on") < low.index(f"create policy {name} on")

    def test_financial_tables_are_read_only_at_the_grant_level_too(self):
        low = _norm(self._sql())
        revoked = re.search(r"revoke all on (.*?) from authenticated;", low).group(1)
        granted = re.search(r"grant select on (.*?) to authenticated;", low).group(1)
        expected = {f"public.{t}" for t in READ_ONLY_FOR_CLIENTS}
        assert {x.strip() for x in revoked.split(",")} == expected and {x.strip() for x in granted.split(",")} == expected
        assert low.index("revoke all on public.invoices") < low.index("grant select on public.invoices")
        assert "revoke all on all tables in schema public from anon;" in low

    def test_the_script_has_no_transaction_control_of_its_own(self):
        assert not re.search(r"(^|;) ?(begin|commit|rollback)\b", _norm(self._sql()))


class TestClientWriteHardening:
    """LIMITS: vanilla PostgreSQL + the shim's roles/auth.uid(). Real Supabase (PostgREST, real default privileges, real JWT claims) is NOT covered."""

    def _ready(self, db: Db, harden: bool = True):
        db.base()
        inv_a, ids = seed_legacy_data(db)
        inv_b = str(uuid.uuid4())
        db.run("insert into public.invoices (id, user_id, invoice_number, subtotal, total_amount, status) values (%s, %s, 'INV-B', 50, 50, 'pending')", (inv_b, USER_B))
        db.run("insert into public.invoice_items (invoice_id, description, quantity, unit_price, total) values (%s, 'item a', 1, 100, 100), (%s, 'item b', 1, 50, 50)", (inv_a, inv_b))
        db.run("insert into public.customers (user_id, name) values (%s, 'Cust A'), (%s, 'Cust B')", (USER_A, USER_B))
        db.apply(MONEY)
        if harden:
            db.apply(HARDEN)
        return inv_a, inv_b, ids

    def test_control_without_the_hardening_a_client_can_tamper_with_its_invoice(self, db):
        """Proves the problem and gives the tests below teeth: before 20260102000100 any signed-in user could rewrite their own invoices."""
        inv_a, _, _ = self._ready(db, harden=False)
        assert _as(db, "authenticated", USER_A, "update public.invoices set total_amount = 1, version = 99 where id = %s returning id", (inv_a,)) == [(uuid.UUID(inv_a),)]
        assert len(_as(db, "authenticated", USER_A, "delete from public.invoice_items where invoice_id = %s returning id", (inv_a,))) == 1
        assert len(_as(db, "authenticated", USER_A, "delete from public.invoices where id = %s returning id", (inv_a,))) == 1  # even the invoice of a paid order

    def test_hardened_clients_still_read_only_their_own_invoices_and_items(self, db):
        inv_a, inv_b, _ = self._ready(db)
        assert _as(db, "authenticated", USER_A, "select id from public.invoices") == [(uuid.UUID(inv_a),)]
        assert _as(db, "authenticated", USER_B, "select id from public.invoices") == [(uuid.UUID(inv_b),)]
        assert len(_as(db, "authenticated", USER_A, "select id from public.invoice_items")) == 1  # only the items of their own invoice

    def test_hardened_clients_cannot_modify_invoices_or_items(self, db):
        import psycopg
        inv_a, inv_b, _ = self._ready(db)
        cust_b = db.one("select id from public.customers where name = 'Cust B'")[0]
        attempts = [
            ("update public.invoices set total_amount = 1 where id = %s", (inv_a,)),
            ("update public.invoices set status = 'cancelled', version = 99, currency = 'EUR' where id = %s", (inv_a,)),
            ("update public.invoices set status = 'pending' where id = %s", (inv_a,)),
            ("update public.invoices set customer_id = %s where id = %s", (cust_b, inv_a)),  # another tenant's customer
            ("delete from public.invoices where id = %s", (inv_a,)),
            ("insert into public.invoices (user_id, invoice_number) values (%s, 'FORGED')", (USER_A,)),
            ("update public.invoice_items set unit_price = 0", None),
            ("delete from public.invoice_items", None),
            ("insert into public.invoice_items (invoice_id, description, unit_price, total) values (%s, 'x', 1, 1)", (inv_a,)),
            ("truncate public.invoices", None),
            ("truncate public.invoice_items", None),
        ]
        for sql, params in attempts:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                _as(db, "authenticated", USER_A, sql, params)
        assert db.one("select total_amount, status, version from public.invoices where id = %s", (inv_a,)) == (100, "pending", 1)  # nothing changed
        assert db.one("select count(*) from public.invoice_items")[0] == 2

    def test_hardened_clients_cannot_write_any_other_financial_table(self, db):
        import psycopg
        inv_a, _, ids = self._ready(db)
        for t in ("payment_links", "transactions", "refunds", "audit_logs", "ai_messages"):
            for sql in (f"update public.{t} set id = id", f"delete from public.{t}", f"truncate public.{t}"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    _as(db, "authenticated", USER_A, sql)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _as(db, "authenticated", USER_A, "insert into public.refunds (user_id, transaction_id, provider, provider_refund_id, amount, currency) values (%s, %s, 'paypal', 'FORGED', 1, 'USD')", (USER_A, ids["completed"]))
        assert len(_as(db, "authenticated", USER_A, "select id from public.transactions")) == 4  # reads are unaffected
        assert len(_as(db, "authenticated", USER_A, "select id from public.refunds")) == 1  # the legacy refund of A's refunded payment
        assert _as(db, "authenticated", USER_B, "select id from public.refunds") == []

    def test_tables_the_hardening_does_not_cover_keep_their_client_access(self, db):
        self._ready(db)
        assert len(_as(db, "authenticated", USER_A, "insert into public.customers (user_id, name) values (%s, 'New') returning id", (USER_A,))) == 1
        assert len(_as(db, "authenticated", USER_A, "update public.profiles set business_name = 'Acme' where id = %s returning id", (USER_A,))) == 1
        assert [r[0] for r in _as(db, "authenticated", USER_A, "select name from public.customers order by name")] == ["Cust A", "New"]

    def test_anon_has_no_access_to_any_table(self, db):
        import psycopg
        self._ready(db)
        for t in READ_ONLY_FOR_CLIENTS + CLIENT_WRITABLE + ("webhook_events",):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                _as(db, "anon", None, f"select * from public.{t}")

    def test_the_backend_owner_path_can_still_write_everything_it_needs(self, db):
        """The FastAPI backend connects as the table owner (DATABASE_URL), never as a Data-API role. LIMIT: the test connection is a superuser
        owner on a disposable server; on Supabase the owner is `postgres`. Both own the tables, which is what the privileges rely on."""
        inv_a, inv_b, ids = self._ready(db)
        assert db.one("select count(*) from pg_tables where schemaname = 'public' and tableowner <> current_user")[0] == 0
        db.run("update public.invoices set total_amount = 120, version = version + 1, status = 'paid' where id = %s", (inv_a,))  # a verified payment marks it paid
        db.run("insert into public.invoice_items (invoice_id, description, quantity, unit_price, total) values (%s, 'extra', 1, 20, 20)", (inv_a,))
        db.run("delete from public.invoice_items where description = 'extra'")
        db.run("update public.transactions set status = 'partially_refunded', refunded_amount = 10 where id = %s", (ids["completed"],))
        db.run("insert into public.refunds (user_id, transaction_id, provider, provider_refund_id, amount, currency, source) values (%s, %s, 'paypal', 'R-OWNER', 10, 'USD', 'api')", (USER_A, ids["completed"]))
        db.run("update public.refunds set updated_at = now() where provider_refund_id = 'R-OWNER'")
        db.run("insert into public.audit_logs (user_id, action) values (%s, 'test')", (USER_A,))
        assert db.one("select status, version from public.invoices where id = %s", (inv_a,)) == ("paid", 2)
        assert db.one("select count(*) from public.refunds where transaction_id = %s", (ids["completed"],))[0] == 1
        assert db.one("select count(*) from pg_trigger where tgname = 'trg_guard_invoice_paid' and not tgisinternal")[0] == 1  # defence in depth is still installed

    def test_the_privilege_matrix_is_exactly_as_designed(self, db):
        self._ready(db)
        for t in READ_ONLY_FOR_CLIENTS:
            assert _privs(db, "authenticated", t) == {"SELECT"}, t
        for t in CLIENT_WRITABLE:
            assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= _privs(db, "authenticated", t), t
        assert _privs(db, "authenticated", "webhook_events") == set()
        for t in READ_ONLY_FOR_CLIENTS + CLIENT_WRITABLE + ("webhook_events",):
            assert _privs(db, "anon", t) == set(), t
            assert {"SELECT", "INSERT", "UPDATE", "DELETE"} <= _privs(db, "service_role", t), t  # the shim's default privileges; the migrations never revoke from it (static test)
        assert db.one("select relrowsecurity from pg_class where oid = 'public.refunds'::regclass")[0] is True
        assert sorted(r[0] for r in db.all("select policyname from pg_policies where tablename in ('invoices', 'invoice_items', 'refunds')")) == [
            "invoice_items_select", "invoices_select", "refunds_select"]

    def test_reruns_change_nothing(self, db):
        self._ready(db)
        matrix = lambda: {(t, r): sorted(_privs(db, r, t)) for t in READ_ONLY_FOR_CLIENTS + CLIENT_WRITABLE + ("webhook_events",) for r in ("anon", "authenticated", "service_role")}
        before_snapshot, before_matrix = snapshot(db), matrix()
        db.apply(MONEY)
        db.apply(HARDEN)
        db.apply(HARDEN)
        assert snapshot(db) == before_snapshot and matrix() == before_matrix

    def test_money_integrity_after_the_hardening_is_still_a_noop_on_policies_and_grants(self, db):
        """Re-running file 3 after file 4 (the documented post-cutover backfill) must not loosen anything file 4 tightened."""
        self._ready(db)
        before = {t: sorted(_privs(db, "authenticated", t)) for t in READ_ONLY_FOR_CLIENTS}
        db.apply(MONEY)
        assert {t: sorted(_privs(db, "authenticated", t)) for t in READ_ONLY_FOR_CLIENTS} == before


class TestRefundsAreNeverExposedWhileTheMigrationRuns:
    """A tool that runs the file statement by statement must never leave refunds readable: the lockdown comes right after CREATE TABLE."""

    def _state(self, db: Db):
        return (db.one("select relrowsecurity from pg_class where oid = 'public.refunds'::regclass")[0],
                db.one("select has_table_privilege('anon', 'public.refunds', 'select')")[0],
                db.one("select has_table_privilege('authenticated', 'public.refunds', 'insert') or has_table_privilege('authenticated', 'public.refunds', 'update') "
                       "or has_table_privilege('authenticated', 'public.refunds', 'delete') or has_table_privilege('authenticated', 'public.refunds', 'truncate')")[0])

    def test_refunds_is_private_before_the_backfill_creates_its_first_row(self, db):
        db.base()
        seed_legacy_data(db)
        sql = MONEY.read_text(encoding="utf-8").replace("\r\n", "\n")
        head, tail = sql.split(BACKFILL_MARK)
        db.run(head)  # everything up to and including CREATE TABLE refunds, but not the backfill that inserts rows
        assert self._state(db) == (True, False, False)
        assert db.one("select count(*) from public.refunds")[0] == 0
        db.run(BACKFILL_MARK + tail)
        assert db.one("select count(*) from public.refunds where source = 'legacy'")[0] == 1
        assert self._state(db) == (True, False, False)

    def test_the_window_check_has_teeth_without_the_lockdown_the_table_is_open(self, db):
        """Negative control: the same split on a copy of the file WITHOUT the lockdown block reproduces the original exposure."""
        db.base()
        seed_legacy_data(db)
        sql = MONEY.read_text(encoding="utf-8").replace("\r\n", "\n")
        start, end = sql.index("-- Lock the new table down IMMEDIATELY."), sql.index("create index if not exists ix_refunds_txn")
        mutated = sql[:start] + sql[end:]
        assert "revoke all on public.refunds from anon;" in mutated  # the end-of-file statements are still there; only the early lockdown is gone
        db.run(mutated.split(BACKFILL_MARK)[0])
        rls, anon_select, client_write = self._state(db)
        assert rls is False and anon_select is True and client_write is True


# ================================================================== the demo-data function is an owner tool, not a client API
class TestSeedFunctionIsNotAClientApi:
    def test_the_seed_file_revokes_execute_from_public_anon_and_authenticated(self):
        low = _norm(SEED.read_text(encoding="utf-8"))
        revoke = "revoke execute on function public.seed_paypilot_demo(uuid) from public, anon, authenticated;"
        assert revoke in low and low.index("create or replace function public.seed_paypilot_demo") < low.index(revoke)
        assert "grant execute" not in low
        assert "security definer" not in low  # a definer function would run with the OWNER's rights for whoever can call it

    def test_no_migration_or_seed_statement_revokes_anything_from_service_role(self):
        for path in list(MIGRATIONS.glob("*.sql")) + [SEED]:
            for stmt in re.findall(r"revoke [^;]*;", _norm(path.read_text(encoding="utf-8"))):
                assert "service_role" not in stmt, (path.name, stmt)

    def test_nothing_in_the_app_calls_the_seed_function(self):
        """If a client workflow ever needed it, this test (and the revoke) must be revisited deliberately."""
        offenders = [str(p.relative_to(ROOT)) for base in ("backend/app", "frontend/src") for p in (ROOT / base).rglob("*")
                     if p.is_file() and p.suffix in (".py", ".ts", ".tsx") and "seed_paypilot_demo" in p.read_text(encoding="utf-8", errors="ignore")]
        assert offenders == []


class TestSeedFunctionPermissions:
    """LIMITS: vanilla PostgreSQL + the shim, whose default privileges mimic Supabase's (functions executable by anon/authenticated/service_role)."""

    def _ready(self, db: Db):
        db.base()
        db.apply(MONEY)
        db.apply(HARDEN)
        db.apply(SEED)

    def test_the_owner_can_run_it_and_it_creates_the_demo_data(self, db):
        self._ready(db)
        db.run("insert into auth.users (id, email) values (%s, 'demo@example.com')", (USER_A,))
        db.run("select public.seed_paypilot_demo(%s)", (USER_A,))
        counts = db.one("select (select count(*) from public.customers), (select count(*) from public.invoices), (select count(*) from public.invoice_items), "
                        "(select count(*) from public.transactions)")
        assert counts == (3, 3, 3, 4)

    def test_clients_cannot_execute_it_the_owner_can_and_service_role_is_left_untouched(self, db):
        """service_role is deliberately NOT asserted to have EXECUTE: that depends on the project's default privileges (the shim only approximates
        Supabase's; the real project must be checked). What is asserted is that the seed file's REVOKE does not change service_role at all."""
        import psycopg
        db.base()
        db.apply(MONEY)
        db.apply(HARDEN)
        seed = SEED.read_text(encoding="utf-8").replace("\r\n", "\n")
        without_revoke = "\n".join(line for line in seed.split("\n") if not line.startswith("revoke execute on function"))
        assert without_revoke != seed
        privilege = lambda role: db.one("select has_function_privilege(%s, 'public.seed_paypilot_demo(uuid)', 'execute')", (role,))[0]
        db.apply_text(without_revoke)
        before = {role: privilege(role) for role in ("anon", "authenticated", "service_role")}
        assert before["anon"] is True and before["authenticated"] is True  # control (shim defaults): without the revoke the function is exposed
        db.apply(SEED)
        assert privilege("anon") is False and privilege("authenticated") is False
        assert privilege("service_role") == before["service_role"]  # untouched, whatever the project's defaults gave it
        assert privilege(db.one("select current_user")[0]) is True  # the owner
        assert db.one("select not exists (select 1 from pg_proc p cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
                      "where p.oid = 'public.seed_paypilot_demo(uuid)'::regprocedure and a.grantee = 0)")[0] is True  # PUBLIC has no EXECUTE
        for role in ("anon", "authenticated"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                _as(db, role, USER_A, "select public.seed_paypilot_demo(%s)", (USER_A,))
        assert db.one("select count(*) from public.customers")[0] == 0  # nothing was written

    def test_rerunning_the_seed_file_keeps_the_revoke(self, db):
        self._ready(db)
        db.apply(SEED)
        db.apply(SEED)
        assert db.one("select has_function_privilege('anon', 'public.seed_paypilot_demo(uuid)', 'execute') or has_function_privilege('authenticated', 'public.seed_paypilot_demo(uuid)', 'execute')")[0] is False


# ================================================================== the signup trigger function is not a client API (but signup still works)
SIGNUP_REVOKE = "revoke execute on function public.handle_new_user() from public, anon, authenticated;"


class TestSignupTriggerFunctionText:
    def test_the_function_exists_before_the_hardening_runs_and_the_revoke_names_its_exact_signature(self):
        init = _norm(INIT.read_text(encoding="utf-8"))
        assert "create or replace function public.handle_new_user() returns trigger" in init  # defined (no arguments) by the first migration
        assert "execute function public.handle_new_user()" in init  # and attached as the signup trigger there
        assert SIGNUP_REVOKE in _norm(HARDEN.read_text(encoding="utf-8"))
        assert INIT.name < HARDEN.name  # applied in filename order

    def test_the_revoke_does_not_name_service_role_or_touch_other_functions(self):
        revokes = re.findall(r"revoke execute [^;]*;", _norm(HARDEN.read_text(encoding="utf-8")))
        assert revokes == [SIGNUP_REVOKE]


class TestSignupTriggerFunctionPermissions:
    """LIMITS: vanilla PostgreSQL + the shim (its default privileges mimic Supabase's); the inserting role stands in for supabase_auth_admin."""

    def _text_without_the_revoke(self) -> str:
        text = HARDEN.read_text(encoding="utf-8").replace("\r\n", "\n")
        stripped = "\n".join(line for line in text.split("\n") if not line.startswith("revoke execute on function public.handle_new_user()"))
        assert stripped != text
        return stripped

    def _roles(self, db: Db):
        """Two throw-away cluster roles: one with NO grants at all (whatever it can do comes from PUBLIC) and one that may insert into auth.users."""
        suffix = uuid.uuid4().hex[:8]
        public_only, inserter = f"pp_pubonly_{suffix}", f"pp_signup_{suffix}"
        for role in (public_only, inserter):
            db.run(f"create role {role} nologin")
        db.run(f"grant usage on schema auth, public to {public_only}, {inserter}")
        db.run(f"grant insert, select on auth.users to {inserter}")
        return public_only, inserter

    def _drop_roles(self, db: Db, *roles):
        db.run("reset role")
        for role in roles:
            db.run(f"drop owned by {role}")
            db.run(f"drop role if exists {role}")

    def _execute(self, db: Db, role: str) -> bool:
        return db.one("select has_function_privilege(%s, 'public.handle_new_user()', 'execute')", (role,))[0]

    def _signup(self, db: Db, role: str, uid: str, name="New User"):
        db.run(f"set role {role}")
        try:
            db.run("insert into auth.users (id, email, raw_user_meta_data) values (%s, %s, %s::jsonb)", (uid, f"{uid[:8]}@example.com", json.dumps({"full_name": name, "business_name": "Acme"})))
        finally:
            db.run("reset role")

    def test_control_without_the_revoke_public_anon_and_authenticated_can_execute_it(self, db):
        db.base()
        db.apply(MONEY)
        db.apply_text(self._text_without_the_revoke())
        public_only, inserter = self._roles(db)
        try:
            assert self._execute(db, "anon") and self._execute(db, "authenticated") and self._execute(db, public_only)  # the exposure the revoke removes
        finally:
            self._drop_roles(db, public_only, inserter)

    def test_public_anon_and_authenticated_cannot_execute_it_directly(self, db):
        import psycopg
        db.base()
        db.apply(MONEY)
        db.apply(HARDEN)
        public_only, inserter = self._roles(db)
        try:
            assert not self._execute(db, "anon") and not self._execute(db, "authenticated") and not self._execute(db, public_only)
            assert self._execute(db, db.one("select current_user")[0])  # the owner keeps it
            for role in ("anon", "authenticated", public_only):
                db.run(f"set role {role}")
                try:
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        db.run("select public.handle_new_user()")
                finally:
                    db.run("reset role")
            assert db.one("select not exists (select 1 from pg_proc p cross join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) a "
                          "where p.oid = 'public.handle_new_user()'::regprocedure and a.grantee = 0)")[0] is True  # no PUBLIC entry
        finally:
            self._drop_roles(db, public_only, inserter)

    def test_signup_still_creates_the_profile_for_a_role_that_cannot_execute_the_function(self, db):
        db.base()
        db.apply(MONEY)
        db.apply(HARDEN)
        public_only, inserter = self._roles(db)
        try:
            assert not self._execute(db, inserter)  # the inserting role has no EXECUTE on the trigger function ...
            uid = str(uuid.uuid4())
            self._signup(db, inserter, uid)  # ... and the signup trigger still fires
            assert db.one("select email, full_name, business_name from public.profiles where id = %s", (uid,)) == (f"{uid[:8]}@example.com", "New User", "Acme")
            owner_uid = str(uuid.uuid4())
            db.run("insert into auth.users (id, email) values (%s, 'owner@example.com')", (owner_uid,))  # and for the owner as well
            assert db.one("select count(*) from public.profiles where id = %s", (owner_uid,))[0] == 1
        finally:
            self._drop_roles(db, public_only, inserter)

    def test_the_migration_stays_rerunnable_and_signup_keeps_working(self, db):
        db.base()
        db.apply(MONEY)
        db.apply(HARDEN)
        public_only, inserter = self._roles(db)
        try:
            before = (self._execute(db, "anon"), self._execute(db, "authenticated"), self._execute(db, public_only), snapshot(db))
            db.apply(HARDEN)
            db.apply(HARDEN)
            db.apply(MONEY)  # the documented post-cutover rerun of file 3 must not re-grant anything either
            assert (self._execute(db, "anon"), self._execute(db, "authenticated"), self._execute(db, public_only), snapshot(db)) == before
            assert before[:3] == (False, False, False)
            uid = str(uuid.uuid4())
            self._signup(db, inserter, uid)
            assert db.one("select count(*) from public.profiles where id = %s", (uid,))[0] == 1
        finally:
            self._drop_roles(db, public_only, inserter)
