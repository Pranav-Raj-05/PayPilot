"""Proof that PostgreSQL test fixtures cannot drop/alter anything outside a disposable local server.

Everything except the last section runs with NO PostgreSQL available: refusals must happen before any driver call.
"""
import re
from pathlib import Path

import pytest

from tests import pg_safety
from tests.pg_safety import UnsafeDatabaseTarget

PW = "S3cr3tPw-ZZ9"  # distinctive fake password; must never appear in any message
REF = "jqhslizdnbdmpjafdzcw"

UNSAFE_URLS = [
    f"postgresql://postgres:{PW}@db.{REF}.supabase.co:5432/postgres",
    f"postgresql://postgres.{REF}:{PW}@aws-0-eu-west-1.pooler.supabase.com:6543/postgres",
    f"postgresql://postgres:{PW}@aws-0-eu-west-1.pooler.supabase.com:5432/pp_test_abc",
    f"postgresql://u:{PW}@localhost:5432/pp_test_{REF}",  # project ref anywhere in the URL
    f"postgresql://u:{PW}@127.0.0.1:5432/pp_test_x?options=project%3D{REF}",
    f"postgresql://u:{PW}@10.1.2.3:5432/pp_test_abc",
    f"postgresql://u:{PW}@db.example.com:5432/pp_test_abc",
    f"postgresql://u:{PW}@mydb.abc.us-east-1.rds.amazonaws.com:5432/pp_test_abc",
    f"postgresql://u:{PW}@127.0.0.1,db.prod.example.com:5432/pp_test_abc",
    f"postgresql://u:{PW}@/pp_test_abc?host=db.prod.example.com",
    f"postgresql://u:{PW}@/pp_test_abc",  # no explicit host (unix socket / default)
    f"mysql://u:{PW}@localhost:3306/pp_test_abc",
    f"sqlite:///{PW}.db",
    f"http://u:{PW}@localhost/pp_test_abc",
]


@pytest.fixture
def no_driver(monkeypatch):
    """Any PostgreSQL driver / engine / DDL call fails the test; the returned list records offending calls."""
    calls = []

    def boom(name):
        def _f(*a, **k):
            calls.append(name)
            raise AssertionError(f"{name} must not be called before the safety checks pass")
        return _f

    import psycopg, sqlalchemy
    from app.core import db as dbmod
    from app.models import Base

    monkeypatch.setattr(psycopg, "connect", boom("psycopg.connect"))
    monkeypatch.setattr(sqlalchemy, "create_engine", boom("sqlalchemy.create_engine"))
    monkeypatch.setattr(dbmod, "create_engine", boom("app.core.db.create_engine"))
    monkeypatch.setattr(dbmod, "init_engine", boom("init_engine"))
    monkeypatch.setattr(Base.metadata, "drop_all", boom("drop_all"))
    monkeypatch.setattr(Base.metadata, "create_all", boom("create_all"))
    return calls


@pytest.fixture
def opted_in(monkeypatch):
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_PG_TESTS", "1")
    monkeypatch.delenv("PG_TEST_ALLOWED_HOSTS", raising=False)
    for var in pg_safety.REFUSED_ENV_VARS:  # a developer machine may legitimately have PGHOST etc.; unit tests must not depend on it
        monkeypatch.delenv(var, raising=False)


# ------------------------------------------------------------------ admin URL rules
@pytest.mark.parametrize("url", UNSAFE_URLS)
def test_unsafe_admin_urls_are_refused_without_leaking_secrets(url, opted_in):
    with pytest.raises(UnsafeDatabaseTarget) as e:
        pg_safety.assert_safe_admin_url(url)
    assert PW not in str(e.value) and url not in str(e.value)


@pytest.mark.parametrize("url", [
    "postgresql://postgres@127.0.0.1:5432/postgres",
    "postgresql+psycopg://postgres:pw@localhost/postgres",
    "postgres://postgres@localhost/postgres",
    "postgresql://postgres@[::1]:5432/postgres",
])
def test_local_urls_are_accepted(url, opted_in):
    pg_safety.assert_safe_admin_url(url)


def test_extra_allowed_hosts_can_only_be_loopback(monkeypatch):
    """PG_TEST_ALLOWED_HOSTS used to whitelist any host name (a CNAME or an SSH-tunnel name). It can now only add loopback addresses."""
    url = "postgresql://u:pw@pgservice:5432/postgres"
    monkeypatch.delenv("PG_TEST_ALLOWED_HOSTS", raising=False)
    with pytest.raises(UnsafeDatabaseTarget):
        pg_safety.assert_safe_admin_url(url)
    monkeypatch.setenv("PG_TEST_ALLOWED_HOSTS", "other, pgservice")
    with pytest.raises(UnsafeDatabaseTarget):  # the variable itself is refused, and the host is still not loopback
        pg_safety.assert_safe_admin_url(url)
    monkeypatch.setenv("PG_TEST_ALLOWED_HOSTS", "127.0.0.2")
    pg_safety.assert_safe_admin_url("postgresql://u:pw@127.0.0.2:5432/postgres")


def test_allowed_hosts_env_cannot_whitelist_hosted_databases(monkeypatch):
    monkeypatch.setenv("PG_TEST_ALLOWED_HOSTS", f"db.{REF}.supabase.co,x.pooler.supabase.com")
    for url in (f"postgresql://p:{PW}@db.{REF}.supabase.co/postgres", f"postgresql://p:{PW}@x.pooler.supabase.com/postgres"):
        with pytest.raises(UnsafeDatabaseTarget):
            pg_safety.assert_safe_admin_url(url)


def test_malformed_url_error_does_not_echo_the_value():
    with pytest.raises(UnsafeDatabaseTarget) as e:
        pg_safety.assert_safe_admin_url(f"not a url {PW}")
    assert PW not in str(e.value)


# ------------------------------------------------------------------ database-name rules
@pytest.mark.parametrize("name", ["pp_test_ab12", "pp_mig_0f3a9c", "pp_test_x"])
def test_safe_database_names(name):
    pg_safety.assert_safe_database_name(name)


@pytest.mark.parametrize("name", [
    "postgres", "paypilot", "prod", "production", "template1", "pp_test", "pp_mig", "pp_test_", "pp_mig_", "PP_TEST_abc",
    "xpp_test_abc", "test_pp_test_abc", "pp_tests_abc", "pp_test_a;drop database x", 'pp_test_a"b', "pp_test_a b", "", REF,
])
def test_unsafe_database_names_are_refused(name):
    with pytest.raises(UnsafeDatabaseTarget):
        pg_safety.assert_safe_database_name(name)


