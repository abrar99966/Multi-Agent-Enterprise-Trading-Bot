# Scope — Broker L2 Depth Feed

**Status:** 🔵 Proposed — not started
**Author's confidence:** high on the internal design (read from this codebase), *medium* on
per-broker API specifics (see [§7](#7-assumptions-to-verify-before-committing-to-an-estimate))
**Prerequisite for:** a real order book, and a materially stronger pre-trade liquidity check

---

## 1. Why this is worth doing

The order book in the workspace UI is currently **modelled** — a deterministic ladder derived
from the last traded price. It is labelled as such everywhere it appears and is honest about
what it is, but it carries no information.

The stronger argument is not the UI. It is [§6.3 of the architecture](./ARCHITECTURE.md#63-risk-gateway):

> Liquidity — order ≤ x % of visible depth within 5 bps; reject in illiquid names — ≤ 20 % of top-3-level depth

That control is specified against **visible depth**. Without a depth feed it can only be
approximated from traded volume, which is a different quantity: volume is what *did* trade,
depth is what *can* trade right now. In a thin book those diverge exactly when it matters —
during the stress the control exists to survive.

So this work upgrades a risk control from approximate to real. The order book is the visible
by-product.

**Secondary gains:** microstructure features (order-book imbalance, microprice, queue position)
become computable, which [Appendix J](./ARCHITECTURE.md#appendix-j--latency--throughput-budgets)
lists in the target feature fabric and which the current bar-only feed cannot support.

---

## 2. What exists today

| Piece | State |
|---|---|
| `core/events.py::Tick` | Carries `bid`, `ask`, `bid_qty`, `ask_qty` — **L1 only**, one level per side |
| `Streams.MD_TICKS` | Defined and consumed by `risk/gateway.py:102` and `execution/algos.py:162` |
| Tick producers | **None.** No adapter, service, or endpoint publishes a Tick today |
| WebSocket infrastructure | **None anywhere in the backend.** Every market-data path is request/response over `httpx` |
| Broker adapters | `_DhanAdapter`, `_UpstoxAdapter`, `_ZerodhaAdapter`, `_SandboxAdapter`, plus IBKR. `BrokerSpec.streams_market_data` already exists as a capability flag |
| UI consumer | `components/ws/modules/markets/OrderBook.js` + `DepthLadder.js`. Documented removal path: *"If a depth feed is ever added, delete `buildSyntheticLadder`'s call site and the `synthetic` plumbing; DepthLadder itself needs no changes."* |

**The honest summary:** the event schema anticipates L1 quotes, the consumers are wired, and the
UI is ready. What is missing is the entire producing half — plus an L2 schema, because `Tick`
cannot express a ladder.

---

## 3. Design

### 3.1 New event type

`Tick` is deliberately left alone. A depth snapshot is a different shape and a different
cadence, and widening `Tick` would force every existing consumer to reason about optional
ladders.

```python
class DepthLevel(BaseModel):
    price: float
    qty: float
    orders: Optional[int] = None      # order count at level, where the venue publishes it

class DepthSnapshot(BaseModel):
    symbol: str
    ts_exch: int                      # venue timestamp, ns
    bids: List[DepthLevel]            # index 0 = best bid, descending
    asks: List[DepthLevel]            # index 0 = best ask, ascending
    depth_levels: int                 # 5 or 20 — what the venue actually sent
    source: str                       # "dhan" | "upstox" | "zerodha" | "ibkr"
    seq: Optional[int] = None         # venue sequence, when provided — gap detection
```

New stream: `Streams.MD_DEPTH = "md.depth"`.

**Snapshot, not delta.** Indian retail feeds publish full N-level snapshots rather than
incremental book updates, so there is no book to maintain and no resync problem. If a venue
that publishes deltas is ever added, it reconstructs to a snapshot inside its adapter and this
schema does not change.

### 3.2 Determinism

Depth events are journaled like every other event, so replay reproduces them. This is a hard
requirement, not a nicety: once the risk gateway's liquidity check reads depth, a replay that
lacks depth would produce **different verdicts** and break the backtest ≡ replay ≡ live property
that [Core Principle 3](./ARCHITECTURE.md#core-principles) rests on.

Two consequences:

- The gateway must treat *absent* depth as a distinct state from *empty* depth. Absent ⇒ fall
  back to the current volume-based approximation and mark the verdict as degraded. Empty ⇒ a
  genuinely empty book ⇒ reject. Conflating them would silently disable a risk control.
- Depth staleness must be bounded. A snapshot older than N seconds is treated as absent, not as
  truth. A frozen WebSocket that keeps serving a stale ladder is the dangerous failure here.

### 3.3 Connection subsystem (the actual bulk of the work)

There is no WebSocket infrastructure to extend, so this is a new subsystem:

- **Lifecycle** — connect, authenticate, subscribe, unsubscribe, graceful shutdown.
- **Reconnect** — exponential backoff with jitter; re-subscribe the universe on reconnect;
  never silently reconnect without marking the gap.
- **Heartbeat / staleness** — per-symbol last-update watermark feeding the absent/stale logic.
- **Sequence-gap detection** — where the venue provides `seq`; a detected gap marks affected
  symbols stale rather than interpolating.
- **Backpressure** — a full-depth feed on a wide universe is orders of magnitude more messages
  than the current bar polling. The consumer must be able to drop intermediate snapshots
  (conflate to latest) without falling behind. Only the newest snapshot per symbol matters.
- **Subscription budget** — brokers cap instruments per connection (Dhan documents a per-connection
  instrument limit; Kite historically ~3,000). The subscription set must be driven by the active
  watchlist/positions, not the full ~2,900-symbol universe.

Lives in a new `backend/app/marketdata/depth/` package. `BrokerSpec` gains a
`streams_depth: bool` flag beside the existing `streams_market_data`, so the UI can state
truthfully which connected broker can serve a book.

### 3.4 Read path

- `GET /api/v1/market-data/depth/{symbol}` → latest snapshot + an explicit
  `{ status: "live" | "stale" | "absent", age_ms }`.
- The UI's `OrderBook.js` switches on that status: render the real ladder when live, keep the
  existing MODELLED ladder when absent, and show a stale banner when stale. The `MODELLED` chip
  stays in the codebase permanently — it is the correct display whenever no depth broker is
  connected, which is the default configuration.

---

## 4. Phasing

| Phase | Deliverable | Exit criteria |
|---|---|---|
| **0 — Spike** | One broker, one hardcoded symbol, print snapshots to stdout | Real ladder observed; message rate and packet shape measured against assumptions |
| **1 — Schema + journal** | `DepthSnapshot`, `MD_DEPTH`, journal + replay support | A recorded session replays byte-identically with depth events present |
| **2 — Connection subsystem** | Lifecycle, reconnect, staleness, conflation, subscription management | Chaos test: kill the socket mid-session ⇒ symbols go stale, no crash, no stale ladder served as live |
| **3 — One adapter, production** | `streams_depth` on one broker end-to-end | Depth visible in the UI for a connected broker; MODELLED still correct when disconnected |
| **4 — Risk gateway** | Liquidity check reads real depth, with the absent/stale fallback | Check verified against a known thin instrument; degraded-verdict path covered by a test |
| **5 — Second adapter** | Prove the abstraction | Second broker behind the same interface with no changes to phases 1–4 |

Phases 0–3 deliver the visible order book. **Phase 4 is where the actual value is.**

---

## 5. Risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| **Stale book served as live** | The worst outcome. Sizing against a frozen ladder is worse than sizing against no ladder, because it looks authoritative | Per-symbol watermark; hard staleness cutoff; status on every read; gateway treats stale as absent |
| **Replay divergence** | Depth in the verdict path means a depth-less replay changes decisions, breaking the parity property | Journal depth from phase 1, *before* the gateway reads it in phase 4. Order matters |
| **Message-rate blowup** | Full depth on a wide universe can swamp a single-process Python consumer | Conflate to latest-per-symbol; subscribe only the active set; measure in phase 0 before committing |
| **Data-plan gating** | These feeds generally require a paid market-data subscription; the platform's standing mandate is free/OSS until profitable | `streams_depth` capability flag, off by default. This is the first component that would break the free-only mandate — an explicit decision, not a silent one |
| **Broker API drift** | Retail broker APIs change without much notice | Isolate per-broker packet decoding behind the adapter; keep the internal schema venue-neutral |
| **Windows/asyncio + long-lived sockets** | Development host is Windows; long-lived WS behaviour differs from Linux | Test reconnect on the deployment target, not only locally |

---

## 6. Effort

Rough, assuming one engineer familiar with this codebase:

| Phase | Estimate |
|---|---|
| 0 — Spike | 0.5–1 day |
| 1 — Schema + journal | 1–2 days |
| 2 — Connection subsystem | 3–5 days (the reconnect/staleness/backpressure semantics are the cost, not the socket) |
| 3 — First adapter | 2–3 days |
| 4 — Risk gateway integration | 2–3 days (includes determinism tests) |
| 5 — Second adapter | 1–2 days |

**Total ≈ 2–3 weeks** to phase 5; **≈ 1.5 weeks** to a real order book on one broker.

The estimate is dominated by correctness work, not by the feed itself. Anyone quoting "it's just
a WebSocket subscription" is scoping phase 0.

---

## 7. Assumptions to verify before committing to an estimate

These are stated from general knowledge of the vendors and **were not verified against live
documentation or a live account** while writing this. Phase 0 exists to settle them:

- **Dhan** — `dhanhq` SDK exposes a `marketfeed` WebSocket; a "full" packet type is understood to
  carry 5-level depth, with a separate 20-level feed. Requires the Data API plan. *This codebase
  already probes `probe_data_api` per account, so plan state is detectable at runtime.*
- **Upstox** — V3 market-data WebSocket, protobuf-encoded, full mode understood to include
  5-level depth.
- **Zerodha Kite** — WebSocket `full` mode understood to include 5-level depth; historical
  ~3,000-instrument cap per connection.
- **IBKR** — `reqMktDepth` via Gateway/TWS; L2 subject to per-exchange market-data subscriptions.

Verify for each: packet shape, depth levels, instrument cap per connection, message rate under
load, plan/subscription cost, and whether the account currently in use is entitled.

---

## 8. Recommendation

Do **not** start this to improve the order book. The modelled ladder is honest and adequate for
an operator who mostly needs price context.

Start it when either is true:

1. The pre-trade liquidity check needs to be real — i.e. position sizes have grown enough that
   *"is there enough book to absorb this?"* is a question with real money behind it; or
2. Microstructure features are wanted in the fabric, which requires book data regardless.

Both point at the same trigger: **when order sizes start interacting with available liquidity.**
Until then this is infrastructure ahead of need, and the free/OSS mandate argues against paying
for a data plan to power a panel.
