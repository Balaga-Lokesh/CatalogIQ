"""Configuration read from environment variables (defaults come from the brief).

Real-provider settings (only used when LLM_PROVIDER is not "mock"):
  LLM_API_KEY    API key; if unset, the provider's own variable is used
                 (GROQ_API_KEY, OPENROUTER_API_KEY, GEMINI_API_KEY)
  LLM_MODEL      override the provider's default model
  LLM_BASE_URL   override the provider's API address
  LLM_TIMEOUT_S  seconds before a call is abandoned (default 30)
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_concurrency: int
    mock_latency_ms: int
    mock_failure_rate: float
    db_path: str
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_timeout_s: float = 30.0


def load_settings() -> Settings:
    return Settings(
        llm_provider=os.getenv("LLM_PROVIDER", "mock").strip().lower(),
        llm_concurrency=int(os.getenv("LLM_CONCURRENCY", "5")),
        mock_latency_ms=int(os.getenv("MOCK_LATENCY_MS", "200")),
        mock_failure_rate=float(os.getenv("MOCK_FAILURE_RATE", "0.1")),
        db_path=os.getenv("DB_PATH", "catalogiq.db"),
        llm_api_key=os.getenv("LLM_API_KEY") or None,
        llm_model=os.getenv("LLM_MODEL") or None,
        llm_base_url=os.getenv("LLM_BASE_URL") or None,
        llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "30")),
    )