def test_opt_in_is_required(monkeypatch):
    monkeypatch.delenv("ALLOW_DESTRUCTIVE_PG_TESTS", raising=False)
    with pytest.raises(UnsafeDatabaseTarget, match="ALLOW_DESTRUCTIVE_PG_TESTS"):
        pg_safety.assert_safe_destructive_target("postgresql://u@localhost/pp_test_abc")
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_PG_TESTS", "true")  # only the literal "1" counts
    with pytest.raises(UnsafeDatabaseTarget):
        pg_safety.assert_safe_destructive_target("postgresql://u@localhost/pp_test_abc")
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_PG_TESTS", "1")
    assert pg_safety.assert_safe_destructive_target("postgresql://u@localhost/pp_test_abc") == "pp_test_abc"


# ------------------------------------------------------------------ redaction
def test_redact_shows_only_host_port_dbname():
    out = pg_safety.redact(f"postgresql://admin:{PW}@localhost:5433/pp_test_abc?sslmode=require")
    assert out == "localhost:5433/pp_test_abc"
    for secret in (PW, "admin", "sslmode", "postgresql"):
        assert secret not in out
    assert PW not in pg_safety.redact(f"garbage {PW}")


# ------------------------------------------------------------------ skip behaviour
def test_admin_url_skips_when_env_unset(monkeypatch):
    monkeypatch.delenv("TEST_POSTGRES_URL", raising=False)
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_PG_TESTS", "1")
    with pytest.raises(pytest.skip.Exception, match="TEST_POSTGRES_URL"):
        pg_safety.pg_test_admin_url()


def test_admin_url_skips_without_opt_in_and_never_connects(monkeypatch, no_driver):
    monkeypatch.setenv("TEST_POSTGRES_URL", f"postgresql://u:{PW}@localhost/postgres")
    monkeypatch.delenv("ALLOW_DESTRUCTIVE_PG_TESTS", raising=False)
    with pytest.raises(pytest.skip.Exception, match="ALLOW_DESTRUCTIVE_PG_TESTS=1") as e:
        pg_safety.pg_test_admin_url()
    assert PW not in str(e.value) and not no_driver


@pytest.mark.parametrize("url", [u for u in UNSAFE_URLS if u.startswith("postgres")])
def test_admin_url_errors_not_skips_for_unsafe_url(url, monkeypatch, no_driver):
    monkeypatch.setenv("TEST_POSTGRES_URL", url)
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_PG_TESTS", "1")
    with pytest.raises(UnsafeDatabaseTarget) as e:
        pg_safety.pg_test_admin_url()
    assert PW not in str(e.value) and not no_driver


# ------------------------------------------------------------------ _boot / fixtures refuse before touching any driver
@pytest.mark.parametrize("url", [
    f"postgresql://postgres:{PW}@db.{REF}.supabase.co:5432/postgres",
    f"postgresql://postgres.{REF}:{PW}@aws-0-eu-west-1.pooler.supabase.com:6543/postgres",
    f"postgresql://u:{PW}@10.1.2.3:5432/pp_test_abc",
    f"postgresql://u:{PW}@localhost:5432/postgres",  # local host but a real-looking database name
    f"postgresql://u:{PW}@localhost:5432/paypilot",
    f"postgresql://u:{PW}@localhost:5432/prod",
    f"postgresql://u:{PW}@localhost:5432/pp_test",
    f"postgresql://u:{PW}@localhost:5432/",
    f"mysql://u:{PW}@localhost:5432/pp_test_abc",
])
def test_boot_refuses_unsafe_urls_before_any_driver_call(url, opted_in, no_driver):
    from tests.test_profile_concurrency import _boot

    with pytest.raises(UnsafeDatabaseTarget) as e:
        _boot(url)
    assert PW not in str(e.value) and url not in str(e.value)
    assert no_driver == []


def test_boot_refuses_without_opt_in_even_for_a_safe_looking_url(monkeypatch, no_driver):
    from tests.test_profile_concurrency import _boot

    monkeypatch.delenv("ALLOW_DESTRUCTIVE_PG_TESTS", raising=False)
    with pytest.raises(UnsafeDatabaseTarget, match="ALLOW_DESTRUCTIVE_PG_TESTS"):
        _boot(f"postgresql://u:{PW}@localhost:5432/pp_test_abc")
    assert no_driver == []


def test_boot_verifies_current_database_before_drop_all(monkeypatch, opted_in):
    """If the URL looks safe but the server really connected us to another database, drop_all must never run."""
    from sqlalchemy import create_engine
    from app.core import db as dbmod
    from app.models import Base
    from tests.test_profile_concurrency import _boot

    # A SQLite engine stands in for "the connection"; patch current_database() semantics via a fake engine object.
    class FakeResult:
        def scalar(self):
            return "paypilot_real"

    class FakeConn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def exec_driver_sql(self, stmt): return FakeResult()

    disposed = []

    class FakeEngine:
        def connect(self): return FakeConn()
        def dispose(self): disposed.append(True)

    monkeypatch.setattr(dbmod, "init_engine", lambda url: FakeEngine())

    def boom(*a, **k):
        raise AssertionError("drop_all must not run")

    monkeypatch.setattr(Base.metadata, "drop_all", boom)
    monkeypatch.setattr(Base.metadata, "create_all", boom)
    with pytest.raises(UnsafeDatabaseTarget, match="paypilot_real"):
        _boot("postgresql://u@localhost:5432/pp_test_abc")
    assert disposed == [True]


def test_postgres_client_fixture_refuses_unsafe_env_without_driver_calls(monkeypatch, no_driver, providers):
    from tests.test_profile_concurrency import postgres_client

    gen = postgres_client.__wrapped__(providers) if hasattr(postgres_client, "__wrapped__") else None
    assert gen is not None
    monkeypatch.setenv("TEST_POSTGRES_URL", f"postgresql://postgres:{PW}@db.{REF}.supabase.co:5432/postgres")
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_PG_TESTS", "1")
    with pytest.raises(UnsafeDatabaseTarget) as e:
        next(gen)
    assert PW not in str(e.value) and not no_driver


def test_postgres_client_fixture_skips_when_env_unset(monkeypatch, no_driver, providers):
    from tests.test_profile_concurrency import postgres_client

    monkeypatch.delenv("TEST_POSTGRES_URL", raising=False)
    with pytest.raises(pytest.skip.Exception):
        next(postgres_client.__wrapped__(providers))
    assert not no_driver


def test_temp_database_refuses_unsafe_prefix_before_connecting(monkeypatch, no_driver):
    monkeypatch.setenv("TEST_POSTGRES_URL", "postgresql://u@localhost/postgres")
    monkeypatch.setenv("ALLOW_DESTRUCTIVE_PG_TESTS", "1")
    for prefix in ("postgres", "paypilot", "prod", "pp"):
        with pytest.raises(UnsafeDatabaseTarget):
            with pg_safety.temp_database(prefix):
                pytest.fail("block must not run")
    assert not no_driver


