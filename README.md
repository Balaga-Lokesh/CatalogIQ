# CatalogIQ

CatalogIQ takes messy marketplace listings (`AMUL   butter 500G`, `pck of 2`) and uses an LLM to turn them into a clean, searchable catalogue: a tidy title, one of 8 categories, a brand and up to 5 tags.

The work is in the pipeline around the LLM:
- **Jobs run in the background.** Submitting one returns `202` immediately.
- **LLM calls run in parallel, but never more than `LLM_CONCURRENCY` at once**, across all jobs.
- **Failed calls are retried** with exponential backoff.
- **Identical content is never sent to the LLM twice**, even when duplicates arrive at the same moment.
- **Interrupted jobs resume after a restart** without redoing finished work.

- **Backend:** Python 3.10+, FastAPI, asyncio, SQLite
- **Frontend:** one page of plain HTML, CSS and JavaScript at `/`
- **LLM:** a built-in mock (the default), or a real provider through any OpenAI-compatible API. I use **Groq** (`openai/gpt-oss-120b`, free tier); Ollama, OpenRouter and Gemini also work.
- **Design write-up:** [DESIGN.md](DESIGN.md)

## Setup (clean machine)

You need Python 3.10 or newer and Git.

```bash
git clone <this repository> catalogiq
cd catalogiq
python -m venv .venv
```

Activate the virtual environment:

