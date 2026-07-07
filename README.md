# Enterprise AI-Powered Trading Assistant

An intelligent, multi-agent AI trading system for Indian and International financial markets. This system acts as a sophisticated hedge-fund-like assistant, combining technical analysis, macro-economic awareness, and news sentiment with a mandatory human-in-the-loop approval workflow.

## 🏗️ Architecture

The system is built on a **Multi-Agent AI Architecture** where specialized agents collaborate to generate high-conviction trade recommendations.

### Core Agents:
- **Technical Analysis Agent**: Analyzes OHLCV, Order Flow, Options Chain, and Indicators.
- **News Intelligence Agent**: Processes global news, sentiment, and event impact.
- **Macro Economics Agent**: Monitors RBI/Fed, Inflation, GDP, and Interest Rates.
- **Risk Manager Agent**: Enforces stop-losses, daily limits, and drawdown protection.
- **Capital Allocation Agent**: Uses Kelly Criterion and VaR for smart position sizing.
- **Decision Agent**: Aggregates all insights into a final recommendation.
- **Learning Agent**: Implements Reinforcement Learning from past trade outcomes.

## 🚀 Tech Stack

- **Backend**: Python FastAPI, PostgreSQL (Relational), ChromaDB (Vector DB), Redis.
- **AI/ML**: PyTorch, HuggingFace, LangChain, XGBoost.
- **Frontend**: Next.js, Tailwind CSS, Shadcn/UI, Recharts.
- **Real-time**: WebSockets, Kafka/RabbitMQ.
- **Infrastructure**: Docker, Kubernetes.

## 📂 Project Structure

```text
├── ai/                 # AI/ML Models and RL Pipelines
│   ├── agents/         # Specialized AI Agent logic
│   ├── models/         # Pre-trained models and training scripts
│   └── rl/             # Reinforcement Learning environments
├── backend/            # FastAPI Backend
│   ├── app/
│   │   ├── api/        # REST Endpoints
│   │   ├── core/       # Config, Security, Logging
│   │   ├── db/         # Database sessions and migrations
│   │   ├── models/     # SQLAlchemy/SQLModel models
│   │   ├── schemas/    # Pydantic schemas
│   │   └── services/   # Business logic
├── frontend/           # Next.js Dashboard
├── infra/              # Docker, K8s, Terraform
├── scripts/            # Data ingestion and utility scripts
└── docs/               # Detailed documentation and diagrams
```

## 🛠️ Key Features

- **Mandatory Human Approval**: No trade is executed without explicit user confirmation.
- **Explainable AI**: Every recommendation comes with a detailed reasoning report.
- **Capital Preservation**: Priority-one logic to minimize drawdowns and avoid gambling.
- **Global Context**: Understands correlations between wars, oil prices, and sector rotation.
- **Self-Learning**: Continuously improves based on winning/losing trades and user feedback.

## 🚦 Getting Started

(Instructions for setup will be added as implementation progresses)

## ⚖️ Disclaimer

This is an AI-assisted trading system and not financial advice. Trading involves risk.
