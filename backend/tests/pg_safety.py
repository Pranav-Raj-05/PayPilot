"""Safety rails for tests that create/drop PostgreSQL databases or tables.

Contract
--------
* ``TEST_POSTGRES_URL``  = ADMIN URL of a DISPOSABLE local PostgreSQL server. It is only used to CREATE and DROP uniquely
  named databases. No test creates or drops tables in the database named inside that URL.
* ``ALLOW_DESTRUCTIVE_PG_TESTS=1`` = explicit opt-in. Without it PostgreSQL tests skip and nothing connects.
* ``PG_TEST_ALLOWED_HOSTS`` (optional, comma separated) = extra allowed LOOPBACK hosts (``localhost`` or a loopback IP literal).
  It can never allow a non-loopback host: every host must be loopback regardless of this variable.

What is validated
-----------------
1. The URL is parsed ONCE and the PARSED parts (host, port, user, password, database) are what the driver is given as keyword
   arguments. The text is never handed to libpq, so percent-encoding, extra ``@`` tricks or query parameters cannot make libpq
   connect somewhere other than what was checked.
2. Exactly one explicit host, which must be ``localhost`` (resolving only to loopback addresses) or a loopback IP literal.
   Host names (CNAMEs), private/public IPs, unix sockets, multi-host lists and percent-encoded hosts are refused.
3. Only the query parameters in ``ALLOWED_QUERY_KEYS`` are accepted. ``host``, ``hostaddr``, ``port``, ``dbname``, ``service``,
   ``options`` and everything else that could redirect or reconfigure the connection is refused.
4. The port is ALWAYS passed explicitly (the URL's port, else 5432). libpq would otherwise fall back to ``PGPORT``, i.e. an inherited
   environment variable could pick the destination of an otherwise validated ``localhost`` URL. ``database_url`` and ``normalize_url``
   (used for SQLAlchemy engines, which also omit a missing port) render the explicit port too.
5. Environment policy (see ``TARGET_ENV_VARS`` / ``CREDENTIAL_FORWARDING_ENV_VARS`` / ``PERMITTED_ENV_VARS``). libpq reads ~40 ``PG*``
   variables, and the set depends on the libpq version (the Windows wheel of psycopg bundles libpq 14, the Linux wheel libpq 17).
   Variables that choose WHERE the connection goes, WHICH database it opens, or that change the session behind the test's back must be
   UNSET (presence, even an empty value, is a refusal): ``PGHOST``, ``PGHOSTADDR``, ``PGPORT``, ``PGDATABASE``, ``PGSERVICE``,
   ``PGSERVICEFILE``, ``PGOPTIONS``. So must the variable that FORWARDS the user's Kerberos credentials to whatever server answers
   (``PGGSSDELEGATION``): it cannot redirect the connection, but it would hand credentials over before the server check in step 6 can
   notice a tunnel, and nothing in this project needs it. They are re-checked immediately before the driver is called.
   FAIL CLOSED: any ``PG*`` variable that the installed libpq reports but that is in none of the lists is refused when present, and a test
   fails until it is classified, so a newer libpq cannot add a redirecting variable unnoticed.
   Variables that only affect authentication, TLS, encoding or timing cannot change
   the destination (host, port and database are explicit arguments, and exactly one host is allowed), so they are permitted:
   ``PGUSER``, ``PGPASSWORD``, ``PGPASSFILE``, ``PGSSL*`` (including ``PGSSLCERTMODE``, whether a client certificate is sent, and
   ``PGSSLNEGOTIATION``, how the TLS handshake starts), ``PGREQUIRESSL`` (deprecated), ``PGGSSENCMODE``, ``PGKRBSRVNAME``, ``PGREQUIREAUTH``, ``PGCHANNELBINDING``,
   ``PGREQUIREPEER``, ``PGGSSLIB``, ``PGCONNECT_TIMEOUT``, ``PGCLIENTENCODING``, ``PGAPPNAME``, ``PGTZ``, ``PGDATESTYLE``, ``PGGEQO``, ``PGSYSCONFDIR`` (only locates the
   service file, which is refused anyway), ``PGTARGETSESSIONATTRS`` and ``PGLOADBALANCEHOSTS`` (only meaningful with several hosts, which
   are refused). A permitted variable can make a connection fail; it cannot make it go somewhere else.
6. After connecting (and before the first CREATE/DROP), the server itself is checked: ``current_database()`` must equal the
   database the URL names, every database on the server must be ``postgres``/a template/a ``pp_test_*``/``pp_mig_*`` database, and
   no hosted-service role (``supabase_admin``, ``rdsadmin`` ...) may exist. This is what catches an SSH tunnel or port-forward on
   ``localhost`` that actually leads to a shared or hosted server.
7. Destructive operations only ever target databases named ``pp_test_<id>`` / ``pp_mig_<id>``.

Every refusal raises :class:`UnsafeDatabaseTarget` BEFORE a destructive statement runs (checks 1-4 and 6 before any connection is
opened) and never includes a password or the full connection string (see :func:`redact`).
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import uuid
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy.engine import make_url

SAFE_DB_PREFIXES = ("pp_test_", "pp_mig_")
DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "::1")
FORBIDDEN_SUBSTRINGS = ("supabase.co", "supabase.com", "supabase.net", "pooler.supabase", "amazonaws.com")
PAYPILOT_PROJECT_REF = "jqhslizdnbdmpjafdzcw"
ALLOWED_SCHEMES = ("postgresql", "postgres")
ALLOWED_QUERY_KEYS = ("sslmode", "connect_timeout", "application_name")
DEFAULT_PORT = 5432
TARGET_ENV_VARS = ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS")
# Cannot redirect the connection, but would hand the user's Kerberos credentials to whatever server answers (libpq 16+: gssdelegation).
CREDENTIAL_FORWARDING_ENV_VARS = ("PGGSSDELEGATION",)
REFUSED_ENV_VARS = TARGET_ENV_VARS + CREDENTIAL_FORWARDING_ENV_VARS
PERMITTED_ENV_VARS = ("PGUSER", "PGPASSWORD", "PGPASSFILE", "PGSSLMODE", "PGSSLCERT", "PGSSLKEY", "PGSSLROOTCERT", "PGSSLCRL", "PGSSLCRLDIR",
                      "PGSSLSNI", "PGSSLCOMPRESSION", "PGSSLMINPROTOCOLVERSION", "PGSSLMAXPROTOCOLVERSION", "PGGSSENCMODE", "PGKRBSRVNAME",
                      "PGSSLCERTMODE", "PGSSLNEGOTIATION", "PGREQUIRESSL",  # libpq 16 / 17 / deprecated: TLS client-cert and handshake behaviour only
                      "PGREQUIREAUTH", "PGCHANNELBINDING", "PGCONNECT_TIMEOUT", "PGCLIENTENCODING", "PGAPPNAME", "PGTZ", "PGDATESTYLE", "PGGEQO",
                      "PGSYSCONFDIR", "PGTARGETSESSIONATTRS", "PGLOADBALANCEHOSTS", "PGREQUIREPEER", "PGGSSLIB")
SERVER_DATABASES = ("postgres", "template0", "template1")
HOSTED_SERVICE_ROLES = ("supabase_admin", "supabase_auth_admin", "supabase_storage_admin", "supabase_read_only_user", "pgbouncer",
                        "rdsadmin", "cloudsqladmin", "azure_pg_admin", "neon_superuser", "doadmin")
_SAFE_NAME_RE = re.compile(r"^[a-z0-9_]+$")
_BAD_CHARS_RE = re.compile(r"[\x00-\x20\x7f]")


class UnsafeDatabaseTarget(Exception):
    """A test tried to do something destructive against a database that is not provably disposable."""


def _is_loopback_ip(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _localhost_resolves_to_loopback_only() -> bool:
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo("localhost", None)}
    except OSError:
        return False
    return bool(addrs) and all(_is_loopback_ip(a.split("%", 1)[0]) for a in addrs)


def _is_loopback_host(host: str) -> bool:
    return _localhost_resolves_to_loopback_only() if host == "localhost" else _is_loopback_ip(host)


def _allowed_hosts() -> set[str]:
    """Default loopback names plus PG_TEST_ALLOWED_HOSTS (loopback only). An entry that is not loopback is itself a refusal: the variable cannot widen the policy."""
    extra = [h.strip().lower() for h in os.environ.get("PG_TEST_ALLOWED_HOSTS", "").split(",") if h.strip()]
    for h in extra:
        if not (h == "localhost" or _is_loopback_ip(h)):
            raise UnsafeDatabaseTarget("refusing: PG_TEST_ALLOWED_HOSTS may only list 'localhost' or loopback IP addresses (an entry was neither)")
    return {h.lower() for h in DEFAULT_ALLOWED_HOSTS} | set(extra)


def _parse(url: str):
    try:
        return make_url(url)
    except Exception:  # never echo the (possibly secret) text of a malformed URL
        raise UnsafeDatabaseTarget("refusing: the PostgreSQL URL could not be parsed (value not shown)") from None


def redact(url: str) -> str:
    """host:port/dbname only. Never user, password, query string or scheme details."""
    try:
        u = make_url(url)
    except Exception:
        return "<unparseable url>"
    host = u.host or "<no-host>"
    if ":" in host:
        host = f"[{host}]"
    port = f":{u.port}" if u.port else ""
    return f"{host}{port}/{u.database or ''}"


def libpq_reported_env_vars() -> set[str]:
    """Every environment variable the INSTALLED libpq says it reads (psycopg asks libpq itself, so it follows the bundled libpq version)."""
    from psycopg import pq

    return {o.envvar.decode() for o in pq.Conninfo.get_defaults() if o.envvar}


def unclassified_libpq_env_vars(reported: set[str] | None = None) -> list[str]:
    """Variables libpq reads that are in none of the three lists. Anything returned here has not been reviewed, so it is treated as unsafe."""
    known = set(REFUSED_ENV_VARS) | set(PERMITTED_ENV_VARS)
    return sorted((libpq_reported_env_vars() if reported is None else reported) - known)


def assert_clean_environment(where: str = "the connection") -> None:
    """Refuse when a libpq variable that selects the target, alters the session or forwards credentials is present, or when ANY variable
    the installed libpq reads is present without having been classified (fail closed). Names only are reported, never values."""
    present = [var for var in REFUSED_ENV_VARS if os.environ.get(var) is not None]  # presence, not truthiness: libpq honours an empty value too
    if present:
        raise UnsafeDatabaseTarget(f"refusing {where}: environment variable(s) {', '.join(present)} can redirect or alter the connection; unset them")
    unknown = [var for var in unclassified_libpq_env_vars() if os.environ.get(var) is not None]
    if unknown:
        raise UnsafeDatabaseTarget(f"refusing {where}: environment variable(s) {', '.join(unknown)} are read by libpq but have not been classified in "
                                   "pg_safety.py; unset them (or classify them after review)")


def _effective_port(u) -> int:
    return u.port if u.port is not None else DEFAULT_PORT


def normalize_url(url: str) -> str:
    """Validated URL text with the host lower-cased and the port ALWAYS explicit. For SQLAlchemy engines, which (like libpq) would
    otherwise fall back to ``PGPORT`` when the URL has no port."""
    assert_safe_admin_url(url)
    u = _parse(url)
    return u.set(host=u.host.strip().lower(), port=_effective_port(u)).render_as_string(hide_password=False)


def assert_safe_admin_url(url: str) -> None:
    """Raise UnsafeDatabaseTarget (before any connection) unless the URL points at a local PostgreSQL server and cannot be redirected."""
    if not url or not isinstance(url, str):
        raise UnsafeDatabaseTarget("refusing: empty PostgreSQL URL")
    if _BAD_CHARS_RE.search(url):
        raise UnsafeDatabaseTarget("refusing: the PostgreSQL URL contains whitespace or control characters (value not shown)")
    u = _parse(url)
    where = redact(url)
    backend = u.drivername.split("+", 1)[0]
    if backend not in ALLOWED_SCHEMES:
        raise UnsafeDatabaseTarget(f"refusing the URL: scheme '{backend}' is not postgresql/postgres")
    lowered = url.lower()
    for bad in FORBIDDEN_SUBSTRINGS:
        if bad in lowered:
            raise UnsafeDatabaseTarget(f"refusing {where}: target looks like a hosted/production database ({bad})")
    if PAYPILOT_PROJECT_REF in lowered:
        raise UnsafeDatabaseTarget(f"refusing {where}: target references the PayPilot project ref")

    for key, value in u.query.items():  # anything that could redirect or reconfigure the connection
        if key not in ALLOWED_QUERY_KEYS:
            raise UnsafeDatabaseTarget(f"refusing {where}: URL query parameter '{key}' is not allowed (allowed: {', '.join(ALLOWED_QUERY_KEYS)})")
        if not isinstance(value, str):
            raise UnsafeDatabaseTarget(f"refusing {where}: URL query parameter '{key}' is repeated")
    assert_clean_environment(where)

    host = u.host
    if not host:
        raise UnsafeDatabaseTarget(f"refusing {where}: no explicit host (unix sockets/defaults are not allowed)")
    if "," in host:
        raise UnsafeDatabaseTarget(f"refusing {where}: multi-host connection strings are not allowed")
    host = host.strip().lower()
    _allowed_hosts()  # validates PG_TEST_ALLOWED_HOSTS (a non-loopback entry is refused); it can never widen what is allowed below
    if not _is_loopback_host(host):
        raise UnsafeDatabaseTarget(f"refusing {where}: host '{host}' is not a loopback address (only localhost, 127.0.0.0/8 and ::1 are allowed)")
    if u.port is not None and not (1 <= u.port <= 65535):
        raise UnsafeDatabaseTarget(f"refusing {where}: invalid port")


def assert_safe_database_name(name: str) -> None:
    """Only databases whose name starts with a SAFE_DB_PREFIX (plus a non-empty [a-z0-9_] suffix) may be created/dropped/reset."""
    if not isinstance(name, str) or not name:
        raise UnsafeDatabaseTarget("refusing: empty database name")
    ok = name.startswith(SAFE_DB_PREFIXES) and _SAFE_NAME_RE.match(name) and any(len(name) > len(p) for p in SAFE_DB_PREFIXES if name.startswith(p))
    if not ok:
        raise UnsafeDatabaseTarget(
            f"refusing database '{name}': destructive operations are only allowed on databases named "
            f"{' or '.join(p + '<id>' for p in SAFE_DB_PREFIXES)}"
        )


def assert_opt_in() -> None:
    if os.environ.get("ALLOW_DESTRUCTIVE_PG_TESTS") != "1":
        raise UnsafeDatabaseTarget("refusing: ALLOW_DESTRUCTIVE_PG_TESTS=1 is not set (explicit opt-in required)")


def assert_safe_destructive_target(url: str, dbname: str | None = None) -> str:
    """Full pre-flight for any destructive statement on ``url``: opt-in, host/URL rules, database-name rule. Returns the db name."""
    assert_opt_in()
    assert_safe_admin_url(url)
    name = dbname if dbname is not None else (_parse(url).database or "")
    assert_safe_database_name(name)
    return name


def pg_test_admin_url() -> str:
    """Admin URL for tests: skips (clear reason) if not configured/opted in; raises UnsafeDatabaseTarget if the URL is unsafe."""
    import pytest

    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("PostgreSQL tests skipped: set TEST_POSTGRES_URL to the admin URL of a DISPOSABLE local PostgreSQL server "
                    "and ALLOW_DESTRUCTIVE_PG_TESTS=1")
    if os.environ.get("ALLOW_DESTRUCTIVE_PG_TESTS") != "1":
        pytest.skip("PostgreSQL tests skipped: TEST_POSTGRES_URL is set but ALLOW_DESTRUCTIVE_PG_TESTS=1 is not "
                    "(explicit opt-in required to create/drop throw-away databases)")
    assert_safe_admin_url(url)
    return url


def _connect_kwargs(url: str, dbname: str | None = None) -> dict:
    """The PARSED, validated target as driver keyword arguments (the URL text itself never reaches libpq)."""
    assert_safe_admin_url(url)
    u = _parse(url)
    kwargs: dict = {"host": u.host.strip().lower(), "port": _effective_port(u), "dbname": dbname if dbname is not None else (u.database or "postgres")}
    if u.username:
        kwargs["user"] = u.username
    if u.password is not None:
        kwargs["password"] = u.password
    for key, value in u.query.items():
        kwargs[key] = value
    return kwargs


def _query(conn, sql: str, params=None):
    """Run a read-only catalog query on a psycopg OR a SQLAlchemy connection and return its cursor/result."""
    if hasattr(conn, "exec_driver_sql"):
        return conn.exec_driver_sql(sql, tuple(params) if params else ())
    return conn.execute(sql, params) if params is not None else conn.execute(sql)


def assert_disposable_server(conn, expected_database: str | None = None) -> None:
    """Interrogate the server we are REALLY connected to. Refuses a shared/hosted server reached through a tunnel or port-forward."""
    current = _query(conn, "select current_database()").fetchone()[0]
    if expected_database is not None and current != expected_database:
        raise UnsafeDatabaseTarget(f"refusing: connected to database '{current}' but the URL names '{expected_database}'")
    foreign = [n for (n,) in _query(conn, "select datname from pg_database where not datistemplate").fetchall()
               if n != "postgres" and not n.startswith(SAFE_DB_PREFIXES)]
    if foreign:
        raise UnsafeDatabaseTarget(f"refusing: the server hosts {len(foreign)} database(s) that are not postgres/pp_test_*/pp_mig_*; "
                                   "it is not a disposable test server (a tunnel to a shared server?)")
    roles = [n for (n,) in _query(conn, "select rolname from pg_roles where rolname = any(%s)", (list(HOSTED_SERVICE_ROLES),)).fetchall()]
    if roles:
        raise UnsafeDatabaseTarget(f"refusing: the server has hosted-service role(s) ({', '.join(sorted(roles))}); it is not a disposable test server")


def connect_verified(url: str, *, dbname: str | None = None, autocommit: bool = True):
    """Open a psycopg connection to a validated local target and verify the server before returning it. The caller owns the connection."""
    import psycopg

    kwargs = _connect_kwargs(url, dbname)
    assert_clean_environment()  # again, immediately before the driver reads the environment (it could have changed since validation)
    conn = psycopg.connect(autocommit=autocommit, connect_timeout=int(kwargs.pop("connect_timeout", 10)), **kwargs)
    try:
        assert_disposable_server(conn, expected_database=kwargs["dbname"])
    except BaseException:
        conn.close()
        raise
    return conn


def _admin_connect(admin_url: str):
    return connect_verified(admin_url)


def database_url(admin_url: str, name: str) -> str:
    """Same server/credentials/driver as ``admin_url`` but pointing at database ``name``."""
    assert_safe_admin_url(admin_url)
    u = _parse(admin_url)
    return u.set(host=u.host.strip().lower(), port=_effective_port(u), database=name).render_as_string(hide_password=False)


def verify_connected_database(conn, url: str) -> str:
    """Defense in depth before any table-level destructive statement: ask the server which database this is (``current_database()``),
    require a safe name and require it to be the database ``url`` names. Accepts a psycopg or a SQLAlchemy connection. Returns the name."""
    if hasattr(conn, "exec_driver_sql"):  # SQLAlchemy Connection
        actual = conn.exec_driver_sql("select current_database()").scalar()
    else:
        actual = conn.execute("select current_database()").fetchone()[0]
    assert_safe_database_name(actual)
    wanted = _parse(url).database
    if actual != wanted:
        raise UnsafeDatabaseTarget(f"refusing: connected to database '{actual}' but the URL names '{wanted}'")
    return actual


def verify_connection(conn, url: str) -> str:
    """verify_connected_database + assert_disposable_server: what ``_boot``-style code must call on a fresh engine before ``drop_all``."""
    name = verify_connected_database(conn, url)
    assert_disposable_server(conn, expected_database=name)
    return name


def create_database(admin_url: str, name: str) -> None:
    from psycopg import sql

    assert_safe_destructive_target(admin_url, name)
    with _admin_connect(admin_url) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def drop_database(admin_url: str, name: str) -> None:
    from psycopg import sql

    assert_safe_destructive_target(admin_url, name)
    with _admin_connect(admin_url) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def database_exists(admin_url: str, name: str) -> bool:
    assert_safe_admin_url(admin_url)
    with _admin_connect(admin_url) as conn:
        return conn.execute("select 1 from pg_database where datname = %s", (name,)).fetchone() is not None


def list_databases(admin_url: str, prefix: str = "") -> list[str]:
    assert_safe_admin_url(admin_url)
    with _admin_connect(admin_url) as conn:
        rows = conn.execute("select datname from pg_database where datname like %s order by 1", (prefix.replace("_", "\\_") + "%",)).fetchall()
    return [r[0] for r in rows]


@contextmanager
def temp_database(prefix: str = "pp_test") -> Iterator[str]:
    """Create ``{prefix}_{hex}`` on the disposable server, yield its URL, ALWAYS drop it afterwards.

    A failure while creating it triggers a best-effort drop of that unique name (a half-created database must not be left behind), and
    a failing cleanup never replaces the exception the test body raised: it is attached to it as a note instead."""
    admin = pg_test_admin_url()
    name = f"{prefix}_{uuid.uuid4().hex[:12]}"
    assert_safe_database_name(name)  # refuses unsafe prefixes before anything is created
    try:
        create_database(admin, name)
    except BaseException:
        try:
            drop_database(admin, name)
        except Exception:  # the original error is the interesting one
            pass
        raise
    try:
        yield database_url(admin, name)
    except BaseException as body_error:
        try:
            drop_database(admin, name)
        except Exception as cleanup_error:
            body_error.add_note(f"ALSO: cleanup of temporary database '{name}' failed: {type(cleanup_error).__name__} (it may need manual removal)")
        raise
    else:
        drop_database(admin, name)
