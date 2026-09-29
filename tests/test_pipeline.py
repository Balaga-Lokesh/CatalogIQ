"""Tests for the pipeline: concurrency limit, retries, de-duplication (requirement 8)."""

import asyncio
import json
import time

import pytest

from app.llm.base import LLMError, LLMProvider
from app.llm.mock import MockProvider
from app.metrics import Metrics
from app.pipeline import Enricher, MemoryCache

GOOD = json.dumps({"clean_title": "Amul Butter", "category": "Groceries", "brand": "Amul", "tags": ["butter"]})


class FakeProvider(LLMProvider):
    """Scripted provider. Tracks its OWN concurrency, so the tests don't rely
    on the Metrics code they are checking.

    script: what each successive call does - a string to return, or an
    Exception to raise. When the script runs out, `default` is used.
    fail_titles: products that always fail, whatever the script says.
    """

    name = "fake"

    def __init__(self, latency_s=0.0, script=(), default=GOOD, fail_titles=()):
        self.latency_s = latency_s
        self.script = list(script)
        self.default = default
        self.fail_titles = set(fail_titles)
        self.calls = 0
        self.active = 0
        self.max_active = 0

    async def enrich(self, raw_title, raw_description=""):
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.latency_s)
            if raw_title in self.fail_titles:
                raise LLMError(f"{raw_title} always fails")
            outcome = self.script.pop(0) if self.script else self.default
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        finally:
            self.active -= 1


def make_enricher(provider, concurrency=5, sleep=None, backoff_base_s=0.0):
    metrics = Metrics()
    kwargs = {"backoff_base_s": backoff_base_s}
    if sleep is not None:
        kwargs["sleep"] = sleep
    return Enricher(provider, concurrency, metrics, MemoryCache(), **kwargs), metrics


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Concurrency limit (requirement 3)
# ---------------------------------------------------------------------------

def test_never_more_than_limit_calls_at_once():
    provider = FakeProvider(latency_s=0.02)

    async def scenario():
        enricher, metrics = make_enricher(provider, concurrency=3)
        await asyncio.gather(*(enricher.enrich(f"product {i}") for i in range(30)))
        return metrics

    metrics = run(scenario())
    assert provider.calls == 30
    assert provider.max_active == 3               # reached the limit, never above it
    assert metrics.max_concurrent_llm_calls == 3  # and the metric agrees


def test_limit_is_shared_across_jobs():
    # Two "jobs" running at the same time through the same Enricher.
    provider = FakeProvider(latency_s=0.02)

    async def scenario():
        enricher, _ = make_enricher(provider, concurrency=4)
        job_a = asyncio.gather(*(enricher.enrich(f"a {i}") for i in range(20)))
        job_b = asyncio.gather(*(enricher.enrich(f"b {i}") for i in range(20)))
        await asyncio.gather(job_a, job_b)

    run(scenario())
    assert provider.max_active == 4


def test_limit_holds_with_the_real_mock_and_failures():
    provider = MockProvider(latency_ms=10, failure_rate=0.3)

    async def scenario():
        enricher, metrics = make_enricher(provider, concurrency=5)
        results = await asyncio.gather(*(enricher.enrich(f"item {i}") for i in range(60)))
        return metrics, results

    metrics, results = run(scenario())
    successes = sum(r.ok for r in results)
    assert metrics.max_concurrent_llm_calls <= 5
    # every call either succeeded (once per successful product) or counted as an error
    assert metrics.llm_calls_total == successes + metrics.llm_errors_total
    assert metrics.llm_errors_total > 0            # with a 30% failure rate, retries happened


def test_parallel_is_faster_than_serial():
    provider = FakeProvider(latency_s=0.05)

    async def scenario():
        enricher, _ = make_enricher(provider, concurrency=10)
        start = time.perf_counter()
        await asyncio.gather(*(enricher.enrich(f"p {i}") for i in range(10)))
        return time.perf_counter() - start

    assert run(scenario()) < 0.3  # serial would be 0.5 s


# ---------------------------------------------------------------------------
# Retries (requirement 4) and validation failures (requirement 5)
# ---------------------------------------------------------------------------

def test_retries_until_success():
    provider = FakeProvider(script=[LLMError("boom"), LLMError("boom")])  # then GOOD

    async def scenario():
        enricher, metrics = make_enricher(provider)
        return await enricher.enrich("Amul butter"), metrics

    result, metrics = run(scenario())
    assert result.ok
    assert provider.calls == 3
    assert metrics.llm_calls_total == 3
    assert metrics.llm_errors_total == 2


