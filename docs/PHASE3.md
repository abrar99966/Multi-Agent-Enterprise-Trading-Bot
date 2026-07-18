# Phase 3 — Slow Path: Bounded Parameter Control, Regime, LLM Analyst

**Date:** 2026-06-10 · **Status:** complete · **173 tests pass.**
Builds on [docs/PHASE2.md](PHASE2.md); plan in [docs/ARCHITECTURE.md](ARCHITECTURE.md) §6.

## What Phase 3 delivers

The slow path: strategic intelligence that can only influence trading through a
single bounded interface, and never blocks or breaks the fast path.

| Module | Files | Role |
|---|---|---|
| Failure isolation | `slowpath/base.py` | `SlowPathAgent` guards every bus callback in try/except — a crashing analyst is swallowed + counted, never propagates to the bus. |
| Parameter boundary | `slowpath/params.py` | `ParameterController` — the slow path's ONLY write interface. Enforces bounds, direction asymmetry, rate, quorum, TTL. |
| Regime classifier | `slowpath/regime.py` | Deterministic volatility-regime classifier (trend/chop/stress/crisis) → emits tightening proposals only. |
| LLM analyst | `slowpath/analyst.py` | `LLMAnalyst` — provider-agnostic; turns a structured assessment into a bounded proposal. OFF the deterministic path. |
| LLM providers | `slowpath/providers.py` | Vendor-agnostic provider layer: `StubProvider` (default, offline), `OpenAICompatibleProvider` (httpx — OpenAI/Groq/Together/OpenRouter/Gemini/Ollama/LM Studio/vLLM/Mistral/DeepSeek/xAI), `AnthropicProvider` (native SDK). `build_provider(LLMConfig)` factory. |
| Event | `core/events.py` | `ParameterChangeProposal` + `CTL_PARAM_PROPOSALS` stream. |
| Consumer | `risk/gateway.py` | Subscribes `CTL_PARAMS`; `_effective_limit()` applies risk.* overrides so tightenings constrain trading. |

## The only write interface: `ParameterChangeProposal` (§6.2)

Analysts emit proposals; the `ParameterController` decides what changes:

- **Bounds** — every parameter has `[min, max]` and a max step per change (large jumps rejected).
- **Direction asymmetry** — a proposal moving a parameter in its *conservative* direction TIGHTENS and **auto-applies**; a loosening is **held for human approval** (`approve_loosening`). A hallucinating analyst can only make the system more conservative.
- **Rate limit** — at most N applied changes per parameter per window (event-time).
- **Quorum** — a parameter can require ≥ `min_sources` independent sources to agree before a tightening applies (applies the most conservative agreed value). One noisy signal cannot swing capital.
- **TTL** — every applied change expires back to the human-set baseline unless renewed, driven by bar event-time. Reverting to baseline is always safe and needs no approval, so a stuck analyst cannot leave the system permanently drifted.

Applied changes publish as `ParameterChange` on `CTL_PARAMS`; the gateway consumes `risk.*` as effective-limit overrides. The fast path **never waits** on any of this.

## Slow-path outage is harmless (the chaos property)

`SlowPathAgent` sandboxes each agent's callbacks. `tests/test_slowpath.py`:
- a `_BrokenAnalyst` that raises on every bar does **not** stop the bus dispatching to healthy subscribers (`errors == n`, all bars still delivered);
- attaching it to a live session leaves **every fast-path stream byte-identical** to a baseline run without it.

So an LLM outage, a bad model output, or an analyst bug cannot touch trading — it continues on last-known-good / TTL-decayed parameters.

## LLM containment + vendor independence (§5–6)

`LLMAnalyst` is **provider-agnostic**: it asks a configured `LLMProvider` for a
structured `EventImpactAssessment`, then maps it to a bounded proposal —
**bearish → tighten (auto), bullish → loosen (held)**. The model is the *source*
of a proposal, never an order; the controller is the boundary.

The model is chosen entirely by config (`ETB_LLM_PROVIDER` / `ETB_LLM_MODEL` /
`ETB_LLM_API_KEY` / `ETB_LLM_BASE_URL`), never hard-wired:

- `OpenAICompatibleProvider` (raw httpx, **no extra dependency**) covers OpenAI,
  Groq, Together, OpenRouter, Google Gemini (its OpenAI endpoint), Mistral,
  DeepSeek, xAI, and every local Llama server (Ollama, LM Studio, vLLM) — free
  and paid. Presets supply base URL + a default model; both overridable.
- `AnthropicProvider` (native SDK, lazy-imported) for Anthropic models.
- `StubProvider` (deterministic, offline) is the default — nothing external is
  required and tests stay hermetic.

`build_provider(LLMConfig)` / `LLMAnalyst.from_config(...)` are the single
config-driven entry points. The whole multi-vendor HTTP path is tested with
`httpx.MockTransport` (no network); the provider call itself is the only
non-deterministic piece and is off the replay path by construction.

## Determinism preserved

The regime classifier and controller are pure functions of the event stream
(proposals + bar-driven TTL); no wall clock, no RNG. `enable_slow_path=True`
sessions replay bit-identically (`CTL_PARAM_PROPOSALS` and `CTL_PARAMS` streams
included). The LLM analyst is the only non-deterministic piece and is off the
replay path by construction.

## Exit criteria (met)

- **Slow-path outage provably harmless** — chaos test green.
- **Tightening auto-applies, loosening human-gated** — controller tests + gateway-tightening test (a tightened limit rejects an order that previously fit).
- **Replay determinism holds with the slow path on.**

## Deferred / carried forward

- Qdrant + knowledge graph for analyst memory → later (the analyst pattern + containment are in place; retrieval is additive).
- Regime label feeding the autonomy tier policy (Phase 2 left tiers regime-agnostic) → wire `RegimeClassifier.market_regime` into `TierPolicy` next.
- Live news/macro feed to drive `LLMAnalyst.assess_and_propose` on a schedule → Phase 4+ data integration.
- Slow-path scaling (multiple analysts, quorum across LLM + statistical signals) → the machinery supports it (`min_sources`); wiring is additive.
