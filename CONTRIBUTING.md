# Contributing to Multi-Agent Enterprise Trading Bot

Thank you for your interest in contributing! This document provides guidelines for contributing to the project.

---

## 🚀 Getting Started

### Prerequisites

- Python 3.12+
- Node.js 18+
- Git
- (Optional) Docker & Docker Compose for infrastructure services

### Development Setup

```bash
# Clone the repository
git clone https://github.com/abrar99966/Multi-Agent-Enterprise-Trading-Bot.git
cd Multi-Agent-Enterprise-Trading-Bot

# Create virtual environment
python -m venv venv
.\venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Setup frontend
cd frontend && npm install && cd ..

# Run tests to verify setup
pytest
```

---

## 📁 Project Structure

Understanding the codebase layout:

| Directory | Purpose |
|-----------|---------|
| `backend/app/api/v1/` | REST endpoint handlers |
| `backend/app/agents/` | AI agent implementations |
| `backend/app/services/` | Core business logic |
| `backend/app/risk/` | Risk management gateway |
| `backend/app/execution/` | SOR & execution algorithms |
| `backend/app/slowpath/` | LLM-powered intelligence |
| `backend/app/bus/` | Event backbone |
| `frontend/pages/` | Next.js page components |
| `tests/` | Test suite |
| `docs/` | Documentation |

---

## 🔀 Development Workflow

### Branch Naming

- `feature/description` — New features
- `fix/description` — Bug fixes
- `docs/description` — Documentation updates
- `refactor/description` — Code refactoring
- `test/description` — Test additions/fixes

### Commit Messages

Use clear, descriptive commit messages:

```
feat: add IBKR adapter for global market execution
fix: resolve race condition in recommendation dedup
docs: update deployment guide for Kubernetes
test: add risk gateway pre-trade validation tests
refactor: extract feature fabric into incremental module
```

### Pull Request Process

1. Create a feature branch from `master`
2. Make your changes with tests
3. Ensure all tests pass: `pytest`
4. Update documentation if needed
5. Submit a PR with a clear description

---

## 🧪 Testing Guidelines

### Running Tests

```bash
# All tests
pytest

# Specific module
pytest tests/test_risk_gateway.py

# With verbose output
pytest -v

# With coverage
pytest --cov=backend/app
```

### Writing Tests

- Place tests in the `tests/` directory
- Name test files `test_*.py`
- Use the provided `conftest.py` fixtures
- Test both happy paths and error cases
- For broker-related tests, use the sandbox adapters

### Test Categories

| Category | Files | Focus |
|----------|-------|-------|
| Risk & Safety | `test_risk_gateway.py`, `test_tiers.py` | Pre-trade gates, kill switches |
| Execution | `test_paper_broker.py`, `test_phase4.py` | Order placement, SOR |
| Data Integrity | `test_audit_chain.py`, `test_journal.py` | Event journal, tamper detection |
| AI/ML | `test_e2e_model.py`, `test_features.py` | Signal generation, features |
| Infrastructure | `test_bus.py`, `test_marketdata.py` | Event bus, market data |

---

## 🏗️ Architecture Guidelines

### Critical Rules

1. **LLMs never in the order path** — They produce `ParameterChangeProposal` events only
2. **Risk is a boundary, not advice** — All orders must pass through the risk gateway
3. **Determinism in the fast path** — No wall-clock, no RNG, no I/O in decision functions
4. **Replay parity** — The same event log through the same binary must produce the same output
5. **Fail-safe by default** — If the slow path dies, trading continues on last-known-good parameters

### Adding a New Broker Adapter

1. Add the adapter class to `backend/app/services/broker_adapters.py`
2. Implement the standard interface: `connect`, `get_balance`, `place_order`, `get_positions`
3. Register in the broker catalog with field schemas
4. Add tests in `tests/`

### Adding a New LLM Provider

1. Add the provider to `backend/app/slowpath/providers.py`
2. Follow the existing pattern (OpenAI-compatible uses `httpx`)
3. Register the provider name in the configuration docs
4. No code changes needed for OpenAI-compatible providers — just set `ETB_LLM_PROVIDER=openai_compatible`

---

## 📝 Documentation

- Update `README.md` for user-facing changes
- Update `docs/` for architectural changes
- Update `USER_GUIDE.md` for new features or workflows
- Add inline docstrings for complex functions

---

## ⚠️ Important Notes

- **Never commit** `.env` files, broker credentials, or API keys
- **Always test** with paper/sandbox mode before any live trading changes
- **Risk-related changes** require extra review and testing
- The test suite must pass before any merge

---

## 📬 Questions?

Open an issue on GitHub or reach out to [@abrar99966](https://github.com/abrar99966).