@pytest.mark.parametrize("name", ["postgres", "paypilot", "template1", "pp_test", "prod"])
def test_drop_and_create_database_refuse_unsafe_names_without_connecting(name, opted_in, no_driver):
    admin = "postgresql://u@localhost/postgres"
    with pytest.raises(UnsafeDatabaseTarget):
        pg_safety.drop_database(admin, name)
    with pytest.raises(UnsafeDatabaseTarget):
        pg_safety.create_database(admin, name)
    assert not no_driver


def test_drop_database_refuses_unsafe_server_without_connecting(opted_in, no_driver):
    with pytest.raises(UnsafeDatabaseTarget) as e:
        pg_safety.drop_database(f"postgresql://postgres:{PW}@db.{REF}.supabase.co/postgres", "pp_test_abc")
    assert PW not in str(e.value) and not no_driver


# ------------------------------------------------------------------ regression guard for future fixtures
def test_only_the_guarded_call_site_may_call_drop_all():
    tests_dir = Path(__file__).parent
    offenders = []
    guarded = 0
    for path in tests_dir.rglob("*.py"):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"\.drop_all\(|\bdrop_all\(", text):
            if path.name == "test_profile_concurrency.py":
                guarded += 1
                # the single call site must be preceded by the guard inside _boot
                body = text[text.index("def _boot("):]
                assert body.index("_guard_destructive_target(url)") < body.index("drop_all(")
                assert body.index("normalize_url(url)") < body.index("init_engine(url)") < body.index("verify_connection(conn, url)") < body.index("drop_all(")
                assert text.count("drop_all(") == 1
            else:
                offenders.append(path.name)
    assert guarded == 1
    assert not offenders, f"{offenders} call drop_all outside the guarded _boot; route it through pg_safety first"


def test_no_test_module_reads_the_admin_url_directly_for_ddl():
    """Fixtures must go through pg_safety (pg_test_admin_url / temp_database) rather than using TEST_POSTGRES_URL themselves."""
    allowed = {"pg_safety.py", "test_pg_test_safety.py", "test_migrations_postgres.py", "test_money_integrity.py"}  # others: skip-gating only, DB work goes via temp databases
    tests_dir = Path(__file__).parent
    for path in tests_dir.rglob("*.py"):
        if path.name in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"os\.environ(?:\.get)?\(?\[?[\"']TEST_POSTGRES_URL", text):
            line = text[: m.start()].count("\n") + 1
            pytest.fail(f"{path.name}:{line} reads TEST_POSTGRES_URL directly; use pg_safety.pg_test_admin_url()/temp_database()")


# ------------------------------------------------------------------ integration (needs a disposable server)
def test_temp_database_lifecycle_and_unsafe_drop_refusal():
    admin = pg_safety.pg_test_admin_url()  # skips unless TEST_POSTGRES_URL + ALLOW_DESTRUCTIVE_PG_TESTS=1
    with pg_safety.temp_database("pp_test") as url:
        name = pg_safety._parse(url).database
        assert name.startswith("pp_test_")
        assert pg_safety.database_exists(admin, name)
        with pg_safety.temp_database("pp_test") as url2:
            assert pg_safety._parse(url2).database != name  # unique
    assert not pg_safety.database_exists(admin, name)

    with pytest.raises(RuntimeError, match="boom"):
        with pg_safety.temp_database("pp_test") as url3:
            name3 = pg_safety._parse(url3).database
            assert pg_safety.database_exists(admin, name3)
            raise RuntimeError("boom")
    assert not pg_safety.database_exists(admin, name3)

    for unsafe in ("postgres", "template1", "pp_test"):
        with pytest.raises(UnsafeDatabaseTarget):
            pg_safety.drop_database(admin, unsafe)
    assert pg_safety.database_exists(admin, "postgres")
    # an unsafe admin URL pointing at the same server is irrelevant: the destructive statement runs on a *different* db name
    with pytest.raises(UnsafeDatabaseTarget):
        pg_safety.temp_database("prod").__enter__()


def test_boot_refuses_the_admin_database_on_a_live_server():
    admin = pg_safety.pg_test_admin_url()
    from tests.test_profile_concurrency import _boot

    with pytest.raises(UnsafeDatabaseTarget):
        _boot(admin)  # its database is e.g. "postgres": must be refused before drop_all


# ====================================================================== Stage B3.1: bypasses found in the Stage B3 audit
# Every test below runs with NO PostgreSQL available (no_driver fails the test on any connection) unless it says otherwise.
class TestCustomHostsCannotWidenThePolicy:
    """PG_TEST_ALLOWED_HOSTS used to accept ANY host (a CNAME to a hosted database, a bare public IP, a tunnel name)."""

    @pytest.mark.parametrize("host", ["db.internal.example", "pgservice", "10.0.0.5", "192.168.1.20", "172.16.0.9", "203.0.113.7", "[2001:db8::1]"])
    def test_a_host_listed_in_the_env_variable_is_still_refused_unless_it_is_loopback(self, host, monkeypatch, opted_in, no_driver):
        monkeypatch.setenv("PG_TEST_ALLOWED_HOSTS", host.strip("[]"))
        with pytest.raises(UnsafeDatabaseTarget) as e:
            pg_safety.assert_safe_admin_url(f"postgresql://u:{PW}@{host}:5432/postgres")
        assert PW not in str(e.value)
        assert not no_driver

    def test_a_bad_env_entry_poisons_even_a_localhost_url(self, monkeypatch, opted_in, no_driver):
        monkeypatch.setenv("PG_TEST_ALLOWED_HOSTS", "localhost, db.prod.example")
        with pytest.raises(UnsafeDatabaseTarget, match="PG_TEST_ALLOWED_HOSTS"):
            pg_safety.assert_safe_admin_url(f"postgresql://u:{PW}@localhost:5432/postgres")

    def test_loopback_extras_are_accepted(self, monkeypatch, opted_in):
        monkeypatch.setenv("PG_TEST_ALLOWED_HOSTS", "localhost,127.0.0.2,::1")
        pg_safety.assert_safe_admin_url("postgresql://u@127.0.0.2:5432/postgres")
        pg_safety.assert_safe_admin_url("postgresql://u@localhost:5432/postgres")

    def test_localhost_that_resolves_to_a_non_loopback_address_is_refused(self, monkeypatch, opted_in, no_driver):
        import socket

        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, 1, 6, "", ("203.0.113.9", 0))])
        with pytest.raises(UnsafeDatabaseTarget):
            pg_safety.assert_safe_admin_url("postgresql://u@localhost:5432/postgres")

    def test_localhost_that_cannot_be_resolved_is_refused(self, monkeypatch, opted_in, no_driver):
        import socket

        def fail(*a, **k):
            raise socket.gaierror("no resolver")

        monkeypatch.setattr(socket, "getaddrinfo", fail)
        with pytest.raises(UnsafeDatabaseTarget):
            pg_safety.assert_safe_admin_url("postgresql://u@localhost:5432/postgres")

    @pytest.mark.parametrize("host", ["127.1", "2130706433", "0x7f.0.0.1", "localhost.", "LOCALHOST.example.com", "0.0.0.0", "::ffff:10.0.0.5", "::"])
    def test_loopback_lookalikes_are_refused(self, host, opted_in, no_driver):
        h = f"[{host}]" if ":" in host else host
        with pytest.raises(UnsafeDatabaseTarget):
            pg_safety.assert_safe_admin_url(f"postgresql://u@{h}:5432/postgres")
        assert not no_driver

    @pytest.mark.parametrize("host", ["127.0.0.1", "127.5.6.7", "[::1]", "localhost", "LOCALHOST"])
    def test_genuine_loopback_hosts_are_accepted(self, host, opted_in):
        pg_safety.assert_safe_admin_url(f"postgresql://u@{host}:5432/postgres")


