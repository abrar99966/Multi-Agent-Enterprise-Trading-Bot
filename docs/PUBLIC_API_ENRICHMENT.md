# Public-API Enrichment — Macro Regime, Symbology, Market-Data Failover

**Status:** ✅ Complete
**Date:** 2026-07-18
**Architecture Reference:** [TARGET_ARCHITECTURE.md](./TARGET_ARCHITECTURE.md) §5–6 (slow path, parameter control)
**Scope:** Three free public data sources, confined to the slow path and product surface.

---

## 1. Overview

Three free public APIs were integrated to enrich the slow path and the product
surface. Every integration obeys the same hard constraints:

- **Off the deterministic fast path.** No source touches the order path, the risk
  gateway's decision, or replay. They use the real wall clock and the network, so
  they can never be part of a bit-identical replay.
- **Key-optional, fail-closed to empty.** A blank key disables the source; the app
  runs exactly as before (free/OSS mandate). Every method degrades gracefully —
  on any failure it returns an empty result and never raises.
- **Tighten-only where they influence risk.** The macro analyst can only *tighten*
  gross exposure; a misread makes the system more conservative, never more
  aggressive. It emits proposals through the sole `ParameterController` boundary —
  it can never loosen a limit or place an order.

| Source | Key | Feeds | Purpose |
|--------|-----|-------|---------|
| **US Treasury** daily par-yield curve | none | macro regime analyst | 10Y-2Y spread; inversion = stress precursor |
| **FRED** (Federal Reserve Economic Data) | free (`ETB_FRED_API_KEY`) | macro regime analyst | VIXCLS (implied vol), other series |
| **OpenFIGI** (Bloomberg symbology) | keyless; free key raises rate limit (`ETB_OPENFIGI_API_KEY`) | symbol resolver | broker-neutral FIGI id — kills cross-broker symbol skew |
| **Finnhub** | free (`ETB_FINNHUB_API_KEY`) | market-data failover + news | backup quotes/candles + news sentiment |

---

## 2. Components

### 2.1 Macro data adapter — `services/macro_data.py`

Reads the **US Treasury** daily par-yield-curve XML feed (no key; indexed by year
via `field_tdr_date_value`, with a previous-year fallback for early January) and
**FRED** series (keyed). Parsing is split into pure functions —
`parse_treasury_xml` and `parse_fred_json` — so they are unit-tested offline with
fixtures, no network. Results are TTL-cached. `YieldCurvePoint` exposes
`spread_10y_2y` and `inverted`.

### 2.2 Macro regime analyst — `slowpath/macro_regime.py`

An off-replay `SlowPathAgent`. `classify_macro_regime(spread, vix)` (pure) maps
the curve spread + VIX to `{None, stress, crisis}`:

- VIX ≥ 40 or deep inversion (≤ −0.5) → **crisis**
- VIX ≥ 30 or any inversion (< 0) → **stress**
- otherwise → **None** (calm)

On stress it publishes a bounded **tightening** of `risk.max_gross_exposure`:
stress → 60% of baseline, crisis → 50%. The crisis factor is capped at 50% so a
proposal applies from baseline within the controller's `max_step_frac` (0.5) in
one poll; deeper cuts ratchet over successive polls. `poll_and_propose()` is
async and never called inside the deterministic bus loop.

### 2.3 Macro regime service — `engine/macro_regime_service.py`

Hosts the analyst on a **long-lived** bus so proposals auto-publish and apply —
unlike the ephemeral per-round paper sessions whose bus is discarded each run. It
owns a `LiveClock`, a journal-less `MemoryBus`, a `ParameterController` (the slow
path's only write interface), and the analyst.

Each poll: `poll_and_propose()` → (on stress) a proposal is applied by the
controller; a heartbeat `Bar` is published so the controller's TTL-expiry check
runs on real time, reverting overrides to baseline when macro calms; the bus is
drained. `effective_limits()` is the read side a live trading session consults to
inherit macro-driven constraints. Opt-in (started via REST) — never auto-runs.

`simulate_poll(spread, vix)` drives one poll against a synthetic reading through
the **real** bus + controller (then restores the live source) so the tightening
is observable for demos/ops — not a data source.

### 2.4 OpenFIGI resolver — `services/openfigi_symbols.py`

`map_symbol(ticker, exch_code)` POSTs to OpenFIGI's mapping API and returns a
`FigiRef` (broker-neutral FIGI id + canonical name/type). Works keyless (~25
req/min); a free key raises the limit. Strips `.NS`/`.BO`/`-EQ` suffixes so all
symbol spellings resolve to the same FIGI. In-process cached; `404` on no mapping.
`parse_mapping_response` is a pure, offline-tested function.

### 2.5 Finnhub provider — `services/finnhub_provider.py`

Keyed client (disabled without a key). Two uses, both off the fast path:

1. **Market-data failover** — a backup quote source wired into
   `market_data.get_quote_routed` as a tier **between** the connected broker and
   the Yahoo fallback (broker → Finnhub → Yahoo). The quote's `source` field
   reports which served it.
2. **News sentiment** — `company_news` / `news_sentiment` for slow-path analyst
   evidence. `parse_quote` and `sentiment_score` are pure, offline-tested.
   (Free-tier company-news carries no per-article score → sentiment degrades to
   neutral; the paid `/news-sentiment` endpoint carries scores.)

---

## 3. REST surface

Mounted under `/api/v1/slowpath` (see [API.md](./API.md#slow-path--macro-enrichment--symbology)):
`GET /macro`, `GET /symbology/{ticker}`, `GET /enrichment/status`, and the macro
service `status` / `start` / `stop` / `poll` / `simulate`. Market-data quotes gain
the Finnhub failover tier transparently.

---

## 4. Configuration

`.env` (all blank = source disabled; app unchanged):

```
ETB_FRED_API_KEY=        # free: https://fred.stlouisfed.org/docs/api/api_key.html
ETB_FINNHUB_API_KEY=     # free: https://finnhub.io/
ETB_OPENFIGI_API_KEY=    # optional; keyless works, key raises the rate limit
```

The US Treasury yield curve needs no key and is always on.

---

## 5. Verification

- **Tests:** `tests/test_public_api_integrations.py` (20 offline tests) — pure
  parsers, graceful degradation without keys, tighten-only proposals, TTL revert,
  service simulate. No network in the test suite (data source injected/faked).
- **Live-verified:** real Treasury yield curve; FRED VIX; OpenFIGI FIGI ids
  (AAPL/RELIANCE/TCS); Finnhub quote + news + active failover tier
  (`source: finnhub`); forced-stress simulation:
  `spread=-0.1&vix=33` → stress → gross 2.0M→1.2M;
  `spread=-0.6&vix=45` → crisis → →1.0M (ratcheted), live source restored.

---

## 6. Invariants preserved

- LLMs / external data remain **out of the order path**. The fast path is
  untouched — inference stays a pure function; `engine/runner.py` wiring is
  unchanged.
- The risk gateway remains the sole order boundary; the macro analyst reaches it
  only through the `ParameterController`, tighten-only.
- Slow-path failure isolation holds: analyst errors are swallowed and counted
  (`analyst_errors`); a macro outage changes nothing.
