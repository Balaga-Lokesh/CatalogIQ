"""The core: enrich one product (requirements 3, 4, 6).

    enrich(product)
      1. cache hit?            -> return stored result, no LLM call
      2. same content in flight? -> wait for that call's result, no LLM call
      3. otherwise             -> call the LLM with retries, store the result

ONE Enricher is shared by every job, so its semaphore is the global limit on
LLM calls in progress.

Why no lock around steps 1-3: asyncio runs one task at a time and only
switches tasks at an `await`. Checking the cache, checking _in_flight and
registering our own future happen with no `await` in between, so no other
task can slip in and start a second call for the same content.
"""

import asyncio
import logging
import random
from dataclasses import dataclass, replace
from typing import Awaitable, Callable, Protocol

from app.llm.base import LLMError, LLMProvider
from app.metrics import Metrics
from app.normalize import content_key
from app.ratelimit import RateLimiter
from app.schemas import Enrichment
from app.validation import validate_enrichment

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 4          # 1 call + 3 retries
BACKOFF_BASE_S = 0.2      # 200 ms, then 400 ms, then 800 ms
BACKOFF_JITTER = 0.1      # up to +10%, so failures don't retry in lock-step


class EnrichmentCache(Protocol):
    """Where finished enrichments are stored, keyed by content_key.
    The app uses SQLite (app/db.py); tests use a plain dict."""

    def get_cached(self, key: str) -> Enrichment | None: ...
    def put_cached(self, key: str, enrichment: Enrichment) -> None: ...


class MemoryCache:
    def __init__(self) -> None:
        self._data: dict[str, Enrichment] = {}

    def get_cached(self, key: str) -> Enrichment | None:
        return self._data.get(key)

    def put_cached(self, key: str, enrichment: Enrichment) -> None:
        self._data[key] = enrichment


@dataclass(frozen=True)
class EnrichResult:
    enrichment: Enrichment | None
    error: str | None = None
    cache_hit: bool = False

    @property
    def ok(self) -> bool:
        return self.enrichment is not None


class Enricher:
    def __init__(
        self,
        provider: LLMProvider,
        concurrency: int,
        metrics: Metrics,
        cache: EnrichmentCache,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        backoff_base_s: float = BACKOFF_BASE_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._provider = provider
        self._rate_limiter = rate_limiter   # optional: paces call starts per minute
        self._semaphore = asyncio.Semaphore(concurrency)
        self._metrics = metrics
        self._cache = cache
        self._max_attempts = max_attempts
        self._backoff_base_s = backoff_base_s
        self._sleep = sleep            # injectable so tests can record the delays
        self._rng = rng or random.Random()
        # content_key -> Future that resolves to the EnrichResult of the call in progress
        self._in_flight: dict[str, asyncio.Future[EnrichResult]] = {}

    async def enrich(self, raw_title: str, raw_description: str | None = None) -> EnrichResult:
        key = content_key(raw_title, raw_description)

        # 1. Already enriched before?
        cached = self._cache.get_cached(key)
        if cached is not None:
            return EnrichResult(cached, cache_hit=True)

        # 2. Same content being enriched right now? Wait for it.
        #    shield(): if THIS waiter is cancelled, don't cancel the shared future.
        pending = self._in_flight.get(key)
        if pending is not None:
            result = await asyncio.shield(pending)
            return replace(result, cache_hit=True) if result.ok else result

        # 3. We are the first: register, so later duplicates wait for us.
        future: asyncio.Future[EnrichResult] = asyncio.get_running_loop().create_future()
        self._in_flight[key] = future
        try:
            result = await self._enrich_with_retries(raw_title, raw_description or "")
            if result.ok:
                self._cache.put_cached(key, result.enrichment)
            future.set_result(result)
            return result
        finally:
            # Cache write, set_result and this removal run with no await between
            # them, so a newcomer sees either "in flight" or "cached", never neither.
            del self._in_flight[key]
            if not future.done():
                # We were cancelled (e.g. shutdown): waiters are cancelled too,
                # so their items stay pending and are resumed after a restart.
                future.cancel()

    async def _enrich_with_retries(self, raw_title: str, raw_description: str) -> EnrichResult:
        last_error = "no attempts made"
        for attempt in range(1, self._max_attempts + 1):
            try:
                raw = await self._call_llm(raw_title, raw_description)
                return EnrichResult(validate_enrichment(raw))
            except LLMError as exc:          # call failed OR output invalid
                self._metrics.error()
                last_error = str(exc)
            except Exception as exc:         # a bug, not an LLM failure: don't retry
                self._metrics.error()
                log.exception("unexpected error enriching %r", raw_title)
                return EnrichResult(None, error=f"unexpected error: {exc!r}")
            if attempt < self._max_attempts:
                await self._sleep(self.backoff_delay(attempt))
        return EnrichResult(None, error=f"failed after {self._max_attempts} attempts: {last_error}")

    async def _call_llm(self, raw_title: str, raw_description: str) -> str:
        # Rate limit first (waiting for our turn is not "in progress"), then
        # the semaphore. The semaphore wraps ONE attempt, not the retry loop:
        # a task sleeping in backoff gives its slot to someone else.
        if self._rate_limiter is not None:
            await self._rate_limiter.acquire()
        async with self._semaphore:
            self._metrics.call_started()
            try:
                return await self._provider.enrich(raw_title, raw_description)
            finally:
                self._metrics.call_finished()

    def backoff_delay(self, attempt: int) -> float:
        """attempt 1 -> ~0.2 s, 2 -> ~0.4 s, 3 -> ~0.8 s (exponential + small jitter)."""
        base = self._backoff_base_s * 2 ** (attempt - 1)
        return base * (1 + self._rng.uniform(0, BACKOFF_JITTER))
