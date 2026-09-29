"""Configuration read from environment variables (defaults come from the brief).

Real-provider settings (only used when LLM_PROVIDER is not "mock"):
  LLM_API_KEY    API key; if unset, the provider's own variable is used
                 (GROQ_API_KEY, OPENROUTER_API_KEY, GEMINI_API_KEY)
  LLM_MODEL      override the provider's default model
  LLM_BASE_URL   override the provider's API address
  LLM_TIMEOUT_S  seconds before a call is abandoned (default 30)
  LLM_MAX_RPM    max LLM calls started per minute (0 = no limit). Default:
                 no limit, except groq, which defaults to 14 (free tier).

Values can also be put in a `.env` file in the project root (git-ignored,
so API keys never reach the repository). Real environment variables win
over the file.
"""

import os
from dataclasses import dataclass
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


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
    llm_max_rpm: float | None = None   # None = the provider's default


def load_env_file(path: Path = ENV_FILE) -> None:
    """Minimal .env support: KEY=VALUE lines, # comments, optional quotes.
    A variable already set in the real environment is never overridden,
    and empty values are skipped (so `GROQ_API_KEY=` means "not set")."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def load_settings() -> Settings:
    load_env_file()
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
        llm_max_rpm=float(os.environ["LLM_MAX_RPM"]) if os.getenv("LLM_MAX_RPM") else None,
    )
