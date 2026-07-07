"""Market screener — run strategy signals across a universe at the latest bar.

A screener answers "which symbols are firing a signal *right now*". It reuses the
SAME signal functions the backtest and live TechnicalAgent use, so a screen hit
means exactly what a live signal means — no separate, drifting scan logic.

Two design choices that make this fast and trustworthy:

  • Reads bars straight from the durable store (`bar_store`), never the network,
    so scanning the whole stored market (~thousands of symbols) is offline and
    quick. Ingest data once (POST /learning/data/ingest) and screen forever.

  • Ranks hits by each symbol's *backtested edge* — the win rate / Sharpe / score
    the strategy earned on that symbol's own history in the last tournament
    (`tuned_params.json`). That surfaces proven setups, not just raw indicator
    matches. This is the differentiator over a vanilla Chartink-style scan.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import fields
from typing import List, Optional

from . import bar_store
from .strategies import DEFAULT_STRATEGY, StrategyParams, get_strategy
from .tune import load_tuned_params
from .universe import _STORE_IV

log = logging.getLogger(__name__)


def _params_from(tp: dict) -> StrategyParams:
    """Build StrategyParams from a tuned-params dict, ignoring stray/legacy keys."""
    valid = {f.name for f in fields(StrategyParams)}
    return StrategyParams(**{k: v for k, v in (tp or {}).items() if k in valid})


def _edge_for(metrics: Optional[dict], skey: str) -> dict:
    """Pull win_rate / sharpe / score for strategy `skey` from a symbol's tournament metrics.

    Looks at the winning entry first, then the per-strategy leaderboard, so we can
    rank by the strategy's own backtested track record even when it wasn't the
    tournament winner. Returns {"trained": False} for untrained symbols.
    """
    if not metrics:
        return {"trained": False}
    best = metrics.get("best") or {}
    if best.get("strategy") == skey:
        return {"win_rate": best.get("win_rate"), "sharpe": best.get("sharpe"),
                "score": best.get("score"), "n_trades": best.get("n_trades"), "trained": True}
    for row in metrics.get("leaderboard") or []:
        if row.get("strategy") == skey:
            return {"win_rate": row.get("win_rate"), "sharpe": row.get("sharpe"),
                    "score": row.get("score"), "n_trades": row.get("n_trades"), "trained": True}
    return {"trained": False}


def _screen_sync(symbols: List[str], store_iv: str, strategy_key: Optional[str],
                 signal_filter: Optional[str], tuned_payload: dict, min_bars: int) -> List[dict]:
    """Synchronous core — runs in a worker thread (sqlite reads + pure-Python signals)."""
    tuned_params = tuned_payload.get("tuned_params") or {}
    per_metrics = tuned_payload.get("per_symbol_metrics") or {}
    hits: List[dict] = []

    for sym in symbols:
        symu = (sym or "").upper()
        bars = bar_store.get_bars(symu, store_iv)
        if len(bars) < min_bars:
            continue

        tp = tuned_params.get(symu)
        if strategy_key:
            # Force one strategy across the whole universe. Use that symbol's tuned
            # params only when they actually belong to this strategy; else defaults.
            skey = strategy_key
            params = _params_from(tp) if (tp and tp.get("strategy") == skey) else StrategyParams(strategy=skey)
        else:
            # "Tournament winner" mode — each symbol screens with whatever strategy
            # won on its own history. Untrained symbols have no winner, so skip them.
            if not tp:
                continue
            skey = tp.get("strategy", DEFAULT_STRATEGY)
            params = _params_from(tp)

        strat = get_strategy(skey)
        sig = strat.signal_fn(bars, params)
        if sig == "neutral":
            continue
        if signal_filter and sig != signal_filter:
            continue

        edge = _edge_for(per_metrics.get(symu), skey)
        last = bars[-1]
        hits.append({
            "symbol": symu,
            "signal": sig,
            "strategy": skey,
            "strategy_label": strat.label,
            "price": round(last.c, 2) if last.c else None,
            "bar_t": last.t,
            "n_bars": len(bars),
            "win_rate": edge.get("win_rate"),
            "sharpe": edge.get("sharpe"),
            "score": edge.get("score"),
            "n_trades": edge.get("n_trades"),
            "trained": edge.get("trained", False),
        })
    return hits


async def screen(symbols: List[str], *, interval: str = "day",
                 strategy: Optional[str] = None, signal: str = "any",
                 min_win_rate: Optional[float] = None, limit: int = 100,
                 min_bars: int = 60) -> dict:
    """Screen `symbols` at the latest stored bar and rank hits by backtested edge.

    strategy=None  → each symbol screens with its tournament-winning strategy.
    strategy="key" → force that one strategy across every symbol.
    signal         → "bullish" | "bearish" | "any".
    min_win_rate   → drop hits whose backtested win rate (0..1) is below this.

    Ranking: trained hits first, then by composite score, win rate, Sharpe.
    """
    store_iv = _STORE_IV.get(interval, "day")
    tuned = load_tuned_params()
    sig_filter = signal if signal in ("bullish", "bearish") else None

    hits = await asyncio.to_thread(
        _screen_sync, symbols, store_iv, strategy, sig_filter, tuned, min_bars
    )

    if min_win_rate is not None:
        hits = [h for h in hits if (h.get("win_rate") or 0.0) >= min_win_rate]

    # Trained + higher edge floats to the top; untrained (no track record) sinks.
    hits.sort(
        key=lambda h: (
            1 if h.get("trained") else 0,
            h["score"] if h.get("score") is not None else -1e9,
            h.get("win_rate") or 0.0,
            h.get("sharpe") or 0.0,
        ),
        reverse=True,
    )

    total = len(hits)
    if limit and limit > 0:
        hits = hits[:limit]
    return {"total": total, "returned": len(hits), "hits": hits}


# ---- External cross-check -------------------------------------------------------------

def _annotate_sync(symbols: List[str], store_iv: str, tuned_payload: dict, min_bars: int) -> dict:
    """For each external symbol, attach OUR view: backtested edge + current live signal.

    This is what makes external screeners worth pulling — a Chartink/TradingView hit
    is just a candidate; here we say whether *our own* backtest gives that symbol a
    proven edge and whether *our* live strategy agrees with the signal right now.
    """
    tuned_params = tuned_payload.get("tuned_params") or {}
    per_metrics = tuned_payload.get("per_symbol_metrics") or {}
    out: dict = {}
    for sym in symbols:
        symu = (sym or "").upper()
        tp = tuned_params.get(symu)
        skey = tp.get("strategy", DEFAULT_STRATEGY) if tp else None
        edge = _edge_for(per_metrics.get(symu), skey) if skey else {"trained": False}

        our_signal = None
        bars = bar_store.get_bars(symu, store_iv)
        in_store = len(bars) >= min_bars
        if in_store and tp:
            strat = get_strategy(skey)
            our_signal = strat.signal_fn(bars, _params_from(tp))

        out[symu] = {
            "in_store": in_store,
            "n_bars": len(bars),
            "our_signal": our_signal,           # bullish/bearish/neutral, or None if untrained/no data
            "our_strategy": skey,
            "win_rate": edge.get("win_rate"),
            "sharpe": edge.get("sharpe"),
            "score": edge.get("score"),
            "n_trades": edge.get("n_trades"),
            "trained": edge.get("trained", False),
        }
    return out


async def annotate_external(symbols: List[str], *, interval: str = "day", min_bars: int = 60) -> dict:
    """Map each symbol → our backtested edge + current live signal (offline, store-based)."""
    store_iv = _STORE_IV.get(interval, "day")
    tuned = load_tuned_params()
    return await asyncio.to_thread(_annotate_sync, symbols, store_iv, tuned, min_bars)
