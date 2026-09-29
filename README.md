# CatalogIQ

A service that takes messy product listings and uses an LLM to turn them into a
clean, searchable catalogue.

> Work in progress. Sections marked TODO will be filled in as the project grows.

## Project layout

```
app/
  main.py        FastAPI app: API routes + serves the frontend
  config.py      settings from environment variables
  schemas.py     categories and data shapes
  db.py          SQLite storage
  normalize.py   content key for de-duplication
  validation.py  checks LLM output
  metrics.py     LLM call counters
  pipeline.py    enrich one product: concurrency limit, retries, de-dup
  jobs.py        background jobs
  llm/           LLM providers (mock + real) behind one interface
static/          frontend (plain HTML, CSS, JS)
tests/           pytest tests
data/            sample CSV of listings
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```

## Run (mock mode)

```bash
uvicorn app.main:app --port 8000
```

Open http://localhost:8000.

## Run with the real LLM

TODO

## Tests

```bash
pytest
```

## Prompt

TODO

## Assumptions and unfinished work

TODO

## How AI tools were used

TODO
