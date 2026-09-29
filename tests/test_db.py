"""Tests for SQLite storage."""

import pytest

from app.db import Database
from app.schemas import Enrichment

BUTTER = Enrichment("Amul Butter 500 g", "Groceries", "Amul", ["butter", "dairy"])
PRODUCTS = [
    {"sku": "B2", "raw_title": "AMUL butter 500G", "raw_description": "pck of 2"},
    {"sku": "A1", "raw_title": "Tata tea gold 1kg"},
]


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    yield database
    database.close()


def finish_all(db, job_id, enrichment=BUTTER, error=None, cache_hit=False):
    for item in db.pending_items(job_id):
        db.record_item_result(item, enrichment, error, cache_hit)


def test_create_job_stores_job_and_items(db):
    job = db.create_job(PRODUCTS)
    assert job["id"].startswith("j_")
    assert job["status"] == "queued"
    assert (job["total"], job["done"], job["failed"], job["cache_hits"]) == (2, 0, 0, 0)
    assert job["started_at"] is None and job["finished_at"] is None
    assert [i.sku for i in db.pending_items(job["id"])] == ["B2", "A1"]  # submission order


def test_products_appear_only_once_processed(db):
    job = db.create_job(PRODUCTS)
    assert db.get_product("B2") is None
    finish_all(db, job["id"])
    product = db.get_product("B2")
    assert product["status"] == "enriched"
    assert product["clean_title"] == "Amul Butter 500 g"
    assert product["tags"] == ["butter", "dairy"]
    assert product["error"] is None


def test_item_result_updates_counters_and_pending_list(db):
    job = db.create_job(PRODUCTS)
    first, second = db.pending_items(job["id"])
    db.record_item_result(first, BUTTER, None, cache_hit=True)
    db.record_item_result(second, None, "failed after 4 attempts: down", cache_hit=False)

    job = db.get_job(job["id"])
    assert (job["done"], job["failed"], job["cache_hits"]) == (2, 1, 1)
    assert db.pending_items(job["id"]) == []
    failed = db.get_product("A1")
    assert failed["status"] == "failed"
    assert failed["error"] == "failed after 4 attempts: down"
    assert failed["clean_title"] is None


def test_resubmitting_a_sku_updates_it(db):
    finish_all(db, db.create_job(PRODUCTS)["id"])
    new = Enrichment("Amul Butter 100 g", "Groceries", "Amul", ["butter"])
    finish_all(db, db.create_job([{"sku": "B2", "raw_title": "Amul butter 100g"}])["id"], new)
    product = db.get_product("B2")
    assert product["raw_title"] == "Amul butter 100g"
    assert product["raw_description"] is None
    assert product["clean_title"] == "Amul Butter 100 g"


def test_job_lifecycle_and_unfinished_jobs(db):
    job_id = db.create_job(PRODUCTS)["id"]
    assert db.unfinished_job_ids() == [job_id]
    db.mark_job_running(job_id)
    started = db.get_job(job_id)["started_at"]
    assert db.get_job(job_id)["status"] == "running" and started
    db.mark_job_running(job_id)                      # resumed: start time kept
    assert db.get_job(job_id)["started_at"] == started
    db.mark_job_completed(job_id)
    job = db.get_job(job_id)
    assert job["status"] == "completed" and job["finished_at"]
    assert db.unfinished_job_ids() == []


def test_cache_round_trip(db):
    assert db.get_cached("k1") is None
    db.put_cached("k1", BUTTER)
    assert db.get_cached("k1") == BUTTER


def test_data_survives_reopening(tmp_path):
    path = str(tmp_path / "persist.db")
    first = Database(path)
    job_id = first.create_job(PRODUCTS)["id"]
    first.put_cached("k1", BUTTER)
    first.record_item_result(first.pending_items(job_id)[0], BUTTER, None, False)
    first.close()

    second = Database(path)  # like a server restart
    assert second.get_job(job_id)["done"] == 1
    assert [i.sku for i in second.pending_items(job_id)] == ["A1"]  # only unfinished work left
    assert second.get_cached("k1") == BUTTER
    assert second.get_product("B2")["status"] == "enriched"
    second.close()


# ---- listing, filtering, search, review ---------------------------------------

@pytest.fixture
def catalogue(db):
    rows = [
        ("C3", "Colgate paste", Enrichment("Colgate Toothpaste 200 g", "Personal Care", "Colgate", [])),
        ("A1", "Amul butter", Enrichment("Amul Butter 500 g", "Groceries", "Amul", [])),
        ("B2", "tata TEA 100% pure", Enrichment("Tata Tea Gold", "Beverages", "Tata", [])),
        ("D4", "amul cheese_slices", Enrichment("Amul Cheese Slices", "Groceries", "Amul", [])),
    ]
    job_id = db.create_job([{"sku": s, "raw_title": t} for s, t, _ in rows])["id"]
    for item, (_, _, e) in zip(db.pending_items(job_id), rows):
        db.record_item_result(item, e, None, False)
    return db


def test_list_is_sorted_by_sku_and_paginated(catalogue):
    items, total = catalogue.list_products(page=1, page_size=3)
    assert total == 4
    assert [p["sku"] for p in items] == ["A1", "B2", "C3"]
    items, _ = catalogue.list_products(page=2, page_size=3)
    assert [p["sku"] for p in items] == ["D4"]


def test_filter_by_category(catalogue):
    items, total = catalogue.list_products(category="Groceries")
    assert total == 2 and [p["sku"] for p in items] == ["A1", "D4"]


def test_search_is_case_insensitive_on_clean_or_raw_title(catalogue):
    assert {p["sku"] for p in catalogue.list_products(q="AMUL")[0]} == {"A1", "D4"}
    assert [p["sku"] for p in catalogue.list_products(q="toothpaste")[0]] == ["C3"]  # clean title
    assert [p["sku"] for p in catalogue.list_products(q="paste")[0]] == ["C3"]       # raw title


def test_search_treats_like_wildcards_literally(catalogue):
    assert [p["sku"] for p in catalogue.list_products(q="100%")[0]] == ["B2"]
    assert [p["sku"] for p in catalogue.list_products(q="e_s")[0]] == ["D4"]  # only the literal "e_s"
    # unescaped, "%" would match all 4 products; escaped it only matches the text "%"
    assert [p["sku"] for p in catalogue.list_products(q="%")[0]] == ["B2"]


def test_update_product_approves(catalogue):
    updated = catalogue.update_product("A1", {"clean_title": "Amul Salted Butter", "tags": ["butter"]})
    assert updated["status"] == "approved"
    assert updated["clean_title"] == "Amul Salted Butter"
    assert updated["tags"] == ["butter"]
    assert updated["category"] == "Groceries"  # untouched fields stay


def test_update_unknown_product_returns_none(db):
    assert db.update_product("NOPE", {"clean_title": "x"}) is None
