"""Pluggable LLM providers for the slow path.

The analyst is provider-agnostic: it asks a provider for a structured JSON
assessment and never cares which model produced it. This keeps the platform
free to use ANY model on the market -- free/local (Ollama, LM Studio, vLLM,
llama.cpp) or paid (OpenAI, Anthropic, Gemini, Groq, Together, OpenRouter,
Mistral, DeepSeek) -- selected purely by configuration.

Three implementations cover essentially everything:

- ``OpenAICompatibleProvider`` -- raw HTTP against any OpenAI-compatible
  ``/chat/completions`` endpoint (httpx; no extra SDK). One implementation
  serves OpenAI, Groq, Together, OpenRouter, Mistral, DeepSeek, Google Gemini
  (its OpenAI-compatible endpoint), and every local Llama server. Provider
  presets supply base URL + a sensible default model; both are overridable.
- ``AnthropicProvider`` -- the native Anthropic SDK (structured outputs).
- ``StubProvider`` -- deterministic, offline; the default so nothing external
  is required and tests stay hermetic.

``build_provider(LLMConfig)`` is the single factory. LLMConfig is populated
from settings/env (core/config.py), so switching models is a config change,
not a code change. Providers are OFF the deterministic replay path -- they are
only invoked when the analyst explicitly assesses an item.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, runtime_checkable

# base_url, default_model for OpenAI-compatible providers.
OPENAI_COMPATIBLE_PRESETS: Dict[str, tuple] = {
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "together": ("https://api.together.xyz/v1",
                 "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
    "openrouter": ("https://openrouter.ai/api/v1",
                   "meta-llama/llama-3.3-70b-instruct"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai",
               "gemini-2.0-flash"),
    "ollama": ("http://localhost:11434/v1", "llama3.1"),
    "lmstudio": ("http://localhost:1234/v1", "local-model"),
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "mistral": ("https://api.mistral.ai/v1", "mistral-small-latest"),
    "xai": ("https://api.x.ai/v1", "grok-2-latest"),
}

_ANTHROPIC_DEFAULT_MODEL = "claude-opus-4-8"


@dataclass
class LLMConfig:
    """How to reach the model. ``provider`` is one of: ``stub``, ``anthropic``,
    any key in OPENAI_COMPATIBLE_PRESETS, or ``openai_compatible``/``custom``
    (supply your own ``base_url``)."""

    provider: str = "stub"
    model: str = ""          # overrides the preset default
    api_key: str = ""        # for paid endpoints; local servers ignore it
    base_url: str = ""       # required for custom; overrides a preset
    timeout_s: float = 30.0
    extra_headers: Dict[str, str] = None  # e.g. OpenRouter ranking headers

    def __post_init__(self) -> None:
        if self.extra_headers is None:
            self.extra_headers = {}


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    def assess(self, system: str, prompt: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Return a JSON object matching ``schema`` (best effort)."""
        ...


def _extract_json(text: str) -> Dict[str, Any]:
    """Parse a JSON object from model text, tolerating ```json fences and
    surrounding prose (local models are chatty)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    return json.loads(text)


class StubProvider:
    """Deterministic, offline provider. Returns a canned assessment -- the
    default so the platform never requires an external model, and so tests are
    hermetic."""

    name = "stub"

    def __init__(self, response: Optional[Dict[str, Any]] = None, model: str = "stub") -> None:
        self.model = model
        self._response = response or {
            "direction": "neutral",
            "severity": "low",
            "confidence": 0.0,
            "affected": [],
            "rationale": "stub provider: no external model configured",
        }

    def assess(self, system: str, prompt: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        return dict(self._response)


class OpenAICompatibleProvider:
    """Any OpenAI-compatible /chat/completions endpoint, via httpx.

    Requests JSON-object mode and embeds the schema in the prompt (the lowest
    common denominator that local Llama/Ollama servers honor); retries once
    without ``response_format`` for endpoints that reject it. ``http_client``
    can be injected (e.g. an httpx.MockTransport client) for tests."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout_s: float = 30.0,
        extra_headers: Optional[Dict[str, str]] = None,
        http_client: object | None = None,
        name: str = "openai_compatible",
    ) -> None:
        if not base_url:
            raise ValueError("OpenAICompatibleProvider requires a base_url")
        self.name = name
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._extra_headers = extra_headers or {}
        self._client = http_client

    def _http(self):
        if self._client is not None:
            return self._client
        import httpx  # always available (core dependency)

        self._client = httpx.Client(timeout=self._timeout_s)
        return self._client

    def assess(self, system: str, prompt: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        user = (
            f"{prompt}\n\nRespond with ONLY a JSON object matching this schema "
            f"(no prose, no code fences):\n{json.dumps(schema)}"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        headers.update(self._extra_headers)
        url = f"{self._base_url}/chat/completions"
        body = {"model": self.model, "messages": messages, "temperature": 0}

        import httpx

        client = self._http()
        try:
            resp = client.post(
                url, json={**body, "response_format": {"type": "json_object"}},
                headers=headers,
            )
            if resp.status_code >= 400:  # endpoint may reject response_format
                resp = client.post(url, json=body, headers=headers)
        except httpx.TransportError:
            # Connection-level drop (e.g. a local server dying mid-load or
            # choking on response_format): one plain retry without it.
            resp = client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return _extract_json(content)


class AnthropicProvider:
    """Native Anthropic SDK with structured outputs. Lazy-imports anthropic."""

    name = "anthropic"

    def __init__(self, model: str = _ANTHROPIC_DEFAULT_MODEL, api_key: str = "",
                 client: object | None = None) -> None:
        self.model = model or _ANTHROPIC_DEFAULT_MODEL
        self._api_key = api_key
        self._client = client

    def _anthropic_client(self):
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised only without the dep
            raise RuntimeError(
                "AnthropicProvider needs the anthropic SDK: pip install anthropic"
            ) from exc
        self._client = anthropic.Anthropic(api_key=self._api_key) if self._api_key \
            else anthropic.Anthropic()
        return self._client

    def assess(self, system: str, prompt: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        client = self._anthropic_client()
        resp = client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text)


def build_provider(config: LLMConfig) -> LLMProvider:
    """Construct a provider from config. The single place that knows how to map
    a provider name to an implementation."""
    provider = (config.provider or "stub").lower()
    if provider == "stub":
        return StubProvider(model=config.model or "stub")
    if provider == "anthropic":
        return AnthropicProvider(model=config.model, api_key=config.api_key)
    if provider in OPENAI_COMPATIBLE_PRESETS:
        base_url, default_model = OPENAI_COMPATIBLE_PRESETS[provider]
        return OpenAICompatibleProvider(
            base_url=config.base_url or base_url,
            model=config.model or default_model,
            api_key=config.api_key,
            timeout_s=config.timeout_s,
            extra_headers=config.extra_headers,
            name=provider,
        )
    if provider in ("openai_compatible", "custom"):
        return OpenAICompatibleProvider(
            base_url=config.base_url,
            model=config.model,
            api_key=config.api_key,
            timeout_s=config.timeout_s,
            extra_headers=config.extra_headers,
        )
    raise ValueError(
        f"unknown LLM provider {config.provider!r}; known: stub, anthropic, "
        f"{', '.join(sorted(OPENAI_COMPATIBLE_PRESETS))}, openai_compatible"
    )
