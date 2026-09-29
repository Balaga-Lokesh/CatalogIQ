"""Background jobs (requirement 2) and crash recovery.

submit() saves the job and its items, starts a background task and returns
at once, so POST /api/jobs answers in milliseconds however big the job is.

Each job runs a small pool of workers. The workers share one iterator over
the job's pending items: each `for item in items` step hands the NEXT item
to whichever worker asks, so every item is processed exactly once. (Safe
without a lock: `next()` never awaits, so two workers can't interleave inside it.)

Why a pool, not one task per item: a 10,000-item job would otherwise put
10,000 tasks in the semaphore's queue at once, and a second job submitted
a moment later would wait behind all of them. With a pool, each job has at
most `workers_per_job` items waiting for a slot, so concurrent jobs take
turns fairly. Memory stays small too.

Crash recovery: progress is saved per item (app/db.py). On startup,
resume_unfinished() restarts every job still `queued` or `running`, and
those jobs only see their items that are still `pending`: finished work is
never redone and no item is lost. An item that was mid-call during the
crash is still `pending`, so it is simply tried again.
"""

import asyncio
import logging

from app.db import Database, JobItem
from app.pipeline import Enricher

log = logging.getLogger(__name__)


class JobManager:
    def __init__(self, db: Database, enricher: Enricher, workers_per_job: int) -> None:
        self._db = db
        self._enricher = enricher
        self._workers_per_job = max(1, workers_per_job)
        # The event loop keeps only a WEAK reference to tasks: without this
        # set, a running job could be garbage-collected mid-way.
        self._tasks: set[asyncio.Task] = set()

    def submit(self, products: list[dict]) -> dict:
        job = self._db.create_job(products)   # saved before we answer: survives a crash
        self._start(job["id"])
        return job

    def resume_unfinished(self) -> list[str]:
        job_ids = self._db.unfinished_job_ids()
        for job_id in job_ids:
            log.info("resuming job %s", job_id)
            self._start(job_id)
        return job_ids

    async def join(self) -> None:
        """Wait until every running job has finished (used by tests)."""
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def shutdown(self) -> None:
        """Stop all jobs. Their unfinished items stay `pending` in the database
        and are picked up by resume_unfinished() on the next start."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def _start(self, job_id: str) -> None:
        task = asyncio.create_task(self._run(job_id), name=f"job-{job_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job_id: str) -> None:
        self._db.mark_job_running(job_id)
        items = iter(self._db.pending_items(job_id))

        async def worker() -> None:
            for item in items:          # shared iterator: each item goes to one worker
                await self._process(item)

        await asyncio.gather(*(worker() for _ in range(self._workers_per_job)))
        self._db.mark_job_completed(job_id)
        log.info("job %s completed", job_id)

    async def _process(self, item: JobItem) -> None:
        # Enricher.enrich() already turns LLM failures into a failed result.
        # This catch is for anything unexpected (a bug, a database error): the
        # item is marked failed and the rest of the job carries on.
        # CancelledError is not an Exception subclass, so shutdown still works.
        try:
            result = await self._enricher.enrich(item.raw_title, item.raw_description)
            self._db.record_item_result(item, result.enrichment, result.error, result.cache_hit)
        except Exception as exc:
            log.exception("failed to process %s in job %s", item.sku, item.job_id)
            try:
                self._db.record_item_result(item, None, f"unexpected error: {exc!r}", False)
            except Exception:
                log.exception("could not record failure for %s", item.sku)
