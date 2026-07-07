"""Horizon-aware recommendation engine — the core of "accurate recs per time frame".

The user picks an investment horizon (1 month, 3 months, 6 months, 1 year, or a
short swing). For each (symbol, horizon) we:

  1. Read the RIGHT timeframe's stored bars — daily for months, weekly for a year.
  2. Run the strategy tournament on that timeframe with the holding period matched
     to the horizon (max_hold_bars), so the winner is the strategy that actually
     performed best *for that horizon*, not a one-size-fits-all intraday rule.
  3. Emit the winning strategy's current signal + a horizon-matched track record
     (win rate / Sharpe / sample backtested on that exact timeframe).

This makes a "1 month" call genuinely different from a "1 year" call and grounds
each in a backtest on the matching timeframe. Served from the durable bar store
(offline, instant) and cached, so it's cheap to call per request.
"""
from __future__ import annotations

import logging
import math
import statistics
import time
from typing import Dict, List, Optional

from . import bar_store
from .backtest import StrategyParams, backtest
from .strategies import STRATEGIES, atr_series, get_strategy

log = logging.getLogger(__name__)

# interval = the bar store key; hold_bars/expiry are in that interval's units.
# All horizons use DAILY bars (what the store holds for ~2000 symbols); hold_bars
# = trading days held. 1Y = ~252 trading days. (Weekly bars aren't ingested, so we
# don't depend on them.)
HORIZONS: Dict[str, dict] = {
    "1M": {"label": "1 Month",  "interval": "day", "min_bars": 80,  "hold_bars": 21,  "expiry_days": 30,  "atr_period": 14},
    "3M": {"label": "3 Months", "interval": "day", "min_bars": 150, "hold_bars": 63,  "expiry_days": 90,  "atr_period": 14},
    "6M": {"label": "6 Months", "interval": "day", "min_bars": 220, "hold_bars": 126, "expiry_days": 180, "atr_period": 20},
    "1Y": {"label": "1 Year",   "interval": "day", "min_bars": 250, "hold_bars": 252, "expiry_days": 365, "atr_period": 20},
    "SW": {"label": "Swing (1-2 wk)", "interval": "day", "min_bars": 60, "hold_bars": 10, "expiry_days": 10, "atr_period": 14},
}
DEFAULT_HORIZON = "1M"

_cache: Dict[tuple, tuple] = {}   # (symbol, horizon) -> (monotonic_ts, result)
_TTL = 900.0                       # 15 min — stored bars change slowly


def list_horizons() -> List[dict]:
    return [{"key": k, "label": v["label"], "interval": v["interval"], "hold_bars": v["hold_bars"]}
            for k, v in HORIZONS.items()]


def is_known_horizon(h: Optional[str]) -> bool:
    return bool(h) and h in HORIZONS


def _fold_windows(n: int):
    """Expanding out-of-sample test windows over the series (each ≥ 40 bars)."""
    cuts = [int(n * 0.55), int(n * 0.70), int(n * 0.85), n]
    windows, prev = [], cuts[0]
    for c in cuts[1:]:
        if c - prev >= 40:
            windows.append((prev, c))
        prev = c
    return windows


def _aggregate(folds) -> Optional[dict]:
    """Pool a strategy's per-fold OOS results into one honest metric set + a
    consistency score (1 = identical win rate every fold, lower = erratic)."""
    trades = sum(r.n_trades for r in folds)
    if trades == 0:
        return None
    win = sum(r.win_rate * r.n_trades for r in folds) / trades
    aw = sum(r.avg_win_pct * r.n_trades for r in folds) / trades
    al = sum(r.avg_loss_pct * r.n_trades for r in folds) / trades
    fold_wins = [r.win_rate for r in folds if r.n_trades >= 2]
    consistency = 1.0 - (statistics.pstdev(fold_wins) if len(fold_wins) >= 2 else 0.0)
    return {
        "win_rate": round(win, 3), "avg_win_pct": round(aw, 3), "avg_loss_pct": round(al, 3),
        "sharpe": round(statistics.mean([r.sharpe for r in folds]), 3),
        "max_drawdown_pct": round(max((r.max_drawdown_pct for r in folds), default=0.0), 2),
        "total_return_pct": round(statistics.mean([r.total_return_pct for r in folds]), 2),
        "n_trades": trades, "consistency": round(consistency, 3),
    }


def _robust_score(agg: Optional[dict]) -> float:
    """Rank metric that rewards a high POOLED OOS win rate, enough trades, and
    CONSISTENCY across folds — so we pick strategies that generalise, not the one
    that got lucky on a single split."""
    if not agg or agg["n_trades"] < 5:
        return -1.0
    coverage = math.sqrt(min(agg["n_trades"], 30) / 30.0)
    return round(agg["win_rate"] * coverage * (0.6 + 0.4 * agg["consistency"]), 4)


