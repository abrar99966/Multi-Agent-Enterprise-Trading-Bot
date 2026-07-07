# Phase 0 — Build & Adversarial Review Record

**Date:** 2026-06-10 · **Status:** Phase 0 exit criteria met.
Companion to [docs/TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md) §16 (migration plan).

## What Phase 0 delivers

A deterministic, fully-journaled paper-trading pipeline — the truth-and-safety
foundation every later phase builds on:

```
synthetic bars → MomentumStrategy → RiskGateway → PaperBroker → PositionTracker
                       (all events teed to a hash-chained JSONL journal)
```

| Module | Files | Role |
|---|---|---|
| Contracts | `core/events.py`, `core/clock.py`, `core/hashing.py`, `bus/base.py` | Event schema (ns UTC), injected clocks, audit-hash spec, bus interface |
| Bus + journal | `bus/memory.py`, `bus/journal.py`, `bus/redpanda.py` | FIFO in-process bus; hash-chained write-ahead journal; Redpanda tee (optional dep) |
| Audit | `audit/chain.py`, `scripts/verify_audit_chain.py` | Independent (pydantic-free) chain verifier + CLI |
| Market data | `marketdata/{store,synthetic,replay,questdb}.py` | SQLite bar store, seeded synthetic source, replay driver, QuestDB adapter (optional) |
| Risk | `risk/{gateway,limits}.py` | The order boundary: 9 pre-trade checks, kill switches, fail-closed |
| Paper/OMS | `paper/broker.py`, `oms/positions.py` | Anti-lookahead fill sim; signed avg-cost position book |
| Strategy/engine | `strategy/momentum.py`, `engine/runner.py` | SMA-crossover reference strategy; session wiring + journal replay |
| Infra | `infra/docker-compose.yml`, `.env.example`, `README.md` | Optional local Redpanda/QuestDB/ClickHouse/Postgres/Redis |

**Exit criteria (all green): 110 tests pass.** A paper session produces trades;
every `exec.orders` record maps to an APPROVED `RiskVerdict` (no order bypasses
risk); the journal chain verifies; and replaying the journaled bars through fresh
components reproduces the **bit-identical** intent/fill sequence
(`realized_pnl_total = 2539.8212243705193`, reproduced cross-process). This
backtest≡live parity is the property the whole architecture rests on.

## Adversarial review

Four reviewers were planned; the two realism/journal reviewers completed in the
build workflow, the two *safety-critical* reviewers (risk-bypass, determinism)
were lost to a session limit and were **completed manually afterward**.

### Risk-gateway bypass review → CLEAN on the critical axis
- **Single-writer invariant holds:** `grep EXEC_ORDERS` confirms only
  `risk/gateway.py:_on_intent` publishes orders; the broker only *subscribes*.
  No code path emits an order without an approved verdict.
- All time via injected `Clock`, read once per intent → replay-exact.
- Fail-closed: every check wrapped (exception → `internal_error` → reject);
  malformed intent → reject; `all(passed)` gate.
- Kill-switch FIFO ordering sound; disengage cannot clear a higher level.
- Limit boundaries correct (`<=` everywhere; rate `<`, since the current intent
  is not yet counted).

### Determinism review → CLEAN + empirically proven
- `replay_from_journal` reuses the exact `_wire()` path → identical subscription
  (and therefore dispatch) order.
- SimClock start (`min ts_open` == default) and advance (`ReplaySource` re-sorts
  by `(ts_close, symbol)`) reproduce identically.
- Intent/fill payloads are pure functions of bar data; the clock affects only
  envelope timestamps and `signal_age`/`rate_limit`, which are identical under an
  identical clock.
- No `uuid`/`random`/wall-clock in any decision path; IDs derive from bar content.

## Findings & dispositions

Severity from the original review; "F#" are referenced from code comments.

