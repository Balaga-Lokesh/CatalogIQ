"""SQLite storage. Data must survive a restart.

TODO:
- schema: products, jobs, job_items, enrichment cache (keyed by content hash)
- connection handling that is safe to use from the async server
- queries used by the API (pagination, category filter, text search)
"""