class TestUrlParametersCannotRedirectTheTarget:
    @pytest.mark.parametrize("query", [
        "dbname=paypilot", "host=db.prod.example.com", "hostaddr=203.0.113.5", "port=6543", "service=prod", "options=-c%20search_path%3Dx",
        "user=postgres", "password=x", "target_session_attrs=read-write", "sslrootcert=/tmp/x", "passfile=/tmp/pgpass", "load_balance_hosts=random",
        "DBNAME=paypilot", "sslmode=require&dbname=paypilot",
    ])
    def test_redirecting_query_parameters_are_refused(self, query, opted_in, no_driver):
        with pytest.raises(UnsafeDatabaseTarget, match="query parameter") as e:
            pg_safety.assert_safe_admin_url(f"postgresql://u:{PW}@localhost:5432/postgres?{query}")
        assert PW not in str(e.value)
        assert not no_driver

    def test_a_repeated_allowed_parameter_is_refused(self, opted_in):
        with pytest.raises(UnsafeDatabaseTarget, match="repeated"):
            pg_safety.assert_safe_admin_url("postgresql://u@localhost:5432/postgres?sslmode=disable&sslmode=require")

    @pytest.mark.parametrize("query", ["sslmode=disable", "connect_timeout=5", "application_name=pytest", "sslmode=prefer&connect_timeout=3"])
    def test_harmless_parameters_are_accepted(self, query, opted_in):
        pg_safety.assert_safe_admin_url(f"postgresql://u@localhost:5432/postgres?{query}")

    def test_the_database_name_comes_only_from_the_path(self, opted_in):
        with pytest.raises(UnsafeDatabaseTarget):
            pg_safety.assert_safe_destructive_target("postgresql://u@localhost/pp_test_abc?dbname=paypilot")
        assert pg_safety.assert_safe_destructive_target("postgresql://u@localhost/pp_test_abc?sslmode=disable") == "pp_test_abc"


