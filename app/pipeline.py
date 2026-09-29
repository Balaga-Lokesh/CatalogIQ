"""The core: enrich one product (requirements 3, 4, 6).

TODO:
- global concurrency limit shared by all jobs (LLM_CONCURRENCY)
- retries: 3 more attempts with exponential backoff starting ~200 ms
- de-duplication: cache hit if content already enriched; if the same
  content is in flight right now, wait for that result instead of calling
"""
