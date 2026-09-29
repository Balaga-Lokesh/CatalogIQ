"""SQLite storage. Data survives a restart.

Tables
  jobs             one row per submitted job, with live counters
  job_items        the raw input of every job, one row per product, with its
                   own status (pending / done / failed). This is what makes
                   crash recovery possible: unfinished work is still on disk.
  products         the catalogue, keyed by SKU. A row is written when the
                   product has been PROCESSED, so its status is always one of
                   enriched / failed / approved (the brief's three values).
  enrichment_cache finished LLM answers keyed by content_key, so identical
                   content is never sent to the LLM again, even after a restart.

Threading: the app only touches the database from the asyncio event-loop
thread (all routes are `async def`), so one connection is shared and no
locking is needed. Each call is a fast local operation (well under a
millisecond for these queries), so blocking the loop briefly is acceptable
at this scale; DESIGN.md discusses what changes at millions of rows.
"""

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from app.schemas import Enrichment

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL,              -- queued / running / completed
    total       INTEGER NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0, -- items finished (success or failure)
    failed      INTEGER NOT NULL DEFAULT 0,
    cache_hits  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS job_items (
    job_id          TEXT NOT NULL REFERENCES jobs(id),
    position        INTEGER NOT NULL,       -- order within the job
    sku             TEXT NOT NULL,
    raw_title       TEXT NOT NULL,
    raw_description TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending / done / failed
    PRIMARY KEY (job_id, position)
);
CREATE INDEX IF NOT EXISTS idx_job_items_pending ON job_items(job_id, status);

CREATE TABLE IF NOT EXISTS products (
    sku             TEXT PRIMARY KEY,       -- also gives "sorted by SKU" for free
    raw_title       TEXT NOT NULL,
    raw_description TEXT,
    clean_title     TEXT,
    category        TEXT,
    brand           TEXT,
    tags            TEXT NOT NULL DEFAULT '[]',   -- JSON list
    status          TEXT NOT NULL,          -- enriched / failed / approved
    error           TEXT,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category, sku);

CREATE TABLE IF NOT EXISTS enrichment_cache (
    content_key TEXT PRIMARY KEY,           -- SHA-256 of the normalised content
    clean_title TEXT NOT NULL,
    category    TEXT NOT NULL,
    brand       TEXT,
    tags        TEXT NOT NULL,              -- JSON list
    created_at  TEXT NOT NULL
);
"""

MAX_PAGE_SIZE = 100


def now_iso() -> str:
    """UTC time like the brief's example: 2026-10-01T09:30:00"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


@dataclass(frozen=True)
class JobItem:
    job_id: str
    position: int
    sku: str
    raw_title: str
    raw_description: str | None


