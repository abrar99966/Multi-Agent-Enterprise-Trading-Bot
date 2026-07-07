# Enterprise AI Trading Assistant - FastAPI entrypoint.
#
# Canonical import root is `app.*` (backend/ on sys.path), same as the test
# suite. The bootstrap below keeps `uvicorn backend.app.main:app` working from
# the repo root; the preferred form is `uvicorn app.main:app --app-dir backend`.
import sys
from pathlib import Path

_BACKEND_DIR = str(Path(__file__).resolve().parents[1])
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api.v1 import trades, auth, market_data, chat, brokers, learning, performance, execution, allocator
from app.db.session import engine
from app.models.database import Base

app = FastAPI(
    title="Enterprise AI Trading Assistant API",
    description="Backend API for managing AI trading recommendations and execution.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Compress responses >1KB â€” the big ones (learning/results ~410KB) shrink ~10x,
# cutting transfer + client parse time on the Training/Screener tabs.
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.on_event("startup")
async def _init_db():
    from sqlalchemy import text

    async with engine.begin() as conn:
        # If the legacy broker_accounts table lacks the new encrypted-creds
        # columns, drop it so create_all can rebuild with the new schema.
        try:
            cols = await conn.execute(text("PRAGMA table_info(broker_accounts)"))
            existing = {row[1] for row in cols.fetchall()}
            if existing and "api_key_enc" not in existing:
                await conn.execute(text("DROP TABLE broker_accounts"))
        except Exception:
            pass
        await conn.run_sync(Base.metadata.create_all)

        # Light-weight in-place migration for columns added after table existed.
        # SQLite tolerates ADD COLUMN on the fly; catch & ignore if already there.
        for col_ddl in (
            "ALTER TABLE broker_accounts ADD COLUMN token_issued_at DATETIME",
            "ALTER TABLE broker_accounts ADD COLUMN token_expires_at DATETIME",
            "ALTER TABLE trades ADD COLUMN broker_account_id INTEGER",
            "ALTER TABLE trades ADD COLUMN broker_name VARCHAR(50)",
            "ALTER TABLE trades ADD COLUMN side VARCHAR(10)",
            "ALTER TABLE trades ADD COLUMN order_type VARCHAR(20)",
            "ALTER TABLE trades ADD COLUMN product VARCHAR(20)",
            "ALTER TABLE trades ADD COLUMN placed_price FLOAT",
            "ALTER TABLE trades ADD COLUMN is_paper BOOLEAN DEFAULT 0",
            "ALTER TABLE trades ADD COLUMN last_error VARCHAR(500)",
            "ALTER TABLE trade_recommendations ADD COLUMN graded_at DATETIME",
            "ALTER TABLE trade_recommendations ADD COLUMN price_after_1h FLOAT",
            "ALTER TABLE trade_recommendations ADD COLUMN price_after_24h FLOAT",
            "ALTER TABLE trade_recommendations ADD COLUMN signal_correct_1h BOOLEAN",
            "ALTER TABLE trade_recommendations ADD COLUMN signal_correct_24h BOOLEAN",
            "ALTER TABLE trade_recommendations ADD COLUMN actual_move_pct_1h FLOAT",
            "ALTER TABLE trade_recommendations ADD COLUMN actual_move_pct_24h FLOAT",
            "ALTER TABLE trade_recommendations ADD COLUMN horizon VARCHAR(8)",
            "ALTER TABLE trade_recommendations ADD COLUMN horizon_due_at DATETIME",
            "ALTER TABLE trade_recommendations ADD COLUMN horizon_correct BOOLEAN",
            "ALTER TABLE trade_recommendations ADD COLUMN horizon_move_pct FLOAT",
            "ALTER TABLE trade_recommendations ADD COLUMN graded_horizon_at DATETIME",
        ):
            try:
                await conn.execute(text(col_ddl))
            except Exception:
                pass

        # Backfill expiry for existing connected accounts so the countdown badge
        # appears immediately. We use created_at as a proxy issuance time and
        # apply the SEBI daily-expiry rule (06:00 IST = 00:30 UTC).
        try:
            await conn.execute(text("""
                UPDATE broker_accounts
                SET token_issued_at = created_at
                WHERE token_issued_at IS NULL AND created_at IS NOT NULL
            """))
            await conn.execute(text("""
                UPDATE broker_accounts
                SET token_expires_at = datetime(
                    CASE
                      WHEN time('now') < '00:30:00' THEN date('now')
                      ELSE date('now', '+1 day')
                    END || ' 00:30:00'
                )
                WHERE token_expires_at IS NULL
                  AND broker_name IN ('dhan','zerodha','upstox','angelone','icici_breeze')
            """))
        except Exception:
            pass

    # Pre-warm the broker symbol resolvers in the background so the very first
    # quote request doesn't pay the CSV download/parse cost on the hot path.
    import asyncio
    from app.services import dhan_symbols, upstox_symbols
    asyncio.create_task(dhan_symbols.ensure_loaded())
    asyncio.create_task(upstox_symbols.ensure_loaded())

    # Pre-warm the bar-store coverage cache (a full-table scan) so the first
    # Training/Screener tab load doesn't pay the ~2s cost on the hot path.
    from app.learning import bar_store
    asyncio.create_task(asyncio.to_thread(bar_store.coverage_summary))


app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(trades.router, prefix="/api/v1/trades", tags=["Trades"])
app.include_router(market_data.router, prefix="/api/v1/market-data", tags=["Market Data"])
app.include_router(chat.router, prefix="/api/v1/chat", tags=["Chat"])
app.include_router(brokers.router, prefix="/api/v1/brokers", tags=["Brokers"])
app.include_router(learning.router, prefix="/api/v1/learning", tags=["Learning"])
app.include_router(performance.router, prefix="/api/v1", tags=["Performance & Risk"])
app.include_router(execution.router, prefix="/api/v1", tags=["Execution & Surveillance"])
app.include_router(allocator.router, prefix="/api/v1", tags=["Allocator & Learning"])

# Layer 6 — read-side dashboards (docs/LAYER6_DASHBOARDS.md). Projections over
# the event journal + TCA store; strictly read-only.
from app.api.v1 import dashboards  # noqa: E402

app.include_router(dashboards.router, prefix="/api/v1", tags=["Dashboards"])

_DASH_HTML = Path(__file__).resolve().parent / "dashboards" / "static" / "index.html"


@app.get("/dash", include_in_schema=False)
async def dash_ui():
    from fastapi.responses import HTMLResponse

    return HTMLResponse(_DASH_HTML.read_text(encoding="utf-8"))


@app.get("/")
async def root():
    return {"message": "Welcome to the Trading Assistant API", "status": "running"}


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
