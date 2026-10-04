"""Passwords, session tokens and login throttling."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque

from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

SESSION_COOKIE = "ownlife_session"
# Every state-changing API request must carry this header. A page on another site
# cannot add a custom header to a request to this one without a CORS preflight,
# which this server never grants — so the header proves the request comes from
# OwnLife's own frontend. That is the CSRF defence, on top of SameSite=Lax.
CSRF_HEADER = "x-ownlife"

_hasher = PasswordHash((Argon2Hasher(),))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password, password_hash)
    except Exception:  # malformed hash: treat as a failed login, never as a crash
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session_token() -> tuple[str, str]:
    """Returns (token for the cookie, hash for the database)."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


class LoginThrottle:
    """At most `max_failures` failed logins per key in a sliding window."""

    def __init__(self, max_failures: int = 8, window_seconds: int = 300) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._failures[key]
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def allowed(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, time.monotonic())) < self.max_failures

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune(key, now).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


login_throttle = LoginThrottle()
