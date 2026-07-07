# API Reference

Complete REST API reference for the Multi-Agent Enterprise Trading Bot.

**Base URL:** `http://127.0.0.1:8000`
**Interactive Docs:** `http://127.0.0.1:8000/docs` (Swagger UI)

---

## Health

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Welcome message + status |
| `GET` | `/health` | Health check |
| `GET` | `/dash` | Institutional dashboard (HTML) |

---

## Brokers

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/brokers/supported` | Broker catalog with field schemas |
| `GET` | `/api/v1/brokers/accounts` | List connected broker accounts |
| `POST` | `/api/v1/brokers/connect` | Connect a new broker |
| `POST` | `/api/v1/brokers/accounts/{id}/refresh` | Re-fetch balance from broker |
| `POST` | `/api/v1/brokers/accounts/{id}/refresh-token` | Rotate access token in-place |
| `DELETE` | `/api/v1/brokers/accounts/{id}` | Disconnect broker account |
| `POST` | `/api/v1/brokers/upstox/probe` | Diagnostic: probe Upstox token vs both URLs |

### Connect Request Body
```json
{
  "broker_name": "upstox",
  "api_key": "your-api-key",
  "access_token": "your-access-token",
  "is_paper": false
}
```

---

## Market Data

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/market-data/providers` | Active data sources + fallback info |
| `GET` | `/api/v1/market-data/quotes/{symbol}` | Single quote (auto-routed to best source) |
| `GET` | `/api/v1/market-data/intraday/{symbol}` | Intraday OHLCV bars |
| `GET` | `/api/v1/market-data/watchlist` | Batched quotes for all watchlist symbols |
| `GET` | `/api/v1/market-data/news/{symbol}` | News headlines for symbol |

### Data Source Routing
The market data service automatically routes requests:
1. **Indian symbol + live broker** → Broker API (real-time)
2. **US symbol + live broker** → Broker API
3. **Fallback** → Yahoo Finance (15-min delayed)

---

## Trades & Recommendations

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/trades/recommendations` | Current PENDING recommendations |
| `GET` | `/api/v1/trades/recommendations?refresh=true` | Force regeneration with fresh data |
| `GET` | `/api/v1/trades/recommendations?symbols=RELIANCE,TCS` | Filter by symbols |
| `GET` | `/api/v1/trades/{id}/preview` | Preview order details before placement |
| `POST` | `/api/v1/trades/{id}/approve` | Approve and place order (through risk gate) |
| `POST` | `/api/v1/trades/{id}/reject` | Reject recommendation |
| `GET` | `/api/v1/trades/history` | Placed order history |

### Recommendation Response
```json
{
  "id": 42,
  "symbol": "RELIANCE",
  "side": "BUY",
  "confidence": 0.72,
  "entry_price": 2850.50,
  "stop_loss": 2793.49,
  "target": 2993.03,
  "quantity": 10,
  "rr_ratio": 2.5,
  "reasoning": "RSI(14)=38.2 oversold with uptrend SMA crossover...",
  "params_source": "tuned",
  "strategy": "rsi_sma",
  "expires_at": "2026-07-08T04:00:00Z",
  "status": "PENDING"
}
```

---

## Training & Learning

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/learning/universes` | Available symbol universe presets |
| `POST` | `/api/v1/learning/train` | Kick off background training |
| `GET` | `/api/v1/learning/status` | Training progress (`done`, `total`, `current_symbol`) |
| `GET` | `/api/v1/learning/results` | Last training run results + tuned params |
| `GET` | `/api/v1/learning/backtest/{symbol}` | One-shot diagnostic backtest |

### Train Request Body
```json
{
  "preset": "indexes_nifty50",
  "interval": "30minute",
  "lookback_days": 90
}
```

### Universe Presets
| Preset | Symbols | Typical Time |
|--------|---------|-------------|
| `watchlist` | 6 | ~5s |
| `indexes` | 16 | ~30s |
| `nifty50` | 50 | ~90s |
| `indexes_nifty50` | 66 | ~2 min |

---

## Performance & Risk

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/performance/stats?days=7` | Hit rate, expectancy, per-symbol breakdown |
| `POST` | `/api/v1/performance/grade-now` | Force immediate signal grading |
| `GET` | `/api/v1/risk/limits` | Current risk gates + today's usage |
| `POST` | `/api/v1/risk/limits` | Update risk limits |
| `POST` | `/api/v1/risk/kill` | Engage kill switch (block all live orders) |
| `POST` | `/api/v1/risk/resume` | Disengage kill switch |

### Risk Limits Body
```json
{
  "per_trade_max_inr": 5000,
  "daily_max_loss_inr": 1000,
  "daily_max_trades": 10
}
```

### Performance Stats Response
```json
{
  "graded_count": 150,
  "hit_rate_1h": 0.583,
  "hit_rate_24h": 0.512,
  "expectancy_per_signal": 0.0023,
  "avg_correct_move": 0.0145,
  "avg_wrong_move": 0.0098,
  "per_symbol": { "RELIANCE": { "hit_rate": 0.62, "avg_move": 0.018 } },
  "recent": [...]
}
```

---

## Execution & Surveillance

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/execution/algos` | Available execution algorithms |
| `POST` | `/api/v1/execution/simulate` | Simulate execution with impact model |
| `GET` | `/api/v1/surveillance/alerts` | Surveillance alert feed |
| `GET` | `/api/v1/tca/report` | Transaction Cost Analysis report |

---

## Allocator & Learning

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/allocator/weights` | Current strategy allocation weights |
| `POST` | `/api/v1/allocator/update` | Update allocation (bandit posterior) |
| `GET` | `/api/v1/allocator/history` | Allocation change history |

---

## Dashboards

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/dashboards/overview` | Platform overview metrics |
| `GET` | `/api/v1/dashboards/tca` | TCA dashboard data |
| `GET` | `/api/v1/dashboards/risk` | Risk exposure dashboard |
| `GET` | `/api/v1/dashboards/performance` | Performance analytics dashboard |

---

## Chat

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/chat/` | Natural-language Q&A about the platform |

---

## Authentication (Stub)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/auth/login` | Login (stub) |
| `POST` | `/api/v1/auth/register` | Register (stub) |

---

## Error Handling

All errors return consistent JSON:

```json
{
  "detail": "Blocked by risk limits: per-trade cap exceeded (₹12,000 > ₹5,000 limit)"
}
```

### Common HTTP Status Codes

| Code | Meaning |
|------|---------|
| `200` | Success |
| `400` | Bad request / Risk limit blocked |
| `404` | Resource not found |
| `422` | Validation error |
| `500` | Internal server error |

---

## Rate Limits

- Recommendation regeneration: 30-minute per-symbol cache (bypass with `?refresh=true`)
- Performance grading: rate-limited to once per 60 seconds (bypass with `/grade-now`)
- Market data: provider-dependent (Yahoo Finance has rate limits)
