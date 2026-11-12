"""Sliding-window rate limiter (in memory and per process: use Redis if you run more than one instance).

Who is limited:
* AUTHENTICATED endpoints are limited PER USER. `user_rate_limit` depends on the auth dependency, so the user is known *before* the
  window is checked and one user can never consume another user's quota. (Resolving the user inside the limiter matters: a limiter
  that only reads `request.state.user_id` runs before authentication and silently falls back to the IP.)
* PUBLIC endpoints (customer checkout) are limited per client IP via `ip_rate_limit`.

Client IP: forwarded headers are NEVER trusted by default (TRUSTED_PROXY_COUNT=0 -> the TCP peer address is used). Behind N trusted
reverse proxies set TRUSTED_PROXY_COUNT=N: the client address is then the N-th entry from the right of X-Forwarded-For, i.e. the
address the outermost trusted proxy saw. Entries further left are client-supplied and ignored, so they cannot be spoofed.
"""
import ipaddress
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.security import CurrentUser, get_current_user

_hits: dict[str, deque] = defaultdict(deque)
_lock = Lock()


def _hit(key: str, limit: int, window_seconds: int) -> None:
    now = time.monotonic()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= limit:
            raise AppError("Too many requests, slow down", code="rate_limited", status_code=429)
        q.append(now)


def client_ip(request: Request, trusted_proxy_count: int = 0) -> str:
    peer = request.client.host if request.client else "unknown"
    if trusted_proxy_count <= 0:
        return peer
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    if len(hops) < trusted_proxy_count:
        return peer  # fewer hops than trusted proxies: the header is not from our proxy chain, ignore it
    candidate = hops[-trusted_proxy_count]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return peer


def user_rate_limit(name: str, limit: int, window_seconds: int = 60):
    """Per-user limit for authenticated endpoints. Unauthenticated requests get 401 before any quota is used."""
    def dependency(user: CurrentUser = Depends(get_current_user)) -> None:
        _hit(f"{name}:user:{user.id}", limit, window_seconds)

    return dependency


def ip_rate_limit(name: str, limit: int, window_seconds: int = 60):
    """Per-client-IP limit for public endpoints."""
    def dependency(request: Request, settings: Settings = Depends(get_settings)) -> None:
        _hit(f"{name}:ip:{client_ip(request, settings.trusted_proxy_count)}", limit, window_seconds)

    return dependency


def reset() -> None:
    _hits.clear()
