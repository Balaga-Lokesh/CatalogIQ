"""Configuration read from environment variables (defaults come from the brief)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_concurrency: int
    mock_latency_ms: int
    mock_failure_rate: float
    db_path: str


def load_settings() -> Settings:
    return Settings(
        llm_provider=os.getenv("LLM_PROVIDER", "mock"),
        llm_concurrency=int(os.getenv("LLM_CONCURRENCY", "5")),
        mock_latency_ms=int(os.getenv("MOCK_LATENCY_MS", "200")),
        mock_failure_rate=float(os.getenv("MOCK_FAILURE_RATE", "0.1")),
        db_path=os.getenv("DB_PATH", "catalogiq.db"),
    )
