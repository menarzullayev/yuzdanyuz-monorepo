"""Minimal shared circuit breaker for synchronous calls to outside services (audit F08).

A gateway outage must not pile up web threads: each request would wait for the full
HTTP timeout. After ``threshold`` failures inside ``window`` seconds the breaker opens
and callers fail fast for ``cooldown`` seconds; then one probe call is let through
(half-open). A success closes it, a failed probe opens it again.

State lives in the Django cache (Redis in production), so every web pod sees the same
breaker. Losing the cache only closes the breaker, which is the safe direction.
"""

from __future__ import annotations

import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)


class CircuitBreaker:
    def __init__(self, name: str, *, threshold: int = 5, window: int = 60, cooldown: int = 60):
        self.name = name
        self.threshold = threshold
        self.window = window
        self.cooldown = cooldown
        self._failures = f'cb:{name}:failures'
        self._open = f'cb:{name}:open'
        self._tripped = f'cb:{name}:tripped'
        self._probe = f'cb:{name}:probe'

    def allow(self) -> bool:
        """False while open. After the cooldown exactly one caller gets a probe."""
        if cache.get(self._open):
            return False
        if cache.get(self._tripped):
            # Half-open: the first caller after the cooldown probes, the rest wait.
            return bool(cache.add(self._probe, 1, timeout=self.cooldown))
        return True

    def record_success(self) -> None:
        cache.delete_many([self._failures, self._tripped, self._probe, self._open])

    def record_failure(self) -> None:
        if cache.get(self._tripped):
            self._trip()  # failed probe
            return
        cache.add(self._failures, 0, timeout=self.window)
        try:
            failures = cache.incr(self._failures)
        except ValueError:  # expired between add and incr
            cache.set(self._failures, 1, timeout=self.window)
            failures = 1
        if failures >= self.threshold:
            self._trip()

    def _trip(self) -> None:
        cache.set(self._open, 1, timeout=self.cooldown)
        # Remembered past the cooldown so the next call is treated as a probe.
        cache.set(self._tripped, 1, timeout=self.cooldown * 10)
        cache.delete_many([self._failures, self._probe])
        logger.warning('circuit breaker %s opened for %ss', self.name, self.cooldown)
