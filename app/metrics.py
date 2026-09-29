"""Pipeline counters (requirement 7), exposed at GET /api/metrics.

- llm_calls_total: every attempt, including retries
- llm_errors_total: every failed attempt
- max_concurrent_llm_calls: highest number of calls ever in progress at once

TODO: implement the counters.
"""
