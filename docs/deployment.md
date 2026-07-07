# Deployment Guide

This document covers deploying the Multi-Agent Enterprise Trading Bot from local development through to production environments.

---

## 🖥️ Local Development

### Quick Start (Windows)

```powershell
# One-command startup
.\start.ps1
```

This starts:
1. **FastAPI backend** → `http://127.0.0.1:8000` (API + institutional dashboard at `/dash`)
2. **Next.js frontend** → `http://127.0.0.1:3001` (classic trading desk)

### Manual Startup

```powershell
# Terminal 1 — Backend
.\venv\Scripts\activate
uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --log-level warning

# Terminal 2 — Frontend
cd frontend
npm run dev
```

### Local Services (Optional)

For features beyond SQLite-only mode, use the included Docker Compose stack:

```bash
cd infra
docker compose up -d
```

This starts:
| Service | Port | Purpose |
|---------|------|---------|
| PostgreSQL | 5432 | Production-grade OMS/positions store |
| Redpanda | 9092 | Durable event bus (Kafka-compatible) |
| Redpanda Console | 8080 | Web UI for event stream inspection |
| QuestDB | 9000/9009 | High-performance tick/bar store |

Configure the backend to use these services via `.env`:
```env
ETB_REDPANDA_BROKERS=localhost:9092
ETB_QUESTDB_ILP_HOST=localhost
DATABASE_URL=postgresql+asyncpg://etb:etb_local_dev@localhost:5432/etb
```

---

## 📦 Containerization (Docker)

### Backend Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY data/ ./data/

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
```

### Frontend Dockerfile

```dockerfile
FROM node:18-alpine

WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci --production

COPY frontend/ .
RUN npm run build

EXPOSE 3001
CMD ["npm", "start"]
```

### Full Stack — Docker Compose

The included `infra/docker-compose.yml` provides a production-ready stack:

```bash
cd infra
cp .env.example .env    # Edit with your settings
docker compose up -d
```

---

## ☸️ Orchestration (Kubernetes)

### Deployment Strategy

| Component | Type | Replicas | Notes |
|-----------|------|----------|-------|
| `api` | Deployment | 2+ | FastAPI backend, stateless |
| `frontend` | Deployment | 2+ | Next.js, behind CDN |
| `risk-gateway` | Deployment | 1 (active) + 1 (standby) | **Isolated namespace**, sole broker credential holder |
| PostgreSQL | StatefulSet | 1 primary + 1 replica | Managed service preferred |
| Redpanda | StatefulSet | 3 | NVMe storage required |
| QuestDB | StatefulSet | 1 primary + 1 replica | High IOPS storage |

### Critical Separation

The **risk gateway** runs in its own namespace with:
- Dedicated service account
- Network policies blocking strategy pods from broker endpoints
- Separate secret store (Vault scope or cloud KMS)
- Independent deploy pipeline

### Resource Requirements

| Component | CPU | Memory | Storage |
|-----------|-----|--------|---------|
| Backend API | 1 core | 1 GB | — |
| Frontend | 0.5 core | 512 MB | — |
| Risk Gateway | 2 cores (pinned) | 2 GB | — |
| PostgreSQL | 2 cores | 4 GB | 50 GB SSD |
| Redpanda | 4 cores | 8 GB | 100 GB NVMe |
| QuestDB | 2 cores | 4 GB | 200 GB SSD |

---

## ☁️ Cloud Infrastructure

### Recommended (AWS ap-south-1 / Mumbai)

| Component | Service | Why |
|-----------|---------|-----|
| Compute | EKS (K8s) + dedicated EC2 for risk gateway | Managed K8s, isolated risk |
| Database | RDS PostgreSQL | Managed backups, HA |
| Tick Store | QuestDB on dedicated instance | IOPS-sensitive |
| Event Bus | Redpanda on dedicated instances | Latency-sensitive |
| Secrets | AWS Secrets Manager + KMS | Broker credential encryption |
| Monitoring | CloudWatch + Prometheus + Grafana | System + trading metrics |
| Object Storage | S3 with Object Lock (WORM) | Audit log retention (7 years) |

### Clock Synchronization

All trading hosts must use PTP-disciplined clocks:
- AWS: Amazon Time Sync Service (ns-precision via PTP)
- All timestamps: UTC nanosecond
- MiFID II RTS 25 compliant (≤100µs divergence)

---

## 🔄 CI/CD Pipeline

```
┌─────────────┐     ┌──────────────┐     ┌────────────┐     ┌──────────┐
│  PR Created  │ ──→ │  Lint + Test  │ ──→ │   Build    │ ──→ │  Deploy  │
└─────────────┘     └──────────────┘     └────────────┘     └──────────┘
                     • pytest (30+ tests)   • Docker images    • Paper env first
                     • flake8/ruff          • Push to ECR      • Prod via GitOps
                     • Security scan        • Sign artifacts   • Risk gateway
                     • Audit chain verify                        separately
```

### Pipeline Steps

1. **Lint & Test:** Run `pytest`, type checking, and code formatting
2. **Security Scan:** Check for vulnerabilities in dependencies
3. **Build:** Generate Docker images, push to container registry
4. **Deploy to Paper:** Full stack against live data, simulated fills (always-on environment)
5. **Soak in Paper:** Minimum 1 full trading day unattended
6. **Deploy to Production:** ArgoCD / Helm chart update, canary rollout
7. **Risk Gateway:** Independent pipeline, separate approval, four-eyes review

---

## 🛡️ Production Best Practices

### Security
- [ ] Set `BROKER_ENC_KEY` to a strong Fernet key (not the dev seed)
- [ ] Broker credentials scoped to risk gateway namespace only
- [ ] Strategy pods' egress to broker endpoints firewalled
- [ ] Signed model artifacts verified at load time
- [ ] Four-eyes approval for risk-limit increases
- [ ] No human shell on trading hosts (break-glass only)

### Reliability
- [ ] Risk gateway hot-standby with journal replication
- [ ] Cancel-on-disconnect configured at broker level
- [ ] Position reconciliation running (30s cadence)
- [ ] Kill switch tested monthly in paper environment
- [ ] Audit chain integrity verified daily

### Monitoring
- [ ] Prometheus metrics for all trading-critical paths
- [ ] HDR histograms for latency (µs-resolution buckets)
- [ ] Order lifecycle tracing (signal → intent → risk → fill)
- [ ] Alerting on: feed staleness, position mismatch, slippage anomaly
- [ ] TCA reports generated for every fill

### Data Retention
- [ ] Event journal: WORM storage, 7-year retention (SEBI compliance)
- [ ] Trade history: indefinite
- [ ] Market data bars: indefinite (QuestDB)
- [ ] Logs: 90 days hot, 1 year cold

---

## 🌐 Environment Configuration

See [`infra/.env.example`](../infra/.env.example) for the complete configuration reference.

### Critical Production Variables

```env
# Security — MUST change from defaults
BROKER_ENC_KEY=<your-fernet-key>

# Database — use Postgres in production
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/etb

# Event bus — enable durable persistence
ETB_REDPANDA_BROKERS=redpanda-0:9092,redpanda-1:9092,redpanda-2:9092

# Tick store — enable high-performance bar storage
ETB_QUESTDB_ILP_HOST=questdb-host

# LLM provider — configure slow-path intelligence
ETB_LLM_PROVIDER=openai
ETB_LLM_MODEL=gpt-4o-mini
ETB_LLM_API_KEY=<your-key>
```
