"""API contract tests (Part 2 of the brief), run against the mock provider."""

import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_settings(tmp_path, latency_ms=0, failure_rate=0.0, concurrency=5):
    return Settings(llm_provider="mock", llm_concurrency=concurrency, mock_latency_ms=latency_ms,
                    mock_failure_rate=failure_rate, db_path=str(tmp_path / "api.db"))


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:   # `with` runs startup/shutdown
        yield c


def wait_for_job(client, job_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "completed":
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not complete: {job}")


def submit(client, products):
    response = client.post("/api/jobs", json={"products": products})
    assert response.status_code == 202, response.text
    return response.json()


SAMPLE = [
    {"sku": "B2", "raw_title": "  AMUL   butter 500G", "raw_description": "pck of 2"},
    {"sku": "A1", "raw_title": "Tata tea gold 1kg"},
    {"sku": "C3", "raw_title": "amul butter 500g", "raw_description": "PCK OF 2"},   # duplicate of B2
    {"sku": "D4", "raw_title": "Colgate toothpaste 200g", "raw_description": None},
]


# ---- health and metrics -------------------------------------------------------

def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "llm_provider": "mock", "llm_concurrency": 5}


def test_metrics_are_integers_and_count_calls(client):
    wait_for_job(client, submit(client, SAMPLE)["id"])
    metrics = client.get("/api/metrics").json()
    assert set(metrics) == {"llm_calls_total", "llm_errors_total", "max_concurrent_llm_calls"}
    assert all(isinstance(v, int) for v in metrics.values())
    assert metrics["llm_calls_total"] == 3          # 4 products, 1 duplicate
    assert 1 <= metrics["max_concurrent_llm_calls"] <= 5


# ---- jobs ------------------------------------------------------------------------

def test_job_lifecycle(client):
    job = submit(client, SAMPLE)
    assert job["id"].startswith("j_")
    assert job["status"] == "queued"
    assert set(job) == {"id", "status", "total", "done", "failed", "cache_hits",
                        "created_at", "started_at", "finished_at"}

    done = wait_for_job(client, job["id"])
    assert (done["total"], done["done"], done["failed"], done["cache_hits"]) == (4, 4, 0, 1)
    assert done["started_at"] and done["finished_at"]


def test_post_job_returns_202_within_1_second(tmp_path):
    settings = make_settings(tmp_path, latency_ms=200)
    products = [{"sku": f"S{i:05d}", "raw_title": f"product {i}"} for i in range(2000)]
    with TestClient(create_app(settings)) as client:
        start = time.perf_counter()
        response = client.post("/api/jobs", json={"products": products})
        elapsed = time.perf_counter() - start
        assert response.status_code == 202
        assert elapsed < 1.0


def test_api_stays_fast_while_a_job_runs(tmp_path):
    settings = make_settings(tmp_path, latency_ms=50, concurrency=5)
    with TestClient(create_app(settings)) as client:
        job = submit(client, [{"sku": f"S{i}", "raw_title": f"product {i}"} for i in range(200)])
        for _ in range(5):
            start = time.perf_counter()
            assert client.get("/api/health").status_code == 200
            assert client.get(f"/api/jobs/{job['id']}").status_code == 200
            assert time.perf_counter() - start < 0.2
        assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "running"


def test_job_with_failures_still_completes(tmp_path):
    settings = make_settings(tmp_path, failure_rate=1.0)    # every call fails
    with TestClient(create_app(settings)) as client:
        job = wait_for_job(client, submit(client, SAMPLE[:2])["id"])
        assert (job["done"], job["failed"]) == (2, 2)
        product = client.get("/api/products/A1").json()
        assert product["status"] == "failed"
        assert "4 attempts" in product["error"]
        assert client.get("/api/metrics").json()["llm_errors_total"] == 8


@pytest.mark.parametrize("body", [
    {"products": []},
    {},
    {"products": "not a list"},
    {"products": [{"raw_title": "no sku"}]},
    {"products": [{"sku": "", "raw_title": "empty sku"}]},
    {"products": [{"sku": "A1"}]},
    {"products": [{"sku": "A1", "raw_title": "   "}]},
    {"products": [{"sku": "A1", "raw_title": "ok"}, {"sku": "A2"}]},   # one bad product rejects the job
    {"products": [{"sku": "A1", "raw_title": "ok", "raw_description": 5}]},
    {"products": ["not an object"]},
])
def test_invalid_job_is_400(client, body):
    response = client.post("/api/jobs", json=body)
    assert response.status_code == 400
    assert list(response.json()) == ["error"]


def test_invalid_json_body_is_400(client):
    response = client.post("/api/jobs", content=b"{not json", headers={"content-type": "application/json"})
    assert response.status_code == 400
    assert "error" in response.json()


def test_unknown_job_is_404(client):
    response = client.get("/api/jobs/j_nope")
    assert response.status_code == 404
    assert response.json() == {"error": "job j_nope not found"}


