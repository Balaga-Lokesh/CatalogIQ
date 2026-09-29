"""Real LLM provider for any OpenAI-compatible chat API.

Groq, Ollama, OpenRouter and Google Gemini all offer the same
POST {base_url}/chat/completions endpoint, so one class covers every free
option in the brief; only the address, model and API key differ (PRESETS).

Every failure (network error, timeout, rate limit, server error, a reply
without content) is raised as LLMError, so the pipeline retries it.
"""

import os
import re
from dataclasses import dataclass

import httpx

from app.llm.base import LLMError, LLMProvider
from app.llm.prompt import SYSTEM_PROMPT, user_message


@dataclass(frozen=True)
class Preset:
    base_url: str
    model: str
    key_env: str | None      # environment variable holding the API key (None = no key)
    json_mode: bool          # send response_format={"type": "json_object"}


# Default models are small, free-tier options. Free models change over time:
# override with LLM_MODEL if a default is retired.
PRESETS = {
    "groq": Preset("https://api.groq.com/openai/v1", "llama-3.1-8b-instant", "GROQ_API_KEY", True),
    "ollama": Preset("http://localhost:11434/v1", "llama3.2:3b", None, True),
    "openrouter": Preset("https://openrouter.ai/api/v1", "meta-llama/llama-3.2-3b-instruct:free",
                         "OPENROUTER_API_KEY", False),
    "gemini": Preset("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash",
                     "GEMINI_API_KEY", False),
}

_CODE_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        name: str,
        base_url: str,
        model: str,
        api_key: str | None = None,
        json_mode: bool = True,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,   # tests pass a fake server
    ) -> None:
        self.name = name
        self.model = model
        self.json_mode = json_mode
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # ONE client for the whole app: it reuses connections (keep-alive)
        # instead of opening a new TLS connection per product.
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout_s, transport=transport
        )

    async def enrich(self, raw_title: str, raw_description: str = "") -> str:
        payload = {
            "model": self.model,
            "temperature": 0,   # same listing -> same answer, as far as the model allows
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message(raw_title, raw_description)},
            ],
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise LLMError(f"{self.name}: timed out") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.name}: network error: {exc}") from exc

        if response.status_code == 429:
            raise LLMError(f"{self.name}: rate limited (HTTP 429)")
        if response.status_code >= 400:
            raise LLMError(f"{self.name}: HTTP {response.status_code}: {response.text[:200]}")

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"{self.name}: unexpected response shape") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError(f"{self.name}: empty reply")
        return strip_code_fence(content)

    async def close(self) -> None:
        await self._client.aclose()


def strip_code_fence(text: str) -> str:
    """Models without JSON mode often wrap JSON in ```json ... ```. That is
    packaging, not content, so it is removed here; the validator then checks
    the JSON itself as strictly as ever."""
    text = text.strip()
    match = _CODE_FENCE.match(text)
    return match.group(1) if match else text


def from_preset(name: str, *, api_key=None, model=None, base_url=None, timeout_s=30.0,
                transport=None) -> OpenAICompatibleProvider:
    preset = PRESETS[name]
    key = api_key or (os.getenv(preset.key_env) if preset.key_env else None)
    if preset.key_env and not key:
        raise ValueError(f"LLM_PROVIDER={name} needs an API key: set {preset.key_env} (or LLM_API_KEY)")
    return OpenAICompatibleProvider(
        name=name,
        base_url=base_url or preset.base_url,
        model=model or preset.model,
        api_key=key,
        json_mode=preset.json_mode,
        timeout_s=timeout_s,
        transport=transport,
    )
