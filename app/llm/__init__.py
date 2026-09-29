"""LLM providers. The mock and the real provider share one interface,
so switching between them is only a change to LLM_PROVIDER.
"""

from app.config import Settings
from app.llm.base import LLMError, LLMProvider
from app.llm.mock import MockProvider
from app.llm.openai_compatible import PRESETS, from_preset

__all__ = ["LLMError", "LLMProvider", "get_provider", "max_calls_per_minute"]


def max_calls_per_minute(settings: Settings) -> float | None:
    """LLM_MAX_RPM if set (0 = no limit), else the provider's default.
    Groq's free tier allows ~8,000 tokens/min; at ~520 tokens per call that
    is ~15 calls/min, so its default is 14 to leave a margin."""
    if settings.llm_max_rpm is not None:
        return settings.llm_max_rpm or None
    preset = PRESETS.get(settings.llm_provider)
    return preset.max_rpm if preset else None


def get_provider(settings: Settings) -> LLMProvider:
    """Build the provider named by LLM_PROVIDER. The rest of the app only
    ever sees an LLMProvider, never a concrete class."""
    if settings.llm_provider == "mock":
        return MockProvider(settings.mock_latency_ms, settings.mock_failure_rate)
    if settings.llm_provider in PRESETS:
        return from_preset(
            settings.llm_provider,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            timeout_s=settings.llm_timeout_s,
        )
    choices = ", ".join(["mock", *PRESETS])
    raise ValueError(f"Unknown LLM_PROVIDER {settings.llm_provider!r}; choose one of: {choices}")
