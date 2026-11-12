# PostgreSQL testing guide

How to run the PostgreSQL-backed tests locally and in CI, what they prove, and what they do **not** prove.

Everything here can be reproduced from this repository alone. No binaries, extra dependencies or scratch scripts are needed beyond a disposable PostgreSQL server that you start yourself.

## 1. Purpose and scope

The default test suite runs on in-memory SQLite. That cannot exercise PostgreSQL row locks (`SELECT ... FOR UPDATE`), the SQL migrations in `supabase/migrations/`, CHECK/trigger/RLS behaviour or concurrent first-request profile creation. The PostgreSQL tests cover exactly that, on **vanilla PostgreSQL**:

| Area | Test | What it checks |
|---|---|---|
| Migrations | `backend/tests/test_migrations_postgres.py` | The four migrations apply on a fresh database (with the compatibility shim), the money-integrity migration is rerunnable, fails closed on unexpected CHECK constraints, preserves trigger states, backfills only what it should, and honours the lock-timeout override; `refunds` is private even when the file runs statement by statement; the client-write hardening makes invoices, invoice items and the financial tables read-only for clients (and `anon` has nothing) while the owner path keeps writing; reruns change nothing; the signup trigger function `handle_new_user()` is not directly executable by `PUBLIC`, `anon` or `authenticated` while signup still creates the profile; the optional `seed_paypilot_demo` function is not executable by `PUBLIC`, `anon` or `authenticated` (the owner keeps it; `service_role` is left untouched, and its access on a real project depends on that project's default privileges) |
| Profile concurrency | `backend/tests/test_profile_concurrency.py` | Concurrent first requests create exactly one profile (PostgreSQL and file-SQLite) |
| Cancel versus capture | `backend/tests/test_money_integrity.py::test_d2_cancel_and_capture_race_on_postgresql` | A capture and a concurrent invoice cancel serialize on row locks and exactly one wins |
| Test safety | `backend/tests/test_pg_test_safety.py` | The guard rails in section 2 refuse unsafe targets |
| Whole suite on the migrated schema | `pytest tests -p tests.support.pg_migrated` | Every ordinary backend test, run against a database built from the real migrations instead of `Base.metadata.create_all` |

When the environment variables below are not set, all of these PostgreSQL tests **skip** with a message; they never fail and never connect anywhere. The plain `pytest -q` therefore needs no PostgreSQL.

## 2. Safety model

The tests create and drop whole databases, so the target is locked down:

* `TEST_POSTGRES_URL` is the **admin URL of a disposable server**, for example `postgresql://postgres@localhost:5432/postgres`. It is used **only** to `CREATE DATABASE` and `DROP DATABASE` uniquely named databases. No test creates or drops tables inside the database named in that URL, and nothing runs in it.
* `ALLOW_DESTRUCTIVE_PG_TESTS=1` is an explicit opt-in. Without it every PostgreSQL test skips and nothing connects. Setting only the URL is not enough.
* The helper module `backend/tests/pg_safety.py` runs before any connection and **refuses** (the test errors, it does not skip) when:
  * the host is not exactly one **loopback** address: `localhost` (which must resolve only to loopback addresses) or a loopback IP literal (`127.0.0.0/8`, `::1`). Host names, private or public IPs, unix sockets, multi-host lists and percent-encoded hosts are refused. `PG_TEST_ALLOWED_HOSTS` (comma separated; CI sets `localhost`) can only list further loopback addresses; it can **never** allow another host, and a non-loopback entry in it is itself refused;
  * the URL has a query parameter other than `sslmode`, `connect_timeout` or `application_name` (so `dbname=`, `host=`, `hostaddr=`, `port=`, `service=`, `options=` and the like cannot redirect the target), or repeats one;
  * a libpq environment variable that selects the target or alters the session is present (`PGHOST`, `PGHOSTADDR`, `PGPORT`, `PGDATABASE`, `PGSERVICE`, `PGSERVICEFILE`, `PGOPTIONS`; see section 2.1);
  * the URL mentions `supabase.co`, `supabase.com`, `supabase.net`, `pooler.supabase` or `amazonaws.com`, or contains the PayPilot Supabase project reference;
  * the scheme is not `postgresql`/`postgres`.
* The URL is parsed once and the **parsed** parts are handed to the driver as keyword arguments, so what is validated is exactly what is connected to. Every connection made by the test code goes through `pg_safety.connect_verified`, which then interrogates the server before any `CREATE`/`DROP`: `current_database()` must be the database the URL names, every database on the server must be `postgres`, a template or a `pp_test_*`/`pp_mig_*` database, and no hosted-service role (`supabase_admin`, `rdsadmin`, `pgbouncer` ...) may exist. This is what catches an SSH tunnel or port-forward on `localhost` that really leads to a shared or hosted server. **Consequence:** a local server that also holds your own databases is not accepted; use a dedicated throw-away server (a container or a scratch directory), as in section 4.
* The guard cannot prove that an *empty*, plain PostgreSQL server reached through a tunnel is local. Do not tunnel production ports to localhost while running these tests (see section 2.2 for exactly what is and is not detected).
* Only databases whose names start with `pp_test_` or `pp_mig_` (plus a random suffix) are ever created, dropped or truncated. Any other name is refused.
* Error messages and logs show only `host:port/dbname`; the user, password and full URL are never printed.

Never point `TEST_POSTGRES_URL` at a shared, staging, production or Supabase database, even with a valid-looking host. The guards are a safety net, not permission.

### 2.1 The connection-safety contract

The URL, the environment and the driver must all describe the same destination. `backend/tests/pg_safety.py` enforces this in order:

1. **Validate the URL** (parsed once, never by substring alone): one explicit host that is loopback, a safe query string, a valid port, no hosted-database marker.
2. **Validate the environment** (below). Presence of a blocked variable, even with an empty value, is a refusal; only the variable *names* are reported, never values.
3. **Require the opt-in** `ALLOW_DESTRUCTIVE_PG_TESTS=1`.
4. **Connect with the parsed parts as keyword arguments**: host, port, database, user, password. The port is **always explicit**: the URL's port, otherwise `5432`. Without that, libpq would use `PGPORT`, and an inherited variable could pick the destination of an otherwise valid `localhost` URL (this was reproduced in Stage B3.2: with `PGPORT=6543` libpq's default port becomes 6543 while the old helper passed no port). SQLAlchemy engines (`_boot`) get the same treatment through `normalize_url`, and `database_url` renders the port too.
5. **Re-check the environment immediately before the driver call**, so a change between validation and connection is still caught.
6. **Verify the server**: `current_database()` equals the database the URL names, every database is `postgres`, a template or `pp_test_*`/`pp_mig_*`, and no hosted-service role exists. Engines are verified the same way before `drop_all`.
7. **Only then** run a destructive statement, and only on a database named `pp_test_<id>` or `pp_mig_<id>`.

