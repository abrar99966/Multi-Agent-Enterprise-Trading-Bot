# Local infrastructure (Phase 0)

Single-host, dev-mode versions of the platform's durable services
(docs/ARCHITECTURE.md, sections 3, 11 and 16). **All of it is
optional in Phase 0** -- see [Running without any of this](#running-without-any-of-this-journal-only-mode).

## Quickstart

Requires Docker Desktop (Compose v2).

```powershell
cd "c:\Workspace\enterprise-trading-bot-Part 2\infra"
Copy-Item .env.example .env     # postgres credentials; edit if you like
docker compose up -d
docker compose ps               # wait until every service shows (healthy)
```

To point the backend at the stack, copy/merge `.env.example` into a `.env`
at the **repo root** (where the Python process runs) so `Settings` picks up
`ETB_REDPANDA_BROKERS=localhost:9092` and `ETB_QUESTDB_ILP_HOST=localhost`.

## Ports

| Service    | Image                                            | Host port | Container port | Purpose                          |
| ---------- | ------------------------------------------------ | --------- | -------------- | -------------------------------- |
| redpanda   | docker.redpanda.com/redpandadata/redpanda:v24.3.1 | 9092      | 9092           | Kafka API (event bus)            |
| redpanda   |                                                  | 9644      | 9644           | Admin API + Prometheus metrics   |
| questdb    | questdb/questdb:8.2.1                            | 9000      | 9000           | HTTP console + REST (`/exec`)    |
| questdb    |                                                  | 9009      | 9009           | ILP ingest (line protocol)       |
| questdb    |                                                  | 8812      | 8812           | PGWire SQL                       |
| clickhouse | clickhouse/clickhouse-server:24.8                | 8123      | 8123           | HTTP interface                   |
| clickhouse |                                                  | 19000     | 9000           | Native TCP (remapped: QuestDB owns host 9000) |
| postgres   | postgres:16                                      | 5432      | 5432           | PGWire (OMS / reference data)    |
| redis      | redis:7                                          | 6379      | 6379           | RESP (control-plane cache)       |

QuestDB console: <http://localhost:9000> - ClickHouse ping: <http://localhost:8123/ping> - Redpanda admin: <http://localhost:9644>

## What each service is for

| Service    | Role (architecture reference)                                                                                          |
| ---------- | ---------------------------------------------------------------------------------------------------------------------- |
| redpanda   | Durable, replayable system-of-record event bus -- Layer 2 "Event Backbone" (section 3.2, section 11); the Phase 0 "every component publishes" target. |
| questdb    | Tick/bar time-series store; the section 16 Phase 0 port target for the SQLite `market_data.db` (section 11).            |
| clickhouse | Columnar analytics over the full event history: TCA, audit and research queries (section 11; feeds the Phase 2 TCA pipeline). |
| postgres   | OMS, positions and reference data -- the transactional, foreign-keyed source of truth (section 11).                     |
| redis      | Control-plane hot state / session cache for the dashboard and API; never hot-path state (section 11).                   |

Single-node dev settings throughout (Redpanda runs `--overprovisioned --smp 1
--memory 1G`); none of this is production tuning.

## Running without any of this (journal-only mode)

The Phase 0 Python code has **no hard dependency on this stack**. The
defaults in `backend/app/core/config.py` are `ETB_REDPANDA_BROKERS=""` and
`ETB_QUESTDB_ILP_HOST=""`, and an empty string means "adapter disabled". In
that mode:

- events flow through the in-process `MemoryBus` (`backend/app/bus/base.py`
  dispatch model) and are persisted as the hash-chained JSONL journal
  (format in `backend/app/core/hashing.py`) under `ETB_JOURNAL_DIR`
  (`data/journal`) -- the WORM audit chain works entirely on local files;
- historical bars live in SQLite at `ETB_BAR_DB_PATH` (`data/market_data.db`).

So tests, replays and paper sessions run with zero containers. Bring the
stack up only when you want a durable bus (Redpanda), time-series SQL over
ticks/bars (QuestDB), or analytics over event history (ClickHouse).

Note: Redpanda advertises `localhost:9092`, which is correct for host-side
Python clients (the only clients in Phase 0). Containers that need to reach
the bus from inside the `etb` network would need an additional internal
listener -- out of scope here.

## Teardown

```powershell
docker compose down             # stop and remove containers; DATA IS KEPT
```

Data lives in named volumes (`etb_redpanda-data`, `etb_questdb-data`,
`etb_clickhouse-data`, `etb_postgres-data`, `etb_redis-data`):

```powershell
docker volume ls --filter name=etb_
```

### Full wipe (destroys all stored events, bars, tables)

```powershell
docker compose down --volumes
```

Or remove a single store, e.g. just postgres:

```powershell
docker compose down
docker volume rm etb_postgres-data
```
