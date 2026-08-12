"""Per-host request rate limiter with a simple circuit breaker.

The collect stage talks to a small handful of hosts — mostly
``news.google.com`` (for the query feeds) and various portal RSS hosts.
Hitting any one of them hard is what got the radar pipeline 503'd
("Sorry... unusual traffic" page on Google News), so we wrap outbound
calls in two things:

* a per-host concurrency cap and a minimum interval between requests
  (with jitter), so we look like a polite client;
* a fast-fail circuit breaker: after a host returns a transient block
  (429 or 5xx) we mark it blocked for ``block_cooldown_seconds`` and
  short-circuit subsequent calls to ``HostBlockedError`` for the rest of
  this process, instead of spending the rest of the run re-issuing
  requests that will keep failing.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class HostBlockedError(RuntimeError):
    """Raised when the circuit breaker has tripped for a host."""

    def __init__(self, host: str, retry_after: float):
        super().__init__(f"Host blocked: {host} (retry in {retry_after:.1f}s)")
        self.host = host
        self.retry_after = retry_after


@dataclass
class HostState:
    host: str
    semaphore: asyncio.Semaphore
    last_request_at: float = 0.0
    blocked_until: float = 0.0
    consecutive_failures: int = 0


@dataclass
class RateLimiter:
    """Per-host limiter.

    Args:
        per_host_concurrency: max simultaneous in-flight requests to a
            given host. 0 disables the semaphore (unbounded).
        request_delay_seconds: minimum spacing between two requests to
            the same host, in seconds. 0 disables the spacing.
        jitter_seconds: random jitter added on top of
            ``request_delay_seconds`` (uniform in [0, jitter]). Must be
            >= 0.
        block_cooldown_seconds: how long a host stays "blocked" after a
            transient block response, before we retry it.
    """

    per_host_concurrency: int = 2
    request_delay_seconds: float = 0.25
    jitter_seconds: float = 0.25
    block_cooldown_seconds: float = 60.0

    _states: dict[str, HostState] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def host_for(self, url: str) -> str:
        return (urlparse(url).hostname or "").lower()

    async def _state_for(self, host: str) -> HostState:
        async with self._lock:
            st = self._states.get(host)
            if st is None:
                st = HostState(
                    host=host,
                    semaphore=asyncio.Semaphore(self.per_host_concurrency)
                    if self.per_host_concurrency > 0
                    else asyncio.Semaphore(1),
                )
                self._states[host] = st
            return st

    async def check(self, url: str) -> None:
        """Raise HostBlockedError if the host is currently blocked."""
        host = self.host_for(url)
        if not host:
            return
        st = await self._state_for(host)
        now = time.monotonic()
        if st.blocked_until > now:
            raise HostBlockedError(host, st.blocked_until - now)

    async def is_blocked(self, url: str) -> bool:
        """Non-raising check: True iff the host is currently blocked."""
        host = self.host_for(url)
        if not host:
            return False
        st = await self._state_for(host)
        return st.blocked_until > time.monotonic()

    async def is_host_blocked(self, host: str) -> bool:
        """Non-raising check by host string (no URL required)."""
        host = host.lower()
        if not host:
            return False
        st = await self._state_for(host)
        return st.blocked_until > time.monotonic()

    async def acquire(self, url: str) -> None:
        """Wait until it is OK to issue a request to ``url``, then claim
        the slot. Pair every successful ``acquire`` with a
        ``record_success`` or ``record_failure``.
        """
        host = self.host_for(url)
        if not host:
            return

        st = await self._state_for(host)
        now = time.monotonic()

        # Circuit breaker fast-fail.
        if st.blocked_until > now:
            raise HostBlockedError(host, st.blocked_until - now)

        await st.semaphore.acquire()
        try:
            # Re-check inside the semaphore so a concurrent
            # record_failure() that just tripped the breaker is honored.
            now = time.monotonic()
            if st.blocked_until > now:
                raise HostBlockedError(host, st.blocked_until - now)

            # Enforce minimum spacing between requests on this host.
            delay = self.request_delay_seconds
            if delay > 0 and st.last_request_at > 0:
                elapsed = now - st.last_request_at
                wait = delay + random.uniform(0.0, self.jitter_seconds) - elapsed
                if wait > 0:
                    await asyncio.sleep(wait)

            st.last_request_at = time.monotonic()
        except BaseException:
            st.semaphore.release()
            raise

    def release(self, url: str) -> None:
        """Release the semaphore acquired by ``acquire``."""
        host = self.host_for(url)
        if not host:
            return
        st = self._states.get(host)
        if st is not None:
            try:
                st.semaphore.release()
            except ValueError:
                pass

    def record_success(self, url: str) -> None:
        host = self.host_for(url)
        st = self._states.get(host)
        if st is None:
            return
        st.consecutive_failures = 0
        st.blocked_until = 0.0

    def record_failure(
        self,
        url: str,
        *,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Mark the host as blocked for ``block_cooldown_seconds``.

        The breaker is conservative: any non-2xx response (or a network
        error) increments the failure counter; three in a row trip the
        breaker — or a single 429/503 trips it immediately.
        """
        host = self.host_for(url)
        if not host:
            return
        st = self._states.get(host)
        if st is None:
            # Lazily create the state if the caller reports a failure
            # without first calling acquire() (e.g. an early error
            # before any successful handshake).
            st = HostState(
                host=host,
                semaphore=asyncio.Semaphore(self.per_host_concurrency)
                if self.per_host_concurrency > 0
                else asyncio.Semaphore(1),
            )
            self._states[host] = st

        st.consecutive_failures += 1

        # Always honor Retry-After (seconds) when present; clamp to a
        # reasonable range to avoid being locked out for hours.
        cooldown = self.block_cooldown_seconds
        if retry_after is not None:
            cooldown = max(min(retry_after, 300.0), 1.0)

        # 999 is LinkedIn's own bot-detection status code.
        immediate_block = status in (403, 429, 503, 999)
        if immediate_block or st.consecutive_failures >= 3:
            st.blocked_until = time.monotonic() + cooldown
            logger.warning(
                "rate limiter: blocking host %s for %.1fs "
                "(status=%s, consecutive_failures=%d)",
                host, cooldown, status, st.consecutive_failures,
            )


# Convenience context manager that pairs acquire/release and converts
# HostBlockedError into a fetchable failure.

class rate_limited:
    """async with limiter.rate_limited(url): ... do a request ... """

    def __init__(self, limiter: RateLimiter, url: str):
        self.limiter = limiter
        self.url = url

    async def __aenter__(self) -> None:
        await self.limiter.acquire(self.url)

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.limiter.release(self.url)
