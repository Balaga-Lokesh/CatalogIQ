"""Tests for background jobs and crash recovery."""

import asyncio

from app.db import Database
from app.jobs import JobManager
from app.llm.base import LLMProvider
from app.metrics import Metrics
from app.pipeline import Enricher
from tests.test_pipeline import GOOD, FakeProvider


def make_manager(db, provider, concurrency=3):
    metrics = Metrics()
    enricher = Enricher(provider, concurrency, metrics, db, backoff_base_s=0.0)
    return JobManager(db, enricher, workers_per_job=concurrency), metrics


def products(n, prefix="item"):
    return [{"sku": f"{prefix}-{i:03d}", "raw_title": f"{prefix} product {i}"} for i in range(n)]


def test_submit_returns_before_processing(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    provider = FakeProvider(latency_s=0.05)

    async def scenario():
        manager, _ = make_manager(db, provider)
        job = manager.submit(products(10))
        assert job["status"] == "queued"      # answered before any work happened
        assert provider.calls == 0
        await manager.join()
        return db.get_job(job["id"])

    job = asyncio.run(scenario())
    assert job["status"] == "completed"
    assert (job["total"], job["done"], job["failed"]) == (10, 10, 0)
    assert job["started_at"] and job["finished_at"]
    assert db.get_product("item-000")["status"] == "enriched"


def test_progress_is_visible_while_running(tmp_path):
    db = Database(str(tmp_path / "t.db"))

    async def scenario():
        manager, _ = make_manager(db, FakeProvider(latency_s=0.02), concurrency=2)
        job = manager.submit(products(20))
        await asyncio.sleep(0.07)
        mid = db.get_job(job["id"])
        await manager.join()
        return mid

    mid = asyncio.run(scenario())
    assert mid["status"] == "running"
    assert 0 < mid["done"] < 20


def test_duplicates_in_a_job_count_as_cache_hits(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    provider = FakeProvider(latency_s=0.01)
    batch = [{"sku": f"S{i}", "raw_title": "Amul  Butter 500g" if i % 2 else "amul butter 500G"} for i in range(6)]

    async def scenario():
        manager, _ = make_manager(db, provider)
        job = manager.submit(batch)
        await manager.join()
        return db.get_job(job["id"])

    job = asyncio.run(scenario())
    assert provider.calls == 1
    assert job["cache_hits"] == 5 and job["done"] == 6


def test_two_jobs_share_the_concurrency_limit(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    provider = FakeProvider(latency_s=0.02)

    async def scenario():
        manager, metrics = make_manager(db, provider, concurrency=3)
        manager.submit(products(15, "a"))
        manager.submit(products(15, "b"))
        await manager.join()
        return metrics

    metrics = asyncio.run(scenario())
    assert provider.max_active == 3
    assert metrics.max_concurrent_llm_calls == 3


def test_two_jobs_progress_side_by_side(tmp_path):
    # Fairness: job B, submitted just after a big job A, must not wait for A to finish.
    db = Database(str(tmp_path / "t.db"))

    async def scenario():
        manager, _ = make_manager(db, FakeProvider(latency_s=0.01), concurrency=2)
        job_a = manager.submit(products(100, "a"))
        job_b = manager.submit(products(4, "b"))
        while db.get_job(job_b["id"])["status"] != "completed":
            await asyncio.sleep(0.01)
        a_done_when_b_finished = db.get_job(job_a["id"])["done"]
        await manager.join()
        return a_done_when_b_finished

    assert asyncio.run(scenario()) < 50


def test_unexpected_error_fails_one_item_and_the_job_carries_on(tmp_path):
    class BuggyProvider(LLMProvider):
        name = "buggy"

        async def enrich(self, raw_title, raw_description=""):
            if raw_title.endswith(" 3"):
                raise KeyError("oops")    # a bug, not an LLMError
            return GOOD

    db = Database(str(tmp_path / "t.db"))

    async def scenario():
        manager, _ = make_manager(db, BuggyProvider())
        job = manager.submit(products(6))
        await manager.join()
        return db.get_job(job["id"])

    job = asyncio.run(scenario())
    assert job["status"] == "completed"
    assert (job["done"], job["failed"]) == (6, 1)
    assert "unexpected error" in db.get_product("item-003")["error"]


def test_restart_resumes_without_redoing_finished_work(tmp_path):
    path = str(tmp_path / "t.db")
    first_provider = FakeProvider(latency_s=0.02)

    async def before_crash():
        db = Database(path)
        manager, _ = make_manager(db, first_provider, concurrency=2)
        job = manager.submit(products(30))
        await asyncio.sleep(0.12)          # part-way through
        await manager.shutdown()           # the "crash"
        db.close()
        return job["id"]

    job_id = asyncio.run(before_crash())

    db = Database(path)                    # the server starts again
    job = db.get_job(job_id)
    finished_before = {p["sku"] for p in db.list_products(page_size=100)[0]}
    assert job["status"] == "running"
    assert 0 < job["done"] < 30
    assert len(finished_before) == job["done"]

    second_provider = FakeProvider(latency_s=0.0)
    called_after = []
    original = second_provider.enrich

    async def recording_enrich(raw_title, raw_description=""):
        called_after.append(raw_title)
        return await original(raw_title, raw_description)

    second_provider.enrich = recording_enrich

    async def after_restart():
        manager, _ = make_manager(db, second_provider, concurrency=2)
        assert manager.resume_unfinished() == [job_id]
        await manager.join()

    asyncio.run(after_restart())
    job = db.get_job(job_id)
    assert job["status"] == "completed"
    assert job["done"] == 30                                     # nothing lost
    assert len(called_after) == 30 - len(finished_before)        # nothing redone
    finished_titles = {f"item product {int(s.split('-')[1])}" for s in finished_before}
    assert not finished_titles & set(called_after)
