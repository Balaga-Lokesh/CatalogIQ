"""Tests for the requests-per-minute pacer."""

import asyncio
import time

import pytest

from app.config import Settings
from app.llm import max_calls_per_minute
from app.metrics import Metrics
from app.pipeline import Enricher, MemoryCache
from app.ratelimit import RateLimiter
from tests.test_pipeline import FakeProvider


class FakeClock:
    """Time that only moves when someone 'sleeps', so tests run instantly."""

    def __init__(self):
        self.now = 100.0
        self.started = []

    def clock(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


def test_calls_are_spaced_evenly():
    fake = FakeClock()
    limiter = RateLimiter(per_minute=12, clock=fake.clock, sleep=fake.sleep)   # one per 5 s

    async def scenario():
        for _ in range(4):
            await limiter.acquire()
            fake.started.append(fake.now)

    asyncio.run(scenario())
    assert fake.started == [100.0, 105.0, 110.0, 115.0]   # first immediately, then every 5 s


def test_concurrent_callers_get_different_slots():
    # 5 tasks ask at the same moment: each reserves the NEXT free slot, none share one.
    limiter = RateLimiter(per_minute=600)   # one per 0.1 s, real clock
    starts = []

    async def one():
        await limiter.acquire()
        starts.append(time.monotonic())

    async def scenario():
        await asyncio.gather(*(one() for _ in range(5)))

    asyncio.run(scenario())
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(g >= 0.09 for g in gaps)


def test_idle_time_is_not_saved_up():
    # After a quiet period the next call starts at once, but calls don't burst:
    # the one after it still waits a full interval.
    fake = FakeClock()
    limiter = RateLimiter(per_minute=60, clock=fake.clock, sleep=fake.sleep)   # one per 1 s

    async def scenario():
        await limiter.acquire()
        fake.now += 30                     # nothing happens for 30 s
        await limiter.acquire()
        fake.started.append(fake.now)
        await limiter.acquire()
        fake.started.append(fake.now)

    asyncio.run(scenario())
    assert fake.started == [130.0, 131.0]   # right away after idling, then a full 1 s gap


def test_rejects_non_positive_rate():
    with pytest.raises(ValueError):
        RateLimiter(per_minute=0)


def test_enricher_paces_llm_calls():
    provider = FakeProvider()

    async def scenario():
        limiter = RateLimiter(per_minute=600)   # 0.1 s apart
        enricher = Enricher(provider, 5, Metrics(), MemoryCache(), rate_limiter=limiter)
        start = time.perf_counter()
        await asyncio.gather(*(enricher.enrich(f"p {i}") for i in range(5)))
        return time.perf_counter() - start

    assert asyncio.run(scenario()) >= 0.35   # 5 starts need 4 gaps of 0.1 s
    assert provider.calls == 5


def test_cache_hits_are_not_rate_limited():
    provider = FakeProvider()

    async def scenario():
        limiter = RateLimiter(per_minute=6)     # 10 s apart: a second LLM call would be slow
        enricher = Enricher(provider, 5, Metrics(), MemoryCache(), rate_limiter=limiter)
        await enricher.enrich("Amul butter")
        start = time.perf_counter()
        await enricher.enrich("AMUL  butter")   # same content -> cache hit, no wait
        return time.perf_counter() - start

    assert asyncio.run(scenario()) < 0.1
    assert provider.calls == 1


def make_settings(provider, max_rpm=None):
    return Settings(llm_provider=provider, llm_concurrency=5, mock_latency_ms=0, mock_failure_rate=0,
                    db_path=":memory:", llm_max_rpm=max_rpm)


def test_default_pacing_per_provider():
    assert max_calls_per_minute(make_settings("mock")) is None       # graders' mock: unlimited
    assert max_calls_per_minute(make_settings("groq")) == 14
    assert max_calls_per_minute(make_settings("ollama")) is None
    assert max_calls_per_minute(make_settings("groq", max_rpm=30)) == 30
    assert max_calls_per_minute(make_settings("groq", max_rpm=0)) is None   # 0 = off
    assert max_calls_per_minute(make_settings("mock", max_rpm=60)) == 60
