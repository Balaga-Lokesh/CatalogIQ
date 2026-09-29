"""Pipeline counters (requirement 7), exposed at GET /api/metrics.

- llm_calls_total: every attempt, including retries
- llm_errors_total: every failed attempt (call error OR invalid output)
- max_concurrent_llm_calls: highest number of calls ever in progress at once

No lock is needed: all updates happen on the single asyncio event-loop
thread, and none of these methods awaits, so an update can't be interrupted
halfway by another task.
"""


class Metrics:
    def __init__(self) -> None:
        self.llm_calls_total = 0
        self.llm_errors_total = 0
        self.max_concurrent_llm_calls = 0
        self.in_flight = 0  # calls in progress right now

    def call_started(self) -> None:
        self.llm_calls_total += 1
        self.in_flight += 1
        self.max_concurrent_llm_calls = max(self.max_concurrent_llm_calls, self.in_flight)

    def call_finished(self) -> None:
        self.in_flight -= 1

    def error(self) -> None:
        self.llm_errors_total += 1

    def snapshot(self) -> dict:
        return {
            "llm_calls_total": self.llm_calls_total,
            "llm_errors_total": self.llm_errors_total,
            "max_concurrent_llm_calls": self.max_concurrent_llm_calls,
        }
