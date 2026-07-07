# System Architecture

## Overview

The Multi-Agent Enterprise Trading Bot is built on a **dual-speed architecture** — a deterministic fast path for trade decisions and an LLM-powered slow path for strategic intelligence. Every event flows through a replayable, hash-chained journal, making backtest, shadow trading, and live trading the same code path.

## High-Level Architecture Diagram

```mermaid
graph TB
    subgraph L1["Layer 1 — Market Connectivity"]
        NSE["NSE/BSE Feeds<br/>Dhan · Upstox · Zerodha"]
        IBKR_FEED["IBKR API<br/>Global Equities"]
        YAHOO["Yahoo Finance<br/>Fallback · 15m delayed"]
        FH["Feed Handlers<br/>normalize · dual timestamps · routing"]
        NSE --> FH
        IBKR_FEED --> FH
        YAHOO --> FH
    end

    subgraph FAST["Fast Path (Deterministic)"]
        FF["Feature Fabric<br/>RSI · SMA · EMA · MACD · Bollinger<br/>SuperTrend · Donchian · incremental"]
        INF["Inference Service<br/>LightGBM GBDT + Strategy Tournament<br/>55 combos × 6 strategies"]
        RG["RISK GATEWAY<br/>Pre-trade checks · Kill switches<br/>Autonomy tiers · Exposure limits"]
        EX["Execution Engine<br/>SOR · IS/VWAP/POV/Adaptive<br/>Impact Model"]
        FH --> FF --> INF -->|"order intent"| RG -->|"approved order"| EX
    end

    EX --> B1["Dhan Gateway"]
    EX --> B2["Upstox Gateway"]
    EX --> B3["Zerodha Gateway"]
    EX --> B4["IBKR Gateway"]

    subgraph BUS["Layer 2 — Event Backbone"]
        JRN[("Hash-Chained Journal<br/>replay-deterministic<br/>WORM audit")]
        RP[("Redpanda Adapter<br/>durable bus · replay")]
    end
    FH -.tee.-> JRN
    INF -.-> JRN
    RG -.verdicts.-> JRN
    EX -.fills.-> JRN
    JRN -.-> RP

    subgraph SLOW["Slow Path — Intelligence Plane"]
        OPENBB["OpenBB Adapter<br/>100+ data providers<br/>equities · macro · news"]
        LLM["Specialist Analysts<br/>Fundamentals · Sentiment<br/>Technical · Macro"]
        GOV["Agent Governor<br/>lifecycle · budgets<br/>rate limits · auto-pause"]
        REG["Regime Classifier<br/>trend · chop · stress · crisis"]
        ALLOC["Capital Allocator<br/>Contextual Bandit<br/>Thompson Sampling"]
        OPENBB --> LLM
        LLM --> REG --> ALLOC
        GOV -.governs.-> LLM
    end
    JRN --> SLOW
    SLOW -->|"ParameterChangeProposal<br/>bounded · rate-limited · audited"| RG

    subgraph LEARN["Learning Plane"]
        DS[("Bar Store<br/>SQLite · QuestDB")]
        TCA_DB[("TCA Store<br/>IS decomposition · markouts")]
        TRAIN["Training Pipeline<br/>GBDT · Strategy Tournament<br/>Walk-forward · Purged CV"]
        SHADOW["Champion–Challenger<br/>Shadow → Canary → Promote"]
        DS --> TRAIN --> SHADOW -->|"signed model artifact"| INF
        TCA_DB --> TRAIN
    end

    subgraph OPS["Control Plane"]
        OMS[("SQLite/Postgres<br/>OMS · Positions · Trades")]
        DASH["Dashboard<br/>Next.js · Tier 2/3 Approvals"]
        SURV["Surveillance<br/>Wash · Spoof · Layering<br/>Detectors"]
        PERF["Performance Tracker<br/>Hit Rate · Expectancy<br/>Outcome Grading"]
    end
    JRN --> OMS
    JRN --> SURV
    DASH <--> RG
```

## Architecture Layers

### 1. Market Connectivity Layer (Layer 1)

Per-venue feed handlers normalize market data into a unified internal schema:

- **Indian Brokers (Live):** Dhan, Upstox, Zerodha — real SDK integrations with auto-routing
- **Global Brokers:** IBKR adapter built (Phase 4)
- **Fallback:** Yahoo Finance (15-min delayed for NSE/BSE, reliable)
- **Routing:** `pick_provider_for(symbol)` selects optimal source per symbol with 30s cache and data-plan probing

### 2. Event Backbone (Layer 2)

Two-tier event system:

- **Hash-Chained Journal:** Every event content-hashed and chained (`hash(eventₙ)` includes `hash(eventₙ₋₁)`) — tamper-evident, replay-deterministic. Verified by `scripts/verify_audit_chain.py`.
- **Redpanda Adapter:** Kafka-compatible durable bus for persistence, replay, and slow-path consumers. Enabled via `ETB_REDPANDA_BROKERS` env var.
- **In-Memory Bus:** Zero-dependency bus for development and testing.

### 3. Feature & Decision Plane (Layer 3 — Fast Path)

Deterministic, explainable decision-making:

