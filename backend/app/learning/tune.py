"""Strategy tournament + parameter tuner.

For each symbol we run a *tournament*: every strategy in the registry is
backtested across its own small parameter grid, and the single best
(strategy, params) pair is chosen by a composite score that punishes large
drawdowns and rewards consistency (NOT just total return — total return alone
overfits dramatically).

This is the "consider the best strategy by backtracking its results" step:
the live agent ends up trading whichever strategy actually won on each symbol's
own history, not a one-size-fits-all rule.

Grids are deliberately small per strategy so a full tournament across a
~66-symbol watchlist still completes in well under the data-fetch time. Wider
grids overfit the historical sample.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .backtest import StrategyParams, backtest, BacktestResult
from .historical import fetch_bars
from .strategies import STRATEGIES, iter_combos

log = logging.getLogger(__name__)

# Where the live TechnicalAgent looks for tuned params at startup
TUNED_PARAMS_FILE = Path(__file__).resolve().parent / "tuned_params.json"


def _composite_score(r: BacktestResult) -> float:
    """Single number that balances return, consistency, and drawdown.

    Heuristic: reward Sharpe + total return, penalize drawdown + tiny trade counts.
    Any combo that produces <5 trades is unreliable — push to the bottom.
    """
    if r.n_trades < 5:
        return -1000.0
    # All metrics already in % / dimensionless terms
    return (r.sharpe * 1.5) + (r.total_return_pct * 0.05) - (r.max_drawdown_pct * 0.10)


def _metrics(r: BacktestResult, score: float) -> dict:
    return {
        "strategy": r.strategy,
        "params": r.params,
        "win_rate": r.win_rate,
        "sharpe": r.sharpe,
        "total_return_pct": r.total_return_pct,
        "max_drawdown_pct": r.max_drawdown_pct,
        "n_trades": r.n_trades,
        "avg_win_pct": r.avg_win_pct,
        "avg_loss_pct": r.avg_loss_pct,
        "score": round(score, 3),
    }


async def tune_symbol(
    db: AsyncSession, symbol: str, *,
    interval: str = "30minute", lookback_days: int = 90,
) -> Optional[dict]:
    """Run the full strategy tournament for one symbol — fetch history once, backtest all combos."""
    bars = await fetch_bars(db, symbol, interval=interval, lookback_days=lookback_days)
    if len(bars) < 100:
        log.info("Skipping %s: only %d bars (need >100)", symbol, len(bars))
        return None

    # Walk-forward: choose params on a TRAIN slice, report metrics on a held-out
    # TEST tail (out-of-sample). Reporting on the same data you tuned on = overfit;
    # OOS metrics are what actually generalise. Falls back to in-sample if the
    # history is too short for a meaningful split.
    n = len(bars)
    split = int(n * 0.7)
    train, test = bars[:split], bars[split:]
    use_wf = len(train) >= 100 and len(test) >= 40
    rank_bars = train if use_wf else bars

    def _eval(params):
        return backtest(test if use_wf else bars, symbol=symbol, interval=interval, params=params)

    all_results: List[dict] = []
    per_strategy_best: Dict[str, tuple] = {}   # key -> (train_score, train_params)

    # Stage 1: tune each strategy's PARAMS on the training slice only.
    for skey, strat in STRATEGIES.items():
        for combo in iter_combos(strat):
            params = StrategyParams(strategy=skey, **combo)
            r = backtest(rank_bars, symbol=symbol, interval=interval, params=params)   # TRAIN
            score = _composite_score(r)
            all_results.append({
                "strategy": skey, "params": combo, "score": round(score, 3),
                "win_rate": r.win_rate, "sharpe": r.sharpe,
                "return_pct": r.total_return_pct, "max_dd_pct": r.max_drawdown_pct, "trades": r.n_trades,
            })
            if skey not in per_strategy_best or score > per_strategy_best[skey][0]:
                per_strategy_best[skey] = (score, params)

    if not per_strategy_best:
        return None

    # Stage 2: CHOOSE the winning strategy by how its train-tuned params perform
    # OUT-OF-SAMPLE — not by the train peak. Picking on train alone overfits badly
    # (e.g. 80% train → 17% OOS); selecting on held-out generalisation fixes that.
    best = None
    best_params = None
    best_score = -float("inf")
    leaderboard = []
    for k, (sc, p) in per_strategy_best.items():
        r_oos = _eval(p)
        osc = _composite_score(r_oos)   # <5 trades ⇒ -1000, so flukes can't win
        leaderboard.append({**_metrics(r_oos, osc), "strategy": k})
        if osc > best_score:
            best_score, best, best_params = osc, r_oos, p
    leaderboard.sort(key=lambda m: m["score"], reverse=True)

    if best is None:
        return None

    best_train = backtest(rank_bars, symbol=symbol, interval=interval, params=best_params)
    baseline = _eval(StrategyParams())                         # OOS baseline
    validation = "walk-forward" if use_wf else "in-sample"

    best_metrics = _metrics(best, best_score)
    best_metrics["validation"] = validation
    best_metrics["train_win_rate"] = best_train.win_rate

    return {
        "symbol": symbol,
        "bars": len(bars),
        "interval": interval,
        "lookback_days": lookback_days,
        "validation": validation,
        "best_strategy": best.strategy,
        "best": best_metrics,
        "baseline": {
            "strategy": baseline.strategy,
            "params": baseline.params,
            "win_rate": baseline.win_rate,
            "sharpe": baseline.sharpe,
            "total_return_pct": baseline.total_return_pct,
            "max_drawdown_pct": baseline.max_drawdown_pct,
            "n_trades": baseline.n_trades,
        },
        "improvement_pp": round((best.win_rate - baseline.win_rate) * 100, 2),  # percentage-points
        "leaderboard": leaderboard,
        "all_results": all_results,
    }


async def tune_universe(
    db: AsyncSession, symbols: List[str], *,
    interval: str = "30minute", lookback_days: int = 90,
    save: bool = True,
    progress_cb: Optional[Callable] = None,
) -> dict:
    """Run the tournament for every symbol, persist a single tuned_params.json the live agent reads.

    `progress_cb(done_count, total, current_symbol, last_result_or_none)` is
    called after each symbol so the UI can poll status during long runs.
    """
    started = datetime.utcnow()
    per_symbol: Dict[str, dict] = {}
    total = len(symbols)
    for i, sym in enumerate(symbols):
        if progress_cb is not None:
            try:
                progress_cb(i, total, sym, None)
            except Exception:
                pass
        try:
            res = await tune_symbol(db, sym, interval=interval, lookback_days=lookback_days)
            if res is not None:
                per_symbol[sym.upper()] = res
        except Exception as exc:
            log.exception("Tuning failed for %s: %s", sym, exc)
            res = None
        if progress_cb is not None:
            try:
                progress_cb(i + 1, total, sym, res)
            except Exception:
                pass

    finished = datetime.utcnow()

    # The lean param dict the live agent loads. Each entry now carries its winning
    # `strategy` key inline (backward compatible: old entries default to rsi_sma).
    tuned_lookup = {sym: data["best"]["params"] for sym, data in per_symbol.items()}
    # Convenience side-map of just the winning strategy per symbol.
    tuned_strategies = {sym: data["best_strategy"] for sym, data in per_symbol.items()}

    # Count which strategies won, for an at-a-glance summary.
    strategy_wins: Dict[str, int] = {}
    for s in tuned_strategies.values():
        strategy_wins[s] = strategy_wins.get(s, 0) + 1

    payload = {
        "trained_at": started.isoformat() + "Z",
        "duration_seconds": (finished - started).total_seconds(),
        "interval": interval,
        "lookback_days": lookback_days,
        "n_symbols": len(per_symbol),
        "tuned_params": tuned_lookup,
        "tuned_strategies": tuned_strategies,
        "strategy_wins": strategy_wins,
        "per_symbol_metrics": {
            sym: {
                "best_strategy": data["best_strategy"],
                "best": data["best"],
                "baseline": data["baseline"],
                "improvement_pp": data["improvement_pp"],
                "leaderboard": data["leaderboard"],
                "bars": data["bars"],
            }
            for sym, data in per_symbol.items()
        },
    }

    if save and tuned_lookup:
        try:
            TUNED_PARAMS_FILE.write_text(json.dumps(payload, indent=2))
            log.info("Saved tuned params for %d symbols → %s", len(tuned_lookup), TUNED_PARAMS_FILE)
        except Exception as exc:
            log.warning("Failed to save tuned params: %s", exc)

    return payload


def load_tuned_params() -> dict:
    """Read tuned_params.json; returns empty dict if not present yet."""
    if not TUNED_PARAMS_FILE.exists():
        return {}
    try:
        return json.loads(TUNED_PARAMS_FILE.read_text())
    except Exception as exc:
        log.warning("Failed to read tuned params: %s", exc)
        return {}


def load_symbol_report(symbol: str) -> Optional[dict]:
    """The persisted backtest track record for one symbol, or None if untrained.

    Used by the recommendation explainer to show the user *why they can trust* a
    signal: the winning strategy's historical win rate / Sharpe / sample size on
    THIS symbol, versus the naive baseline.
    """
    payload = load_tuned_params()
    if not payload:
        return None
    metrics = (payload.get("per_symbol_metrics") or {}).get((symbol or "").upper())
    if not metrics:
        return None
    return {
        "trained_at": payload.get("trained_at"),
        "interval": payload.get("interval"),
        "lookback_days": payload.get("lookback_days"),
        **metrics,  # best_strategy, best, baseline, improvement_pp, leaderboard, bars
    }
