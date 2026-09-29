"""Tests for the mock LLM provider (requirement 1).

Async code is run with asyncio.run(...) so the tests need no pytest plugin.
"""

import asyncio
import json
import random
import time

import pytest

from app.config import Settings
from app.llm import LLMError, get_provider
from app.llm.mock import CATEGORY_KEYWORDS, MockProvider
from app.schemas import CATEGORIES, MAX_TAGS


def make_mock(latency_ms=0, failure_rate=0.0, seed=None):
    rng = random.Random(seed) if seed is not None else None
    return MockProvider(latency_ms=latency_ms, failure_rate=failure_rate, rng=rng)


def test_returns_valid_enrichment_json():
    raw = asyncio.run(make_mock().enrich("Colgate toothpaste 200g", "strong teeth"))
    data = json.loads(raw)

    assert set(data) == {"clean_title", "category", "brand", "tags"}
    assert data["category"] in CATEGORIES
    assert len(data["tags"]) <= MAX_TAGS
    assert all(t == t.lower() for t in data["tags"])


def test_brief_example():
    raw = asyncio.run(make_mock().enrich("  AMUL   butter 500G", "pck of 2"))
    data = json.loads(raw)

    assert data["clean_title"] == "Amul Butter 500 g (Pack of 2)"
    assert data["category"] == "Groceries"
    assert data["brand"] == "Amul"
    assert "butter" in data["tags"]


def test_unknown_product_gets_other_and_null_brand():
    data = json.loads(asyncio.run(make_mock().enrich("Mystery gadget thing")))
    assert data["category"] == "Other"
    assert data["brand"] is None


def test_same_input_same_output():
    mock = make_mock()
    first = asyncio.run(mock.enrich("Tata tea gold 1kg"))
    second = asyncio.run(mock.enrich("Tata tea gold 1kg"))
    assert first == second


def test_always_fails_when_failure_rate_is_1():
    with pytest.raises(LLMError):
        asyncio.run(make_mock(failure_rate=1.0).enrich("anything"))


def test_failure_rate_is_roughly_respected():
    mock = make_mock(failure_rate=0.1, seed=42)

    async def run_many():
        failures = 0
        for _ in range(2000):
            try:
                await mock.enrich("Amul butter")
            except LLMError:
                failures += 1
        return failures

    failures = asyncio.run(run_many())
    assert 150 < failures < 250  # expected about 200 (10% of 2000)


def test_waits_for_latency():
    start = time.perf_counter()
    asyncio.run(make_mock(latency_ms=100).enrich("Amul butter"))
    assert time.perf_counter() - start >= 0.09


def test_latency_does_not_block_other_calls():
    # 10 calls of 100 ms each. If the sleep blocked the event loop they would
    # take ~1 s one after another; with asyncio.sleep they overlap (~0.1 s).
    mock = make_mock(latency_ms=100)

    async def run_ten():
        await asyncio.gather(*(mock.enrich(f"item {i}") for i in range(10)))

    start = time.perf_counter()
    asyncio.run(run_ten())
    assert time.perf_counter() - start < 0.5


def test_keyword_categories_are_all_allowed():
    assert set(CATEGORY_KEYWORDS) <= set(CATEGORIES)


def test_factory_picks_mock_from_config():
    settings = Settings(llm_provider="mock", llm_concurrency=5, mock_latency_ms=200,
                        mock_failure_rate=0.1, db_path=":memory:")
    provider = get_provider(settings)
    assert isinstance(provider, MockProvider)
    assert provider.latency_s == 0.2
    assert provider.failure_rate == 0.1


def test_factory_rejects_unknown_provider():
    settings = Settings(llm_provider="nope", llm_concurrency=5, mock_latency_ms=200,
                        mock_failure_rate=0.1, db_path=":memory:")
    with pytest.raises(ValueError):
        get_provider(settings)