def test_resubmitting_a_sku_updates_it(client):
    wait_for_job(client, submit(client, [{"sku": "A1", "raw_title": "Tata tea gold 1kg"}])["id"])
    wait_for_job(client, submit(client, [{"sku": "A1", "raw_title": "Tata tea premium 500g"}])["id"])
    product = client.get("/api/products/A1").json()
    assert product["raw_title"] == "Tata tea premium 500g"
    assert client.get("/api/products").json()["total"] == 1


# ---- products ------------------------------------------------------------------------

@pytest.fixture
def catalogue(client):
    wait_for_job(client, submit(client, SAMPLE)["id"])
    return client


def test_product_object_shape(catalogue):
    product = catalogue.get("/api/products/B2").json()
    assert product == {
        "sku": "B2", "raw_title": "  AMUL   butter 500G", "raw_description": "pck of 2",
        "clean_title": "Amul Butter 500 g (Pack of 2)", "category": "Groceries", "brand": "Amul",
        "tags": ["butter"], "status": "enriched", "error": None,
    }


def test_unknown_product_is_404(client):
    response = client.get("/api/products/NOPE")
    assert response.status_code == 404
    assert "error" in response.json()


def test_list_products_sorted_and_paginated(catalogue):
    body = catalogue.get("/api/products?page=1&page_size=3").json()
    assert set(body) == {"items", "page", "page_size", "total"}
    assert [p["sku"] for p in body["items"]] == ["A1", "B2", "C3"]
    assert (body["page"], body["page_size"], body["total"]) == (1, 3, 4)
    assert [p["sku"] for p in catalogue.get("/api/products?page=2&page_size=3").json()["items"]] == ["D4"]


def test_list_defaults_and_page_size_cap(catalogue):
    assert catalogue.get("/api/products").json()["page_size"] == 20
    assert catalogue.get("/api/products?page_size=500").json()["page_size"] == 100


@pytest.mark.parametrize("query", ["page=abc", "page_size=ten", "page=1.5", "page=0", "page_size=-1"])
def test_bad_paging_is_400(catalogue, query):
    response = catalogue.get(f"/api/products?{query}")
    assert response.status_code == 400
    assert "error" in response.json()


def test_filter_and_search(catalogue):
    groceries = catalogue.get("/api/products", params={"category": "Groceries"}).json()
    assert {p["sku"] for p in groceries["items"]} == {"B2", "C3"}
    assert catalogue.get("/api/products", params={"q": "TOOTHPASTE"}).json()["total"] == 1
    assert catalogue.get("/api/products", params={"q": "amul", "category": "Groceries"}).json()["total"] == 2
    assert catalogue.get("/api/products", params={"category": "Toys"}).json()["total"] == 0


def test_patch_edits_and_approves(catalogue):
    response = catalogue.patch("/api/products/A1", json={
        "clean_title": " Tata Tea Gold 1 kg ", "category": "Beverages", "tags": ["Tea", "tea", "black tea"],
    })
    assert response.status_code == 200
    product = response.json()
    assert product["status"] == "approved"
    assert product["clean_title"] == "Tata Tea Gold 1 kg"
    assert product["tags"] == ["tea", "black tea"]
    assert catalogue.get("/api/products/A1").json()["status"] == "approved"


def test_patch_with_no_changes_just_approves(catalogue):
    product = catalogue.patch("/api/products/D4", json={}).json()
    assert product["status"] == "approved"
    assert product["category"] == "Personal Care"


@pytest.mark.parametrize("body", [
    {"category": "Toys"},
    {"category": "groceries"},       # the API expects the exact category name
    {"clean_title": ""},
    {"clean_title": "   "},
    {"tags": "tea"},
    {"tags": ["a", "b", "c", "d", "e", "f"]},
])
def test_patch_invalid_is_400(catalogue, body):
    response = catalogue.patch("/api/products/A1", json=body)
    assert response.status_code == 400
    assert "error" in response.json()
    assert catalogue.get("/api/products/A1").json()["status"] == "enriched"   # unchanged


def test_patch_unknown_sku_is_404(client):
    response = client.patch("/api/products/NOPE", json={"clean_title": "x"})
    assert response.status_code == 404
    assert "error" in response.json()


# ---- everything else -----------------------------------------------------------------

def test_unknown_route_uses_error_format(client):
    response = client.get("/api/nothing-here")
    assert response.status_code == 404
    assert list(response.json()) == ["error"]


def test_frontend_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_jobs_resume_after_restart(tmp_path):
    settings = make_settings(tmp_path, latency_ms=30, concurrency=2)
    products = [{"sku": f"S{i:03d}", "raw_title": f"product {i}"} for i in range(40)]
    with TestClient(create_app(settings)) as client:
        job_id = submit(client, products)["id"]
        time.sleep(0.2)
    # leaving the `with` block shut the server down mid-job; start a new one on the same DB
    with TestClient(create_app(settings)) as client:
        job = wait_for_job(client, job_id)
        assert (job["done"], job["total"]) == (40, 40)
        assert client.get("/api/products").json()["total"] == 40
