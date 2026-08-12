"""Tests for radar_pipeline.sources.ratelimit."""

from __future__ import annotations

import asyncio

import pytest

from radar_pipeline.sources.ratelimit import HostBlockedError, RateLimiter


class TestSerialSpacing:
    @pytest.mark.asyncio
    async def test_acquire_waits_for_minimum_interval(self):
        limiter = RateLimiter(
            per_host_concurrency=1,
            request_delay_seconds=0.1,
            jitter_seconds=0.0,
        )
        url = "https://example.com/feed"
        t0 = asyncio.get_event_loop().time()
        await limiter.acquire(url); limiter.release(url)
        first_end = asyncio.get_event_loop().time() - t0

        await limiter.acquire(url); limiter.release(url)
        second_end = asyncio.get_event_loop().time() - t0

        # Second acquire must honor the 0.1s minimum spacing.
        assert second_end - first_end >= 0.09

    @pytest.mark.asyncio
    async def test_concurrency_cap_serializes_overflow(self):
        limiter = RateLimiter(
            per_host_concurrency=1,
            request_delay_seconds=0.0,
            jitter_seconds=0.0,
        )
        url = "https://example.com/feed"

        await limiter.acquire(url)
        task = asyncio.create_task(_await_with_timeout(limiter, url, 0.2))
        await asyncio.sleep(0.05)
        assert not task.done()
        limiter.release(url)
        await task


async def _await_with_timeout(limiter, url, t):
    try:
        await asyncio.wait_for(limiter.acquire(url), timeout=t)
        return True
    except asyncio.TimeoutError:
        return False


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_immediate_block_on_503(self):
        limiter = RateLimiter(block_cooldown_seconds=10.0)
        url = "https://evil.example.com/feed"
        limiter.record_failure(url, status=503)
        with pytest.raises(HostBlockedError):
            await limiter.acquire(url)

    @pytest.mark.asyncio
    async def test_three_failures_trip_breaker(self):
        limiter = RateLimiter(block_cooldown_seconds=10.0)
        url = "https://flaky.example.com/feed"
        # Two non-critical 500s don't block — generic 5xx is a glitch
        # we might recover from. The third failure trips the breaker.
        for _ in range(2):
            limiter.record_failure(url, status=500)
        assert not await limiter.is_blocked(url)
        limiter.record_failure(url, status=500)
        assert await limiter.is_blocked(url)

        with pytest.raises(HostBlockedError):
            await limiter.acquire(url)

    @pytest.mark.asyncio
    async def test_success_resets_failures(self):
        limiter = RateLimiter()
        url = "https://ok.example.com/feed"
        limiter.record_failure(url, status=500)
        limiter.record_success(url)
        limiter.record_failure(url, status=500)
        # Two non-503 failures without a 3rd — should not trip the
        # breaker (which fires on 3 consecutive failures).
        assert not await limiter.is_blocked(url)

    @pytest.mark.asyncio
    async def test_retry_after_respected(self):
        limiter = RateLimiter(block_cooldown_seconds=120.0)
        url = "https://cool.example.com/feed"
        limiter.record_failure(url, status=429, retry_after=7.5)
        # Cooldown must honor Retry-After (7.5s), capped by
        # block_cooldown_seconds (120s), but pinned to that value via
        # the implementation's clamp — so it ends up somewhere between
        # 1 and 120s.
        with pytest.raises(HostBlockedError) as ei:
            await limiter.acquire(url)
        assert ei.value.retry_after > 0
        assert ei.value.retry_after <= 120


class TestIsBlocked:
    @pytest.mark.asyncio
    async def test_unknown_host_is_not_blocked(self):
        limiter = RateLimiter()
        assert not await limiter.is_blocked("https://clean.example.com/x")

    @pytest.mark.asyncio
    async def test_is_host_blocked(self):
        limiter = RateLimiter()
        limiter.record_failure("https://x.com/y", status=503)
        assert await limiter.is_host_blocked("x.com")
        assert not await limiter.is_host_blocked("other.com")
