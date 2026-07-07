"""AI-agent training endpoints — Phase 1: rule-based backtest + grid tune."""
import asyncio
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...agents.base import technical_agent_singleton
from ...db.session import get_db
from ...learning import bar_store
from ...learning.backtest import StrategyParams, backtest
from ...learning.historical import fetch_bars
from ...learning.ingest import ingest_universe
from ...learning.screener import screen, annotate_external
from ...learning.strategies import STRATEGIES, list_strategies
from ...services import external_screeners
from ...learning.tune import tune_universe, load_tuned_params
from ...learning.universe import (
    UNIVERSES, is_known_preset, list_universes, resolve_universe_async,
)

log = logging.getLogger(__name__)
router = APIRouter()


class TrainRequest(BaseModel):
    preset: Optional[str] = Field(None, description="watchlist, indexes, nifty50, indexes_plus_nifty50, all_nse, custom")
    symbols: Optional[List[str]] = Field(None, description="Required when preset='custom' or to override")
    interval: str = Field("30minute", description="1minute, 30minute, day, week")
    lookback_days: int = Field(90, ge=14, le=9000, description="History depth. Daily training wants years (e.g. 1825) for walk-forward + horizons; intraday is Yahoo-capped at 60d.")
    max_symbols: Optional[int] = Field(None, ge=1, description="Cap the universe size (useful for the whole-market 'all_nse' preset)")


# Single in-flight job — avoid hammering the data source with parallel tunes
_lock = asyncio.Lock()
_last_result: Optional[dict] = None
_status: dict = {"state": "idle"}


def _update_progress(done: int, total: int, current: str, last_result: Optional[dict]):
    """Called by the tuner after each symbol completes."""
    _status["state"] = "running"
    _status["progress"] = {
        "done": done,
        "total": total,
        "percent": round(100 * done / total, 1) if total > 0 else 0,
        "current_symbol": current,
        "last_result": (
            {"symbol": current, "win_rate": last_result["best"]["win_rate"],
             "sharpe": last_result["best"]["sharpe"], "n_trades": last_result["best"]["n_trades"],
             "strategy": last_result.get("best_strategy")}
            if last_result else None
        ),
    }


@router.get("/universes")
async def universes():
    """List the preset symbol universes available to the UI."""
    return {"universes": list_universes()}


@router.get("/strategies")
async def strategies():
    """List the strategies that compete in the tournament (and their grid sizes)."""
    return {"strategies": list_strategies()}


@router.get("/screen")
async def screen_market(
    preset: str = Query("stored", description="stored, all_nse, nifty50, indexes, watchlist, custom"),
    symbols: Optional[str] = Query(None, description="Comma-separated tickers (used when preset=custom)"),
    strategy: Optional[str] = Query(None, description=f"Force one strategy; omit to use each symbol's tournament winner. One of: {', '.join(STRATEGIES.keys())}"),
    signal: str = Query("any", description="bullish, bearish, or any"),
    interval: str = Query("day", description="Bar interval to read from the store (day, 30minute, week)"),
    min_win_rate: Optional[float] = Query(None, ge=0, le=100, description="Drop hits below this backtested win rate (%)"),
    max_symbols: Optional[int] = Query(None, ge=1, description="Cap the universe size scanned"),
    limit: int = Query(100, ge=1, le=1000, description="Max hits returned"),
):
    """Screen a universe for symbols firing a signal *now*, ranked by backtested edge.

    Reads the latest stored bars (offline — ingest first via POST /learning/data/ingest)
    and runs the same strategy functions the live agent trades, so a hit means a live
    signal. With `strategy` omitted, each symbol is screened with the strategy that won
    its own tournament; pass `strategy` to force one across the whole market.
    """
    if strategy and strategy not in STRATEGIES:
        raise HTTPException(status_code=400, detail=f"Unknown strategy '{strategy}'. Valid: {list(STRATEGIES.keys())}")
    custom = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    syms = await resolve_universe_async(preset, custom, max_symbols=max_symbols, interval=interval)
    if not syms:
        raise HTTPException(
            status_code=400,
            detail="Empty universe. For 'stored' you must ingest data first (POST /learning/data/ingest).",
        )
    result = await screen(
        syms, interval=interval, strategy=strategy, signal=signal,
        min_win_rate=(min_win_rate / 100.0 if min_win_rate is not None else None),
        limit=limit,
    )
    return {
        "preset": preset,
        "interval": interval,
        "strategy": strategy or "tournament_winner",
        "signal": signal,
        "n_universe": len(syms),
        **result,
    }