class TestMalformedAndAmbiguousTargets:
    @pytest.mark.parametrize("url", [
        f"postgresql://u:{PW}@localhost,localhost:5432/postgres",  # multi-host, even when every host is loopback
        f"postgresql://u:{PW}@127.0.0.1,::1/postgres",
        f"postgresql://u:{PW}@%6Cocalhost:5432/postgres",  # percent-encoded host: libpq would decode it, we must not guess
        f"postgresql://u:{PW}@local%68ost/postgres",
        f"postgresql://localhost:{PW}@db.example.com:5432/postgres",  # 'localhost' only in the userinfo
        f"postgresql://u:{PW}@localhost:5432 /postgres",  # whitespace
        f"postgresql://u:{PW}@localhost:5432/postgres\n?host=db.example.com",
        f"postgresql://u:{PW}@localhost:5432/postgres\x00",
        f"postgresql://u:{PW}@localhost:0/postgres",
        f"postgresql://u:{PW}@localhost:70000/postgres",
        f"postgresql://u:{PW}@localhost:notaport/postgres",
        f"postgresql://u:{PW}@:5432/postgres",
        f"postgresql+psycopg://u:{PW}@db.{REF}.supabase.co/postgres",
        f"postgresql://u:{PW}@db.x.supabase.net:5432/postgres",
        "",
        "   ",
        None,
    ])
    def test_refused_without_a_connection_and_without_leaking(self, url, opted_in, no_driver):
        with pytest.raises(UnsafeDatabaseTarget) as e:
            pg_safety.assert_safe_admin_url(url)
        assert PW not in str(e.value)
        assert not no_driver

    @pytest.mark.parametrize("var", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"])
    def test_libpq_environment_that_redirects_the_connection_is_refused(self, var, monkeypatch, opted_in, no_driver):
        monkeypatch.setenv(var, "203.0.113.5")
        with pytest.raises(UnsafeDatabaseTarget, match=var):
            pg_safety.assert_safe_admin_url("postgresql://u@localhost:5432/postgres")
        assert not no_driver

    @pytest.mark.parametrize("url", ["postgresql://u@localhost:5432/postgres", "postgres://u:p%40ss@127.0.0.1:55432/postgres?sslmode=disable",
                                      "postgresql+psycopg://u@[::1]/postgres", "postgresql://localhost/postgres"])
    def test_legitimate_local_targets_are_still_accepted(self, url, opted_in):
        pg_safety.assert_safe_admin_url(url)


class TestTheDriverIsGivenTheParsedTargetNotTheText:
    def test_connect_kwargs_are_the_validated_parts(self, opted_in):
        kw = pg_safety._connect_kwargs("postgresql://us%40er:p%40ss%2Fw@127.0.0.1:55432/pp_test_abc?sslmode=disable")
        assert kw == {"host": "127.0.0.1", "dbname": "pp_test_abc", "port": 55432, "user": "us@er", "password": "p@ss/w", "sslmode": "disable"}

    def test_a_dbname_override_replaces_only_the_database(self, opted_in):
        assert pg_safety._connect_kwargs("postgresql://u@localhost/postgres", "pp_test_x")["dbname"] == "pp_test_x"

    def test_unsafe_urls_never_produce_kwargs(self, opted_in, no_driver):
        for url in ("postgresql://u@localhost/postgres?dbname=x", "postgresql://u@db.example.com/postgres"):
            with pytest.raises(UnsafeDatabaseTarget):
                pg_safety._connect_kwargs(url)

    def test_connect_verified_refuses_unsafe_targets_before_connecting(self, opted_in, no_driver):
        for url in UNSAFE_URLS:
            with pytest.raises(UnsafeDatabaseTarget):
                pg_safety.connect_verified(url)
        assert not no_driver

    def test_connect_verified_hands_psycopg_discrete_arguments(self, opted_in, monkeypatch):
        import psycopg

        seen = {}

        class Conn:
            closed = False
            def execute(self, sql, params=None):
                return Result(sql)
            def close(self): self.closed = True

        class Result:
            def __init__(self, sql): self.sql = sql
            def fetchone(self): return ("pp_test_abc",)
            def fetchall(self): return [("postgres",), ("pp_test_abc",)] if "pg_database" in self.sql else []

        def fake_connect(*args, **kwargs):
            seen["args"], seen["kwargs"] = args, kwargs
            return Conn()

        monkeypatch.setattr(psycopg, "connect", fake_connect)
        pg_safety.connect_verified("postgresql://u:pw@localhost:55432/pp_test_abc?connect_timeout=3&sslmode=disable")
        assert seen["args"] == ()  # no conninfo string: libpq never parses the URL text
        assert seen["kwargs"] == {"autocommit": True, "connect_timeout": 3, "host": "localhost", "dbname": "pp_test_abc", "port": 55432,
                                  "user": "u", "password": "pw", "sslmode": "disable"}


class TestTheServerItselfIsInterrogated:
    """Catches what no URL check can: an SSH tunnel / port-forward on localhost that leads to a shared or hosted server."""

    class Conn:
        def __init__(self, current="postgres", databases=("postgres", "pp_test_a", "pp_mig_b"), roles=()):
            self.current, self.databases, self.roles, self.closed = current, list(databases), list(roles), False

        def execute(self, sql, params=None):
            outer = self
            class R:
                def fetchone(self_): return (outer.current,)
                def fetchall(self_): return [(d,) for d in outer.databases] if "pg_database" in sql else [(r,) for r in outer.roles if r in params[0]]
            return R()

        def close(self): self.closed = True

    def test_a_clean_disposable_server_passes(self):
        pg_safety.assert_disposable_server(self.Conn(), expected_database="postgres")

    @pytest.mark.parametrize("databases", [("postgres", "paypilot"), ("postgres", "app_production", "pp_test_a"), ("postgres", "_supabase"), ("postgres", "PP_TEST_A")])
    def test_a_server_hosting_other_databases_is_refused(self, databases):
        with pytest.raises(UnsafeDatabaseTarget, match="not a disposable test server"):
            pg_safety.assert_disposable_server(self.Conn(databases=databases))

    @pytest.mark.parametrize("role", ["supabase_admin", "supabase_auth_admin", "pgbouncer", "rdsadmin", "cloudsqladmin", "azure_pg_admin"])
    def test_a_server_with_hosted_service_roles_is_refused(self, role):
        with pytest.raises(UnsafeDatabaseTarget, match="hosted-service role"):
            pg_safety.assert_disposable_server(self.Conn(roles=[role]))

    def test_current_database_must_match_the_url(self):
        with pytest.raises(UnsafeDatabaseTarget, match="URL names"):
            pg_safety.assert_disposable_server(self.Conn(current="paypilot"), expected_database="postgres")

    def test_the_connection_is_closed_when_the_server_is_refused(self, opted_in, monkeypatch):
        import psycopg

        conn = self.Conn(databases=("postgres", "paypilot"), current="postgres")
        monkeypatch.setattr(psycopg, "connect", lambda *a, **k: conn)
        with pytest.raises(UnsafeDatabaseTarget):
            pg_safety.connect_verified("postgresql://u@localhost/postgres")
        assert conn.closed

    def test_every_admin_operation_interrogates_the_server_before_the_destructive_statement(self, opted_in, monkeypatch):
        import psycopg

        statements = []

        class Spy(self.Conn):
            def execute(self, sql, params=None):
                statements.append(str(sql))
                return super().execute(sql, params)

        for operation in (lambda: pg_safety.create_database("postgresql://u@localhost/postgres", "pp_test_abc"),
                          lambda: pg_safety.drop_database("postgresql://u@localhost/postgres", "pp_test_abc")):
            statements.clear()
            conn = Spy(databases=("postgres", "paypilot"))
            monkeypatch.setattr(psycopg, "connect", lambda *a, **k: conn)
            with pytest.raises(UnsafeDatabaseTarget):
                operation()
            assert not any("DATABASE" in s.upper() and ("CREATE" in s.upper() or "DROP" in s.upper()) for s in statements)

    def test_verify_connected_database_handles_both_connection_kinds(self):
        class SA:
            def __init__(self, name): self.name = name
            def exec_driver_sql(self, sql):
                outer = self
                class R:
                    def scalar(self_): return outer.name
                return R()

        assert pg_safety.verify_connected_database(SA("pp_test_abc"), "postgresql://u@localhost/pp_test_abc") == "pp_test_abc"
        assert pg_safety.verify_connected_database(self.Conn(current="pp_mig_abc"), "postgresql://u@localhost/pp_mig_abc") == "pp_mig_abc"
        with pytest.raises(UnsafeDatabaseTarget, match="paypilot"):
            pg_safety.verify_connected_database(SA("paypilot"), "postgresql://u@localhost/pp_test_abc")
        with pytest.raises(UnsafeDatabaseTarget, match="URL names"):
            pg_safety.verify_connected_database(SA("pp_test_other"), "postgresql://u@localhost/pp_test_abc")


def test_no_test_code_opens_its_own_psycopg_connection():
    """Every connection in the test harness goes through pg_safety.connect_verified (validated target + server interrogation)."""
    tests_dir = Path(__file__).parent
    offenders = []
    for path in tests_dir.rglob("*.py"):
        if path.name in ("pg_safety.py", Path(__file__).name):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\bpsycopg\.connect\(", line) and not line.lstrip().startswith("#"):
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, f"use pg_safety.connect_verified instead of psycopg.connect: {offenders}"


def test_pg_test_allowed_hosts_is_documented_as_loopback_only():
    doc = (Path(__file__).resolve().parents[2] / "docs" / "postgres-testing.md").read_text(encoding="utf-8")
    assert "loopback" in doc and "PG_TEST_ALLOWED_HOSTS" in doc


def test_ci_does_not_widen_the_host_policy():
    ci = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    values = re.findall(r"PG_TEST_ALLOWED_HOSTS:\s*(\S+)", ci)
    assert values and all(v.strip("\"'") in ("localhost", "127.0.0.1", "::1") for v in values)


# ====================================================================== Stage B3.2: PGPORT and the rest of the libpq environment
class TestThePortIsAlwaysExplicit:
    """libpq falls back to $PGPORT when no port is given, so a URL without a port used to leave the destination to the environment."""

    def test_a_url_without_a_port_gets_5432_not_a_libpq_default(self, opted_in):
        assert pg_safety._connect_kwargs("postgresql://u@localhost/postgres")["port"] == 5432

    def test_the_urls_own_port_is_kept(self, opted_in):
        assert pg_safety._connect_kwargs("postgresql://u@127.0.0.1:55432/postgres")["port"] == 55432

    def test_database_url_and_normalize_url_render_the_port(self, opted_in):
        assert pg_safety.database_url("postgresql://u@localhost/postgres", "pp_test_x") == "postgresql://u@localhost:5432/pp_test_x"
        assert pg_safety.database_url("postgresql://u@LOCALHOST:55432/postgres", "pp_test_x") == "postgresql://u@localhost:55432/pp_test_x"
        assert pg_safety.normalize_url("postgresql+psycopg://u@[::1]/postgres").endswith("@[::1]:5432/postgres")
        assert pg_safety.normalize_url("postgresql://u@127.0.0.1:6000/pp_test_a") == "postgresql://u@127.0.0.1:6000/pp_test_a"

    def test_normalize_url_validates_first(self, opted_in, no_driver):
        for url in UNSAFE_URLS:
            with pytest.raises(UnsafeDatabaseTarget):
                pg_safety.normalize_url(url)

    def test_psycopg_is_handed_the_port_even_when_the_url_has_none(self, opted_in, monkeypatch):
        import psycopg

        seen = {}

        class Conn:
            def execute(self, sql, params=None):
                class R:
                    def fetchone(self_): return ("postgres",)
                    def fetchall(self_): return [("postgres",)] if "pg_database" in sql else []
                return R()
            def close(self): pass

        monkeypatch.setattr(psycopg, "connect", lambda *a, **k: seen.update(kwargs=k) or Conn())
        pg_safety.connect_verified("postgresql://u@localhost/postgres")
        assert seen["kwargs"]["port"] == 5432 and seen["kwargs"]["host"] == "localhost" and seen["kwargs"]["dbname"] == "postgres"

    def test_the_sqlalchemy_engine_used_by_boot_gets_an_explicit_port(self, opted_in, monkeypatch):
        from app.core import db as dbmod
        from tests.test_profile_concurrency import _boot

        seen = []

        def fake_init(url):
            seen.append(url)
            raise RuntimeError("stop before any engine exists")

        monkeypatch.setattr(dbmod, "init_engine", fake_init)
        with pytest.raises(RuntimeError, match="stop before"):
            _boot(f"postgresql://u:{PW}@localhost/pp_test_abc")
        assert seen == [f"postgresql://u:{PW}@localhost:5432/pp_test_abc"]


class TestLibpqEnvironmentPolicy:
    PGPORT_VALUES = ["5432", "6543", "7777777", "abc", "", "0", "-1", "99999", " 5432", "5432,5433", "0x1538", "5432;host=evil.example"]
    URLS = ["postgresql://u@localhost/postgres", "postgresql://u@localhost:5432/postgres", "postgresql://u@127.0.0.1:55432/postgres"]

    @pytest.mark.parametrize("value", PGPORT_VALUES)
    @pytest.mark.parametrize("url", URLS)
    def test_pgport_in_the_environment_is_refused_before_any_connection(self, value, url, monkeypatch, opted_in, no_driver):
        monkeypatch.setenv("PGPORT", value)  # unset, same as the URL, conflicting, empty and malformed values alike
        for call in (lambda: pg_safety.assert_safe_admin_url(url), lambda: pg_safety.connect_verified(url), lambda: pg_safety._connect_kwargs(url),
                     lambda: pg_safety.normalize_url(url), lambda: pg_safety.database_url(url, "pp_test_x"),
                     lambda: pg_safety.create_database(url, "pp_test_abc"), lambda: pg_safety.drop_database(url, "pp_test_abc")):
            with pytest.raises(UnsafeDatabaseTarget, match="PGPORT") as e:
                call()
            assert "7777777" not in str(e.value) and "6543" not in str(e.value)  # names only, never values
        assert not no_driver

    @pytest.mark.parametrize("var", pg_safety.REFUSED_ENV_VARS)
    @pytest.mark.parametrize("value", ["x", "", "203.0.113.5"])
    def test_every_target_selecting_variable_is_refused_when_present(self, var, value, monkeypatch, opted_in, no_driver):
        monkeypatch.setenv(var, value)
        with pytest.raises(UnsafeDatabaseTarget, match=var):
            pg_safety.assert_safe_admin_url("postgresql://u@localhost:5432/postgres")
        with pytest.raises(UnsafeDatabaseTarget, match=var):
            pg_safety.connect_verified("postgresql://u@localhost:5432/postgres")
        assert not no_driver

    @pytest.mark.parametrize("var", pg_safety.PERMITTED_ENV_VARS)
    def test_variables_that_cannot_change_the_destination_are_permitted_and_do_not_change_it(self, var, monkeypatch, opted_in):
        baseline = pg_safety._connect_kwargs("postgresql://u@localhost:55432/pp_test_a")
        monkeypatch.setenv(var, "anything")
        pg_safety.assert_safe_admin_url("postgresql://u@localhost:55432/pp_test_a")
        assert pg_safety._connect_kwargs("postgresql://u@localhost:55432/pp_test_a") == baseline  # host/port/dbname are explicit arguments

    def test_every_variable_libpq_reports_is_classified(self):
        """If a newer libpq adds a PG* variable this fails, forcing a conscious decision: target-selecting (refuse) or harmless (permit)."""
        unclassified = pg_safety.unclassified_libpq_env_vars()  # asks the installed libpq; the Windows wheel bundles libpq 14, the Linux wheel libpq 17
        assert not unclassified, f"classify these libpq environment variables in pg_safety.py: {unclassified}"

    def test_the_two_lists_are_disjoint_and_cover_the_minimum_set(self):
        assert not set(pg_safety.REFUSED_ENV_VARS) & set(pg_safety.PERMITTED_ENV_VARS)
        assert set(pg_safety.TARGET_ENV_VARS) <= set(pg_safety.REFUSED_ENV_VARS) and set(pg_safety.CREDENTIAL_FORWARDING_ENV_VARS) <= set(pg_safety.REFUSED_ENV_VARS)
        for var in ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS"):
            assert var in pg_safety.TARGET_ENV_VARS
        for var in ("PGUSER", "PGSSLMODE", "PGSSLROOTCERT", "PGTARGETSESSIONATTRS", "PGCONNECT_TIMEOUT"):
            assert var in pg_safety.PERMITTED_ENV_VARS

    def test_environment_that_changes_between_validation_and_connection_is_caught(self, opted_in, monkeypatch, no_driver):
        """Validation passes, then something sets PGPORT before the driver would read the environment: connect_verified must still refuse."""
        original = pg_safety._connect_kwargs

        def validate_then_tamper(url, dbname=None):
            kwargs = original(url, dbname)
            monkeypatch.setenv("PGPORT", "6543")
            return kwargs

        monkeypatch.setattr(pg_safety, "_connect_kwargs", validate_then_tamper)
        with pytest.raises(UnsafeDatabaseTarget, match="PGPORT"):
            pg_safety.connect_verified("postgresql://u@localhost:5432/postgres")
        assert not no_driver

    def test_boot_refuses_a_redirecting_environment_before_creating_an_engine(self, opted_in, monkeypatch, no_driver):
        from tests.test_profile_concurrency import _boot

        monkeypatch.setenv("PGPORT", "6543")
        with pytest.raises(UnsafeDatabaseTarget, match="PGPORT"):
            _boot("postgresql://u@localhost/pp_test_abc")
        assert not no_driver

    # The complete set of environment variables read by the libpq 17 that the Linux psycopg-binary 3.2.3 wheel bundles (what GitHub Actions runs),
    # extracted from that manylinux wheel (libpq.so.5.17). The Windows wheel bundles libpq 14 and reports fewer, so a Windows-only run could not see the gap.
    LIBPQ_17_ENV_VARS = frozenset("""PGAPPNAME PGCHANNELBINDING PGCLIENTENCODING PGCONNECT_TIMEOUT PGDATABASE PGDATESTYLE PGGEQO PGGSSDELEGATION PGGSSENCMODE
        PGGSSLIB PGHOST PGHOSTADDR PGKRBSRVNAME PGLOADBALANCEHOSTS PGOPTIONS PGPASSFILE PGPASSWORD PGPORT PGREQUIREAUTH PGREQUIREPEER PGREQUIRESSL
        PGSERVICE PGSERVICEFILE PGSSLCERT PGSSLCERTMODE PGSSLCOMPRESSION PGSSLCRL PGSSLCRLDIR PGSSLKEY PGSSLMAXPROTOCOLVERSION PGSSLMINPROTOCOLVERSION
        PGSSLMODE PGSSLNEGOTIATION PGSSLROOTCERT PGSSLSNI PGSYSCONFDIR PGTARGETSESSIONATTRS PGTZ PGUSER""".split())

    def test_every_variable_of_the_libpq_17_that_github_actions_runs_is_classified(self):
        """Replays the CI condition on any machine: CI failed because libpq 17 reads PGGSSDELEGATION, PGSSLCERTMODE and PGSSLNEGOTIATION."""
        assert pg_safety.unclassified_libpq_env_vars(set(self.LIBPQ_17_ENV_VARS)) == []

    def test_the_three_variables_that_broke_ci_have_the_intended_classification(self):
        # transport-only: a TLS client certificate mode and how the TLS handshake starts. They cannot change host, port or database.
        for var in ("PGSSLCERTMODE", "PGSSLNEGOTIATION"):
            assert var in pg_safety.PERMITTED_ENV_VARS and var not in pg_safety.REFUSED_ENV_VARS
        # credential forwarding: refused, because Kerberos credentials would reach whichever server answers, before the server check runs
        assert "PGGSSDELEGATION" in pg_safety.CREDENTIAL_FORWARDING_ENV_VARS and "PGGSSDELEGATION" in pg_safety.REFUSED_ENV_VARS
        assert "PGGSSDELEGATION" not in pg_safety.PERMITTED_ENV_VARS

    def test_pggssdelegation_is_refused_when_present_and_the_permitted_two_are_accepted(self, monkeypatch, opted_in, no_driver):
        monkeypatch.setenv("PGGSSDELEGATION", "1")
        with pytest.raises(UnsafeDatabaseTarget, match="PGGSSDELEGATION") as e:
            pg_safety.assert_safe_admin_url("postgresql://u@localhost:5432/postgres")
        assert "=1" not in str(e.value)
        monkeypatch.delenv("PGGSSDELEGATION")
        for var, value in (("PGSSLCERTMODE", "disable"), ("PGSSLNEGOTIATION", "direct")):
            monkeypatch.setenv(var, value)
            pg_safety.assert_safe_admin_url("postgresql://u@localhost:5432/postgres")
            assert pg_safety._connect_kwargs("postgresql://u@localhost:5432/postgres")["port"] == 5432  # the destination is untouched
            monkeypatch.delenv(var)
        assert not no_driver

    def test_an_unknown_future_libpq_variable_fails_closed(self, monkeypatch, opted_in, no_driver):
        """If a newer libpq starts reading PGFUTURETHING, the classification test fails AND a run with it set is refused at runtime."""
        future = set(self.LIBPQ_17_ENV_VARS) | {"PGFUTURETHING"}
        assert pg_safety.unclassified_libpq_env_vars(future) == ["PGFUTURETHING"]
        monkeypatch.setattr(pg_safety, "libpq_reported_env_vars", lambda: future)
        assert pg_safety.unclassified_libpq_env_vars() == ["PGFUTURETHING"]
        pg_safety.assert_safe_admin_url("postgresql://u@localhost:5432/postgres")  # not set: nothing to refuse
        monkeypatch.setenv("PGFUTURETHING", "x")
        for call in (lambda: pg_safety.assert_safe_admin_url("postgresql://u@localhost:5432/postgres"), lambda: pg_safety.connect_verified("postgresql://u@localhost:5432/postgres"),
                     lambda: pg_safety.create_database("postgresql://u@localhost:5432/postgres", "pp_test_abc")):
            with pytest.raises(UnsafeDatabaseTarget, match="PGFUTURETHING") as e:
                call()
            assert "classified" in str(e.value)
        assert not no_driver

    def test_variables_libpq_does_not_read_are_not_refused(self, monkeypatch, opted_in):
        """Fail closed applies to what libpq reads, not to every PG* name (PGDATA, PG_TEST_ALLOWED_HOSTS and friends stay usable)."""
        monkeypatch.setenv("PGDATA", "/some/dir")
        monkeypatch.setenv("PG_TEST_ALLOWED_HOSTS", "localhost")
        pg_safety.assert_safe_admin_url("postgresql://u@localhost:5432/postgres")

    def test_the_policy_is_documented(self):
        doc = (Path(__file__).resolve().parents[2] / "docs" / "postgres-testing.md").read_text(encoding="utf-8")
        for var in pg_safety.REFUSED_ENV_VARS + pg_safety.PERMITTED_ENV_VARS:
            assert var in doc, f"{var} is classified by pg_safety but not documented"


class TestTempDatabaseCleanup:
    """A failed create must not leave a database behind, and a failed cleanup must never hide the test's own failure."""

    @pytest.fixture
    def calls(self, monkeypatch, opted_in):
        monkeypatch.setenv("TEST_POSTGRES_URL", f"postgresql://u:{PW}@localhost:5432/postgres")
        state = {"created": [], "dropped": [], "create_error": None, "drop_error": None}

        def create(admin, name):
            state["created"].append(name)
            if state["create_error"]:
                raise state["create_error"]

        def drop(admin, name):
            state["dropped"].append(name)
            if state["drop_error"]:
                raise state["drop_error"]

        monkeypatch.setattr(pg_safety, "create_database", create)
        monkeypatch.setattr(pg_safety, "drop_database", drop)
        return state

    def test_success_drops_exactly_once(self, calls):
        with pg_safety.temp_database("pp_test") as url:
            assert url.endswith(calls["created"][0]) and ":5432/" in url
        assert calls["dropped"] == calls["created"] and len(calls["dropped"]) == 1

    def test_a_failed_create_still_attempts_to_remove_the_half_created_database(self, calls):
        calls["create_error"] = RuntimeError("create failed")
        with pytest.raises(RuntimeError, match="create failed"):
            with pg_safety.temp_database("pp_test"):
                pytest.fail("block must not run")
        assert calls["dropped"] == calls["created"]

    def test_a_failed_create_and_a_failed_cleanup_raise_the_create_error(self, calls):
        calls["create_error"], calls["drop_error"] = RuntimeError("create failed"), RuntimeError("drop failed")
        with pytest.raises(RuntimeError, match="create failed"):
            with pg_safety.temp_database("pp_test"):
                pytest.fail("block must not run")

    def test_a_failing_cleanup_does_not_hide_the_tests_own_failure(self, calls):
        calls["drop_error"] = RuntimeError(f"drop failed {PW}")
        with pytest.raises(ValueError, match="the real test failure") as e:
            with pg_safety.temp_database("pp_test"):
                raise ValueError("the real test failure")
        notes = " ".join(getattr(e.value, "__notes__", []))
        assert calls["created"][0] in notes and "cleanup" in notes and PW not in notes  # tells you what to remove, leaks nothing

    def test_a_failing_cleanup_after_a_passing_test_is_reported(self, calls):
        calls["drop_error"] = RuntimeError("drop failed")
        with pytest.raises(RuntimeError, match="drop failed"):
            with pg_safety.temp_database("pp_test"):
                pass


# ====================================================================== inventory of every connection / destructive call site
EXPECTED_CALL_SITES = {
    # file: {kind: count}. A NEW entry means a new place that can connect to a database or destroy data: route it through pg_safety
    # (validate URL -> explicit opt-in -> connect with validated parameters -> verify the server/database -> destructive step), then add it here.
    "conftest.py": {"create_all()": 1, "init_engine()": 1},                       # SQLite only (literal "sqlite://", asserted below)
    "pg_safety.py": {"connect()": 1, "sql:create database": 1, "sql:drop database": 1},  # the single guarded driver call and the two admin statements
    "support/pg_migrated.py": {"sql:truncate": 1},                                # on the session's verified throw-away database
    "test_migrations_postgres.py": {"sql:truncate": 5},  # TRUNCATE attempts made as the 'authenticated' role that must be REFUSED, only through the db fixture (connect_verified -> pp_mig_* database)
    "test_money_integrity.py": {"create_all()": 2, "create_engine()": 1, "sql:drop table": 1},  # in-memory SQLite (literal "sqlite://", asserted below)
    "test_pg_test_safety.py": {"connect()": 1},  # the one raw connection: the live tunnel simulation on the disposable server (SQL strings in this file are fakes/assertions)
    "test_profile_concurrency.py": {"create_all()": 1, "drop_all()": 1, "init_engine()": 1},  # _boot only: guard, normalize, verify, then drop_all
}


def _call_site_inventory():
    import ast

    tests_dir = Path(__file__).parent
    sql_re = re.compile(r"\b(truncate|drop\s+database|create\s+database|drop\s+schema|drop\s+table)\b", re.I)
    calls = {"connect", "create_engine", "init_engine", "drop_all", "create_all"}
    found: dict[str, dict[str, int]] = {}
    literal_engine_args: list[tuple[str, str]] = []
    for path in sorted(tests_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                    docstrings.add(id(first.value))
        rel = path.relative_to(tests_dir).as_posix()
        bucket = found.setdefault(rel, {})
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                if name not in calls:
                    continue
                if name == "connect" and not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "psycopg"):
                    continue
                bucket[name + "()"] = bucket.get(name + "()", 0) + 1
                if name in ("init_engine", "create_engine") and node.args:
                    arg = node.args[0]
                    literal_engine_args.append((rel, arg.value if isinstance(arg, ast.Constant) else "<non-literal>"))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings and path.name != Path(__file__).name:
                hit = sql_re.search(node.value)
                if hit:
                    kind = "sql:" + " ".join(hit.group(1).lower().split())
                    bucket[kind] = bucket.get(kind, 0) + 1
    return {k: v for k, v in found.items() if v}, literal_engine_args


def test_the_set_of_connection_and_destructive_call_sites_is_exactly_the_reviewed_one():
    found, _ = _call_site_inventory()
    assert found == EXPECTED_CALL_SITES, (
        "the set of places that connect to a database or run destructive statements changed. Route the new call site through pg_safety "
        f"(validate -> opt-in -> verified connection -> verify server/database) and update EXPECTED_CALL_SITES.\nfound: {found}")


def test_engines_created_outside_the_guarded_boot_are_literal_sqlite():
    _, engine_args = _call_site_inventory()
    for rel, arg in engine_args:
        if rel == "test_profile_concurrency.py":
            continue  # _boot: guarded, asserted by test_only_the_guarded_call_site_may_call_drop_all
        assert isinstance(arg, str) and arg.startswith("sqlite"), f"{rel} creates an engine from {arg!r}; only literal SQLite URLs are allowed outside _boot"


# ------------------------------------------------------------------ real disposable server (skipped unless the opt-in environment is set)
def test_a_tunnel_style_shared_server_is_refused_by_the_live_interrogation():
    """Simulates "localhost leads to a shared server" by adding a non-test database to the DISPOSABLE server, then proves
    connect_verified refuses it. The extra database is removed in all cases."""
    import psycopg

    admin = pg_safety.pg_test_admin_url()
    foreign = f"zz_shared_{__import__('uuid').uuid4().hex[:8]}"
    raw = psycopg.connect(autocommit=True, **pg_safety._connect_kwargs(admin))  # raw on purpose: pg_safety refuses to create this name
    try:
        raw.execute(f'create database "{foreign}"')
        with pytest.raises(UnsafeDatabaseTarget, match="not a disposable test server"):
            pg_safety.connect_verified(admin)
        with pytest.raises(UnsafeDatabaseTarget, match="not a disposable test server"):
            pg_safety.create_database(admin, "pp_test_must_not_exist")
        assert not [d for d in raw.execute("select datname from pg_database where datname = 'pp_test_must_not_exist'").fetchall()]
    finally:
        raw.execute(f'drop database if exists "{foreign}" with (force)')
        raw.close()
    with pg_safety.connect_verified(admin) as conn:  # the server is clean again
        assert conn.execute("select current_database()").fetchone()[0] == pg_safety._parse(admin).database


def test_connect_verified_to_a_temp_database_reports_the_right_database():
    with pg_safety.temp_database("pp_test") as url:
        with pg_safety.connect_verified(url) as conn:
            assert pg_safety.verify_connected_database(conn, url) == pg_safety._parse(url).database
