"""Pace LLM calls to a requests-per-minute budget.

The semaphore (app/pipeline.py) limits how many calls run AT ONCE; a free
LLM tier also limits how many START PER MINUTE (Groq's free tier: ~8,000
tokens/min, ~520 tokens per call, so ~15 calls/min). Without pacing, calls
fire as fast as slots free up and the provider answers 429 "too many
requests". This spaces call starts evenly instead: at 14/min, one call every
~4.3 s, so the limit is never hit.

How: each caller RESERVES the next free start time, then sleeps until it.
Reading and moving `_next_start` happens with no `await` in between, so two
callers can never reserve the same time - no lock needed (same reasoning as
the in-flight check in the pipeline).
"""

import asyncio
import time
from typing import Awaitable, Callable


class RateLimiter:
    def __init__(
        self,
        per_minute: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute must be positive")
        self.interval_s = 60.0 / per_minute
        self._clock = clock
        self._sleep = sleep          # injectable so tests don't really wait
        self._next_start = 0.0       # earliest time the next call may start

    async def acquire(self) -> None:
        now = self._clock()
        start = max(now, self._next_start)
        self._next_start = start + self.interval_s   # reserve our slot before awaiting
        if start > now:
            await self._sleep(start - now)
