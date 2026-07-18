# Phase 1 — Deterministic GBDT Fast Path + Risk Gateway v1

**Date:** 2026-06-10 · **Status:** complete · **136 tests pass.**
Builds on [docs/PHASE0_REVIEW.md](PHASE0_REVIEW.md); plan in [docs/ARCHITECTURE.md](ARCHITECTURE.md) §5, §7, §16.

## What Phase 1 delivers

Replaces the SMA-crossover reference (and bans the LLM-in-the-loop path) with a
**deterministic GBDT decision engine**, and closes the Phase 0 risk gap (F1).

| Module | Files | Role |
|---|---|---|
| Feature fabric | `features/fabric.py` | 22 features = 13 raw indicators + the 9 strategy-tournament votes, reusing `learning/strategies.py`'s no-lookahead helpers. One indicator codebase → zero train/live skew. |
| Dataset | `learning/dataset.py` | Forward-return labels over `horizon`; features read ≤ t, label reads t+H. No leakage. Slides the **same bounded window** the live fabric uses. |
| Training | `learning/train.py`, `scripts/train_model.py` | LightGBM, `deterministic=True` + `num_threads=1` + fixed seed → byte-identical booster. |
| Artifact | `learning/artifact.py` | Signed JSON: booster text + feature order + thresholds + provenance, SHA-256 over canonical bytes; `model_id = "model-"+sha[:12]`. Loader verifies. |
| Inference | `engine/inference.py` | Loads verified artifact; `score(feats) → (prob, SHAP contributions, model_id)`. Pure function — no wall clock, no RNG. |
| Strategy | `strategy/model_strategy.py` | Long-only state machine with hysteresis (enter ≥ 0.55, exit ≤ 0.45); every intent carries `model_id` + top SHAP attributions. |
| Risk v1 | `risk/gateway.py` | **F1 fix:** working-order reservation (below). |
| Runner | `engine/runner.py` | `strategy_factory` param so the model path runs and **replays** through the identical wiring. |

## Why GBDT, not an LLM, on the decision path

LightGBM 4.6 on Python 3.14: deterministic, microsecond inference, native
per-decision SHAP attributions (`pred_contrib`) for explainability. An LLM is
non-deterministic, multi-second, non-replayable, and hallucinates worst under
distribution shift — disqualifying for the order path (§5). LLMs remain a
Phase 3 slow-path concern (bounded parameter advice only).

The fabric makes the GBDT **learn how to weight the existing strategy
tournament** (the 9 votes are features) plus raw context — formalizing the
tournament into the fast path (§7) rather than discarding prior work.

## F1 resolved — working-order reservation

The gateway now reserves the signed qty of every **approved-but-unfilled** order
and counts it in `position_limit` and `gross_exposure`. A burst of intents can
no longer be approved past the limits before fills land. Reservations release on
fill (converted to real position, no double count), cancel, reject, or expiry.
Determinism preserved: exposure summation iterates **sorted** symbols (Phase 0
finding F7), not set order.

## Exit criteria (met)

- **100% of orders gated** — `tests/test_order_boundary.py` source-scans the
  whole backend and asserts `risk/gateway.py` is the *only* publisher to
  `exec.orders`; behavioral test confirms a rejected intent yields no order.
- **Determinism holds for the model path** — `tests/test_e2e_model.py` proves a
  journaled GBDT session replays bit-identically (intents, fills, PnL). Swapping
  the decision engine from rules to a trained model did not break the contract.
- **Internal latency** — GBDT inference is sub-millisecond per bar; the retail
  broker leg (Phase 3+) remains the real-world bound, as designed.

## Deferred / carried forward

- **Rust risk gateway** (§16 Phase 1 also names this): the Python gateway is the
  correctness reference with full test coverage; the Rust port is bundled with
  the hot-path port in **Phase 5** (premature now — no live latency pressure at
  the retail tier).
- **Network-enforced boundary**: the source-scan is the Phase 1 analog; a real
  egress/network-policy test lands with multi-broker work (Phase 4).
- Realism gaps F2/F3/F4/F6 (limit fills, size/impact, synthetic gaps) unchanged —
  still latent (MARKET-only), Phase 4.
- Feature set is a strong reference, not tuned; monotonic constraints, richer
  microstructure features, and per-symbol/per-horizon models come with the
  champion–challenger formalization (Phase 5, §7).