@router.get("/screen/external/sources")
async def external_sources():
    """List the external screeners (TradingView, Chartink, Screener.in) and their preset scans.

    ⚠ These hit unofficial public endpoints — personal-research use only; they can
    break or rate-limit. See services/external_screeners.py for the ToS caveat.
    """
    return {"sources": external_screeners.list_sources()}


@router.get("/screen/external")
async def screen_external(
    source: str = Query(..., description="tradingview, chartink, or screener_in"),
    scan: str = Query(..., description="Preset key for the source, or a Screener.in screen id/URL"),
    limit: int = Query(100, ge=1, le=500, description="Max hits to pull"),
    annotate: bool = Query(True, description="Cross-check each hit with our backtested edge + live signal"),
    interval: str = Query("day", description="Interval used for our edge/signal annotation"),
):
    """Pull a scan from an external screener, optionally annotated with OUR backtested edge.

    A raw external hit is just a candidate. With annotate=on we attach, per symbol,
    whether our own tournament gave it a proven edge (win rate / Sharpe / score) and
    whether our live strategy agrees with the signal right now — so you can trust the
    external list against your own backtests instead of taking it on faith.
    """
    try:
        result = await external_screeners.run_external_scan(source, scan, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.warning("External scan failed (%s/%s): %s", source, scan, exc)
        raise HTTPException(status_code=502, detail=f"External screener '{source}' failed or changed: {exc}")

    hits = result.get("hits", [])
    if annotate and hits:
        ann = await annotate_external([h["symbol"] for h in hits], interval=interval)
        for h in hits:
            h["edge"] = ann.get(h["symbol"], {"trained": False})
        # Float the trustworthy ones up: in our store + trained + our signal agrees.
        def _rank(h):
            e = h.get("edge") or {}
            return (
                1 if e.get("trained") else 0,
                e["score"] if e.get("score") is not None else -1e9,
                e.get("win_rate") or 0.0,
            )
        hits.sort(key=_rank, reverse=True)
        result["annotated"] = True

    return result


@router.get("/status")
async def status():
    """Current training state + the last saved tuned params file (if any)."""
    persisted = load_tuned_params()
    return {
        "running": _status.get("state") == "running",
        "state": _status,
        "tuned_on_disk": bool(persisted),
        "tuned_summary": {
            "trained_at": persisted.get("trained_at"),
            "n_symbols": persisted.get("n_symbols"),
            "interval": persisted.get("interval"),
            "lookback_days": persisted.get("lookback_days"),
        } if persisted else None,
    }


@router.get("/results")
async def results():
    """Full per-symbol metrics from the last training run, plus persisted params."""
    persisted = load_tuned_params()
    if not persisted and _last_result is None:
        raise HTTPException(status_code=404, detail="No training has been run yet. POST /learning/train first.")
    return {
        "last_run": _last_result,
        "persisted": persisted,
    }


@router.post("/train")
async def train(payload: TrainRequest | None = None, db: AsyncSession = Depends(get_db)):
    """Train the agent against a symbol universe.

    Pulls historical bars (Upstox if connected, otherwise Yahoo Finance fallback),
    runs the strategy tournament per symbol (every strategy × its grid, best wins),
    persists tuned_params.json. The live TechnicalAgent reloads immediately and
    trades each symbol's winning strategy. Run asynchronously in the background so
    long jobs (~2 min for 66 symbols) don't block the request — poll /status
    for progress.
    """
    global _last_result

    if _lock.locked():
        raise HTTPException(status_code=409, detail="Training already in progress. Poll /learning/status.")

    payload = payload or TrainRequest()
    # Resolve preset → symbol list. Dynamic presets (e.g. all_nse) hit the broker
    # instrument master; custom uses payload.symbols verbatim.
    if payload.preset and payload.preset != "custom":
        if not is_known_preset(payload.preset):
            raise HTTPException(
                status_code=400,
                detail=f"Unknown preset '{payload.preset}'. Valid: {list(UNIVERSES.keys()) + ['all_nse', 'custom']}",
            )
        symbols = await resolve_universe_async(payload.preset, max_symbols=payload.max_symbols, interval=payload.interval)
    else:
        symbols = await resolve_universe_async("custom", payload.symbols or [], max_symbols=payload.max_symbols, interval=payload.interval)
        if not symbols:
            # Fallback to default watchlist if neither preset nor symbols supplied
            symbols = await resolve_universe_async("watchlist")
    if not symbols:
        raise HTTPException(status_code=400, detail="Resolved an empty symbol universe. "
                            "For 'all_nse' the instrument master may still be downloading — retry shortly.")

    # Acquire lock + kick off background task so the HTTP request returns immediately
    await _lock.acquire()
    _status.update({
        "state": "running",
        "symbols": symbols,
        "n_symbols": len(symbols),
        "interval": payload.interval,
        "lookback_days": payload.lookback_days,
        "preset": payload.preset,
        "progress": {"done": 0, "total": len(symbols), "percent": 0.0, "current_symbol": None, "last_result": None},
    })

    async def _run():
        global _last_result
        try:
            result = await tune_universe(
                db, symbols,
                interval=payload.interval,
                lookback_days=payload.lookback_days,
                save=True,
                progress_cb=_update_progress,
            )
            _last_result = result
            technical_agent_singleton.reload_tuned()
            _status.update({
                "state": "idle",
                "last_finished_at": result.get("trained_at"),
                "last_n_tuned": result.get("n_symbols", 0),
            })
        except Exception as exc:
            log.exception("Training failed")
            _status.update({"state": "idle", "last_error": str(exc)})
        finally:
            _lock.release()

    asyncio.create_task(_run())
    return {
        "ok": True,
        "kicked_off": True,
        "n_symbols": len(symbols),
        "symbols_preview": symbols[:10] + (["..."] if len(symbols) > 10 else []),
        "interval": payload.interval,
        "lookback_days": payload.lookback_days,
        "next_step": "Poll GET /learning/status for progress, then GET /learning/results when done.",
    }


@router.get("/backtest/{symbol}")
async def quick_backtest(
    symbol: str,
    interval: str = Query("30minute"),
    lookback_days: int = Query(90, ge=14, le=730),
    strategy: str = Query("rsi_sma", description=f"One of: {', '.join(STRATEGIES.keys())}"),
    db: AsyncSession = Depends(get_db),
):
    """One-shot backtest of a single strategy (default params) on one symbol — diagnostic."""
    if strategy not in STRATEGIES:
        raise HTTPException(status_code=400, detail=f"Unknown strategy '{strategy}'. Valid: {list(STRATEGIES.keys())}")
    bars = await fetch_bars(db, symbol, interval=interval, lookback_days=lookback_days)
    if not bars:
        raise HTTPException(
            status_code=404,
            detail=f"No historical bars for {symbol} from either Upstox or Yahoo. "
                   f"Check the symbol spelling and that Upstox/internet is reachable.",
        )
    result = backtest(bars, symbol=symbol, interval=interval, params=StrategyParams(strategy=strategy))
    return {
        "symbol": symbol,
        "strategy": result.strategy,
        "bars_fetched": len(bars),
        "first_bar_t": bars[0].t,
        "last_bar_t": bars[-1].t,
        "metrics": {
            "win_rate": result.win_rate,
            "sharpe": result.sharpe,
            "total_return_pct": result.total_return_pct,
            "max_drawdown_pct": result.max_drawdown_pct,
            "n_trades": result.n_trades,
            "avg_win_pct": result.avg_win_pct,
            "avg_loss_pct": result.avg_loss_pct,
        },
        "params": result.params,
    }


# ---- Historical data store: bulk ingest + coverage -----------------------------------

class IngestRequest(BaseModel):
    preset: Optional[str] = Field(None, description="watchlist, indexes, nifty50, indexes_plus_nifty50, all_nse, custom")
    symbols: Optional[List[str]] = Field(None, description="Used when preset='custom'")
    interval: str = Field("day", description="day (best for a multi-year store), 30minute, week")
    lookback_days: int = Field(1095, ge=30, le=9000, description="History depth to store (default ~3 years)")
    max_symbols: Optional[int] = Field(None, ge=1, description="Cap the universe size")
    throttle: float = Field(0.4, ge=0.0, le=10.0, description="Seconds between network fetches (avoid Yahoo 429)")
    skip_existing: bool = Field(True, description="Skip symbols already stored — makes the run resumable")
    min_bars: int = Field(30, ge=1, le=5000, description="Re-fetch any symbol with FEWER stored bars than this (deepen thin history). Set high (e.g. 400) for a top-up of short series.")


_ingest_lock = asyncio.Lock()
_ingest_status: dict = {"state": "idle"}


def _update_ingest_progress(done: int, total: int, current: str, stats: dict):
    _ingest_status["state"] = "running"
    _ingest_status["progress"] = {
        "done": done, "total": total,
        "percent": round(100 * done / total, 1) if total else 0,
        "current_symbol": current,
        "ingested": stats.get("ingested", 0),
        "skipped": stats.get("skipped", 0),
        "failed": stats.get("failed", 0),
        "bars_added": stats.get("bars_added", 0),
    }


@router.get("/data/coverage")
async def data_coverage():
    """What history is stored: #symbols, total bars, date span, per-interval breakdown."""
    return bar_store.coverage_summary()


@router.get("/data/status")
async def data_status():
    """Current ingestion state + a live coverage snapshot."""
    return {
        "running": _ingest_status.get("state") == "running",
        "state": _ingest_status,
        "coverage": bar_store.coverage_summary(),
    }


@router.post("/data/ingest")
async def data_ingest(payload: IngestRequest | None = None):
    """Bulk-download a universe into the durable bar store (background, resumable).

    Defaults to DAILY bars over ~3 years for the whole NSE market (`all_nse`).
    Stored data is then served to every backtest offline — no re-fetching. The
    run is resumable: re-POST to fill any symbols that failed (e.g. Yahoo 429s).
    """
    if _ingest_lock.locked():
        raise HTTPException(status_code=409, detail="Ingestion already in progress. Poll /learning/data/status.")

    payload = payload or IngestRequest()
    if payload.preset and payload.preset != "custom":
        if not is_known_preset(payload.preset):
            raise HTTPException(status_code=400, detail=f"Unknown preset '{payload.preset}'.")
        symbols = await resolve_universe_async(payload.preset, max_symbols=payload.max_symbols)
    elif payload.symbols:
        symbols = await resolve_universe_async("custom", payload.symbols, max_symbols=payload.max_symbols)
    else:
        # Default to the whole NSE market when nothing is specified.
        symbols = await resolve_universe_async("all_nse", max_symbols=payload.max_symbols)
    if not symbols:
        raise HTTPException(status_code=400, detail="Empty universe — the instrument master may still be downloading; retry shortly.")

    await _ingest_lock.acquire()
    _ingest_status.update({
        "state": "running", "n_symbols": len(symbols), "interval": payload.interval,
        "lookback_days": payload.lookback_days,
        "progress": {"done": 0, "total": len(symbols), "percent": 0.0, "current_symbol": None,
                     "ingested": 0, "skipped": 0, "failed": 0, "bars_added": 0},
    })

    async def _run():
        # Use a fresh DB session — the request's session is closed once it returns.
        from ...db.session import async_session
        try:
            async with async_session() as bg_db:
                stats = await ingest_universe(
                    bg_db, symbols,
                    interval=payload.interval, lookback_days=payload.lookback_days,
                    throttle=payload.throttle, skip_existing=payload.skip_existing,
                    min_bars=payload.min_bars,
                    progress_cb=_update_ingest_progress,
                )
            _ingest_status.update({"state": "idle", "last_stats": stats,
                                   "last_finished_at": datetime.utcnow().isoformat() + "Z"})
        except Exception as exc:
            log.exception("Ingestion failed")
            _ingest_status.update({"state": "idle", "last_error": str(exc)})
        finally:
            _ingest_lock.release()

    asyncio.create_task(_run())
    return {
        "ok": True, "kicked_off": True, "n_symbols": len(symbols),
        "symbols_preview": symbols[:10] + (["..."] if len(symbols) > 10 else []),
        "interval": payload.interval, "lookback_days": payload.lookback_days,
        "note": "Whole-market ingest is slow and may hit Yahoo 429s — it's resumable, so re-run to fill gaps.",
        "next_step": "Poll GET /learning/data/status; once stored, backtests run offline from the store.",
    }
