"""LLM providers. The mock and the real provider share one interface,
so switching between them is only a change to LLM_PROVIDER.
"""

from app.config import Settings
from app.llm.base import LLMError, LLMProvider
from app.llm.mock import MockProvider

__all__ = ["LLMError", "LLMProvider", "get_provider"]


def get_provider(settings: Settings) -> LLMProvider:
    """Build the provider named by LLM_PROVIDER. The rest of the app only
    ever sees an LLMProvider, never a concrete class."""
    if settings.llm_provider == "mock":
        return MockProvider(settings.mock_latency_ms, settings.mock_failure_rate)
    # TODO: real provider (Groq or Ollama) - decided later.
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")
