"""Small in-memory sliding-window rate limiter (per user or IP). Use Redis for multi-instance deployments."""
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Request

from app.core.errors import AppError

_hits: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def rate_limit(name: str, limit: int, window_seconds: int = 60):
    def dependency(request: Request) -> None:
        ident = getattr(request.state, "user_id", None) or (request.client.host if request.client else "anon")
        key = f"{name}:{ident}"
        now = time.monotonic()
        with _lock:
            q = _hits[key]
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= limit:
                raise AppError("Too many requests, slow down", code="rate_limited", status_code=429)
            q.append(now)

    return dependency


def reset() -> None:
    _hits.clear()