def horizon_signal(symbol: str, hkey: str = DEFAULT_HORIZON) -> dict:
    """Best strategy + current signal + horizon-matched backtest metrics for (symbol, horizon).

    Runs a fast strategy-only tournament (default params per strategy, holding
    period matched to the horizon) over stored bars. Cached for _TTL. Sync/CPU —
    callers in async code should wrap in asyncio.to_thread.
    """
    cfg = HORIZONS.get(hkey) or HORIZONS[DEFAULT_HORIZON]
    sym = (symbol or "").upper()
    key = (sym, hkey)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached and now - cached[0] < _TTL:
        return cached[1]

    bars = bar_store.get_bars(sym, cfg["interval"])
    if len(bars) < cfg["min_bars"]:
        res = {"ok": False, "horizon": hkey,
               "reason": f"insufficient {cfg['interval']} history for {sym} "
                         f"({len(bars)}/{cfg['min_bars']} bars) — ingest more data"}
        _cache[key] = (now, res)
        return res

    # ---- Multi-fold walk-forward: pick the strategy that GENERALISES ----
    # A single train/test split rewards whatever got lucky on one tail. Instead we
    # score each strategy across several expanding out-of-sample windows, pooling the
    # results and rewarding consistency. The winner is the one that held up across
    # folds — far less overfit than "best on one split". Headline metrics are the
    # pooled OOS numbers. Falls back to in-sample only when history is too short.
    n = len(bars)
    windows = _fold_windows(n)
    use_wf = len(windows) >= 2

    cand = {}   # skey -> {params, agg, score, signal}
    for skey in STRATEGIES:
        params = StrategyParams(strategy=skey, max_hold_bars=cfg["hold_bars"])
        sig = get_strategy(skey).signal_fn(bars, params)   # current direction on full history
        if use_wf:
            folds = [backtest(bars[a:b], symbol=sym, interval=cfg["interval"], params=params) for a, b in windows]
            agg = _aggregate(folds)
            score = _robust_score(agg)
        else:
            r = backtest(bars, symbol=sym, interval=cfg["interval"], params=params)
            agg = _aggregate([r])
            score = _robust_score(agg)
        cand[skey] = {"params": params, "agg": agg, "score": score, "signal": sig}

    ranked = sorted(cand.items(), key=lambda kv: kv[1]["score"], reverse=True)
    leaderboard = [{"strategy": k,
                    "win_rate": (v["agg"] or {}).get("win_rate"),
                    "sharpe": (v["agg"] or {}).get("sharpe"),
                    "n_trades": (v["agg"] or {}).get("n_trades", 0),
                    "consistency": (v["agg"] or {}).get("consistency"),
                    "score": v["score"], "signal": v["signal"]} for k, v in ranked]

    # Highest robust-ranked strategy that gives a DIRECTION now AND has a real OOS
    # sample; then any directional; else the top (may be neutral = HOLD).
    chosen_key = (
        next((k for k, v in ranked if v["signal"] in ("bullish", "bearish") and v["score"] > 0), None)
        or next((k for k, v in ranked if v["signal"] in ("bullish", "bearish")), None)
        or ranked[0][0]
    )
    chosen = cand[chosen_key]
    best_params, signal = chosen["params"], chosen["signal"]
    strat = get_strategy(chosen_key)
    agg = chosen["agg"] or {}
    validation = "walk-forward (3-fold)" if use_wf else "in-sample"

    # Full-history (in-sample) backtest of the winner — only to show the overfit gap.
    full_r = backtest(bars, symbol=sym, interval=cfg["interval"], params=best_params)

    atr = None
    s = atr_series(bars, cfg["atr_period"])
    if s:
        atr = s[-1]

    # Edge metrics — judge by EXPECTANCY/profit-factor, not raw win rate (trend
    # strategies win <50% but with big winners). edge_ok gates whether we'd actually
    # trade: enough trades + positive expectancy + positive net return out-of-sample.
    wr = agg.get("win_rate", 0.0) or 0.0
    aw = agg.get("avg_win_pct", 0.0) or 0.0
    al = abs(agg.get("avg_loss_pct", 0.0) or 0.0)
    expectancy = round(wr * aw - (1 - wr) * al, 3)
    profit_factor = round((wr * aw) / ((1 - wr) * al), 2) if (al > 0 and wr < 1) else None
    n_tr = agg.get("n_trades", 0) or 0
    ret = agg.get("total_return_pct", 0.0) or 0.0
    edge_ok = bool(n_tr >= 5 and expectancy > 0 and ret > 0)

    res = {
        "ok": True,
        "horizon": hkey,
        "interval": cfg["interval"],
        "hold_bars": cfg["hold_bars"],
        "expiry_days": cfg["expiry_days"],
        "signal": signal,                              # bullish / bearish / neutral
        "strategy": chosen_key,
        "strategy_label": strat.label,
        "params": best_params.to_dict(),
        "atr": atr,
        "last_close": bars[-1].c,
        "bars": len(bars),
        "validation": validation,
        "train_win_rate": full_r.win_rate,             # in-sample reference (overfit gap)
        "edge_ok": edge_ok,                            # gates whether we'd actually trade
        "metrics": {
            "win_rate": agg.get("win_rate", full_r.win_rate),
            "sharpe": agg.get("sharpe", full_r.sharpe),
            "n_trades": agg.get("n_trades", full_r.n_trades),
            "avg_win_pct": agg.get("avg_win_pct", full_r.avg_win_pct),
            "avg_loss_pct": agg.get("avg_loss_pct", full_r.avg_loss_pct),
            "total_return_pct": agg.get("total_return_pct", full_r.total_return_pct),
            "max_drawdown_pct": agg.get("max_drawdown_pct", full_r.max_drawdown_pct),
            "consistency": agg.get("consistency"),
            "expectancy_pct": expectancy,
            "profit_factor": profit_factor,
            "edge_ok": edge_ok,
            "score": chosen["score"],
        },
        "leaderboard": leaderboard[:5],
    }
    _cache[key] = (now, res)
    return res


def expected_move(atr: Optional[float], hold_bars: int, last_close: float) -> Optional[float]:
    """Random-walk expected price move over the holding period: ATR × √(hold_bars).

    Banded to a sane fraction of price for the horizon so levels are neither
    absurdly tight nor wild. None if ATR unavailable.
    """
    if not atr or atr <= 0 or not last_close:
        return None
    move = atr * math.sqrt(max(1, hold_bars))
    return min(max(move, last_close * 0.03), last_close * 0.40)
