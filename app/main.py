"""FastAPI app: the API contract from the brief + serves the frontend at /.

Run with:  python -m app        (see app/__main__.py)

Every route is `async def`, so it runs on the event-loop thread. That keeps
all database access on one thread (see app/db.py) and means a route never
waits for a thread-pool slot while a big job is running.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from app.config import Settings, load_settings
from app.db import MAX_PAGE_SIZE, Database
from app.jobs import JobManager
from app.llm import get_provider
from app.metrics import Metrics
from app.pipeline import Enricher
from app.schemas import CATEGORIES, MAX_TAGS

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

router = APIRouter()


# ---------------------------------------------------------------------------
# App factory, startup and shutdown
# ---------------------------------------------------------------------------

def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: build the pieces ONCE. The single Enricher (and so the single
        # semaphore) is shared by every job - that is the global LLM limit.
        db = Database(settings.db_path)
        metrics = Metrics()
        provider = get_provider(settings)
        enricher = Enricher(provider, settings.llm_concurrency, metrics, db)
        jobs = JobManager(db, enricher, workers_per_job=settings.llm_concurrency)
        app.state.settings, app.state.db, app.state.metrics, app.state.jobs = settings, db, metrics, jobs

        resumed = jobs.resume_unfinished()      # crash recovery
        if resumed:
            log.info("resumed %d unfinished job(s)", len(resumed))
        yield
        # Shutdown: stop jobs (their pending items stay in the DB), then clean up.
        await jobs.shutdown()
        await provider.close()
        db.close()

    app = FastAPI(title="CatalogIQ", lifespan=lifespan)
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    _add_error_handlers(app)
    return app


def _add_error_handlers(app: FastAPI) -> None:
    """Every error body is {"error": "message"} - including FastAPI's own
    404 for unknown routes and 405 for wrong methods."""

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": "invalid request"}, status_code=400)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"error": "internal server error"}, status_code=500)


def bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail=message)


def not_found(message: str) -> HTTPException:
    return HTTPException(status_code=404, detail=message)


async def read_json(request: Request):
    try:
        return await request.json()
    except Exception:
        raise bad_request("request body must be valid JSON")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/health")
async def health(request: Request):
    settings = request.app.state.settings
    return {"status": "ok", "llm_provider": settings.llm_provider, "llm_concurrency": settings.llm_concurrency}


@router.get("/api/metrics")
async def metrics(request: Request):
    return request.app.state.metrics.snapshot()


@router.post("/api/jobs", status_code=202)
async def create_job(request: Request):
    products = parse_products(await read_json(request))
    return request.app.state.jobs.submit(products)   # saved + started; work happens later


@router.get("/api/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    job = request.app.state.db.get_job(job_id)
    if job is None:
        raise not_found(f"job {job_id} not found")
    return job


@router.get("/api/products")
async def list_products(request: Request):
    params = request.query_params
    page = parse_positive_int(params.get("page"), "page", default=1)
    page_size = min(parse_positive_int(params.get("page_size"), "page_size", default=20), MAX_PAGE_SIZE)
    category = params.get("category") or None
    q = (params.get("q") or "").strip() or None
    items, total = request.app.state.db.list_products(page, page_size, category, q)
    return {"items": items, "page": page, "page_size": page_size, "total": total}


@router.get("/api/products/{sku}")
async def get_product(sku: str, request: Request):
    product = request.app.state.db.get_product(sku)
    if product is None:
        raise not_found(f"product {sku} not found")
    return product


@router.patch("/api/products/{sku}")
async def review_product(sku: str, request: Request):
    db = request.app.state.db
    if db.get_product(sku) is None:
        raise not_found(f"product {sku} not found")
    changes = parse_review(await read_json(request))
    return db.update_product(sku, changes)


@router.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


# ---------------------------------------------------------------------------
# Input checks (hand-written so every message is a clear {"error": ...})
# ---------------------------------------------------------------------------

def parse_products(body) -> list[dict]:
    if not isinstance(body, dict) or not isinstance(body.get("products"), list):
        raise bad_request('body must be {"products": [...]}')
    products = body["products"]
    if not products:
        raise bad_request("products must not be empty")

    cleaned = []
    for i, p in enumerate(products):
        if not isinstance(p, dict):
            raise bad_request(f"product {i} must be an object")
        sku, title, description = p.get("sku"), p.get("raw_title"), p.get("raw_description")
        if isinstance(sku, int) and not isinstance(sku, bool):
            sku = str(sku)
        if not isinstance(sku, str) or not sku.strip():
            raise bad_request(f"product {i} has no sku")
        if not isinstance(title, str) or not title.strip():
            raise bad_request(f"product {i} ({sku}) has no raw_title")
        if description is not None and not isinstance(description, str):
            raise bad_request(f"product {i} ({sku}): raw_description must be a string")
        cleaned.append({"sku": sku.strip(), "raw_title": title, "raw_description": description or None})
    return cleaned


def parse_positive_int(value: str | None, name: str, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except ValueError:
        raise bad_request(f"{name} must be a number")
    if number < 1:
        raise bad_request(f"{name} must be at least 1")
    return number


def parse_review(body) -> dict:
    if not isinstance(body, dict):
        raise bad_request("body must be a JSON object")
    changes = {}
    if "clean_title" in body:
        title = body["clean_title"]
        if not isinstance(title, str) or not title.strip():
            raise bad_request("clean_title must not be empty")
        changes["clean_title"] = " ".join(title.split())
    if "category" in body:
        if body["category"] not in CATEGORIES:
            raise bad_request(f"category must be one of: {', '.join(CATEGORIES)}")
        changes["category"] = body["category"]
    if "tags" in body:
        tags = body["tags"]
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise bad_request("tags must be a list of strings")
        cleaned = []
        for tag in tags:
            tag = " ".join(tag.lower().split())
            if tag and tag not in cleaned:
                cleaned.append(tag)
        if len(cleaned) > MAX_TAGS:
            raise bad_request(f"at most {MAX_TAGS} tags allowed")
        changes["tags"] = cleaned
    return changes


app = create_app()
