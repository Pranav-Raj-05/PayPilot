"""Supabase JWT verification. The user identity is ALWAYS derived from the verified token."""
import logging
import uuid
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, Request
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.errors import NotConfigured, Unauthorized
from app.models import Profile

log = logging.getLogger("paypilot.auth")


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None = None


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True)


def decode_token(token: str, settings: Settings) -> dict:
    try:
        alg = jwt.get_unverified_header(token).get("alg", "")
        if alg == "HS256":
            if not settings.supabase_jwt_secret:
                raise NotConfigured("SUPABASE_JWT_SECRET is not configured")
            return jwt.decode(token, settings.supabase_jwt_secret, algorithms=["HS256"], audience="authenticated")
        if alg in ("RS256", "ES256"):
            if not settings.supabase_url:
                raise NotConfigured("SUPABASE_URL is not configured")
            key = _jwks_client(settings.supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json")
            signing_key = key.get_signing_key_from_jwt(token)
            return jwt.decode(token, signing_key.key, algorithms=[alg], audience="authenticated")
        raise Unauthorized("Unsupported token algorithm")
    except jwt.PyJWTError as exc:
        log.info("token rejected: %s", exc.__class__.__name__)
        raise Unauthorized("Invalid or expired session") from exc


def ensure_profile(db: Session, user_id: uuid.UUID, *, email: str, full_name: str | None, business_name: str | None) -> None:
    """Create the profile for a verified user if it does not exist yet - atomically.

    Several requests from the same new user routinely arrive at once. A check-then-insert races (both see "no row",
    both INSERT, one hits the primary-key constraint -> HTTP 500), so the existence check is delegated to the database:
    `INSERT ... ON CONFLICT (id) DO NOTHING` is a single atomic statement on both PostgreSQL and SQLite. An existing
    profile is never modified or replaced.
    """
    if db.get(Profile, user_id) is not None:  # fast path: no write for returning users
        return
    values = {"id": user_id, "email": email, "full_name": full_name, "business_name": business_name}
    dialect = db.get_bind().dialect.name
    if dialect in ("postgresql", "sqlite"):
        insert = pg_insert if dialect == "postgresql" else sqlite_insert
        created = db.execute(insert(Profile).values(**values).on_conflict_do_nothing(index_elements=[Profile.id])).rowcount
    else:  # other dialects: savepoint + tolerate the losing side of the race
        try:
            with db.begin_nested():
                db.add(Profile(**values))
            created = 1
        except IntegrityError:
            created = 0
    if created:
        # Publish the new row right away so concurrent requests see it, and so a SQLite write lock is not held for the
        # rest of a (possibly slow) request.
        db.commit()


def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUser:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthorized("Missing bearer token")
    claims = decode_token(token, get_settings())
    sub = claims.get("sub")
    if not sub:
        raise Unauthorized("Token has no subject")
    email = claims.get("email")
    meta = claims.get("user_metadata") or {}
    ensure_profile(db, _uuid(sub), email=email or "", full_name=meta.get("full_name"), business_name=meta.get("business_name"))
    request.state.user_id = sub
    return CurrentUser(id=sub, email=email)


def _uuid(value: str):
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise Unauthorized("Malformed subject") from exc
