# Multi-Agent Enterprise Trading Bot

[![Python 3.12+](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Next.js 14](https://img.shields.io/badge/Next.js-14-000000?logo=next.js&logoColor=white)](https://nextjs.org)
[![LightGBM](https://img.shields.io/badge/LightGBM-4.6+-2CA02C)](https://lightgbm.readthedocs.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An institutional-grade, multi-agent AI trading platform for Indian (NSE/BSE) and international equity markets. The system combines deterministic GBDT-based signal generation, multi-broker execution with smart order routing, a 4-tier risk gateway, and LLM-powered slow-path intelligence — all enforced through a mandatory human-in-the-loop approval workflow with dynamic autonomy tiers.

> **⚠️ Disclaimer:** This is an AI-assisted trading system and not financial advice. Trading involves risk of total capital loss. Past performance does not guarantee future results.

---

## 🏗️ Architecture Overview

The platform is built on a **dual-speed architecture** — a deterministic fast path for trade decisions and an LLM-powered slow path for strategic intelligence:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    FAST PATH (Deterministic)                        │
│  Market Data → Feature Fabric → GBDT Inference → Risk Gateway → Execution │
│  (sub-ms target internally, 50-300ms via retail broker APIs)       │
├─────────────────────────────────────────────────────────────────────┤
│                    EVENT BACKBONE                                    │
│  Journal (hash-chained, replay-deterministic) + Redpanda (durable) │
├─────────────────────────────────────────────────────────────────────┤
│                    SLOW PATH (Intelligence)                         │
│  LLM Analysts → Regime Classifier → Capital Allocator              │
│  (seconds-to-minutes cadence, bounded parameter proposals only)    │
└─────────────────────────────────────────────────────────────────────┘
```

### Core Design Principles

- **LLMs never sit in the order path** — they adjust bounded parameters, never individual orders
- **Risk is a boundary, not advice** — standalone gateway holds all broker credentials
- **Backtest/live parity** — same code path, replay from the same event journal
- **Fail-safe by construction** — slow path dies → trading continues on last-known-good parameters

---

## 🧠 Multi-Agent System

### Fast-Path Agents (Deterministic)
| Agent | Status | Function |
|-------|--------|----------|
| **Technical Agent** | ✅ Production | RSI, SMA, EMA, MACD, Bollinger, SuperTrend, Donchian — loads per-symbol tuned params from strategy tournament |
| **Decision Agent** | ✅ Production | Blends agent outputs; generates BUY/SELL/HOLD with confidence scoring and Kelly-based sizing |
| **Risk Agent** | ✅ Production | Pre-trade gate enforcement: position limits, exposure caps, fat-finger guards, kill switches |

### Slow-Path Agents (LLM-Powered, Provider-Agnostic)
| Agent | Status | Function |
|-------|--------|----------|
| **Fundamentals Analyst** | ✅ Wired | Earnings quality, valuation metrics, balance sheet analysis |
| **Sentiment Analyst** | ✅ Wired | News flow velocity, narrative shifts, contrarian indicators |
| **Technical Analyst** | ✅ Wired | Price action, support/resistance, momentum divergences |
| **Macro Analyst** | ✅ Wired | Central bank policy, inflation, yield curves, geopolitics |
| **Regime Classifier** | ✅ Wired | Fuses statistical + LLM signals into regime labels (trend/chop/stress/crisis) |
| **Capital Allocator** | ✅ Wired | Contextual bandit (Thompson sampling) for strategy weight allocation |
| **Agent Governor** | ✅ Wired | Lifecycle management, resource tracking, auto-pause on errors |

> **Analyst personas** are inspired by [TradingAgents](https://github.com/TauricResearch/TradingAgents) multi-agent debate patterns, adapted for bounded parameter output. **Agent governance** draws from [Paperclip](https://github.com/paperclipai/paperclip) orchestration patterns.

> Slow-path LLM provider is fully configurable — supports OpenAI, Anthropic, Gemini, Groq, Ollama, LM Studio, and any OpenAI-compatible endpoint. Switch with `ETB_LLM_PROVIDER` env var — zero code changes.

---

## 🚀 Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend API** | Python 3.12+, FastAPI, Uvicorn, SQLAlchemy (async) |
| **AI/ML** | LightGBM (GBDT), SciPy, NumPy, multi-provider LLM integration |
| **Data Enrichment** | OpenBB SDK (optional) — 100+ financial data providers for slow-path intelligence |
| **Frontend** | Next.js 14, React 18, Tailwind CSS |
| **Database** | SQLite (dev) / PostgreSQL (prod), QuestDB (tick store), hash-chained event journal |
| **Event Bus** | In-memory journal (Phase 0) → Redpanda adapter ready |
| **Execution** | Smart Order Router, IS/VWAP/POV/Adaptive algos, TCA engine |
| **Infrastructure** | Docker Compose, Kubernetes manifests |

---

## 📂 Project Structure

```
├── backend/                    # FastAPI Backend
│   └── app/
│       ├── main.py             # App entry, startup migrations, router registration
│       ├── api/v1/             # REST endpoints (11 route modules)
│       │   ├── trades.py       # Recommendations, preview, approve, history
│       │   ├── brokers.py      # Broker CRUD, connect, probe, token refresh
│       │   ├── market_data.py  # Quotes, intraday, watchlist, providers
│       │   ├── learning.py     # Training, screener, backtest, universes
│       │   ├── performance.py  # Hit rate, risk limits, kill switch
│       │   ├── execution.py    # Execution algos, SOR, surveillance
│       │   ├── allocator.py    # Capital allocation, bandit, RL execution
│       │   ├── dashboards.py   # Layer 6 read-side analytics dashboards
│       │   ├── chat.py         # Natural-language Q&A + analyze intent
│       │   └── slowpath.py    # Slow-path REST API (analyze, agents, governance)
│       ├── agents/             # Multi-agent AI system
│       │   ├── base.py         # Technical, News, Macro, Risk agents
│       │   └── orchestrator.py # Decision agent — blends all agent outputs
│       ├── allocator/          # Capital allocation & learning
│       │   ├── bandit.py       # Contextual bandit (Thompson sampling)
│       │   ├── gates.py        # Champion-challenger promotion gates
│       │   └── rl_execution.py # Offline RL execution research
│       ├── audit/              # WORM audit chain
│       ├── bus/                # Event backbone
│       │   ├── journal.py      # Hash-chained, replay-deterministic log
│       │   ├── memory.py       # In-memory bus for testing
│       │   └── redpanda.py     # Redpanda/Kafka adapter
│       ├── execution/          # Execution stack
│       │   ├── algos.py        # IS, VWAP, POV, Adaptive execution algos
│       │   ├── impact_model.py # Pre-trade market impact estimation
│       │   └── sor.py          # Smart Order Router (multi-broker)
│       ├── features/           # Feature engineering
│       │   └── fabric.py       # Incremental feature fabric
│       ├── hotpath/            # Low-latency components
│       │   ├── profiler.py     # Hot-path latency profiler
│       │   └── dma_evaluator.py# DMA economics framework
│       ├── learning/           # Training & backtesting
│       │   ├── strategies.py   # 6 strategy types, 55 param combos
│       │   ├── backtest.py     # Bar-by-bar replay with realistic fees
│       │   ├── tune.py         # Grid search + composite scoring
│       │   ├── historical.py   # Multi-source bar fetcher
│       │   ├── screener.py     # Symbol screener
│       │   └── universe.py     # 4+ preset symbol universes
│       ├── marketdata/         # Market data layer
│       │   ├── store.py        # Durable bar store
│       │   ├── bridge.py       # Feed bridge
│       │   ├── replay.py       # Replay engine for backtests
│       │   └── questdb.py      # QuestDB integration
│       ├── risk/               # Risk management
│       │   ├── gateway.py      # Standalone risk gateway
│       │   ├── limits.py       # Hard limits engine
│       │   ├── tiers.py        # Dynamic autonomy tiers
│       │   └── approver.py     # Multi-tier approval workflow
│       ├── services/           # Business logic (19 modules)
│       │   ├── broker_adapters.py    # 8 broker adapters (3 real, 5 sandbox)
│       │   ├── broker_service.py     # Fernet-encrypted credential CRUD
│       │   ├── trade_service.py      # Recommendation lifecycle
│       │   ├── execution_router.py   # Broker selection for execution
│       │   ├── market_data.py        # Routed market data service
│       │   ├── openbb_adapter.py     # OpenBB SDK data enrichment (optional)
│       │   ├── outcome_tracker.py    # Signal grading vs actual market moves
│       │   └── risk_limits.py        # Pre-trade gates, kill switch
│       ├── slowpath/           # LLM-powered intelligence
│       │   ├── analyst.py      # LLM analyst agent (base)
│       │   ├── personas.py     # 4 specialist analysts (Fundamentals/Sentiment/Technical/Macro)
│       │   ├── governance.py   # Agent lifecycle governor (register/pause/resume/terminate)
│       │   ├── providers.py    # 12+ LLM provider adapters
│       │   ├── orchestrator.py # SlowPathOrchestrator — enriches + dispatches
│       │   ├── regime.py       # Regime classification
│       │   └── params.py       # Bounded parameter proposals
│       ├── surveillance/       # Compliance & surveillance
│       │   └── detectors.py    # Wash/spoof/layering detectors
│       └── tca/                # Transaction Cost Analysis
│           ├── engine.py       # Full TCA pipeline
│           ├── shortfall.py    # Implementation shortfall decomposition
│           └── store.py        # TCA data store
├── frontend/                   # Next.js 14 Dashboard
│   ├── pages/
│   │   ├── index.js            # Main trading desk (75KB)
│   │   ├── brokers.js          # Broker management
│   │   ├── training.js         # AI training with live progress
│   │   ├── performance.js      # Hit rate, risk limits, kill switch
│   │   ├── screener.js         # Symbol screener
│   │   └── monitor.js          # System monitor
│   ├── components/             # Reusable UI components
│   ├── lib/                    # Custom hooks (useLivePoll, useCandles)
│   └── styles/                 # Tailwind + theme + animations
├── ai/                         # AI/ML research
│   └── rl/                     # Reinforcement learning environments
├── scripts/                    # Utility & automation scripts
│   ├── daily_train.ps1         # Automated daily training
│   ├── run_paper_session.py    # Paper trading session runner
│   ├── run_real_backtest.py    # Historical backtest runner
│   ├── backtest_report.py      # Performance report generator
│   ├── train_model.py          # Model training script
│   └── verify_audit_chain.py   # Audit chain integrity checker
├── tests/                      # Comprehensive test suite (261 tests, 32 files)
├── infra/                      # Infrastructure
│   ├── docker-compose.yml      # Full stack (Postgres, Redpanda, QuestDB)
│   └── .env.example            # Environment variable template
├── docs/                       # Detailed documentation
│   ├── TARGET_ARCHITECTURE.md  # Institutional target-state design (50KB)
│   ├── PHASE0_REVIEW.md - PHASE5_IMPLEMENTATION.md  # Phase docs
│   └── architecture.md         # System architecture diagram
├── start.ps1                   # One-command startup script
├── requirements.txt            # Python dependencies
└── USER_GUIDE.md               # Comprehensive user guide (49KB)
```

---

## 🔌 Supported Brokers

| Broker | Region | Live SDK | Real-Time Data | Order Execution | Cost |
|--------|--------|----------|---------------|-----------------|------|
| **Dhan** | 🇮🇳 India | ✅ `dhanhq` | Trading API ✅ / Data API ₹500/mo | ✅ Live orders | Trading free |
| **Upstox Pro** | 🇮🇳 India | ✅ `upstox-python-sdk` | ✅ Free real-time | ✅ Live orders | Free |
| **Zerodha Kite** | 🇮🇳 India | ✅ `kiteconnect` | ✅ With subscription | ✅ Live orders | ₹2000/mo |
| **IBKR** | 🌍 Global | 🔧 Adapter built | Planned | Planned | Per-use |
| Angel One | 🇮🇳 India | 📋 Sandbox | — | Simulated | — |
| Alpaca | 🇺🇸 US | 📋 Sandbox | — | Simulated | — |
| Binance | 🌍 Global | 📋 Sandbox | — | Simulated | — |
| ICICI Breeze | 🇮🇳 India | 📋 Sandbox | — | Simulated | — |

---

## 🛡️ Risk Management

The platform implements a **4-level kill switch** escalation and dynamic autonomy tiers:

| Tier | Mode | When Used |
|------|------|-----------|
| **Tier 1** | Fully autonomous | Liquid instruments, small size, all limits green |
| **Tier 2** | Auto-execute after timeout | Medium risk, or earnings window |
| **Tier 3** | Human approval required | New strategies, large size, crisis regime |
| **Kill Switch** | All trading halted | Emergency, or manual override |

**Pre-trade gates** (enforced at service layer, not UI):
- Per-trade position cap (₹)
- Daily loss cap with auto-halt
- Daily trade count cap
- Master kill switch
- Fat-finger guards and price collars

---

## 🚦 Quick Start

### Prerequisites
- Python 3.12+
- Node.js 18+
- Git

### 1. Clone & Setup

```bash
git clone https://github.com/abrar99966/Multi-Agent-Enterprise-Trading-Bot.git
cd Multi-Agent-Enterprise-Trading-Bot

# Create virtual environment
python -m venv venv

# Activate (Windows)
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
# Copy the example env file
copy infra\.env.example .env

# Edit .env with your settings (broker keys, LLM provider, etc.)
```

### 3. Setup Frontend

```bash
cd frontend
npm install
cd ..
```

### 4. Start Everything

```powershell
# One-command startup (Windows PowerShell)
.\start.ps1

# Or manually:
# Backend
.\venv\Scripts\uvicorn.exe app.main:app --app-dir backend --port 8000

# Frontend (separate terminal)
cd frontend && npm run dev
```

### 5. Access the Application

| URL | Description |
|-----|-------------|
| `http://127.0.0.1:8000/dash` | Institutional dashboard (new) |
| `http://127.0.0.1:3001` | Classic trading desk |
| `http://127.0.0.1:8000/docs` | API documentation (Swagger) |
| `http://127.0.0.1:8000/api/v1/slowpath/dashboard` | Agent governance dashboard |
| `http://127.0.0.1:8000/health` | Health check endpoint |

---

## 🧪 Testing

The project includes 30+ test files covering all major subsystems:

```bash
# Run all tests
pytest

# Run specific test categories
pytest tests/test_risk_gateway.py      # Risk gateway tests
pytest tests/test_paper_broker.py      # Paper trading tests
pytest tests/test_audit_chain.py       # Audit chain integrity
pytest tests/test_journal.py           # Event journal tests
pytest tests/test_tca.py               # TCA pipeline tests
pytest tests/test_bus.py               # Event bus tests
pytest tests/test_phase4.py            # Multi-broker execution
pytest tests/test_phase5.py            # Learning & allocation
```

---

## 📊 Training & Backtesting

The platform includes a **strategy tournament** system that evaluates 55 parameter combinations across 6 strategy types:

| Strategy | Combinations | Description |
|----------|-------------|-------------|
| `rsi_sma` | 27 | RSI + SMA mean-reversion |
| `ema_cross` | 6 | Fast/slow EMA trend following |
| `macd` | 2 | MACD momentum |
| `bollinger` | 6 | Bollinger band mean-reversion |
| `supertrend` | 6 | ATR-based trend following |
| `breakout` | 3 | Donchian N-bar breakout |

**Training features:**
- Walk-forward backtesting with no look-ahead bias
- Realistic fee modeling (0.05% per leg)
- Composite scoring: `1.5×Sharpe + 0.05×Return − 0.10×Drawdown`
- Minimum 5-trade requirement (no lucky one-shots)
- Live progress bar with per-symbol results
- Hot-reload of tuned parameters to live agents

---

## 🔧 Configuration

### Key Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BROKER_ENC_KEY` | dev seed | Fernet key for encrypting broker credentials |
| `DATABASE_URL` | SQLite | Async SQLAlchemy connection string |
| `ETB_LLM_PROVIDER` | `stub` | LLM provider (openai/anthropic/gemini/groq/ollama/...) |
| `ETB_LLM_MODEL` | — | Model name for the selected provider |
| `ETB_LLM_API_KEY` | — | API key for the LLM provider |
| `ETB_REDPANDA_BROKERS` | — | Redpanda/Kafka brokers (enables durable bus) |
| `ETB_QUESTDB_ILP_HOST` | — | QuestDB host for tick storage |
| `ETB_BAR_DB_PATH` | `data/market_data.db` | SQLite bar store path |
| `ETB_JOURNAL_DIR` | `data/journal` | Event journal directory |

See [`infra/.env.example`](infra/.env.example) for the complete list.

---

## 🗺️ Development Phases

The platform was built following a phased approach, prioritizing **risk reduction first, speed last**:

| Phase | Focus | Status |
|-------|-------|--------|
| **Phase 0** | Truth & Safety Foundations — event journal, audit chain, replay determinism | ✅ Complete |
| **Phase 1** | Deterministic Decisions — GBDT fast path, risk gateway, pre-trade gates | ✅ Complete |
| **Phase 2** | Measurement & Parity — TCA pipeline, IS decomposition, backtest=replay | ✅ Complete |
| **Phase 3** | Slow Path & Intelligence — LLM analysts, regime classifier, parameter proposals | ✅ Complete |
| **Phase 4** | Multi-Broker & Execution — IBKR adapter, SOR, execution algos, surveillance | ✅ Complete |
| **Phase 5** | Learning & Speed — bandit allocator, offline RL, hot-path profiler, DMA evaluator | ✅ Complete |

See [`docs/TARGET_ARCHITECTURE.md`](docs/TARGET_ARCHITECTURE.md) for the full architectural design document.

---

## 📖 Documentation

| Document | Description |
|----------|-------------|
| [`USER_GUIDE.md`](USER_GUIDE.md) | Comprehensive page-by-page user guide (49KB) |
| [`docs/TARGET_ARCHITECTURE.md`](docs/TARGET_ARCHITECTURE.md) | Institutional target-state architecture (51KB) |
| [`docs/PHASE0_REVIEW.md`](docs/PHASE0_REVIEW.md) | Phase 0 — Truth & Safety review |
| [`docs/PHASE1.md`](docs/PHASE1.md) | Phase 1 — Deterministic decisions |
| [`docs/PHASE2.md`](docs/PHASE2.md) | Phase 2 — Measurement & parity |
| [`docs/PHASE3.md`](docs/PHASE3.md) | Phase 3 — Slow path & feeds |
| [`docs/PHASE4_IMPLEMENTATION.md`](docs/PHASE4_IMPLEMENTATION.md) | Phase 4 — Multi-broker & execution |
| [`docs/PHASE5_IMPLEMENTATION.md`](docs/PHASE5_IMPLEMENTATION.md) | Phase 5 — Learning & speed |
| [`docs/LAYER6_DASHBOARDS.md`](docs/LAYER6_DASHBOARDS.md) | Layer 6 — Read-side dashboards |
| [`docs/PUBLIC_API_ENRICHMENT.md`](docs/PUBLIC_API_ENRICHMENT.md) | Public-API enrichment — macro regime, symbology, market-data failover |
| [`docs/API.md`](docs/API.md) | REST API reference |
| [`docs/architecture.md`](docs/architecture.md) | System architecture diagram |
| [`docs/deployment.md`](docs/deployment.md) | Deployment strategy |

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## ⚖️ License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

## 📬 Contact

**Abrar Ahmed** — [@abrar99966](https://github.com/abrar99966)

Project Link: [https://github.com/abrar99966/Multi-Agent-Enterprise-Trading-Bot](https://github.com/abrar99966/Multi-Agent-Enterprise-Trading-Bot)
