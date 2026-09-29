"""Tests for the real (OpenAI-compatible) provider, against a fake HTTP server."""

import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.llm import get_provider
from app.llm.base import LLMError
from app.llm.openai_compatible import OpenAICompatibleProvider, from_preset, strip_code_fence
from app.validation import validate_enrichment

ANSWER = {"clean_title": "Amul Butter 500 g (Pack of 2)", "category": "Groceries",
          "brand": "Amul", "tags": ["butter"]}


def reply(content, status=200):
    return httpx.Response(status, json={"choices": [{"message": {"role": "assistant", "content": content}}]})


def make_provider(handler, json_mode=True, api_key="test-key"):
    return OpenAICompatibleProvider("groq", "https://llm.test/v1", "test-model", api_key=api_key,
                                    json_mode=json_mode, transport=httpx.MockTransport(handler))


def call(provider, title="  AMUL   butter 500G", description="pck of 2"):
    async def go():
        try:
            return await provider.enrich(title, description)
        finally:
            await provider.close()
    return asyncio.run(go())


def test_sends_prompt_model_key_and_json_mode():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return reply(json.dumps(ANSWER))

    raw = call(make_provider(handler))
    assert validate_enrichment(raw).clean_title == "Amul Butter 500 g (Pack of 2)"
    assert seen["url"] == "https://llm.test/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    body = seen["body"]
    assert body["model"] == "test-model"
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system" and "Groceries" in body["messages"][0]["content"]
    assert json.loads(body["messages"][1]["content"].removeprefix("Listing: ")) == {
        "raw_title": "  AMUL   butter 500G", "raw_description": "pck of 2"}


def test_json_mode_can_be_off():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return reply(json.dumps(ANSWER))

    call(make_provider(handler, json_mode=False))
    assert "response_format" not in seen["body"]


def test_code_fence_is_stripped():
    raw = call(make_provider(lambda r: reply("```json\n" + json.dumps(ANSWER) + "\n```")))
    assert json.loads(raw) == ANSWER
    assert strip_code_fence("```\n{}\n```") == "{}"
    assert strip_code_fence('{"a": 1}') == '{"a": 1}'


@pytest.mark.parametrize("response, message", [
    (httpx.Response(429, text="slow down"), "rate limited"),
    (httpx.Response(500, text="boom"), "HTTP 500"),
    (httpx.Response(401, text="bad key"), "HTTP 401"),
    (httpx.Response(200, text="not json"), "unexpected response shape"),
    (httpx.Response(200, json={"choices": []}), "unexpected response shape"),
    (reply(""), "empty reply"),
    (reply(None), "empty reply"),
])
def test_http_problems_become_llm_errors(response, message):
    with pytest.raises(LLMError, match=message):
        call(make_provider(lambda r: response))


def test_timeout_and_network_errors_become_llm_errors():
    def timeout(request):
        raise httpx.ReadTimeout("too slow", request=request)

    def refused(request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(LLMError, match="timed out"):
        call(make_provider(timeout))
    with pytest.raises(LLMError, match="network error"):
        call(make_provider(refused))


def test_no_auth_header_without_key():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return reply(json.dumps(ANSWER))

    call(make_provider(handler, api_key=None))
    assert seen["auth"] is None


def test_presets_need_their_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        from_preset("groq")
    monkeypatch.setenv("GROQ_API_KEY", "from-env")
    provider = from_preset("groq")
    assert provider.model == "llama-3.1-8b-instant"
    asyncio.run(provider.close())


def test_ollama_needs_no_key():
    provider = from_preset("ollama")
    assert provider.name == "ollama"
    asyncio.run(provider.close())


def test_factory_builds_real_provider_from_settings():
    settings = Settings(llm_provider="groq", llm_concurrency=5, mock_latency_ms=0, mock_failure_rate=0,
                        db_path=":memory:", llm_api_key="k", llm_model="other-model")
    provider = get_provider(settings)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == "other-model"
    asyncio.run(provider.close())
