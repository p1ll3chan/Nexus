"""Shared-secret authentication for phone-bridge.

The Tailnet already encrypts traffic, but an application level shared secret
prevents any other device on the tailnet from injecting notifications.  Tokens
are compared in constant time and failed attempts are rate limited per source
address to slow down brute force attempts.
"""

from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field


class AuthError(RuntimeError):
    """Raised when an authentication attempt is rejected."""


@dataclass
class AuthAttempt:
    timestamp: float
    success: bool
    device: str = ""


@dataclass
class Authenticator:
    token: str
    max_attempts: int = 10
    window_seconds: float = 60.0

    _attempts: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))
    history: deque[AuthAttempt] = field(default_factory=lambda: deque(maxlen=200))

    def verify(self, candidate: str, source: str = "", device: str = "") -> bool:
        """Return True when ``candidate`` matches the configured token."""

        allowed = self._is_allowed(source)
        ok = bool(self.token) and bool(candidate) and hmac.compare_digest(
            candidate.encode("utf-8"), self.token.encode("utf-8")
        )
        self._record(source, ok, device)
        if not allowed:
            raise AuthError("too many failed authentication attempts")
        if not ok:
            raise AuthError("invalid authentication token")
        return True

    def is_locked(self, source: str) -> bool:
        return not self._is_allowed(source)

    def _is_allowed(self, source: str) -> bool:
        if not source:
            return True
        bucket = self._attempts[source]
        cutoff = time.monotonic() - self.window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        return len(bucket) < self.max_attempts

    def _record(self, source: str, success: bool, device: str) -> None:
        if not success and source:
            self._attempts[source].append(time.monotonic())
        self.history.append(AuthAttempt(time.monotonic(), success, device))

    def failures(self, source: str = "") -> int:
        if source:
            return len(self._attempts.get(source, ()))
        return sum(1 for attempt in self.history if not attempt.success)

    def reset(self, source: str = "") -> None:
        if source:
            self._attempts.pop(source, None)
        else:
            self._attempts.clear()
