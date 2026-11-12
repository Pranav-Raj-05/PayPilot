from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings

_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def _normalize(url: str) -> str:
    # Supabase hands out postgresql:// URLs; use the psycopg3 driver explicitly.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def init_engine(url: str | None = None):
    global _engine, _SessionLocal
    url = _normalize(url or get_settings().database_url)
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or url in ("sqlite://", "sqlite:///"):
            kwargs["poolclass"] = StaticPool
    _engine = create_engine(url, **kwargs)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def get_engine():
    if _engine is None:
        init_engine()
    return _engine


def session_factory() -> sessionmaker[Session]:
    if _SessionLocal is None:
        init_engine()
    return _SessionLocal  # type: ignore[return-value]


def get_db() -> Iterator[Session]:
    db = session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def upgrade_sqlite_dev_schema(engine) -> list[str]:
    """`create_all` creates missing TABLES but never adds columns to existing ones, so a local SQLite file created before a model gained a
    column would break. For SQLite only (local development / tests) add any missing column, using the model's server default for NOT
    NULL columns. Production schema changes are the SQL files in supabase/migrations. Returns what it added."""
    from app.models import Base

    added: list[str] = []
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)}'
                if col.server_default is not None:
                    ddl += f" DEFAULT {col.server_default.arg}"
                conn.execute(text(ddl))
                added.append(f"{table.name}.{col.name}")
    return added