```bash
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

## Run in mock mode (the default)

```bash
python -m app
```

This serves http://localhost:8000. `python -m app` runs uvicorn listening on both `127.0.0.1` and `::1`. With plain `uvicorn app.main:app --port 8000` (IPv4 only), Windows spends about 2 s on each new connection to `localhost` trying IPv6 first. If you use plain uvicorn, open http://127.0.0.1:8000 instead.

Open http://localhost:8000 and upload [`data/sample_listings.csv`](data/sample_listings.csv) (240 listings, 40 of them duplicates). With the mock defaults (200 ms latency, 10% failures, 5 at a time), a run takes about 13 seconds and shows at least 40 cache hits.

To change the mock's behaviour, set environment variables before starting:

```bash
# macOS / Linux
LLM_CONCURRENCY=10 MOCK_LATENCY_MS=500 MOCK_FAILURE_RATE=0.3 python -m app
```

```powershell
# Windows (PowerShell)
$env:LLM_CONCURRENCY=10; $env:MOCK_LATENCY_MS=500; $env:MOCK_FAILURE_RATE=0.3
python -m app
```

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `mock`, `groq`, `ollama`, `openrouter` or `gemini` |
| `LLM_CONCURRENCY` | `5` | Max LLM calls in progress at once, across all jobs |
| `MOCK_LATENCY_MS` | `200` | How long each mock call takes |
| `MOCK_FAILURE_RATE` | `0.1` | Chance (0–1) that a mock call fails |
| `DB_PATH` | `catalogiq.db` | SQLite file; delete it to start fresh |
| `PORT` | `8000` | Port for `python -m app` |
| `LLM_API_KEY` | – | API key (or `GROQ_API_KEY` / `OPENROUTER_API_KEY` / `GEMINI_API_KEY`) |
| `LLM_MODEL` | per provider | Override the default model |
| `LLM_BASE_URL` | per provider | Override the API address |
| `LLM_TIMEOUT_S` | `30` | Seconds before a call is abandoned |
| `LLM_MAX_RPM` | none (`groq`: 14) | Max LLM calls **started** per minute; `0` = no limit |

## Run with the real LLM

**Groq (what I use):** create a free API key at https://console.groq.com. Then:

```bash
# macOS / Linux
LLM_PROVIDER=groq GROQ_API_KEY=your-key LLM_CONCURRENCY=3 python -m app
```

```powershell
# Windows (PowerShell)
$env:LLM_PROVIDER="groq"; $env:GROQ_API_KEY="your-key"; $env:LLM_CONCURRENCY=3
python -m app
```

**Groq's free tier and the rate limiter.** I measured the limits on my key: about **8,000 tokens per minute** and **1,000 requests per day** per model. One call uses about 520 tokens, so only about 15 calls per minute fit. Sending faster just produces `429 Too Many Requests`.

So the app has a small pacer ([app/ratelimit.py](app/ratelimit.py)) that spaces call *starts* evenly. For `groq` it defaults to 14 per minute, one call every ~4.3 s; change it with `LLM_MAX_RPM`. It works alongside the concurrency limit: the semaphore caps calls *in progress*, and the pacer caps calls *started per minute*. Cache hits skip both.

Measured run: the first 30 sample rows took 120 s, with 30/30 done, 0 failed, 0 errors, no 429s, and 2 cache hits. The 240-row sample needs about 15 minutes on the free tier, so for a live demo with the real LLM, use [`data/sample_small.csv`](data/sample_small.csv): the first 25 rows, about 2 minutes.

The default model is `openai/gpt-oss-120b` with `reasoning_effort: low`. Of the free models I compared, it put the most listings in the right category, and low effort cuts it from about 650 to about 520 tokens per call with the same answers.

**Ollama (local, no key):** install from https://ollama.com. Then:

```bash
ollama pull llama3.2:3b
LLM_PROVIDER=ollama python -m app
```

**OpenRouter / Gemini:** set `LLM_PROVIDER=openrouter` with `OPENROUTER_API_KEY`, or `LLM_PROVIDER=gemini` with `GEMINI_API_KEY`. Free model names change over time; if a default model is retired, set `LLM_MODEL`.

**Using a `.env` file instead:** copy `.env.example` to `.env` and fill it in, for example `LLM_PROVIDER=groq` and `GROQ_API_KEY=...`. The app reads it at startup. Real environment variables win over the file.

API keys come only from environment variables or `.env`. `.env` is git-ignored, and `.env.example` lists every setting without real values.

## Run the tests

```bash
pytest
```

There are 145 tests, and all use the mock or a fake HTTP server, so no network is needed. The ones the brief asks for are:
- `tests/test_pipeline.py`:
  - **Concurrency limit:** checked with a fake provider that counts its own parallel calls, including across two jobs.
  - **Retries:** 4 attempts, delays of 0.2/0.4/0.8 s, invalid output retried, and a slot released during backoff.
  - **De-duplication:** sequential duplicates, simultaneous duplicates making one call, shared failures, failures not cached, and cancellation safety.
- `tests/test_jobs.py`: background jobs, fairness between jobs, and crash-and-resume.
- `tests/test_api.py`: the whole API contract, `202` within 1 s, and responsiveness during a job.

To regenerate the sample data:

```bash
python scripts/generate_sample_data.py
```

## API

| Method | Path | Success | Errors |
|---|---|---|---|
| GET | `/api/health` | `{"status","llm_provider","llm_concurrency"}` | – |
| GET | `/api/metrics` | `{"llm_calls_total","llm_errors_total","max_concurrent_llm_calls"}` | – |
| POST | `/api/jobs` `{"products":[...]}` | `202` job object | `400` empty list, missing `sku`/`raw_title`, bad JSON |
| GET | `/api/jobs/<id>` | job with live counters | `404` |
| GET | `/api/products?page&page_size&category&q` | `{"items","page","page_size","total"}`, sorted by SKU | `400` non-numeric or < 1 paging |
| GET | `/api/products/<sku>` | product | `404` |
| PATCH | `/api/products/<sku>` | product with `status: approved` | `400` bad category / empty title / bad tags, `404` |

Every error body is `{"error": "message"}`.

## The prompt

The system prompt ([app/llm/prompt.py](app/llm/prompt.py)), sent with `temperature: 0` and, where the provider supports it, `response_format: {"type": "json_object"}`:

```text
You clean up product listings for an Indian marketplace catalogue.

Reply with ONLY a JSON object with exactly these keys:
- "clean_title": a tidy, human-readable product title. Fix capitalisation and spacing.
  Write units like "500 g", "1 kg", "750 ml", "1 L". Put pack sizes at the end, like
  "(Pack of 2)". Use only facts from the listing; never add details it doesn't state.
- "category": exactly one of: Groceries, Beverages, Personal Care, Household, Electronics, Fashion, Home & Kitchen, Other.
  Use "Other" if none fits.
- "brand": the brand name exactly as a shopper would write it, if the listing clearly
  states one; otherwise null. Never guess a brand that isn't in the listing.
- "tags": up to 5 short lowercase search keywords (no brand, no units).

