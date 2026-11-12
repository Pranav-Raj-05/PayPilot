"""Profile auto-creation must be atomic: many simultaneous first requests for the same user must all succeed
and create exactly one profile (no IntegrityError -> HTTP 500)."""
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests import pg_safety
from tests.conftest import make_token

WORKERS = 16
ROUNDS = 5


def _is_sqlite(url: str) -> bool:
    return url.strip().lower().startswith("sqlite")


def _guard_destructive_target(url: str) -> None:
    """Refuse (UnsafeDatabaseTarget) BEFORE any engine/connection exists unless `url` is a disposable PostgreSQL database."""
    if _is_sqlite(url):
        return
    pg_safety.assert_safe_destructive_target(url)  # opt-in, host/URL rules, safe database name


def _boot(url: str):
    """Rebuild the schema in `url` and return the app. Drops/creates tables, so for PostgreSQL it is only ever allowed on a
    disposable local server and a database named pp_test_*/pp_mig_*; anything else raises UnsafeDatabaseTarget."""
    _guard_destructive_target(url)
    if not _is_sqlite(url):
        url = pg_safety.normalize_url(url)  # explicit port: neither SQLAlchemy nor libpq may fall back to PGPORT

    from app.core import db as dbmod, ratelimit
    from app.core.config import get_settings
    from app.main import create_app
    from app.models import Base

    engine = dbmod.init_engine(url)
    if not _is_sqlite(url):
        try:  # defense in depth: verify what we are REALLY connected to before the first DROP
            with engine.connect() as conn:
                pg_safety.verify_connection(conn, url)  # current_database() must be a safe name AND the one the URL names; server must be disposable
        except BaseException:
            engine.dispose()
            raise
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    ratelimit.reset()
    return create_app()


@pytest.fixture
def file_sqlite_client(providers, tmp_path):
    """File-backed SQLite: separate connections per request thread (unlike the shared in-memory test DB)."""
    app = _boot(f"sqlite:///{tmp_path / 'race.db'}")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def postgres_client(providers):
    """PostgreSQL variant on a UNIQUE throw-away database created (and always dropped) on the disposable server named by
    TEST_POSTGRES_URL. Skips unless TEST_POSTGRES_URL and ALLOW_DESTRUCTIVE_PG_TESTS=1 are set. Never touches the admin database."""
    with pg_safety.temp_database("pp_test") as url:
        app = _boot(url)
        with TestClient(app) as c:
            yield c


def _count_profiles(user_id: str) -> int:
    from app.core.db import session_factory
    from app.models import Profile

    with session_factory()() as db:
        return db.scalar(select(func.count()).select_from(Profile).where(Profile.id == uuid.UUID(user_id)))


def _fire(client: TestClient, headers: dict, n: int = WORKERS, path: str = "/api/auth/me"):
    """Release n requests at the same instant."""
    barrier = threading.Barrier(n)

    def hit(_):
        barrier.wait()
        return client.get(path, headers=headers)

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(hit, range(n)))


# ------------------------------------------------------------------ run on every dialect
@pytest.fixture(params=["sqlite", "postgres"])
def any_client(request):
    return request.getfixturevalue("file_sqlite_client" if request.param == "sqlite" else "postgres_client")


def test_concurrent_first_requests_create_one_profile(any_client):
    for _ in range(ROUNDS):  # a fresh user each round, all requests racing for the same new profile
        uid = str(uuid.uuid4())
        headers = {"Authorization": f"Bearer {make_token(uid, email='race@example.com')}"}
        responses = _fire(any_client, headers)
        assert [r.status_code for r in responses] == [200] * WORKERS, [r.text for r in responses if r.status_code != 200][:1]
        assert _count_profiles(uid) == 1
        assert {r.json()["id"] for r in responses} == {uid}


def test_concurrent_requests_on_a_business_endpoint(any_client):
    uid = str(uuid.uuid4())
    headers = {"Authorization": f"Bearer {make_token(uid)}"}
    responses = _fire(any_client, headers, path="/api/dashboard")
    assert all(r.status_code == 200 for r in responses)
    assert _count_profiles(uid) == 1


def test_repeated_sequential_requests_keep_first_values(any_client):
    uid = str(uuid.uuid4())
    h1 = {"Authorization": f"Bearer {make_token(uid, email='first@example.com')}"}
    h2 = {"Authorization": f"Bearer {make_token(uid, email='changed@example.com')}"}
    assert any_client.get("/api/auth/me", headers=h1).json()["email"] == "first@example.com"
    for _ in range(3):
        r = any_client.get("/api/auth/me", headers=h2)
        assert r.status_code == 200 and r.json()["email"] == "first@example.com"  # existing profile is never overwritten
    assert _count_profiles(uid) == 1


def test_existing_profile_is_untouched(any_client):
    from app.core.db import session_factory
    from app.models import Profile

    uid = uuid.uuid4()
    with session_factory()() as db:
        db.add(Profile(id=uid, email="seed@example.com", full_name="Seeded", business_name="Biz", default_currency="EUR"))
        db.commit()
    r = any_client.get("/api/auth/me", headers={"Authorization": f"Bearer {make_token(str(uid), email='other@example.com')}"})
    assert r.status_code == 200
    assert (r.json()["email"], r.json()["full_name"], r.json()["business_name"], r.json()["default_currency"]) == ("seed@example.com", "Seeded", "Biz", "EUR")
    assert _count_profiles(str(uid)) == 1


def test_new_profile_fields_come_from_the_verified_token(any_client):
    import jwt, time
    from tests.conftest import SECRET
    uid = str(uuid.uuid4())
    tok = jwt.encode({"sub": uid, "email": "meta@example.com", "aud": "authenticated", "exp": int(time.time()) + 600,
                      "user_metadata": {"full_name": "Meta Name", "business_name": "Meta Biz"}}, SECRET, algorithm="HS256")
    me = any_client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert (me["email"], me["full_name"], me["business_name"], me["default_currency"]) == ("meta@example.com", "Meta Name", "Meta Biz", "USD")


def test_auth_still_enforced_under_concurrency(file_sqlite_client):
    """The fix must not create profiles for unauthenticated / forged tokens."""
    uid = str(uuid.uuid4())
    forged = {"Authorization": f"Bearer {make_token(uid, secret='x' * 40)}"}
    assert {r.status_code for r in _fire(file_sqlite_client, forged)} == {401}
    assert {r.status_code for r in _fire(file_sqlite_client, {})} == {401}
    assert {r.status_code for r in _fire(file_sqlite_client, {"Authorization": f"Bearer {make_token('not-a-uuid')}"})} == {401}
    assert _count_profiles(uid) == 0


def test_cors_headers_present_on_success(file_sqlite_client):
    uid = str(uuid.uuid4())
    r = file_sqlite_client.get("/api/auth/me", headers={"Authorization": f"Bearer {make_token(uid)}", "Origin": "http://localhost:5173"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "http://localhost:5173"
    evil = file_sqlite_client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers  # CORS is still restricted, not disabled
