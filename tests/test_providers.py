"""LLM provider layer: stub, factory, and OpenAI-compatible HTTP (mocked).

The OpenAI-compatible provider is exercised with httpx.MockTransport so the
whole multi-vendor path is tested deterministically with no network."""
from __future__ import annotations

import json

import httpx
import pytest

from app.slowpath.providers import (
    OPENAI_COMPATIBLE_PRESETS,
    AnthropicProvider,
    LLMConfig,
    OpenAICompatibleProvider,
    StubProvider,
    _extract_json,
    build_provider,
)

_SCHEMA = {"type": "object", "properties": {"direction": {"type": "string"}}}
_ASSESSMENT = {"direction": "bearish", "severity": "high", "confidence": 0.9,
               "affected": ["X"], "rationale": "y"}


# ---------------------------------------------------------------- json extraction


def test_extract_json_plain() -> None:
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_strips_fences_and_prose() -> None:
    assert _extract_json('Sure!\n```json\n{"a": 1}\n```\nDone.') == {"a": 1}
    assert _extract_json('here: {"a": 2} ok') == {"a": 2}


# ---------------------------------------------------------------- stub


def test_stub_provider_is_deterministic() -> None:
    p = StubProvider(response=_ASSESSMENT)
    assert p.assess("s", "p", _SCHEMA) == _ASSESSMENT
    assert p.assess("s", "p", _SCHEMA) == _ASSESSMENT  # same every call
    assert p.name == "stub"


# ---------------------------------------------------------------- factory


def test_build_provider_selects_implementations() -> None:
    assert isinstance(build_provider(LLMConfig(provider="stub")), StubProvider)
    assert isinstance(build_provider(LLMConfig(provider="anthropic")), AnthropicProvider)
    groq = build_provider(LLMConfig(provider="groq"))
    assert isinstance(groq, OpenAICompatibleProvider) and groq.name == "groq"
    # Preset supplies a default model when none is configured.
    assert groq.model == OPENAI_COMPATIBLE_PRESETS["groq"][1]


def test_build_provider_custom_requires_base_url() -> None:
    with pytest.raises(ValueError):
        build_provider(LLMConfig(provider="openai_compatible"))  # no base_url


def test_build_provider_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown LLM provider"):
        build_provider(LLMConfig(provider="nope"))


def test_build_provider_model_override() -> None:
    p = build_provider(LLMConfig(provider="ollama", model="llama3.3"))
    assert isinstance(p, OpenAICompatibleProvider) and p.model == "llama3.3"


# ---------------------------------------------------------------- OpenAI-compatible


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _completion(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def test_openai_compatible_parses_and_sets_auth() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        body = json.loads(request.content)
        seen["model"] = body["model"]
        return httpx.Response(200, json=_completion(json.dumps(_ASSESSMENT)))

    p = OpenAICompatibleProvider(
        base_url="https://api.example.com/v1", model="m1", api_key="sk-test",
        http_client=_client(handler),
    )
    assert p.assess("sys", "prompt", _SCHEMA) == _ASSESSMENT
    assert seen["auth"] == "Bearer sk-test"
    assert seen["url"].endswith("/chat/completions")
    assert seen["model"] == "m1"


def test_openai_compatible_retries_without_response_format() -> None:
    calls = {"n": 0, "had_rf": []}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = json.loads(request.content)
        calls["had_rf"].append("response_format" in body)
        if "response_format" in body:  # endpoint rejects it
            return httpx.Response(400, json={"error": "unsupported"})
        return httpx.Response(200, json=_completion(json.dumps(_ASSESSMENT)))

    p = OpenAICompatibleProvider(
        base_url="http://localhost:11434/v1", model="llama3.1",
        http_client=_client(handler),
    )
    assert p.assess("sys", "prompt", _SCHEMA) == _ASSESSMENT
    assert calls["n"] == 2  # first with response_format (400), retry without
    assert calls["had_rf"] == [True, False]


def test_openai_compatible_handles_fenced_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(f"```json\n{json.dumps(_ASSESSMENT)}\n```"))

    p = OpenAICompatibleProvider(base_url="https://x/v1", model="m",
                                 http_client=_client(handler))
    assert p.assess("s", "p", _SCHEMA) == _ASSESSMENT


def test_openai_compatible_raises_on_persistent_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    p = OpenAICompatibleProvider(base_url="https://x/v1", model="m",
                                 http_client=_client(handler))
    with pytest.raises(httpx.HTTPStatusError):
        p.assess("s", "p", _SCHEMA)