class Database:
    def __init__(self, path: str) -> None:
        # check_same_thread=False: FastAPI's TestClient drives the app from a
        # different thread than the one that opened the connection. The app
        # itself still uses the connection from one thread at a time.
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self.conn.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---- jobs ----------------------------------------------------------------

    def create_job(self, products: list[dict]) -> dict:
        """Store the job and all its items in ONE transaction: either the whole
        job is saved or none of it is."""
        job_id = "j_" + secrets.token_hex(6)
        with self.conn:
            self.conn.execute(
                "INSERT INTO jobs (id, status, total, created_at) VALUES (?, 'queued', ?, ?)",
                (job_id, len(products), now_iso()),
            )
            self.conn.executemany(
                "INSERT INTO job_items (job_id, position, sku, raw_title, raw_description) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (job_id, i, p["sku"], p["raw_title"], p.get("raw_description"))
                    for i, p in enumerate(products)
                ],
            )
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else None

    def unfinished_job_ids(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT id FROM jobs WHERE status IN ('queued', 'running') ORDER BY created_at, id"
        ).fetchall()
        return [r["id"] for r in rows]

    def pending_items(self, job_id: str) -> list[JobItem]:
        rows = self.conn.execute(
            "SELECT job_id, position, sku, raw_title, raw_description FROM job_items "
            "WHERE job_id = ? AND status = 'pending' ORDER BY position",
            (job_id,),
        ).fetchall()
        return [JobItem(**dict(r)) for r in rows]

    def mark_job_running(self, job_id: str) -> None:
        with self.conn:
            # COALESCE keeps the original start time when a job is resumed.
            self.conn.execute(
                "UPDATE jobs SET status = 'running', started_at = COALESCE(started_at, ?) WHERE id = ?",
                (now_iso(), job_id),
            )

    def mark_job_completed(self, job_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE jobs SET status = 'completed', finished_at = ? WHERE id = ?",
                (now_iso(), job_id),
            )

    def record_item_result(
        self, item: JobItem, enrichment: Enrichment | None, error: str | None, cache_hit: bool
    ) -> None:
        """Everything that changes when one item finishes, in ONE transaction:
        the product row, the item's status and the job's counters. After a
        crash they can never disagree (e.g. counted as done but not saved)."""
        failed = enrichment is None
        with self.conn:
            self._upsert_product(item, enrichment, error)
            self.conn.execute(
                "UPDATE job_items SET status = ? WHERE job_id = ? AND position = ?",
                ("failed" if failed else "done", item.job_id, item.position),
            )
            self.conn.execute(
                "UPDATE jobs SET done = done + 1, failed = failed + ?, cache_hits = cache_hits + ? "
                "WHERE id = ?",
                (int(failed), int(cache_hit and not failed), item.job_id),
            )

    def _upsert_product(self, item: JobItem, e: Enrichment | None, error: str | None) -> None:
        # "Submitting a SKU that already exists updates it": raw fields and the
        # new enrichment replace the old ones. On failure the old clean fields
        # are cleared, since they described the OLD raw content.
        self.conn.execute(
            """
            INSERT INTO products (sku, raw_title, raw_description, clean_title, category, brand,
                                  tags, status, error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sku) DO UPDATE SET
                raw_title = excluded.raw_title, raw_description = excluded.raw_description,
                clean_title = excluded.clean_title, category = excluded.category,
                brand = excluded.brand, tags = excluded.tags, status = excluded.status,
                error = excluded.error, updated_at = excluded.updated_at
            """,
            (
                item.sku, item.raw_title, item.raw_description,
                e.clean_title if e else None, e.category if e else None, e.brand if e else None,
                json.dumps(e.tags if e else []),
                "failed" if e is None else "enriched",
                error, now_iso(),
            ),
        )

    # ---- enrichment cache (the EnrichmentCache protocol from app/pipeline.py) --

    def get_cached(self, key: str) -> Enrichment | None:
        row = self.conn.execute(
            "SELECT clean_title, category, brand, tags FROM enrichment_cache WHERE content_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return Enrichment(row["clean_title"], row["category"], row["brand"], json.loads(row["tags"]))

    def put_cached(self, key: str, e: Enrichment) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO enrichment_cache "
                "(content_key, clean_title, category, brand, tags, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (key, e.clean_title, e.category, e.brand, json.dumps(e.tags), now_iso()),
            )

    # ---- products ------------------------------------------------------------

    def list_products(
        self, page: int = 1, page_size: int = 20, category: str | None = None, q: str | None = None
    ) -> tuple[list[dict], int]:
        where, params = [], []
        if category:
            where.append("category = ?")
            params.append(category)
        if q:
            # LIKE is case-insensitive for ASCII in SQLite. Escape the LIKE
            # wildcards so a search for "100%" means the text "100%".
            pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(clean_title LIKE ? ESCAPE '\\' OR raw_title LIKE ? ESCAPE '\\')")
            params += [pattern, pattern]
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        total = self.conn.execute(f"SELECT COUNT(*) FROM products {where_sql}", params).fetchone()[0]
        rows = self.conn.execute(
            f"SELECT * FROM products {where_sql} ORDER BY sku LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size],
        ).fetchall()
        return [_product(r) for r in rows], total

    def get_product(self, sku: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM products WHERE sku = ?", (sku,)).fetchone()
        return _product(row) if row else None

    def update_product(self, sku: str, changes: dict) -> dict | None:
        """A human review: apply the edits (already validated) and approve."""
        if self.get_product(sku) is None:
            return None
        fields = {k: v for k, v in changes.items() if k in ("clean_title", "category", "tags")}
        if "tags" in fields:
            fields["tags"] = json.dumps(fields["tags"])
        fields.update(status="approved", error=None, updated_at=now_iso())
        assignments = ", ".join(f"{k} = ?" for k in fields)  # keys come from the fixed list above
        with self.conn:
            self.conn.execute(f"UPDATE products SET {assignments} WHERE sku = ?", [*fields.values(), sku])
        return self.get_product(sku)


def _product(row: sqlite3.Row) -> dict:
    """A database row -> the product object from the brief's API contract."""
    return {
        "sku": row["sku"],
        "raw_title": row["raw_title"],
        "raw_description": row["raw_description"],
        "clean_title": row["clean_title"],
        "category": row["category"],
        "brand": row["brand"],
        "tags": json.loads(row["tags"]),
        "status": row["status"],
        "error": row["error"],
    }