def test_gives_up_after_4_attempts():
    provider = FakeProvider(default=LLMError("still down"))

    async def scenario():
        enricher, metrics = make_enricher(provider)
        return await enricher.enrich("Amul butter"), metrics

    result, metrics = run(scenario())
    assert not result.ok
    assert result.enrichment is None
    assert "4 attempts" in result.error and "still down" in result.error
    assert provider.calls == 4
    assert metrics.llm_errors_total == 4


def test_invalid_output_is_retried():
    provider = FakeProvider(script=["not json", json.dumps({"clean_title": "X", "category": "Dairy"})])

    async def scenario():
        enricher, metrics = make_enricher(provider)
        return await enricher.enrich("Amul butter"), metrics

    result, metrics = run(scenario())
    assert result.ok
    assert provider.calls == 3
    assert metrics.llm_errors_total == 2


def test_backoff_is_exponential_from_200ms():
    delays = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    provider = FakeProvider(default=LLMError("down"))

    async def scenario():
        enricher, _ = make_enricher(provider, sleep=fake_sleep, backoff_base_s=0.2)
        await enricher.enrich("Amul butter")

    run(scenario())
    assert len(delays) == 3  # no sleep after the final attempt
    for got, expected in zip(delays, [0.2, 0.4, 0.8]):
        assert expected <= got <= expected * 1.1  # base + up to 10% jitter


def test_one_failed_product_does_not_stop_the_others():
    provider = FakeProvider(fail_titles={"p 0"})

    async def scenario():
        enricher, _ = make_enricher(provider, concurrency=1)
        return await asyncio.gather(*(enricher.enrich(f"p {i}") for i in range(5)))

    results = run(scenario())
    assert [r.ok for r in results] == [False, True, True, True, True]


def test_slot_is_released_during_backoff():
    # Concurrency 1. Product A fails and sleeps 0.2 s before retrying.
    # Product B must use the free slot during that sleep, not wait for A.
    provider = FakeProvider(script=[LLMError("x")], latency_s=0.0)

    async def scenario():
        enricher, _ = make_enricher(provider, concurrency=1, backoff_base_s=0.2)
        finished = []

        async def track(name):
            await enricher.enrich(name)
            finished.append(name)

        await asyncio.gather(track("A"), track("B"))
        return finished

    assert run(scenario()) == ["B", "A"]


# ---------------------------------------------------------------------------
# De-duplication (requirement 6)
# ---------------------------------------------------------------------------

def test_same_content_later_is_a_cache_hit():
    provider = FakeProvider()

    async def scenario():
        enricher, _ = make_enricher(provider)
        first = await enricher.enrich("  AMUL   butter", "500G")
        second = await enricher.enrich("amul butter 500g")
        return first, second

    first, second = run(scenario())
    assert provider.calls == 1
    assert not first.cache_hit
    assert second.cache_hit
    assert second.enrichment == first.enrichment


def test_duplicates_at_the_same_moment_make_one_call():
    provider = FakeProvider(latency_s=0.05)

    async def scenario():
        enricher, _ = make_enricher(provider)
        return await asyncio.gather(*(enricher.enrich("Amul Butter 500g") for _ in range(10)))

    results = run(scenario())
    assert provider.calls == 1
    assert sum(r.cache_hit for r in results) == 9
    assert all(r.ok for r in results)


def test_waiters_share_the_failure_without_extra_calls():
    provider = FakeProvider(latency_s=0.01, default=LLMError("down"))

    async def scenario():
        enricher, _ = make_enricher(provider)
        return await asyncio.gather(*(enricher.enrich("Amul Butter") for _ in range(5)))

    results = run(scenario())
    assert provider.calls == 4                # one product's 4 attempts, not 5 x 4
    assert all(not r.ok for r in results)
    assert not any(r.cache_hit for r in results)


def test_failures_are_not_cached():
    provider = FakeProvider(script=[LLMError("x")] * 4)  # first product fails 4 times, then GOOD

    async def scenario():
        enricher, _ = make_enricher(provider)
        first = await enricher.enrich("Amul Butter")
        second = await enricher.enrich("Amul Butter")  # submitted again later: tried again
        return first, second

    first, second = run(scenario())
    assert not first.ok
    assert second.ok and not second.cache_hit
    assert provider.calls == 5


def test_cancelled_waiter_does_not_cancel_the_shared_call():
    provider = FakeProvider(latency_s=0.05)

    async def scenario():
        enricher, _ = make_enricher(provider)
        owner = asyncio.create_task(enricher.enrich("Amul Butter"))
        await asyncio.sleep(0)                   # let the owner start its call
        waiter = asyncio.create_task(enricher.enrich("Amul Butter"))
        await asyncio.sleep(0.01)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        return await owner

    result = run(scenario())
    assert result.ok and not result.cache_hit
    assert provider.calls == 1