Example
Listing: {"raw_title": "  AMUL   butter 500G", "raw_description": "pck of 2"}
Reply: {"clean_title": "Amul Butter 500 g (Pack of 2)", "category": "Groceries", "brand": "Amul", "tags": ["butter", "dairy", "salted butter"]}
```

The user message is the listing as JSON, which keeps seller text clearly marked as data:

```text
Listing: {"raw_title": "  AMUL   butter 500G", "raw_description": "pck of 2"}
```

Every reply goes through the same validator as the mock ([app/validation.py](app/validation.py)):
- **Rejected, so the call is retried:** invalid JSON, a missing or empty title, or a category outside the 8.
- **Fixed:** unambiguous cosmetic issues (tag case, duplicate tags, more than 5 tags, "Unknown" brand becomes null, category case), so they don't cost another LLM call.

## Project layout

```
app/
  __main__.py     `python -m app`: starts the server on 127.0.0.1 and ::1
  main.py         API routes, error format, startup/shutdown (lifespan)
  config.py       settings from environment variables
  pipeline.py     Enricher: cache -> in-flight wait -> semaphore + retries
  ratelimit.py    paces LLM call starts per minute (free-tier limits)
  jobs.py         background jobs, worker pools, resume after restart
  db.py           SQLite schema and queries
  normalize.py    content key for de-duplication
  validation.py   checks and cleans LLM output
  metrics.py      LLM call counters
  schemas.py      categories and the Enrichment shape
  llm/            provider interface, mock, OpenAI-compatible provider, prompt
static/           index.html, app.js, style.css
tests/            pytest suite
data/             sample_listings.csv
scripts/          sample data generator
```

## Assumptions

Where the brief left something open, I chose the following:

**Job counters**
- `done` counts every finished item, success or failure, so progress is `done / total` and a completed job has `done == total`. `failed` and `cache_hits` are subsets of `done`.
- A duplicate that waits for an identical in-flight item counts as a **cache hit**, because it never calls the LLM.
- If the original fails, its waiters fail too, since that content already used 4 attempts. Failures are not cached, so resubmitting the content tries again.
- Invalid output counts in `llm_errors_total`, because it is a failed attempt.

**Products**
- A product row is created when its item has been **processed**, so `status` is only ever `enriched`, `failed` or `approved`. Until then `GET /api/products/<sku>` returns `404`, or the previous version of an existing SKU.
- Resubmitting an existing SKU **replaces** it, including any approved edits, and its status goes back to `enriched`. On failure, the old clean fields are cleared, because they described the old raw text.

**POST /api/jobs**
- One invalid product rejects the whole job (`400`), naming the product's position.
- Integer SKUs are accepted and stored as strings.

**GET /api/products**
- `page_size` above 100 is **capped** at 100. `page` or `page_size` below 1 is a `400`.

**PATCH /api/products/<sku>**
- The category must be the exact name.
- Tags are lowercased and de-duplicated; more than 5 is a `400`.
- The brand isn't editable, since the brief lists only title, category and tags.
- An empty body approves the product as it is.
- An unknown SKU is a `404`, checked before the body.

**Other**
- Timestamps are UTC, without a timezone suffix, matching the brief's example format.
- The metrics are kept in memory and reset when the server restarts. Jobs, products and the cache persist.

## Unfinished / what I'd do next

- **Rate limiting is per process:** the pacer counts calls, not tokens, and lives in one process. At scale it should be a shared token bucket (e.g. in Redis) that also honours `Retry-After` (DESIGN.md §3). Batching several products per prompt would make Groq's free tier about 4–5 times faster.
- **Pointless retries:** client errors other than 429 (e.g. 401, bad key) are retried like any failure; they could fail fast instead.
- **Single process:** SQLite means one server process. Multiple workers would need Postgres and leases on job items (DESIGN.md §2).
- **Search:** it's a `LIKE` scan, fine for this size. Full-text search and cursor pagination are described in DESIGN.md §4.
- **Duplicated category list:** the frontend keeps its own copy of the 8 categories instead of fetching them.
- **No login:** there is no authentication, and no upload size limit.

## How I used AI tools

I used Claude Code (Anthropic) as a pair programmer. We built the project one step at a time, in this order:
1. mock provider
2. validation
3. pipeline
4. database
5. jobs
6. API
7. frontend
8. real provider
9. docs

Each step had its own commits and tests. Before each step we wrote down the questions it had to answer (for example, "should a task keep its semaphore slot while it sleeps between retries?"). I kept Cornell-style notes of every design decision and why it was made, and used them to review the concurrency, caching and recovery code until I could explain each line. The trade-offs in DESIGN.md are ones I can argue for.
