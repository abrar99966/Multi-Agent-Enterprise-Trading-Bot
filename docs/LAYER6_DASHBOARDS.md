# Layer 6 — Visualization & Operations

**Status:** read-side BUILT (2026-06-11, 224 tests green) — phases 6.0/6.2/6.3
below; write surfaces (approval queue, kill switches) deferred until a live
session host exists. · **Depends on:** Phases 0–5 (built + verified)

## What is built

- `backend/app/dashboards/projections.py` — pure journal folds: trading
  (positions, PnL curve from `oms.positions` snapshots, tape), risk (verdicts,
  rejections by check, tiers, param changes, kill events), AI (model ids,
  per-decision SHAP attributions, slow-path proposals + evidence), platform
  (stream rates + chain status), paged event inspector.
- `backend/app/api/v1/dashboards.py` — read-only REST under `/api/v1/dash/*`:
  journal index with chain verification, the four views, events pager,
  **incident replay** (`POST /dash/journal/{name}/replay` — re-runs the bars
  through fresh components and diffs regenerated intents/fills against the
  journal; returns `match` + first divergence), TCA aggregates from the SQLite
  store, model-artifact metadata, masked LLM config. Path-traversal guarded.
- `backend/app/dashboards/static/index.html` — zero-dependency single-page UI
  at `GET /dash` (vanilla JS + canvas; no Node, no CDN, no build step, ₹0).
  Tabs: Trading | Risk | AI | TCA | Replay | Platform.
- Run: `uvicorn app.main:app --app-dir backend` → http://127.0.0.1:8000/dash
- Tests: `tests/test_dashboards.py` — every view asserted equal to the session
  summary it projects; replay endpoint asserted `match=True` on a clean journal.
**Principle:** every dashboard is a *projection of the event journal and derived
stores*. The event-sourced architecture already produces all the data; Layer 6
adds read-side surfaces, not new trading logic.

## Ground rules

1. **Read-only by default.** Dashboards never mutate trading state directly.
   The only write surfaces are the ones the architecture already defines, and
   they go through proper events (auditable, replayable):
   - `ApprovalDecision` (Tier 2/3 queue) → `ctl.approval_decisions`
   - `KillSwitch` engage/disengage (K1–K4 buttons) → `ctl.kill`
   - `ParameterController.approve_loosening()` (pending loosenings)
2. **One read API, many dashboards.** A single FastAPI service (already a
   dependency; `backend/app/api/` scaffold exists) serves REST + WebSocket/SSE.
   Sources: live bus tail, journal files (hash-verified), TCA store, model
   artifacts, bar store.
3. **The journal is the source of truth.** Any dashboard view must be
   reconstructable from a journal replay — which is also what makes the
   Incident Replay dashboard nearly free.

## The six dashboards → existing data

| Dashboard | What it shows | Data source (already produced) |
|---|---|---|
| **Trading** | positions, avg price, realized/unrealized PnL curve, intent→order→fill tape, last prices | `oms.positions`, `exec.fills`, `signal.intents`, `exec.orders`, `md.bars` |
| **Risk** | baseline vs *effective* limits (slow-path overrides), working reservations + headroom/symbol, verdict stream with full check lists, rejections by reason, kill-switch panel (K1–K4), Tier 2/3 approval queue with approve/reject, pending loosenings | `risk.verdicts` (full audit checks), `ctl.params`, `ctl.kill`, `ctl.approval_requests`/`_decisions`, gateway `working()`/`_effective_limit()` |
| **AI** | active `model_id` + signed-artifact provenance, probability timeline, per-decision SHAP top-k (every intent carries `attributions`), regime label timeline, LLM analyst config (provider/model) + recent assessments/proposals with evidence, slow-path error counters | `signal.intents.attributions`, artifact JSON, `ctl.param_proposals`, `RegimeClassifier.market_regime`, `SlowPathAgent.errors` |
| **TCA** | IS decomposition (delay/execution/fees bps), markout curves (+1/+5/+30), per-strategy / per-symbol slicing, trends across sessions | `tca` store (SQLite now, ClickHouse adapter ready), session `summary["tca"]` |
| **Incident Replay** | pick a journal → chain+anchor verification → deterministic replay → scrub through events, inspect state (positions, working, effective limits) at any seq, live-vs-replay diff, export incident report | `JournalReader`, `audit.chain.verify_journal`, `PaperSession.replay_from_journal` — the event-sourcing payoff; zero new data needed |
| **Platform Monitoring** | event rates per stream, journal chain status + tip, bus dispatch counts, slow-path error counters, feature-fabric warmup, component latency histograms | mostly existing counters; **the one new instrumentation need:** a timing wrapper around bus dispatch publishing to a metrics sink |

## Build phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **6.0 Read API** | FastAPI read-side: journal endpoints (events by stream/seq/time, chain status), session summaries, TCA queries, artifact metadata; SSE/WS live tail bridging the bus; reuse `api/v1/auth.py` scaffold for auth | every table above queryable via REST; live tail streams a paper session |
| **6.1 Trading + Risk** | the two operationally critical dashboards + the approval queue and kill-switch actions (the only writes) | an operator can watch a session live, approve a Tier-3 intent, and fire/clear K2 from the UI — all visible in the journal |
| **6.2 AI + TCA** | model/explainability views + cost analytics | per-intent SHAP rendered; TCA slices match `backtest_report.py` numbers |
| **6.3 Incident Replay** | journal picker, verify, replay, scrubber, live-vs-replay diff | replay of any journal renders bit-identical streams; diff view flags any divergence |
| **6.4 Monitoring + alerting** | dispatch-latency instrumentation, rates, alert rules; wire the existing Telegram `notification_service.py` for pages | K-switch engagement and chain-verification failure page within seconds |

Frontend: Next.js + Tailwind (already named in README; `frontend/mock_dashboard.jsx`
exists as a starting sketch). 6.0 can be exercised with plain `curl` before any
UI exists — keep the API the product, the UI a client.

## Explicitly deferred

- Grafana/Prometheus stack → when the docker compose infra is actually in use
  (the compose file already exists).
- Multi-user auth/roles → single-operator assumption until Phase 4 (multi-broker).
- Dashboard-driven parameter *editing* beyond the three sanctioned write surfaces
  — limits change through the risk module / ParameterController only.
