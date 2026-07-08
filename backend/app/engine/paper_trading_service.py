"""Continuous paper-trading background service.

Runs an infinite loop of paper-trading sessions using multiple strategies,
records results to the database, and tracks cumulative learning metrics.
The service runs as a background asyncio task started by FastAPI on startup.

Key features:
  - Rotates through the top strategies from backtesting
  - Each round generates fresh synthetic market data (new seed)
  - Saves trade results + performance stats to the DB
  - Tracks per-strategy win rates and Sharpe ratios over time
  - Exposes a status endpoint at /api/v1/paper-trading/status
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Strategy configs ranked by backtest performance
STRATEGY_CONFIGS = [
    {"name": "MACD Fast (8/17/9)",   "module": "macd",       "class": "MACDStrategy",          "params": {"strategy_id": "macd-fast", "fast_period": 8, "slow_period": 17}},
    {"name": "MACD (12/26/9)",       "module": "macd",       "class": "MACDStrategy",          "params": {}},
    {"name": "Breakout (10-bar)",    "module": "breakout",   "class": "BreakoutStrategy",      "params": {"strategy_id": "breakout-fast", "period": 10}},
    {"name": "SMA Crossover (5/20)", "module": "momentum",   "class": "MomentumStrategy",      "params": {"strategy_id": "momentum-fast", "fast": 5, "slow": 20}},
    {"name": "VWAP TrendFollow",     "module": "vwap",       "class": "VWAPStrategy",          "params": {"strategy_id": "vwap-agg", "trend_sma": 30, "cross_confirm_bars": 1}},
    {"name": "Breakout (20-bar)",    "module": "breakout",   "class": "BreakoutStrategy",      "params": {}},
    {"name": "SMA Crossover (10/30)","module": "momentum",   "class": "MomentumStrategy",      "params": {}},
    {"name": "VWAP TrendFollow Std", "module": "vwap",       "class": "VWAPStrategy",          "params": {"trend_sma": 40, "cross_confirm_bars": 2}},
    {"name": "Triple EMA (5/13/26)", "module": "triple_ema", "class": "TripleEMAStrategy",     "params": {}},
    {"name": "BB Momentum (1.5x)",   "module": "bollinger",  "class": "BollingerBandsStrategy", "params": {"strategy_id": "bbands-tight", "num_std": 1.5, "trend_period": 40}},
    {"name": "RSI Momentum (10)",    "module": "rsi",        "class": "RSIStrategy",           "params": {"strategy_id": "rsi-agg", "period": 10, "trend_sma": 40, "fast_sma": 8}},
]

SYMBOLS = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "NIFTY", "SBIN", "ICICIBANK", "BAJFINANCE"]
N_BARS = 500
ROUND_INTERVAL_SECONDS = 30  # time between rounds


class PaperTradingService:
    """Manages continuous paper trading in the background."""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None
        self._round_count = 0
        self._total_sessions = 0
        self._started_at: datetime | None = None
        self._last_round_at: datetime | None = None
        self._strategy_stats: dict[str, dict[str, Any]] = {}
        self._recent_results: list[dict[str, Any]] = []
        self._max_recent = 50

    @property
    def status(self) -> dict[str, Any]:
        """Current service status."""
        rankings = sorted(
            self._strategy_stats.values(),
            key=lambda s: s.get("sharpe", 0),
            reverse=True,
        )
        return {
            "running": self._running,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_round_at": self._last_round_at.isoformat() if self._last_round_at else None,
            "rounds_completed": self._round_count,
            "total_sessions": self._total_sessions,
            "strategies_tracked": len(self._strategy_stats),
            "strategy_rankings": rankings,
            "recent_results": self._recent_results[-10:],
        }

    def start(self) -> None:
        """Start the background paper trading loop."""
        if self._running:
            return
        self._running = True
        self._started_at = datetime.now(timezone.utc)
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Paper trading service started")

    def stop(self) -> None:
        """Stop the background paper trading loop."""
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("Paper trading service stopped")

    def _make_factory(self, config: dict):
        """Create a strategy factory from config."""
        import importlib

        mod = importlib.import_module(f"app.strategy.{config['module']}")
        cls = getattr(mod, config["class"])
        params = config.get("params", {})

        def factory(bus, clock):
            return cls(bus, clock, **params)
        return factory

    async def _run_loop(self) -> None:
        """Main loop: runs paper trading rounds continuously."""
        logger.info("Paper trading loop starting — %d strategies, %d symbols",
                    len(STRATEGY_CONFIGS), len(SYMBOLS))

        while self._running:
            try:
                await self._run_round()
                self._round_count += 1
                self._last_round_at = datetime.now(timezone.utc)

                # Log progress every 5 rounds
                if self._round_count % 5 == 0:
                    self._log_summary()

                # Wait before next round
                await asyncio.sleep(ROUND_INTERVAL_SECONDS)

            except asyncio.CancelledError:
                logger.info("Paper trading loop cancelled")
                break
            except Exception as e:
                logger.error("Paper trading round failed: %s", e, exc_info=True)
                await asyncio.sleep(5)

    async def _run_round(self) -> None:
        """Run one round: all strategies on the same seed for fair comparison."""
        seed = random.randint(1, 100_000)

        for config in STRATEGY_CONFIGS:
            if not self._running:
                break
            try:
                result = await asyncio.to_thread(
                    self._run_single_session, config, seed
                )
                self._record_result(config["name"], result, seed)
                self._total_sessions += 1
            except Exception as e:
                logger.warning("Strategy %s failed (seed=%d): %s",
                              config["name"], seed, e)

    def _run_single_session(self, config: dict, seed: int) -> dict[str, Any]:
        """Run a single paper trading session (blocking)."""
        from app.engine.runner import PaperSession

        factory = self._make_factory(config)
        session = PaperSession(
            symbols=SYMBOLS,
            n_bars=N_BARS,
            seed=seed,
            strategy_factory=factory,
            auto_release_max_tier=3,
            approver_max_tier=3,
            enable_tca=True,
        )
        summary = session.run()

        # Compute unrealized P&L
        unrealized = 0.0
        for sym in SYMBOLS:
            qty, avg = session.tracker.position(sym)
            if qty != 0:
                lp = summary["last_prices"].get(sym, avg)
                unrealized += qty * (lp - avg)

        summary["unrealized_pnl"] = unrealized
        summary["total_pnl"] = summary["realized_pnl_total"] + unrealized
        return summary

    def _record_result(self, name: str, result: dict, seed: int) -> None:
        """Record result and update running statistics."""
        pnl = result["total_pnl"]
        fills = result.get("fills", 0)

        # Update per-strategy stats
        stats = self._strategy_stats.setdefault(name, {
            "name": name,
            "sessions": 0,
            "wins": 0,
            "total_pnl": 0.0,
            "pnl_list": [],
            "total_fills": 0,
            "sharpe": 0.0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
            "best_pnl": float("-inf"),
            "worst_pnl": float("inf"),
        })

        stats["sessions"] += 1
        stats["total_pnl"] += pnl
        stats["pnl_list"].append(pnl)
        stats["total_fills"] += fills
        if pnl > 0:
            stats["wins"] += 1
        stats["best_pnl"] = max(stats["best_pnl"], pnl)
        stats["worst_pnl"] = min(stats["worst_pnl"], pnl)

        # Recompute aggregate metrics
        n = stats["sessions"]
        stats["win_rate"] = round(stats["wins"] / n * 100, 1)
        stats["avg_pnl"] = round(stats["total_pnl"] / n, 2)

        pnl_list = stats["pnl_list"]
        avg = stats["avg_pnl"]
        if n > 1:
            std = math.sqrt(sum((p - avg) ** 2 for p in pnl_list) / (n - 1))
            stats["sharpe"] = round(avg / std, 3) if std > 0 else 0
        stats["best_pnl"] = round(stats["best_pnl"], 2)
        stats["worst_pnl"] = round(stats["worst_pnl"], 2)

        # Keep pnl_list bounded (for memory)
        if len(stats["pnl_list"]) > 200:
            stats["pnl_list"] = stats["pnl_list"][-200:]

        # Recent results log
        self._recent_results.append({
            "strategy": name,
            "pnl": round(pnl, 2),
            "fills": fills,
            "seed": seed,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._recent_results) > self._max_recent:
            self._recent_results = self._recent_results[-self._max_recent:]

    def _log_summary(self) -> None:
        """Log a summary of current performance."""
        rankings = sorted(
            self._strategy_stats.values(),
            key=lambda s: s.get("sharpe", 0),
            reverse=True,
        )
        logger.info("=" * 80)
        logger.info("PAPER TRADING ROUND %d | %d total sessions",
                    self._round_count, self._total_sessions)
        logger.info("%-30s %10s %8s %8s %6s", "Strategy", "Avg P&L", "Sharpe", "Win%", "Runs")
        logger.info("-" * 70)
        for r in rankings[:5]:
            logger.info("%-30s %+10.2f %8.3f %7.1f%% %6d",
                       r["name"], r["avg_pnl"], r["sharpe"],
                       r["win_rate"], r["sessions"])
        logger.info("=" * 80)


# Singleton instance
paper_trading_service = PaperTradingService()
