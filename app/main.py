"""FastAPI app: API routes + serves the frontend at /.

Run with:  uvicorn app.main:app --port 8000
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import load_settings

settings = load_settings()
app = FastAPI(title="CatalogIQ")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "llm_concurrency": settings.llm_concurrency,
    }


# TODO: GET  /api/metrics
# TODO: POST /api/jobs
# TODO: GET  /api/jobs/{job_id}
# TODO: GET  /api/products
# TODO: GET  /api/products/{sku}
# TODO: PATCH /api/products/{sku}
# TODO: every error must be {"error": "message"}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