- **Feature Fabric:** Incremental computation of technical indicators (RSI, SMA, EMA, MACD, Bollinger, SuperTrend, Donchian) — O(1)/O(k) updates per tick
- **Inference:** LightGBM GBDT classifiers/regressors per strategy with SHAP-style feature attributions
- **Strategy Tournament:** 55 parameter combinations across 6 strategies, composite-scored with honesty rails
- **Determinism Rule:** No wall-clock, no RNG, no I/O inside the decision function

### 4. Risk & Execution Plane

Security boundary and trade execution:

- **Risk Gateway:** Standalone module, sole credential holder. Pre-trade checks include position limits, exposure caps, fat-finger guards, rate limits, and self-trade prevention
- **Kill Switches:** 4-level escalation (K1: halt strategy → K2: cancel-all → K3: de-risk ladder → K4: drop sessions)
- **Dynamic Autonomy Tiers:** Tier 1 (auto) → Tier 2 (timeout-approval) → Tier 3 (human required)
- **Execution Engine:** Smart Order Router (SOR) + execution algorithms (IS/VWAP/POV/Adaptive) + pre-trade impact model
- **TCA:** Full implementation shortfall decomposition with markout analysis

### 5. Intelligence Plane (Slow Path)

LLM-powered strategic adaptation with multi-source data enrichment:

- **OpenBB Data Adapter:** Optional integration with the [OpenBB SDK](https://openbb.co) for enriched analyst context — equity fundamentals, company profiles, FRED macro indicators, world/company news from 100+ data providers. Graceful degradation if not installed.
- **Specialist Analyst Personas:** 4 domain-specific LLM analysts inspired by [TradingAgents](https://github.com/TauricResearch/TradingAgents) debate patterns:
  - **FundamentalsAnalyst** — earnings, valuation, balance sheet health
  - **SentimentAnalyst** — news flow velocity, narrative shifts, contrarian signals
  - **TechnicalAnalyst** — price action, support/resistance, momentum divergences
  - **MacroAnalyst** — central bank policy, inflation, yield curves, geopolitics
- **Agent Governor:** [Paperclip](https://github.com/paperclipai/paperclip)-inspired lifecycle management — register/pause/resume/terminate agents, per-agent token budget tracking, rate limiting, and automatic pause on error threshold breach.
- **Provider-Agnostic:** 12+ LLM backends (OpenAI, Anthropic, Gemini, Groq, Ollama, etc.)
- **Bounded Output:** Only `ParameterChangeProposal` events — regime labels, strategy weights, risk-limit tightening
- **Direction Asymmetry:** Tightening auto-applies; loosening requires human approval
- **TTL Expiry:** Every parameter change expires back to baseline unless renewed

### 6. Learning Plane

Offline-first learning with safe deployment:

- **Strategy Tournament:** Walk-forward backtesting with purged CV, min-trade gates, fee modeling
- **Contextual Bandit:** Thompson sampling for capital allocation across strategies
- **Champion–Challenger:** Shadow → canary → promotion pipeline with probabilistic Sharpe ratio gates
- **Offline RL:** IQL/CQL research for execution tactics (shadow mode only)

## Data Flow at Runtime

```
Browser → Next.js (pages: dashboard, brokers, training, performance, screener, monitor)
           ↓
           useLivePoll hooks (visibility-aware, abortable, content-hash dedup)
           ↓
           REST → FastAPI backend (app.main:app)
              ↓
              services/* business logic
                  ↓
                  broker_adapters.* (8 adapters: 3 real SDK, 5 sandbox)
                  ↓
                  market_data fallback to Yahoo Finance
              ↓
              SQLAlchemy → SQLite/Postgres (broker_accounts, recommendations, trades, risk_limits)
              ↓
              Event Journal (hash-chained, replay-deterministic)
           ↓
           JSON response
       ↓
       Components re-render (memoised, hash-dedup'd)
```

## Polling Cadences

| Endpoint | Interval | Purpose |
|----------|----------|---------|
| `/health` | once on load | Verify backend up |
| `/market-data/watchlist` | 20s | Live ticker, KPI breadth |
| `/market-data/intraday/{symbol}` | 20s | Chart data |
| `/trades/recommendations` | 60s | Recommendations (30min server cache) |
| `/trades/history` | 60s | Order history |
| `/brokers/accounts` | 60s | Broker badges + capital sum |
| `/risk/limits` | 60s | Kill-switch banner state |
| `/performance/stats` | 120s | Hit-rate header pill |
| `/learning/status` | 2s (training only) | Progress bar |

All polls pause when tab is hidden (`visibilitychange`) and abort in-flight requests before issuing new ones.

## Database Schema

| Table | Purpose |
|-------|---------|
| `users` | User accounts (auth stub) |
| `broker_accounts` | Encrypted broker credentials, connection status, token expiry |
| `trade_recommendations` | AI-generated recommendations with grading fields |
| `trades` | Placed orders with broker details |
| `risk_limits` | Per-user risk gates + kill switch state |
| `market_regimes` | Regime labels from classifier |

## Security Model

- **Credentials:** AES-128-CBC + HMAC encryption (Fernet) at rest
- **Broker Isolation:** Execution router never routes to paper accounts for real orders
- **API Masking:** Raw keys never returned in API responses
- **Risk Gates:** Kill switch < 1s to halt all trading
- **Approval Flow:** Every order requires preview → confirm (LIVE orders get rose-red confirmation)
