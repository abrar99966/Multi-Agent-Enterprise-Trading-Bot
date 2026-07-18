# Phase 2 — TCA, Autonomy Tiers, Backtest≡Replay Parity

**Date:** 2026-06-10 · **Status:** complete · **154 tests pass.**
Builds on [docs/PHASE1.md](PHASE1.md); plan in [docs/ARCHITECTURE.md](ARCHITECTURE.md) §7.3, §8.4, §16.

## What Phase 2 delivers

| Area | Files | Role |
|---|---|---|
| TCA math | `tca/shortfall.py` | Implementation Shortfall decomposition (Perold §7.3) + markouts. Pure functions. |
| TCA engine | `tca/engine.py` | Streams intents/orders/fills/bars → one TcaResult per fill (delay/execution/fees bps + markouts at +1/+5/+30 bars). Derived analytics, not journaled. |
| TCA store | `tca/store.py` | `SqliteTcaStore` (default) + `ClickHouseTcaStore` (optional dep) — the analytics sink. |
| Autonomy tiers | `risk/tiers.py` | `TierPolicy`: classifies each approved intent into Tier 1/2/3 (§8.4). |
| Approval routing | `risk/gateway.py`, `risk/approver.py` | Gateway releases the order only if `tier <= auto_release_max_tier`; else emits an `ApprovalRequest` and waits for an `ApprovalDecision`. `AutoApprover` stands in for the human in headless runs. |
| Report | `scripts/backtest_report.py` | Runs a session, prints TCA + tier distribution, persists TCA, and proves backtest≡replay. |

## Implementation Shortfall (the credit-assignment math, §7.3)

For side sign s (+1 buy / −1 sell), decision price p_d (signal-bar close), arrival
price p_a (fill-bar open), fill price p_f, qty q, fees f:

```
delay_cost     = s·(p_a − p_d)·q     # latency: signal → arrival
execution_cost = s·(p_f − p_a)·q     # spread + impact
total_IS       = delay + execution + f = s·(p_f − p_d)·q + f
markout_bps(h) = s·(mid_{t_fill+h} − p_f)/p_f·1e4   # did price keep moving our way?
```

Positive = cost (worse for us). Markouts use mid (bar close), immune to bid-ask
bounce. Opportunity cost is in the formula for when partial fills arrive (Phase 4).
A sample run shows delay≈0 (synthetic `open[k+1]==close[k]`), execution≈2 bps
(the slippage), fees≈1 bps — the decomposition correctly attributes each component.

## Autonomy tiers v1 (§8.4) — "start everything Tier 2/3, earn Tier 1"

`TierPolicy` escalates on: untrusted strategy → Tier 3; order > 1% NAV → Tier 3;
0.25–1% NAV → Tier 2; tight limit headroom → 2/3. Only a **trusted** strategy
with a small order and ample headroom earns Tier 1 (autonomous). The `trusted`
set is empty by default, so nothing is autonomous until explicitly earned —
exactly the conservative Phase 2 stance.

Release path: the gateway's `auto_release_max_tier` (default **1**) is the real
autonomy ceiling. Tier 2/3 intents are *held* — an `ApprovalRequest` is emitted
and no exposure is reserved until an approving `ApprovalDecision` arrives. In the
headless harness `AutoApprover` clears them deterministically (production routes
to the dashboard: Tier 2 timeout-auto, Tier 3 human). The order boundary is
intact: every order still flows solely from `risk/gateway.py`.

## Backtest ≡ replay parity (formalized)

`scripts/backtest_report.py` runs the session, then replays the journal through
the identical code path and asserts the intent, fill, AND derived-TCA sequences
match. With tiers + approval round-trips + TCA all in the pipeline, replay is
still **bit-identical** — determinism survived three new event types and two new
components. This is the property the platform rests on, now packaged as a
runnable check (exit 0 = chain OK + replay PASS).

## Exit criteria (met)

- **TCA on every fill** — `summary["tca"]["n_fills"] == fills`; persisted per-fill.
- **Replay determinism green** — `tests/test_phase2.py` + the report script.
- **Tier-1 must be earned** — default `trusted=∅` and `auto_release_max_tier=1`,
  so a strategy reaches autonomous trading only by explicit promotion (the
  "4 clean weeks" gate becomes a promotion into `trusted`, Phase 5).

## Deferred / carried forward

- Real regime input to the tier policy → Phase 3 (slow path); currently assumed
  normal.
- Human approval UI (dashboard) and Tier 2 timeout semantics → Phase 8; the
  `AutoApprover` is the deterministic stand-in.
- ClickHouse is wired but optional; SQLite is the default TCA store until infra
  is provisioned.
- Opportunity-cost term is zero until partial fills (Phase 4).
