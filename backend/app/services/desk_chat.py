"""Conversational LLM path for the Copilot — the fallback for questions the
deterministic intent handlers in api/v1/chat.py do not cover.

WHY this exists separately from slowpath/providers.py: those providers speak
`assess(system, prompt, schema)` and force a JSON object out of the model, which
is right for a bounded ParameterChangeProposal but wrong for prose. A desk chat
wants free-form text. This module makes one async, plain-text call to the same
OpenAI-compatible endpoint the provider layer targets (Ollama by default), and —
critically — injects the platform's REAL live state into the system prompt so
the model answers grounded in what is actually true, not from its imagination.

Contract:
  * OFF the order path. This can never place, modify, or cancel a trade; it only
    returns text. That is stated to the model and enforced by there being no tool.
  * Degrades to None on any failure (provider is 'stub', server down, timeout),
    so the caller keeps its accurate canned fallback rather than surfacing an error.
  * Bounded history so a long session can't grow the prompt without limit.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from app.core.config import get_settings

log = logging.getLogger(__name__)

_SYSTEM = (
    "You are Helios, the AI copilot embedded in an institutional trading desk. "
    "You answer questions about THIS desk — its positions, risk, strategies, macro "
    "regime, and market data — concisely and factually, in at most a few sentences. "
    "You are an ADVISOR: you cannot place, modify, or cancel orders, and you must "
    "never claim to have done so. Ground every answer in the LIVE FACTS provided "
    "below; if the facts do not cover the question, say what you do know and that "
    "the rest is not available, rather than inventing numbers. Do not output JSON "
    "or code fences unless asked."
)

# Keep the prompt bounded regardless of session length.
_MAX_HISTORY_TURNS = 8
_MAX_MSG_CHARS = 2000


def llm_available() -> bool:
    """True when a real (non-stub) conversational provider is configured."""
    s = get_settings()
    provider = (s.llm_provider or "stub").strip().lower()
    if provider in ("", "stub"):
        return False
    # Local/OpenAI-compatible servers need a base_url; hosted ones need a key.
    if provider in ("ollama", "lmstudio", "vllm", "openai_compatible"):
        return bool(s.llm_base_url.strip())
    return bool(s.llm_api_key.strip()) or bool(s.llm_base_url.strip())


def _base_url() -> str:
    s = get_settings()
    base = (s.llm_base_url or "").strip().rstrip("/")
    if not base:
        # Sensible default for the documented Ollama setup.
        base = "http://localhost:11434/v1"
    return base


async def desk_reply(
    message: str,
    facts: str,
    history: Optional[List[dict]] = None,
) -> Optional[str]:
    """One grounded, plain-text completion. Returns None on any failure so the
    caller can fall back. `facts` is a pre-rendered block of live desk state;
    `history` is [{role, content}] of prior turns."""
    if not llm_available():
        return None

    s = get_settings()
    system = f"{_SYSTEM}\n\nLIVE FACTS (as of now):\n{facts}"
    messages = [{"role": "system", "content": system}]
    for turn in (history or [])[-_MAX_HISTORY_TURNS:]:
        role = turn.get("role")
        content = (turn.get("content") or "")[:_MAX_MSG_CHARS]
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message[:_MAX_MSG_CHARS]})

    body = {
        "model": s.llm_model or "llama3.1:8b",
        "messages": messages,
        "temperature": 0.2,   # low: this is a factual desk assistant, not creative
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if s.llm_api_key.strip():
        headers["Authorization"] = f"Bearer {s.llm_api_key.strip()}"

    import httpx

    url = f"{_base_url()}/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=s.llm_timeout_s) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        text = (text or "").strip()
        return text or None
    except Exception as exc:
        # First call also loads the model on a cold Ollama, which can exceed the
        # timeout; the caller degrades gracefully either way.
        log.info("desk_reply LLM call failed (%s) — falling back to canned reply", exc)
        return None