`backend/tests/test_pg_test_safety.py` also keeps an inventory of every call site that can connect or destroy data (`EXPECTED_CALL_SITES`). A new call site fails that test until it is routed through `pg_safety` and listed on purpose.

**Environment variables that are refused when present** (they decide where the connection goes, which database it opens, or what the session does behind the test's back):

| Variable | Why |
|---|---|
| `PGHOST`, `PGHOSTADDR` | choose or override the server address |
| `PGPORT` | chooses the port (the gap closed in Stage B3.2) |
| `PGDATABASE` | chooses the database |
| `PGSERVICE`, `PGSERVICEFILE` | load host, port and database from a service file |
| `PGOPTIONS` | injects session settings (`-c ...`); the URL `options` parameter is refused for the same reason |
| `PGGSSDELEGATION` | libpq 16+: forwards your Kerberos credentials to the server. It cannot redirect the connection, but the credentials would reach whichever server answers (for example through a tunnel) before the server check can notice; nothing here needs it |

Host, port and database are passed explicitly, so these could not win anyway; they are refused as well so that the environment never contradicts the URL and so that a future code path that forgets an argument cannot be redirected.

**Environment variables that are permitted**, because they can make a connection *fail* or change how it authenticates, encrypts or times out, but cannot make it go somewhere else (exactly one host, port and database are fixed in the call): `PGUSER`, `PGPASSWORD`, `PGPASSFILE`, `PGSSLMODE`, `PGSSLCERT`, `PGSSLKEY`, `PGSSLROOTCERT`, `PGSSLCRL`, `PGSSLCRLDIR`, `PGSSLSNI`, `PGSSLCOMPRESSION`, `PGSSLMINPROTOCOLVERSION`, `PGSSLMAXPROTOCOLVERSION`, `PGSSLCERTMODE` (libpq 16+, whether a client certificate is sent), `PGSSLNEGOTIATION` (libpq 17, how the TLS handshake starts: `postgres` or `direct`), `PGREQUIRESSL` (deprecated), `PGGSSENCMODE`, `PGGSSLIB`, `PGKRBSRVNAME`, `PGREQUIREAUTH`, `PGREQUIREPEER`, `PGCHANNELBINDING`, `PGCONNECT_TIMEOUT`, `PGCLIENTENCODING`, `PGAPPNAME`, `PGTZ`, `PGDATESTYLE`, `PGGEQO`, `PGSYSCONFDIR` (only locates the service file, which is refused anyway), `PGTARGETSESSIONATTRS` and `PGLOADBALANCEHOSTS` (only meaningful with several hosts, which are refused).

**The set depends on the libpq version.** psycopg bundles its own libpq: the Windows wheel of `psycopg-binary 3.2.3` bundles libpq 14 (28 variables), the Linux wheel that GitHub Actions installs bundles libpq 17 and reads three more (`PGGSSDELEGATION`, `PGSSLCERTMODE`, `PGSSLNEGOTIATION`). A Windows-only run therefore cannot see every variable. The guard **fails closed** in two places: a test asserts that every variable the installed libpq reports (and, separately, the complete libpq 17 set that CI runs) is classified, and at run time any variable the installed libpq reads that is in none of the lists is refused when present. A newer libpq that adds a variable therefore forces a decision instead of slipping through. Names that libpq does not read (`PGDATA`, `PG_TEST_ALLOWED_HOSTS`) are not affected.

### 2.2 What the guard cannot prove

* `localhost` is checked to resolve only to loopback addresses, but it is resolved by the operating system; whoever controls your hosts file controls it.
* A tunnel or port-forward (SSH `-L`, a container port mapping to a remote machine) looks like loopback to any URL check. The server interrogation (step 6) catches a tunnel to a server that holds other databases or hosted-service roles, which covers Supabase, RDS, Cloud SQL and any server that is not exclusively a test server. It **cannot** distinguish a local, empty, plain PostgreSQL server from a remote, empty, plain one that happens to be tunnelled. Do not tunnel production ports to localhost while these tests run.
* A local server that also holds your own databases is refused on purpose. Use a dedicated throw-away server.
* The checks protect the *test suite*. They are not a security boundary against someone who edits the test code.

## 3. Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `TEST_POSTGRES_URL` | yes, for PostgreSQL tests | Admin URL of a disposable local server. Never a Supabase URL |
| `ALLOW_DESTRUCTIVE_PG_TESTS` | yes, must be exactly `1` | Opt-in to creating/dropping `pp_test_*` / `pp_mig_*` databases |
| `PG_TEST_ALLOWED_HOSTS` | no | Extra allowed **loopback** addresses only (never another host); optional |
| `PGHOST`, `PGHOSTADDR`, `PGPORT`, `PGDATABASE`, `PGSERVICE`, `PGSERVICEFILE`, `PGOPTIONS` | must be **unset** | The helper refuses to run while any of them is present, even empty (section 2.1). Put the port in the URL instead |
| `money_integrity.lock_timeout` | no (PostgreSQL GUC, not an OS variable) | Overrides the migration's `lock_timeout` (default `10s`) for the session that runs it: `set money_integrity.lock_timeout = '500ms';` before running the migration file. The migration reads it with `current_setting('money_integrity.lock_timeout', true)` and resets `lock_timeout` at the end. The migration tests use this to prove it fails fast instead of waiting on a held lock |

## 4. Getting a disposable PostgreSQL

Nothing is added to the repository for this. Pick one.

**Docker (recommended)**, matches CI (PostgreSQL 17), trust auth, discarded with `--rm`:

```
docker run --rm -d --name pp-pg-test -p 127.0.0.1:55432:5432 -e POSTGRES_HOST_AUTH_METHOD=trust postgres:17
```

then use `postgresql://postgres@127.0.0.1:55432/postgres`. Stop it with `docker stop pp-pg-test` (the data goes with it). Binding to `127.0.0.1` keeps the trust-auth server off your network.

**Native install**: install PostgreSQL 16 or 17 from your OS package manager or the official installer, create a throw-away cluster (`initdb`, a fresh data directory, listening on localhost only) and use a superuser or a role with `CREATEDB`. Do not reuse a server that holds data you care about.

**Embedded PostgreSQL wheels** exist on PyPI. They are optional, unvetted third-party code that ships a PostgreSQL binary, and they are **not** a dependency of this project. The maintainers used one once during development to get a local server without Docker. If you consider one, vet the package and its binaries yourself first; the documented route is Docker or a native install.

Local ad-hoc runs may use a different major version (for example 16.x) than CI (17) and Supabase (17.x); see section 8.

## 5. Running the tests

Run from `backend/`. Set the variables first (shown for bash; in PowerShell use `$env:TEST_POSTGRES_URL = "..."` and `$env:ALLOW_DESTRUCTIVE_PG_TESTS = "1"`):

```
cd backend
pip install -r requirements-dev.txt
export TEST_POSTGRES_URL="postgresql://postgres@127.0.0.1:55432/postgres"
export ALLOW_DESTRUCTIVE_PG_TESTS=1
```

Use `-rs` to print skip reasons so you can confirm nothing was silently skipped.

```
# 1. migration tests
python -m pytest tests/test_migrations_postgres.py -q -rs

# 2. profile concurrency (PostgreSQL and file-SQLite variants)
python -m pytest tests/test_profile_concurrency.py -q -rs

# 3. cancel versus capture race
python -m pytest "tests/test_money_integrity.py::test_d2_cancel_and_capture_race_on_postgresql" -q -rs

# 4. the test-safety guards
python -m pytest tests/test_pg_test_safety.py -q -rs

# 5. complete backend suite with PostgreSQL enabled (PostgreSQL tests run instead of skipping)
python -m pytest -q -rs

# 6. complete backend suite on the migrated schema (opt-in plugin)
python -m pytest tests -p tests.support.pg_migrated -q -rs
```

Without PostgreSQL (the default developer run and the plain CI `backend` job), unset both variables and run `pytest -q`: the PostgreSQL tests are skipped.

Step 6 uses `python -m pytest` so that the `tests` package is importable when the plugin loads. The plugin (`backend/tests/support/pg_migrated.py`) creates **one** throw-away `pp_mig_*` database per pytest session, applies `tests/support/supabase_shim.sql` and the four real migrations, points the in-memory-SQLite `client` fixtures at that database and truncates the data tables before each test. The application connects as the database owner, which bypasses RLS, so RLS is covered by the migration tests, not by this run. Tests that bring their own databases (`test_migrations_postgres.py`, the `postgres_client` tests: profile-concurrency PostgreSQL variants and the race test) are deselected while the plugin is active, which is why steps 1 to 3 run separately. Without the opt-in variables the plugin skips every test rather than silently falling back to SQLite. See the module docstring for the full list of limitations.

Frontend (no PostgreSQL involved):

```
cd frontend
npm ci
npm test
npm run build
```

### Expected results

The counts change as tests are added, so treat these as a reference taken at the time of writing and compare the trend (no failures, and no unexpected skips with PostgreSQL enabled):

* Plain `pytest -q` (no PostgreSQL variables): everything passes; the PostgreSQL-only tests appear as skipped.
* With PostgreSQL enabled: zero failures and zero PostgreSQL skips in steps 1 to 6. Any `SKIPPED` line mentioning `TEST_POSTGRES_URL` means the opt-in variables did not reach pytest.
* Last recorded numbers are listed in section 10.

## 6. Cleaning up abandoned databases

Tests drop their databases even when they fail. A killed run (Ctrl+C at the wrong moment, power loss, a CI runner that was cancelled mid-test) can leave `pp_test_*` / `pp_mig_*` databases behind.

> **WARNING: run this only on a disposable server.** It drops every database whose name matches the two prefixes, including ones that another run is using right now. Never run it on a shared, staging, production or Supabase server. Simply discarding the Docker container is safer.

List them first:

```
select datname from pg_database where datname like 'pp\_test\_%' or datname like 'pp\_mig\_%' order by datname;
```

Then drop only those (psql):

```
select format('drop database %I with (force)', datname)
from pg_database
where datname like 'pp\_test\_%' or datname like 'pp\_mig\_%'
\gexec
```

(`\gexec` is a psql meta-command; from another client run the generated statements one by one.) `DROP DATABASE ... WITH (FORCE)` needs PostgreSQL 13 or newer.

## 7. What the shim is, and why it is not Supabase

`backend/tests/support/supabase_shim.sql` fakes the parts of Supabase that the migrations reference so they can run on vanilla PostgreSQL: an `auth` schema with a minimal `auth.users`, `auth.uid()` / `auth.role()` reading request-claim settings, the `anon` / `authenticated` / `service_role` roles and approximate default privileges. It is an approximation written for tests and **must never be applied to a Supabase project**, which already has the real versions.

| Behaviour | Covered by | Notes |
|---|---|---|
| Migration SQL syntax and semantics, constraints, indexes, triggers, backfills, rerun safety | **Vanilla PostgreSQL** (tests above) | Verified on PostgreSQL 16.x locally and 17 in CI |
| Row locking, `SELECT ... FOR UPDATE` ordering, capture/cancel race | **Vanilla PostgreSQL** | Direct connections, no pooler |
| Concurrent profile creation | **Vanilla PostgreSQL** | |
| RLS policy logic that depends on `auth.uid()` | **Shim** | The shim's `auth.uid()` reads a setting that the tests set. This checks the policy expression, not how Supabase fills the claim |
| The three roles and their grants | **Shim** | Approximation only |
| Real `auth.*` schema, GoTrue users, JWT claims (`sub`, `role`, `aal`, ...) | **Requires a real Supabase development branch** | |
| PostgREST access with a real JWT through RLS | **Requires a real Supabase development branch** | The backend uses the owner role and bypasses RLS; PostgREST is a defence-in-depth path |
| Role grants and default privileges exactly as Supabase sets them | **Requires a real Supabase development branch** | |
| `pgcrypto` and other extensions installed by default | **Requires a real Supabase development branch** | The migrated-schema plugin tolerates a missing `pgcrypto`, a real project must be checked |
| Event triggers installed by Supabase | **Requires a real Supabase development branch** | |
| Pooler (transaction mode) together with `FOR UPDATE` | **Requires a real Supabase development branch** | Tests use direct connections |
| `supabase db push` / the migration runner's transaction behaviour | **Requires a real Supabase development branch** | |
| Owner-role privileges for `ALTER TABLE ... DISABLE TRIGGER` | **Requires a real Supabase development branch** | The backfill disables and re-enables the `touch_updated_at` trigger on `transactions`; this needs table ownership (or equivalent), which the shim's superuser always has |

In short: PostgreSQL behaviour is verified on vanilla PostgreSQL. Supabase-specific behaviour is **not yet verified**; the migration has not been run against a live Supabase project.

## 8. PostgreSQL version gap

CI uses `postgres:17`. The Supabase project runs 17.x. Ad-hoc local runs may use another version (the original development runs used 16.2). Differences between 16 and 17 are unlikely to matter for these migrations, but CI is the reference run; a local 16.x pass is not a substitute for it.

**Status: PostgreSQL 17 has not been run by this project.** Every local result in section 10 is PostgreSQL 16.2, because Docker was not available on the development machine. The `backend-postgres` job in `.github/workflows/ci.yml` is configured for `postgres:17` but has never executed. PostgreSQL 17 compatibility is established only by a green run of that job (every step with zero PostgreSQL skips). Running it needs the work to be committed and pushed, which is a separate approval.

## 9. Pre-flight checklist for the first Supabase development-branch test

Do this on a Supabase **development branch** (or a throw-away project), never directly on production.

1. **Branch or back up first.** The migration is forward-only: there is no automatic down migration. Create the branch from production schema (or take a backup) so you can discard it.
2. **Read-only inspection first.** Before applying anything, list tables, constraints and triggers on the branch. Confirm the schema matches `20260101000000_init_schema.sql` and `20260101000100_rls_policies.sql`: in particular the CHECK constraints on `transactions(status)`, the `touch_updated_at` trigger on `transactions`, `transactions.fee_amount` / `net_amount`, and the owner role you will connect as.
3. **Apply the four migration files individually, in order**, through the SQL editor or `supabase db push` (one method only: the SQL editor records no history) and note whether the runner wraps each file in a transaction. The first two files are not rerunnable. Do not apply `tests/support/supabase_shim.sql`.
4. **Expected objects afterwards:** the `refunds` table, `invoices.version`, `transactions.refunded_amount` and `transactions.invoice_version` columns, `payment_links.invoice_version`, `transactions_status_check` allowing `partially_refunded`, RLS enabled on the new table, the original triggers in their original enabled state, only `invoices_select` and `invoice_items_select` policies on the invoice tables (no `invoices_all`), `authenticated` holding SELECT only on `invoices`, `invoice_items`, `payment_links`, `transactions`, `refunds`, `audit_logs` and `ai_messages`, and `anon` holding no privileges in `public`. A failure with a message beginning `money_integrity:` means a guard refused to touch an unexpected CHECK constraint: read the message, do not work around it.
5. **Do not run the pytest suite against Supabase.** The fixtures create and drop databases and the shim must never be applied there. `pg_safety` refuses Supabase hosts on purpose.
6. **Run the security and performance advisors** on the branch afterwards and review new findings.
7. **Deploy order:** migrate first, then deploy the backend. After the cutover, run the migration's backfill once more (re-running the file is safe) to cover orders created between the migration and the backend deploy.
8. **Lock timeout:** the migration sets `lock_timeout` to `10s`. If it fails with a lock timeout on a busy project, retry in a quiet window or raise it for the session with `set money_integrity.lock_timeout = '30s';` before running the file (any value `lock_timeout` accepts; `0` disables it). If your runner executes the file statement by statement and aborts on the first error, the session keeps the lock timeout until the connection closes or you run `reset lock_timeout;`, and partial progress is not rolled back in that mode (see the header comment of the migration).

## 10. Recorded results (for comparison)

Measured after the client-write hardening migration, on a local PostgreSQL **16.2** (Windows; a throw-away server). CI runs the same commands on PostgreSQL **17**, which has NOT been run locally (not available on the development machine). Counts will grow as tests are added; treat them as a sanity reference, not a contract.

With the PostgreSQL variables **set** (`TEST_POSTGRES_URL`, `ALLOW_DESTRUCTIVE_PG_TESTS=1`):

| Command | Result |
|---|---|
| `tests/test_migrations_postgres.py` | 91 passed (3 consecutive runs) |
| `tests/test_pg_test_safety.py` | 288 passed |
| `tests/test_profile_concurrency.py` | 12 passed (5 consecutive runs) |
| Race test `test_d2_cancel_and_capture_race_on_postgresql` | 1 passed (5 consecutive runs) |
| `tests/test_money_integrity.py` (includes the race test) | 87 passed |
| Complete backend suite, PostgreSQL enabled | 554 passed, 0 failed, 0 skipped (2 consecutive runs) |
| `pytest tests -p tests.support.pg_migrated` (migrated schema) | 364 passed, 190 deselected (the modules that bring their own databases), 1 warning (see below); 2 consecutive runs |

With the PostgreSQL variables **unset** (the default developer run and the plain CI `backend` job): PostgreSQL tests skip with an explanatory reason and nothing connects.

| Command | Result |
|---|---|
| `tests/test_migrations_postgres.py` | 20 passed (static), 71 skipped |
| `tests/test_pg_test_safety.py` | 284 passed, 4 skipped |
| `tests/test_profile_concurrency.py` | 7 passed (file-SQLite variants), 5 skipped |
| Complete backend suite | 473 passed, 81 skipped |
| `pytest tests -p tests.support.pg_migrated` | 364 skipped, 190 deselected |

Frontend: `cd frontend && npm test` = 19 test files, 438 tests passed; `npm run build` succeeds.

Known warning in the migrated-schema run: `SAWarning: DELETE statement on table 'ai_messages' expected to delete 4 row(s); 0 were matched`. The `delete_conversation` endpoint deletes a conversation's messages one by one and then the conversation; on a real schema with `ON DELETE CASCADE` the database has already removed the messages, so the ORM's own DELETE matches nothing. The end state is correct. SQLite does not enforce the foreign key, which is why it never shows there.