| ID | Sev | Finding | Disposition |
|---|---|---|---|
| — | critical | Unkeyed hash chain has no external anchor → **tail truncation** and **wholesale forgery** verify clean | **Fixed (partial, by design).** Docstring corrected to state the real guarantee; `JournalWriter.tip` + `<journal>.head` sidecar added; `verify_journal(expected_head, expected_count)` detects truncation/forgery against a retained tip. A co-located sidecar is only a tripwire — true tamper-evidence (signed/external anchor) is **Phase 1+** (§9). |
| — | major | `_resume` extended a chain corrupted *before* the tail | **Fixed.** Resume now fully chain-verifies; refuses (raises) on any corrupt/non-record/mis-chained line before an optional torn tail. |
| — | major | `journal.py` reader and `audit/chain.py` disagreed on a trailing parseable-non-record line | **Fixed.** Both now tolerate only an *unparseable* final line; a parseable non-record line fails in both. |
| — | major | `fsync=False` default but docstring claimed "on disk" | **Fixed.** Docstring honest (flush=OS cache, fsync=power-safe); `close()` fsyncs the completed journal + anchor once, so a cleanly closed paper journal is durable as a whole without per-record fsync cost. Live (Phase 1) uses `fsync=True`. |
| — | minor | `canonical_json` folded NaN/Infinity as invalid-JSON tokens | **Fixed.** `allow_nan=False` raises at hash time. |
| — | minor | Confirmed `ZeroDivisionError` on a qty=0 fill; `Fill.qty`/`Bar.interval_s` lacked `gt=0` | **Fixed.** Added `Field(gt=0)`; a 0-qty fill is now unconstructable. |
| — | minor | No parent-dir fsync on create | **Fixed.** `_fsync_dir` (no-op where the OS forbids it, e.g. Windows). |
| **F1** | major | **Risk gateway tracks position from fills only — no in-flight/working-order reservation.** A burst of intents can be approved past `position_limit`/`gross_exposure` before fills land | **RESOLVED in Phase 1.** Gateway v1 reserves working signed qty on order release, counts it in `position_limit`/`gross_exposure`, releases it on fill (→ position) / cancel / reject / expiry. See `risk/gateway.py` + `tests/test_risk_working_orders.py`. |
| F2 | major | Paper broker limit fills on a *touch* (`low<=limit`), full size, no queue/volume | **Deferred → Phase 4.** Latent: only MARKET orders exist in Phase 0. Fix when limit/execution algos arrive (strict trade-through + participation cap + partial fills). |
| F3 | major | No size/impact model — any qty fills at open ± 2 bps | **Deferred → Phase 1/4.** Consistent & replay-stable, so relative comparisons hold; absolute PnL is optimistic. Add participation cap + size-scaled slippage before trusting absolute backtest PnL. |
| F4 | minor | `ts_fill` backdated to bar open for traded-through limit fills | **Deferred** (tied to F2). |
| F5 | minor | Backtest vs live one-bar fill-timing divergence once a LiveClock feed lands | **Deferred → Phase 3.** Documented in `paper/broker.py`. |
| F6 | minor | Synthetic bars never gap → limit gap-improvement branches lack integration coverage | **Deferred → Phase 1.** Add seeded occasional gaps. |
| F7 | minor | `gross_exposure` float-sum order & broker pending-dict iteration are deterministic-by-dict-insertion (fragile to refactor) | **Accepted/noted.** Deterministic today; revisit if hot-path is ported (Phase 5). |

## Carried-forward decisions

- **Determinism is a hard contract**, enforced by the replay test in CI. Any new
  decision component must take an injected `Clock`, derive IDs from content, and
  avoid `uuid`/`random`/wall-clock — or the replay test fails.
- **The risk gateway is the only order writer.** A network-policy test (Phase 1)
  will assert strategy hosts cannot reach a broker, making the boundary
  unbypassable by construction, not convention.
- **Workflow lesson:** label-alignment bug when filtering failed agents by index;
  reviewer findings were re-attributed by content. Future workflows must key
  findings to the agent id, not the post-filter index.
