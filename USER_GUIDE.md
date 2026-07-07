# Helios Capital — User Guide

A practical, page-by-page guide to every feature in the app: what it does, how
to use it, what's real, what's stub, and how to take it from "fun toy" to
"genuinely useful trading tool."

> **Honesty notice**: this doc tells you exactly what's wired vs. what's a
> placeholder. Where something is mock/sandbox/stubbed, it's labelled. No
> oversold features. If you're considering live trading, read the
> [Pre-live readiness](#pre-live-readiness-the-checklist-that-matters) section
> before you do anything else.

---

## Table of contents

1. [What this app is](#what-this-app-is)
2. [Quick start (5 minutes)](#quick-start-5-minutes)
3. [Pages](#pages)
   - [Dashboard `/`](#dashboard-)
   - [Brokers `/brokers`](#brokers-brokers)
   - [Training `/training`](#training-training)
   - [Performance `/performance`](#performance-performance)
4. [Core workflows](#core-workflows)
   - [Connect a broker](#workflow-connect-a-broker)
   - [Diagnose a stuck Upstox token](#workflow-diagnose-a-stuck-upstox-token)
   - [Refresh an expiring token](#workflow-refresh-an-expiring-token)
   - [Get real-time market data](#workflow-get-real-time-market-data)
   - [Generate fresh AI recommendations](#workflow-generate-fresh-ai-recommendations)
   - [Approve and place a trade](#workflow-approve-and-place-a-trade)
   - [Train the AI agent](#workflow-train-the-ai-agent)
   - [Set risk limits + use the kill switch](#workflow-set-risk-limits--use-the-kill-switch)
5. [Pre-live readiness — the checklist that matters](#pre-live-readiness-the-checklist-that-matters)
6. [Broker reference](#broker-reference)
7. [Market-data routing](#market-data-routing)
8. [AI recommendation engine](#ai-recommendation-engine)
9. [Order placement system](#order-placement-system)
10. [Performance tracking](#performance-tracking)
11. [Risk limits (hard gates)](#risk-limits-hard-gates)
12. [Training & learning pipeline](#training--learning-pipeline)
13. [Token expiry & SEBI daily refresh](#token-expiry--sebi-daily-refresh)
14. [Security model](#security-model)
15. [Configuration (env vars)](#configuration-env-vars)
16. [API reference](#api-reference)
17. [Troubleshooting](#troubleshooting)
18. [Roadmap to real trading](#roadmap-to-real-trading)
19. [Architecture reference](#architecture-reference)

---

## What this app is

An AI-assisted trading **dashboard, recommendation engine, and disciplined
execution system** for Indian (NSE/BSE) and international equity markets. It:

- Connects to your real broker accounts (Dhan, Upstox, Zerodha — all live SDKs)
- Streams real-time market data from whichever broker you connect, with
  Yahoo Finance fallback (15-min delayed)
- Runs a four-agent ensemble (Technical, News, Macro, Risk) to produce trade
  recommendations with explainable reasoning
- Requires **explicit human approval** before any order is sent to a broker —
  no auto-execution
- **Grades every recommendation** against the actual market move so you know
  the *real* hit rate, not just backtest fantasy
- **Enforces hard pre-trade risk limits** (per-trade cap, daily loss cap,
  trade-count cap, master kill switch) — blocks live orders at the service
  layer, not the UI
- Lets you train (tune) the Technical agent against historical data so the
  live signals improve over time, with universe presets up to 66 symbols
- Tracks every order with Paper-mode / Live-mode separation so you can dry-run
  without risking real money

**Not yet wired** (honest list):
- News agent is stubbed (returns one fake headline per symbol)
- Macro agent is stubbed (returns hardcoded "stable interest rates" commentary)
- No XGBoost / LSTM / RL models — Phase 1 of training is rule-based grid tune
- No WebSocket tick streaming — REST polling at 20–60s intervals
- No options-chain support, no F&O strategies
- No portfolio P&L attribution / advanced position sizing
- Order status doesn't auto-poll back (placed orders show `PLACED` until you manually refresh broker side)

---

## Quick start (5 minutes)

Both servers should already be running. If not:

```powershell
# Backend (from project root)
./venv/Scripts/uvicorn.exe backend.app.main:app --host 127.0.0.1 --port 8000 --log-level warning

# Frontend (from frontend/)
npm run dev
```

Open **http://127.0.0.1:3001**.

To see the system fully alive:
1. Open `/brokers` → click **Dhan** or **Upstox** → paste credentials → connect
2. Open `/performance` → set risk limits (per-trade ₹2,000, daily loss ₹500
   while testing) → leave kill switch off
3. Return to `/` — the header pill flips from amber ("Data: Yahoo delayed")
   to green ("Data: Upstox LIVE"), and a new hit-rate pill starts tracking
   real signal accuracy as soon as graded outcomes accumulate
4. Open `/training` → pick "Indexes + NIFTY 50" preset → click **Train now** →
   watch the per-symbol grid search complete in ~2 minutes

---

## Pages

### Dashboard `/`

The main trading desk. One-page layout split:

```
┌──────────────────────────────────────────────────────────────────┐
│  Helios Capital · NSE OPEN · Mon 20 May · 15:42  · Training · Performance │
│  ⏹ KILL SWITCH ENGAGED   ·   Hit rate: 58% · 124 signals (green)         │
│  Data: Upstox LIVE   ·   2 brokers connected   ·   API Online            │
│  ━━━ Live ticker (scrolling watchlist) ━━━━━━━━━━━━━━━━━━━━━━            │
├─────────────────────────────────────────────────┬─────────────────┤
│  KPI grid (4 cards: real broker-derived metrics)│                 │
│  Big chart (intraday 5m, source badge)          │   AI Trading    │
│                                                  │   Desk chat     │
│  AI Recommendations  [Reload] [Regenerate]       │                 │
│  ┌──────────────────────────────────────────┐   │   (drawer on    │
│  │ RELIANCE  BUY  68%  R:R 2.5  Qty 147     │   │    mobile)      │
│  │ RSI 62.2 · uptrend · params: tuned       │   │                 │
│  │ expires 3h 12m                            │   │                 │
│  │ [Reject]  [Approve & Execute]            │   │                 │
│  └──────────────────────────────────────────┘   │                 │
│                                                  │                 │
│  Order history (your real placed orders)         │                 │
└─────────────────────────────────────────────────┴─────────────────┘
```

**Header elements**:

| Element | Shows | Updates |
|---|---|---|
| Market status pill | NSE open/closed (TZ-correct via `Intl`) | every 30s |
| Date + Clock | today, live HH:MM:SS | clock tickets per-second, isolated leaf component |
| Training link | jumps to `/training` | static |
| Performance link | jumps to `/performance` | static |
| **Kill switch banner** | shown only when engaged — pulsing red, links to /performance | polls /risk/limits every 60s |
| **Hit-rate pill** | real signal accuracy over last 7 days, colour-graded | polls /performance/stats every 120s |
| Data source pill | "Dhan LIVE" / "Upstox LIVE" / "Yahoo delayed" / "Data API not subscribed" | polls /market-data/providers every 120s |
| Broker count pill | "N brokers connected" — clickable | polls /brokers/accounts every 60s |
| Live ticker bar | scrolling watchlist quotes | polls /market-data/watchlist every 20s |

**KPI cards** (all real, none hardcoded):

| Card | Value | Sub-text |
|---|---|---|
| **Deployable Capital** | Σ `balance` across connected INR accounts | Margin available + broker count |
| **Account P&L** | Σ `(equity − balance)` | % vs capital, colour flips red on loss |
| **Open Positions** | count of trades with status `PLACED`/`OPEN` | ₹ exposure + "paper" if all are paper |
| **Watchlist Breadth** | gainers / losers from watchlist | "5 advancing · 2 declining" |

Each KPI has a sparkline backed by a **session ring-buffer** (persisted to
`sessionStorage`, so a tab refresh doesn't wipe history). Sparklines stay
empty for ~20s after first load until the first poll cycle.

**Recommendation cards** show side (BUY/SELL pill), confidence ring, R:R,
quantity, **RSI value**, **trend** (uptrend/sideways/downtrend), reasoning,
and a `params_source: tuned` indicator if the live agent is using
training-tuned parameters.

**Two refresh buttons on Recommendations**:
- **Reload** (subtle outlined) — re-fetches the current list. Cheap, instant.
- **Regenerate** (gold prominent) — hits `?refresh=true`, **forces the agent
  ensemble to re-run for every symbol** with fresh market data. Takes
  5-20 seconds. Shows "Regenerating…" while in flight.

---

### Brokers `/brokers`

Manage which broker accounts the app talks to.

**Per-card features**:
- Status pill (Connected / Disconnected / Error / Token expired)
- **Token expiry countdown** — turns amber under 1h, red when expired
- **Refresh Token** button — inline form to paste a fresh token without
  disconnecting/reconnecting (preserves your API Key)
- **Sync** — re-fetches balance from the broker
- **Disconnect** — two-click confirm pattern, deletes encrypted credentials

**Connect modal** renders **per-broker fields** from the spec:
- Dhan: `Client ID` + `Access Token` (no API secret)
- Upstox: `API Key` + `Access Token` (with **prominent warning** about Paper
  Mode for sandbox tokens, plus auto-fallback that tries both URLs)
- Zerodha: `API Key` + `API Secret` + `Access Token`
- Others (Angel/IBKR/Alpaca/etc.): default field set, sandbox stub mode only

**Paper Mode** does different things per broker:
- **Upstox**: routes API calls to `api-sandbox.upstox.com`. Auto-fallback
  also tries the other URL if the first one rejects, so a Plus-plan token
  mistakenly marked Paper still works.
- **Dhan / Zerodha / Alpaca**: marks the account for *simulated* order
  placement — orders stored as `SIMULATED`, never hit the broker.

---

### Training `/training`

Train the Technical agent against historical data.

**Universe presets** (dropdown):
| Preset | Symbol count | Typical time | Recommended use |
|---|---|---|---|
| Dashboard watchlist | 6 | ~5s | Quick sanity check |
| All major NSE/BSE indexes | 16 | ~30s | Regime-aware signals |
| NIFTY 50 constituents | 50 | ~90s | Individual-stock signals |
| **Indexes + NIFTY 50** | **66** | **~2 min** | **Recommended default** |
| Custom symbols | any | varies | Type your own |

**Bar interval**: 1minute / 30minute (default) / day / week
**Lookback days**: 14-730, default 90

**Live progress bar** while training:
- `X/Y symbols (Z%)` with current symbol
- Last completed symbol's win rate, sharpe, and trade count
- Gradient-filled progress bar (sky-blue → emerald)
- Polls `/learning/status` every 2s, auto-stops on completion

**Per-symbol results table** sorted by win-rate improvement:
- Winning **strategy** per symbol (tournament across 6 strategies)
- Baseline win rate (default params) vs Tuned win rate (best strategy + combo)
- Sharpe, return, trade count
- Improvement in percentage points (green ≥ +1pp, red ≤ -1pp)
- Results table is scrollable (max 600px) for the larger universe

**Persisted params viewer** — expandable details block showing the raw
`tuned_params.json` content; each symbol's entry carries its winning `strategy`
key plus that strategy's tuned params. A "Winning strategies" chip row
summarises how many symbols each strategy won.

**Permanent amber banner** at top: backtest results are NOT live performance.

---

### Performance `/performance`

The truth-telling page. Where you find out if the bot is actually working.

**Top of page**:
- Window selector (1d / 3d / 7d / 30d / 90d)
- "Re-grade now" button — forces immediate grading vs. waiting for the
  60-second internal throttle
- **Big rose-red KILL SWITCH button** on the top-right of the header
  (turns green and labelled "Resume Live Trading" when already engaged)

**Headline metrics** (4 cards):

| Card | What | When green / amber / red |
|---|---|---|
| **Graded signals** | count in selected window | informational |
| **Hit rate (1h)** | % of signals whose direction matched the actual 1h move | green ≥ 55%, amber 48-54%, red < 48% |
| **Expectancy / signal** | `(p_win × avg_win) − (p_loss × avg_loss)` | green if positive |
| **Avg correct move** | average % move on correct calls | informational |

Sub-0.1% moves count as **neutral** — not graded as correct or wrong either way.

**Pre-live readiness checklist** — six conditions auto-computed from real
data:
1. ≥ 100 graded signals tracked
2. Hit rate ≥ 55% on 1h window
3. Positive expectancy per signal
4. Per-trade cap configured
5. Daily loss cap configured
6. Kill switch disengaged

You see the actual current value next to each item. The pill at the top of
the section reads "X/6 checks passing" or "✓ Ready for live".

**Risk limits panel** — editable form with:
- Per-trade max (₹) — single position rupee cap
- Daily loss cap (₹) — live trading auto-halts when this is hit
- Daily trade count cap — max orders per IST day
- "Save limits" button
- Today's usage shown above the form: `5/10 trades · P&L ₹-340 · buffer ₹1,660`
- Kill-switch status (when engaged, a rose-red banner explains live orders are blocked)

**Per-symbol accuracy table** — hit rate and avg move per symbol.

**Recent graded signals table** — last 20 graded recommendations:
- Symbol, side, entry price, +1h price, actual move %, verdict
  (✓ correct / ✗ wrong / pending)

---

## Core workflows

### Workflow: Connect a broker

1. Open `/brokers`
2. Click **Connect** on the broker card you want
3. Modal shows per-broker fields and notes
4. Generate your token outside the app (broker-specific):
   - **Dhan**: web.dhan.co → My Profile → DhanHQ Trading APIs → Generate
   - **Upstox**: account.upstox.com/developer/apps → your app → Generate
     (Algo Trading tab for prod, Sandbox tab for sandbox)
   - **Zerodha**: developers.kite.trade → My Apps → OAuth flow
5. Paste credentials + check **Paper Mode** if applicable
6. Click **Test & Connect**
7. Backend hits the broker's real API; success → live balance shown;
   failure → broker's actual error inline

### Workflow: Diagnose a stuck Upstox token

If `Connect` fails with `UDAPI100050` or `UDAPI100060`, use the
**diagnostic probe** endpoint to test against both URLs without going
through the UI:

```powershell
$body = @{
  api_key = "your-api-key"
  access_token = "your-access-token"
} | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/v1/brokers/upstox/probe `
  -Method POST -Body $body -ContentType 'application/json' | ConvertTo-Json -Depth 5
```

Response tells you which URL the token works on and gives you a clear
recommendation about whether to check Paper Mode.

Outcomes:
- **Token works on production**: uncheck Paper Mode (typical Plus plan token)
- **Token works on sandbox**: check Paper Mode (token from Sandbox tab)
- **Both fail with UDAPI100050**: regenerate token (it's been revoked)
- **Prod fails 100050 + sandbox fails 100060**: your app is registered in
  one tab but not the other; either reactivate segments (Algo Trading) or
  create a new app under the Sandbox tab

### Workflow: Refresh an expiring token

All Indian broker tokens expire at **06:00 IST every day** (SEBI rule).

1. Generate a fresh token from your broker's developer console
2. On the broker card, click **Refresh Token**
3. Inline form opens with a link to the broker's token page
4. Paste the new token → **Verify & Save**
5. Backend validates the new token by calling the broker, then replaces
   the encrypted value in-place — your API key stays put, no need to
   reconnect

### Workflow: Get real-time market data

The router picks the source automatically per symbol:

1. **Indian symbol** + connected Indian broker with active data plan → broker live
2. **US symbol** + connected Alpaca → broker live (Alpaca adapter still stub for now)
3. Otherwise → **Yahoo Finance** (15-min delayed for NSE/BSE)

You see the active source in two places:
- Header pill: "Data: Upstox LIVE" / "Data: Yahoo delayed"
- Per-chart badge: "Upstox LIVE" or "Yahoo · 15m delayed"

**Common gotcha**: if Dhan is connected but you see "Data API not subscribed",
Dhan split Trading API (free) from Data API (₹500/mo) in late 2024. Trading
works; quotes fall back to Yahoo until you subscribe at
web.dhan.co/api-subscription.

### Workflow: Generate fresh AI recommendations

The recommendations section auto-polls every 60s, but server-side dedup
returns cached recs for 30 minutes per symbol. To force regeneration:

- **Reload** button — re-fetches the current cached list (instant)
- **Regenerate** button — hits `?refresh=true`, agent ensemble re-runs for
  every symbol with fresh market data, dashboard updates with new recs
  (5-20s)

Per-symbol dedup ensures **one rec per symbol at a time**: when a new rec is
created, any prior PENDING rec for the same symbol is auto-cancelled.

### Workflow: Approve and place a trade

Safety-first flow — no one-click execution.

1. Click **Approve & Execute** on a recommendation card
2. Modal opens with:
   - **Broker** that will route (auto-picked from connected ones)
   - Side, symbol, qty (editable), order type, product, price
   - Estimated cost in ₹
   - **PAPER MODE** (blue) or **⚠ LIVE TRADING** (rose-red) banner
3. Backend pre-check **before any broker call**:
   - Kill switch off?
   - Order value ≤ per-trade cap?
   - Today's trade count < daily limit?
   - Today's realized loss < daily loss cap?
4. If any check fails → inline error explains exactly which limit blocked
   it (live orders only; paper bypasses these gates)
5. If all pass → real `place_order()` call to the broker SDK
6. Modal closes → toast confirms with broker's actual order ID
7. **Order history** below recommendations updates
8. Today's trade counter increments

### Workflow: Train the AI agent

1. Open `/training`
2. Pick **Symbol universe** (default: Indexes + NIFTY 50, 66 symbols)
3. Pick **Bar interval** (default: 30minute) and **Lookback days** (default: 90)
4. Click **Train now**
5. Live progress bar shows symbol-by-symbol completion
6. When done, per-symbol metrics table appears
7. Live `TechnicalAgent` hot-reloads the new params — next recommendation
   uses them (look for `params_source: "tuned"` in the rec's agent_outputs)

The pipeline pulls historical bars from Upstox if connected, **falls back
to Yahoo Finance otherwise** — so training works even without a broker.

### Workflow: Set risk limits + use the kill switch

1. Open `/performance`
2. Scroll to "Live-trading risk limits" panel
3. Set:
   - **Per-trade max (₹)**: max rupee value of any single position
   - **Daily loss cap (₹)**: when today's realized P&L hits −this, live
     trading is auto-blocked for the day
   - **Daily trade count cap**: max live orders per IST day
4. Click **Save limits**
5. Today's usage updates at the top of the panel

**Kill switch**:
- **Big rose-red button** in the top-right of `/performance` page
- One click → all live orders blocked instantly
- Re-engaging requires the explicit "Resume Live Trading" button
- When engaged, a pulsing red banner appears in the dashboard header too

**Recommended starting limits** for first month of live trading:
- Per-trade: ₹2,000–5,000
- Daily loss: ₹500–1,000
- Daily trades: 5

---

## Pre-live readiness — the checklist that matters

The `/performance` page computes this in real-time from your actual data.
All six must be green before you should consider going live:

| Check | What it means | Why |
|---|---|---|
| **≥ 100 graded signals** | Outcome tracker has scored 100+ recommendations against actual market moves | Smaller samples are noise, not edge |
| **Hit rate ≥ 55% (1h)** | Real signal accuracy, not backtest | Coin-flip = 50%; you need consistent edge |
| **Positive expectancy** | (p_win × avg_win) − (p_loss × avg_loss) > 0 | Even 55% hit rate is bad if winners are tiny and losers are huge |
| **Per-trade cap set** | Risk-limit row has `per_trade_max_inr > 0` | Caps catastrophic single-position loss |
| **Daily loss cap set** | `daily_max_loss_inr > 0` | Caps a bad day from snowballing |
| **Kill switch disengaged** | Master toggle is off | Self-explanatory |

These six conditions are necessary but **not sufficient**. Beyond them:
- Personally review at least 20 recent recommendations and confirm you
  would have made similar calls
- Have your broker app open on your phone while the bot is live, so you
  can see fills with your own eyes for the first few weeks
- Start with **one** broker, smallest possible position size, single
  approved trade per day

---

## Broker reference

| Broker | Region | Real SDK? | Live data? | Trading? | Token model | Cost |
|---|---|---|---|---|---|---|
| **Dhan** | India | ✅ `dhanhq` | Trading API works; Data API requires ₹500/mo plan | ✅ orders work | Long-lived JWT, daily expiry | Trading free, Data API ₹500/mo |
| **Upstox Pro** | India | ✅ `upstox-python-sdk` | ✅ free real-time | ✅ orders work | Daily OAuth, sandbox supported via Paper Mode | Free with account |
| **Zerodha Kite** | India | ✅ `kiteconnect` | ✅ (with Kite Connect sub) | ✅ orders work | Daily OAuth, ₹500 one-time + ₹2000/mo | ₹2000/mo |
| **Angel One** | India | ❌ Sandbox stub | ❌ | Simulated | n/a | n/a |
| **ICICI Breeze** | India | ❌ Sandbox stub | ❌ | Simulated | n/a | n/a |
| **IBKR** | Global | ❌ Sandbox stub | ❌ | Simulated | n/a | n/a |
| **Alpaca** | US | ❌ Sandbox stub | ❌ | Simulated | n/a | n/a |
| **Binance** | Global | ❌ Sandbox stub | ❌ | Simulated | n/a | n/a |

Sandbox stubs validate credential shape (regex check) and return
deterministic synthetic balances — no real API call. Useful for UI dev,
should not be used for real money.

---

## Market-data routing

```
                  ┌────────────────────────┐
   /watchlist     │                        │
   /quotes/{s}    │  market_data_service   │
   /intraday/{s}  │  *_routed methods      │
                  └──────────┬─────────────┘
                             │
                             ▼
                  ┌────────────────────────┐
                  │ pick_provider_for(sym) │   30s cache, data-plan probe
                  └──────────┬─────────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
       Indian + live broker?         US + live broker?
              │                             │
              ▼                             ▼
   ┌──────────────────┐         ┌──────────────────┐
   │ Dhan / Upstox /  │         │ Alpaca (when     │
   │ Zerodha adapter  │         │ wired)           │
   └──────────┬───────┘         └──────────┬───────┘
              │                             │
              └──────────────┬──────────────┘
                             │ on error / no broker
                             ▼
                ┌──────────────────────┐
                │  Yahoo Finance       │  ~15-min delayed (NSE)
                │  free, fallback only │  reliable
                └──────────────────────┘
```

Watchlist requests use **batched** broker quotes when possible (one Upstox/
Dhan API call for 7+ NSE symbols), then fans out missing symbols to Yahoo
in parallel.

---

## AI recommendation engine

**Four agents** orchestrated by a Decision agent:

| Agent | Status | What it actually does |
|---|---|---|
| **TechnicalAgent** | ✅ Real | RSI(14), SMA, trend from intraday bars. Loads per-symbol tuned params from `tuned_params.json` if training has been run. |
| **NewsAgent** | ⚠ **Stub** | Returns single fake headline per symbol with keyword sentiment |
| **MacroAgent** | ⚠ **Stub** | Hardcoded "expansionary, 4.2% inflation, 6.5% interest rate" |
| **RiskAgent** | ⚠ Naive | Stop-loss = `entry_price × 0.98`. No portfolio awareness |
| **DecisionAgent** | ✅ Real aggregation | Blends inputs; bullish/bearish/neutral side; sizing via simplified Kelly |

**Persistence + dedup**:
- Every rec saved to `trade_recommendations` table with a real DB id
- **Per-symbol asyncio.Lock** serialises creation — no race condition duplicates
- **Cancel-on-create**: creating a new rec for symbol X cancels prior PENDING recs for X
- **Self-heal**: `list_active` defensively dedups + cancels orphan duplicates
- 30-min cache: same symbol returns cached row within 30 min unless
  `?refresh=true`
- 4-hour expiry: recs auto-marked `CANCELLED`

**Rec card UI** shows:
- Confidence ring (0-100%, colour-graded)
- Symbol + side pill (BUY/SELL)
- R:R ratio, quantity, **RSI value**, **trend**
- Reasoning sentence (includes per-symbol params source)
- Expires-in countdown
- Approve / Reject buttons (disabled if rec lacks DB id)

---

## Order placement system

**No auto-execution.** Every order requires preview + explicit confirm.

```
Click Approve & Execute
        ↓
GET /api/v1/trades/{id}/preview
        ↓
Backend: pick_execution_broker(symbol)
        ↓
Returns: {broker, order details, is_paper, estimated_cost, warning}
        ↓
UI confirm modal: PAPER (blue) or LIVE (rose) banner
        ↓
User clicks confirm
        ↓
POST /api/v1/trades/{id}/approve
        ↓
Backend:
  if live: check_pre_trade(order_value, kill_switch, daily_caps)
    if blocked: return 400 with reason
  if broker.is_paper:
    store SIM-{uuid}, status="SIMULATED"
  else:
    broker.place_order(creds, OrderRequest)
    store trade with broker order_id, status="PLACED"
    record_trade_placed() ← increments today_trade_count
  rec.status = EXECUTED
        ↓
Toast notification, order history refreshes
```

**Execution broker selection** rules (in order):
1. CONNECTED status
2. `live=True` (real SDK adapter, not sandbox stub)
3. Has `place_order` method
4. Region matches symbol
5. Most recently connected wins

**Order types**: MARKET / LIMIT / SL / SL-M
**Products**: MIS (intraday) / CNC (delivery) / NRML (carry-forward F&O)

---

## Performance tracking

Without honest outcome data, you're flying blind. The outcome tracker
grades every recommendation against the actual market move:

**Grading rules**:
- After 1 hour: fetch current price, compute `(price_now − entry_price) / entry_price`
- BUY signal correct if move > +0.1%, SELL correct if move < −0.1%
- Sub-0.1% moves count as **neutral** (not correct or wrong either way)
- Repeat at 24h for longer-window accuracy

**When grading runs**:
- Lazily: any call to `/performance/stats` triggers it (rate-limited to once per minute)
- Manually: `/performance/grade-now` bypasses the rate limit

**Metrics computed**:
- **Hit rate 1h** — % of graded signals with correct direction
- **Hit rate 24h** — same for 24h window
- **Avg correct move** — mean |move%| on correct calls
- **Avg wrong move** — mean |move%| on wrong calls (sub-threshold moves excluded)
- **Expectancy / signal** — `(p_win × avg_win) − (p_loss × avg_loss)`. If
  negative, the bot is a net loser even with positive win rate.
- **Per-symbol breakdown** — hit rate and avg move per symbol
- **Last 20 recent** — graded signals timeline with verdict per row

---

## Risk limits (hard gates)

Single-row table (`risk_limits`) per user. Defaults set conservatively:

| Limit | Default | What it does |
|---|---|---|
| `per_trade_max_inr` | ₹10,000 | Max ₹ committed in a single position |
| `daily_max_loss_inr` | ₹2,000 | Live trading auto-halts when today's realized loss hits −this |
| `daily_max_trades` | 10 | Max live orders per IST day |
| `kill_switch` | false | Master OFF for all live trading |

**Pre-trade gate flow**:
- Called from `trade_service.process_approval` BEFORE any broker call
- Paper orders bypass all checks
- Live orders blocked with explicit reason if any limit would be breached
- Errors surface inline in the approve modal

**Daily counter reset** happens at 06:00 IST (matches SEBI broker-token
reset). `today_realized_pnl_inr` and `today_trade_count` are auto-zeroed.

**Kill switch endpoints**:
- `POST /api/v1/risk/kill` — engage
- `POST /api/v1/risk/resume` — disengage

Both visible in UI as one-click buttons on `/performance`. When engaged,
a pulsing red banner appears in the dashboard header.

---

## Training & learning pipeline

**Phase 1** — rule-based grid search + per-symbol parameter tuning.

### Universe presets

Pick from the dropdown on `/training`:

| Preset | Symbols | Time |
|---|---|---|
| Dashboard watchlist | 6 | ~5s |
| All major NSE/BSE indexes | 16 | ~30s |
| NIFTY 50 constituents | 50 | ~90s |
| **Indexes + NIFTY 50** | **66** | **~2 min** |
| Custom | typed | varies |

### Data flow

```
POST /api/v1/learning/train { preset, interval, lookback_days }
    ↓
Returns immediately with kick-off response; runs as background task.
    ↓
For each symbol:
    fetch_bars(symbol, interval, lookback_days)
        ↓
        Try Upstox HistoryApi (if connected, sandbox if is_paper)
        ↓ if Upstox returns nothing →
        Fall back to Yahoo Finance Chart API (auto-capped per interval)
        ↓
        Disk cache at backend/.cache/historical/
    ↓
    Strategy tournament — 55 combos across 6 strategies:
        rsi_sma     (RSI + SMA mean-reversion)   27 combos
        ema_cross   (fast/slow EMA trend)         6 combos
        macd        (MACD momentum)               2 combos
        bollinger   (band mean-reversion)         6 combos
        supertrend  (ATR trend follower)          6 combos
        breakout    (Donchian N-bar breakout)     3 combos
        ↓
        backtest(bars, StrategyParams(strategy=..., ...))
            dispatches the chosen strategy's signal fn (registry in strategies.py)
            replays bars one at a time (no look-ahead)
            entry/exit at next bar's OPEN
            0.05% fee per leg (0.1% round-trip)
            stops at -2%, target at +5%, max hold 24 bars
        ↓
        composite_score = 1.5×Sharpe + 0.05×return − 0.10×drawdown
        require ≥5 trades or score = -1000 (no lucky one-shots)
    ↓
    pick best (strategy, combo) across ALL strategies, record baseline comparison
    ↓
    progress_cb(done, total, current, last_result)  ← UI polls /status for this
    ↓
Save backend/app/learning/tuned_params.json
technical_agent_singleton.reload_tuned()  ← live agent updates
```

### Built-in honesty rails

- **Composite score down-weights return, up-weights consistency**
- **Min 5 trades** required
- **No look-ahead bias** — at bar i, signal sees only bars[0..i]
- **Realistic exits** — entry/exit at next bar's *open*
- **Fees baked in** — 0.05% per leg
- **Permanent amber banner** on UI

### Yahoo fallback caveats

Yahoo has interval-specific lookback caps that the fetcher auto-applies:

| Interval | Yahoo max lookback |
|---|---|
| 1m | 7 days |
| 5m, 15m, 30m | 60 days |
| 60m, 1h | 730 days (2 years) |
| 1d | ~25 years |
| 1wk | ~25 years |

If you request 30min for 90 days, Yahoo will only return 60 days. For
longer history at intraday resolution, switch to 1h interval.

---

## Token expiry & SEBI daily refresh

**Why all Indian broker tokens expire daily**: SEBI mandates daily
re-authentication for personal-use broker APIs. Every Indian broker
(Zerodha, Dhan, Upstox, Angel One, ICICI) follows this. **Deadline:
06:00 IST = 00:30 UTC**. No way to bypass for personal-use accounts.

**App behavior**:
1. On connect/refresh, computes `token_expires_at` = next 06:00 IST cutoff
2. UI shows live countdown per broker card (amber < 1h, red when expired)
3. When expired, status pill flips to "Token expired" automatically
4. Auto-startup migration backfills `token_expires_at` for old rows
5. Broker calls hitting an expired token return the broker's actual error
   (UDAPI100050 for Upstox, DH-901 for Dhan)

**Refresh flow** (< 30 seconds):
1. Get fresh token from broker's developer console
2. Click **Refresh Token** on the broker card
3. Paste → **Verify & Save**
4. Same broker_account row, new encrypted token, expiry reset

**Upstox special case**: tokens have an auto-fallback. If routing to the
configured URL fails with 404 (UDAPI100060) or invalid (UDAPI100050), the
adapter tries the *other* URL automatically and persists the working one.

---

## Security model

**Credentials**:
- API keys, secrets, tokens encrypted at rest with `Fernet` (AES-128-CBC + HMAC)
- Fernet key from `BROKER_ENC_KEY` env var
- If unset, dev seed used — set this in production
- API responses never include raw keys, only masked form (`110•••••945`)
- Secrets and access tokens never leave the server

**Broker selection**:
- Execution router never routes to `is_paper=True` accounts for real orders
- Sandbox-stub adapters (`live=False`) never receive real orders

**Approval flow**:
- Every order requires preview-then-confirm
- LIVE confirmation button is rose-red, labelled "Place LIVE order"
- Toast notifications distinguish LIVE vs SIMULATED

**Risk gates** (the hard ones):
- Kill switch can stop everything in < 1s
- Per-trade cap, daily loss cap, daily trade-count cap all blocking

**Database**:
- SQLite local file at `trading_bot.db` for development
- Override with `DATABASE_URL` env var for Postgres/MySQL
- Tables auto-create on startup; light-weight in-place migrations for column additions

---

## Configuration (env vars)

| Variable | Default | Purpose |
|---|---|---|
| `BROKER_ENC_KEY` | dev-only seed | Fernet key for encrypting broker creds. **Set this in production** |
| `DATABASE_URL` | `sqlite+aiosqlite:///./trading_bot.db` | Async SQLAlchemy connection string |
| `SQL_ECHO` | `0` | Set to `1` to log every SQL statement (don't in prod — perf killer) |

Frontend talks to backend at `http://127.0.0.1:8000` by default. Override
with `window.__API__` or edit the constant in `frontend/pages/index.js`.

---

## API reference

### Health
- `GET /health`

### Brokers
- `GET /api/v1/brokers/supported` — catalog with field schemas
- `GET /api/v1/brokers/accounts` — connected accounts
- `POST /api/v1/brokers/connect` — connect a new broker
- `POST /api/v1/brokers/accounts/{id}/refresh` — re-fetch balance
- `POST /api/v1/brokers/accounts/{id}/refresh-token` — rotate token in-place
- `DELETE /api/v1/brokers/accounts/{id}` — disconnect
- `POST /api/v1/brokers/upstox/probe` — diagnostic: probe token vs both Upstox URLs

### Market data
- `GET /api/v1/market-data/providers` — active broker + fallback info
- `GET /api/v1/market-data/quotes/{symbol}` — single quote (routed)
- `GET /api/v1/market-data/intraday/{symbol}` — intraday bars (routed)
- `GET /api/v1/market-data/watchlist` — batched quotes
- `GET /api/v1/market-data/news/{symbol}` — headlines (stub)

### Trades / Recommendations
- `GET /api/v1/trades/recommendations` — current PENDING recs (supports `?refresh=true`, `?symbols=...`)
- `GET /api/v1/trades/{id}/preview` — what would be placed if approved
- `POST /api/v1/trades/{id}/approve` — places real order via risk gate
- `POST /api/v1/trades/{id}/reject` — cancels
- `GET /api/v1/trades/history` — placed orders

### Training / Learning
- `GET /api/v1/learning/universes` — preset symbol lists
- `GET /api/v1/learning/status` — current state + progress (`done`, `total`, `current_symbol`)
- `GET /api/v1/learning/results` — last training run + persisted params
- `POST /api/v1/learning/train` — kick off background tune
- `GET /api/v1/learning/backtest/{symbol}` — one-shot diagnostic backtest

### Performance / Risk
- `GET /api/v1/performance/stats?days=7` — hit rate, expectancy, per-symbol, recent
- `POST /api/v1/performance/grade-now` — force-run the grader
- `GET /api/v1/risk/limits` — current gates + today's usage
- `POST /api/v1/risk/limits` — update any subset of gates
- `POST /api/v1/risk/kill` — engage kill switch
- `POST /api/v1/risk/resume` — disengage kill switch

### Other
- `POST /api/v1/chat/` — natural-language Q&A
- `POST /api/v1/auth/login` / `register` — stubs

---

## Troubleshooting

### `UDAPI100050` — Invalid token on Upstox
Two causes:
1. **Sandbox token + Paper Mode unchecked** — sandbox tokens only work
   against `api-sandbox.upstox.com`. The new auto-fallback now retries
   the other URL automatically, so this should self-correct.
2. **Token expired or revoked** — SEBI daily-expiry, or Upstox revoked
   a leaked token. Regenerate.

### `UDAPI100060` — Resource not found on Upstox sandbox
Your app is registered under one tab but not the other. Either:
- Create a new app in the Sandbox tab specifically, OR
- Use the Algo Trading app token on the production URL (uncheck Paper)

Use the **diagnostic probe** endpoint (`/api/v1/brokers/upstox/probe`) to
test which URL accepts the token before clicking Connect.

### Upstox "Account needs reactivation of segments"
Activate at least Equity Cash at account.upstox.com → Profile → Segments.
Takes 6h-3 business days. Sandbox tab tokens may still work meanwhile.

### Dhan "Data API plan not active"
Trading API is working (balance, orders). For real-time quotes, either:
- Subscribe at web.dhan.co/api-subscription (₹500/mo), OR
- Switch to Upstox (free real-time data with any account)

### Backend slow / timing out
Check `SQL_ECHO=1` isn't set — it logs every query, kills throughput under
poll load.

### Dashboard shows stale numbers
Hard refresh (Ctrl+Shift+R) to clear browser cache.

### Hydration errors in browser console
Should be fixed in current build. If they appear, hard refresh; if they
persist, paste the exact error.

### Approve / Reject buttons don't do anything
The rec lacks a DB id (legacy path). Click **Reload** or **Regenerate**.
New recs are always persisted.

### Order placement blocked with "Blocked by risk limits"
A pre-trade gate fired. The error names which one (kill switch, per-trade
cap, daily trade count, or daily loss limit). Open `/performance` and
adjust the limit OR resume from the kill switch.

### `/performance` shows "hit_rate_1h: 0.0%"
Either you have no graded signals yet (need ~1 hour since first rec for
1h grading) OR all your graded signals were sub-0.1% moves (counted as
not-correct but also not-wrong). Wait, generate more signals, or change
the window to see longer history.

### Duplicate recommendations
The dedup chain (per-symbol asyncio.Lock + cancel-on-create + list_active
self-heal) should prevent these. If you see them, hard-refresh and report
— could indicate a race in concurrent regeneration calls.

### Yahoo Finance calls slow / failing
Yahoo's free endpoint is rate-limited. Best fix: connect a broker with
data access (Upstox = free real-time).

---

## Roadmap to real trading

Six-stage path. Do **not** skip stages.

### Stage 1 — Paper trading (week 1-2)
- All brokers in Paper Mode
- Let the bot generate recommendations against live market data
- Don't touch the kill switch; let signals grade naturally
- Goal: accumulate 100+ graded signals on `/performance`

### Stage 2 — Honest signal review (week 2-3)
- Open `/performance`, check hit rate
- **< 48%**: agent is losing. Don't proceed. Tell me; we wire real News+Macro or move to Phase 2 ML
- **48-54%**: borderline. Run another week; results may improve
- **≥ 55% + positive expectancy**: green light to consider Stage 3

### Stage 3 — Pre-live readiness checklist (week 3)
- All 6 items on `/performance` must be green
- Personally review the last 20 recommendations — would you have made
  similar calls?
- Set conservative limits: per-trade ₹2,000, daily loss ₹500, daily
  trades 3
- Have your broker app open on phone

### Stage 4 — Single live broker, single trade (week 4)
- Uncheck Paper Mode on **ONE** broker (start with Upstox if you have it
  working; it's the cheapest to mess up)
- Approve **ONE** trade with full attention
- Watch the broker app for the fill; compare to what the dashboard says
- Compare actual slippage to the 0.1% I assumed in backtests
- If slippage > 0.3%, the strategy is marginal

### Stage 5 — Small position scaling (month 2)
- ₹2,000 per trade, max 5 trades/day, max ₹1,000 daily loss
- After 50 live orders, compare your `today_realized_pnl_inr` cumulative
  vs. what backtests predicted
- If reality is < 30% of backtest claim, the strategy doesn't transfer
  to live and needs Phase 2 ML work

### Stage 6 — Production scaling (month 3+)
- Only if Stages 4 and 5 showed honest profit
- Scale limits up gradually (10% per week max)
- Never disable the kill switch, daily loss cap, or per-trade cap "because the bot is hot"
- Keep `/performance` open during market hours; one click engages
  the kill switch if anything looks wrong

**Things that will tempt you to skip stages (don't)**:
- A run of 5 wins in a row — that's noise at any hit rate above 30%
- A particularly confident recommendation (high confidence ≠ high accuracy)
- A market move you "felt coming" — confirmation bias is the most
  expensive cognitive bias in trading
- A friend's success story (survivorship bias)

---

## Architecture reference

### Backend (FastAPI)

```
backend/app/
├── main.py                     # FastAPI app, startup migrations, router registration
├── api/v1/
│   ├── auth.py                 # Login/register stubs
│   ├── brokers.py              # /brokers/*, /upstox/probe
│   ├── chat.py                 # /chat — natural-language Q&A
│   ├── learning.py             # /learning/*  (train, status, results, universes)
│   ├── market_data.py          # /market-data/* (quotes, intraday, watchlist, providers)
│   ├── performance.py          # /performance/*, /risk/* (stats, limits, kill switch)
│   └── trades.py               # /trades/* (recommendations, preview, approve, history)
├── agents/
│   ├── base.py                 # TechnicalAgent (real), NewsAgent/MacroAgent/RiskAgent (stubs)
│   └── orchestrator.py         # DecisionAgent — blends agent outputs
├── db/
│   └── session.py              # AsyncSession factory
├── learning/
│   ├── historical.py           # Upstox HistoryApi + Yahoo fallback
│   ├── backtest.py             # Bar-by-bar replay with realistic fees
│   ├── tune.py                 # 27-combo grid search + composite scoring
│   ├── universe.py             # 4 presets (watchlist / indexes / nifty50 / both)
│   └── tuned_params.json       # Written after first train; live agent reads this
├── models/
│   └── database.py             # User, BrokerAccount, TradeRecommendation, Trade, RiskLimits, MarketRegime
├── schemas/
│   ├── broker.py               # BrokerConnectRequest
│   └── trade.py                # TradeRecommendationCreate/Read, TradeApproval
└── services/
    ├── ai_service.py           # Orchestrator entry (fetches quote + intraday, runs agents)
    ├── broker_adapters.py      # 8 adapters (3 real, 5 sandbox), OrderRequest/Quote types
    ├── broker_service.py       # CRUD for broker_accounts, Fernet encryption, token refresh
    ├── dhan_symbols.py         # Ticker → Dhan security_id resolver
    ├── execution_router.py     # pick_execution_broker — picks broker for order placement
    ├── market_data.py          # MarketDataService — routed + Yahoo fallback methods
    ├── market_providers.py     # pick_provider_for — picks broker for data (cached)
    ├── news_service.py         # News fetcher (stub)
    ├── notification_service.py # New-recommendation notifications
    ├── outcome_tracker.py      # Grades signals vs. actual moves, computes hit rate
    ├── risk_engine.py          # Position-sizing helpers
    ├── risk_limits.py          # Pre-trade gate, kill switch, daily caps
    ├── trade_service.py        # Recommendation lifecycle, preview, approval, order placement
    └── upstox_symbols.py       # Ticker → Upstox instrument_key resolver
```

### Frontend (Next.js 14)

```
frontend/
├── pages/
│   ├── _app.js          # ErrorBoundary wrapper
│   ├── index.js         # Main dashboard
│   ├── brokers.js       # Broker management
│   ├── training.js      # AI training (presets, progress bar, results)
│   └── performance.js   # Hit rate, expectancy, recent grades, risk limits, kill switch, checklist
├── components/
│   └── ErrorBoundary.js
├── lib/
│   └── useLivePoll.js   # Abortable, visibility-aware, content-hash dedup
└── styles/
    └── globals.css      # Tailwind + theme + glass-blur + animations
```

### Data flow at runtime

```
Browser → Next.js renders pages
           ↓
           useLivePoll hooks fire (visibility-aware, abortable)
           ↓
           REST → FastAPI backend
              ↓
              services/* business logic
                  ↓
                  broker_adapters.* (real SDKs to Dhan/Upstox/Zerodha)
                  or
                  market_data fallback to Yahoo Finance
              ↓
              SQLAlchemy → SQLite (broker_accounts, recommendations,
                                    trades, risk_limits)
           ↓
           JSON response
       ↓
       Components re-render (memoised, hash-dedup'd)
```

### Polling cadences

| Endpoint | Interval | Why |
|---|---|---|
| `/health` | once on load | Verify backend up |
| `/market-data/watchlist` | 20s | Live ticker, KPI breadth |
| `/market-data/intraday/{symbol}` | 20s | Chart |
| `/market-data/quotes/{symbol}` | 20s | Chart price overlay |
| `/trades/recommendations` | 60s | Recommendations (cached 30min server-side) |
| `/trades/history` | 60s | Order history |
| `/brokers/accounts` | 60s | Broker badges + KPI capital sum |
| `/risk/limits` | 60s | Kill-switch banner state |
| `/performance/stats` | 120s | Hit-rate header pill |
| `/market-data/providers` | 120s | Data-source pill |
| `/learning/status` | 2s (while training only) | Progress bar |

All polls pause when the tab is hidden (`visibilitychange`). All polls abort
their in-flight request before issuing a new one. Content-hash dedup on
`useLivePoll` keeps the same `data` reference if the JSON didn't change —
avoids React re-renders on unchanged polls.

---

## What's next (roadmap items the codebase is set up to receive)

1. **WebSocket tick streaming** — replace REST polling with broker WS bridges
   (Dhan/Upstox/Zerodha all support it). Sub-second updates.
2. **XGBoost classifier (Training Phase 2)** — engineered features
   (multi-horizon returns, MACD, ATR, volume profile), walk-forward
   validated, output is a probability used as a confidence multiplier in the
   live agent.
3. **Real News + Macro agents** — Finnhub for news, FRED + RBI for macro.
4. **Order status polling** — broker fill status pulled back so orders move
   from `PLACED` to `COMPLETE` automatically.
5. **Automatic stop-loss / take-profit follow-ups** — after entry fills,
   place SL and target orders automatically.
6. **Multi-currency P&L aggregation** — currently INR-only; needs FX rates
   when Alpaca (USD) is wired.
7. **Walk-forward backtest validation** — honest out-of-sample numbers
   instead of full-window train+test.
8. **Auto-retrain on rolling window** — weekly cron that re-tunes params.
9. **Notification system** — Slack/Telegram alerts on signals, fills,
   kill-switch events.

Any of these can be next. Say the word.

---

## Final disclaimer

This is an AI-assisted decision-support tool, not financial advice. Trading
involves risk of total capital loss. The honest measured win rate of any
retail rule-based trading system is rarely above 55% after costs. Past
performance (including any backtest in this app) does not guarantee future
results. The pre-live readiness checklist is a minimum bar, not a
sufficient condition for profitable trading. Always trade with capital you
can afford to lose entirely.
