"""Multi-strategy backtesting framework.

Runs ALL registered strategies against identical synthetic market data across
multiple seeds, then produces a ranked comparison table showing which strategy
delivers the best risk-adjusted returns.

Usage:
    python scripts/backtest_all_strategies.py
"""
from __future__ import annotations

import sys, math
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.stdout.reconfigure(encoding="utf-8")

from app.bus.base import EventBus
from app.core.clock import Clock
from app.engine.runner import PaperSession
from app.strategy.momentum import MomentumStrategy
from app.strategy.rsi import RSIStrategy
from app.strategy.bollinger import BollingerBandsStrategy
from app.strategy.macd import MACDStrategy
from app.strategy.vwap import VWAPStrategy
from app.strategy.triple_ema import TripleEMAStrategy
from app.strategy.breakout import BreakoutStrategy

# ── Strategy registry ──────────────────────────────────────────────────
STRATEGIES: dict[str, Any] = {
    "SMA Crossover (10/30)": lambda bus, clock: MomentumStrategy(bus, clock, fast=10, slow=30),
    "SMA Crossover (5/20)":  lambda bus, clock: MomentumStrategy(bus, clock, strategy_id="momentum-fast", fast=5, slow=20),
    "SMA Crossover (20/50)": lambda bus, clock: MomentumStrategy(bus, clock, strategy_id="momentum-slow", fast=20, slow=50),
    "RSI TrendFilter (14)":  lambda bus, clock: RSIStrategy(bus, clock, trend_sma=50, fast_sma=10),
    "RSI TrendFilter (10)":  lambda bus, clock: RSIStrategy(bus, clock, strategy_id="rsi-agg", period=10, oversold=35, overbought=65, trend_sma=40, fast_sma=8),
    "BB TrendFilter (20, 2x)":    lambda bus, clock: BollingerBandsStrategy(bus, clock, trend_period=50),
    "BB TrendFilter (20, 1.5x)":  lambda bus, clock: BollingerBandsStrategy(bus, clock, strategy_id="bbands-tight", num_std=1.5, trend_period=40),
    "MACD (12/26/9)":        lambda bus, clock: MACDStrategy(bus, clock),
    "MACD Fast (8/17/9)":    lambda bus, clock: MACDStrategy(bus, clock, strategy_id="macd-fast", fast_period=8, slow_period=17),
    "VWAP TrendFilter":      lambda bus, clock: VWAPStrategy(bus, clock, trend_sma=40, cross_confirm_bars=2),
    "VWAP TrendFilter Agg":  lambda bus, clock: VWAPStrategy(bus, clock, strategy_id="vwap-agg", trend_sma=30, cross_confirm_bars=1),
    "Triple EMA (5/13/26)":  lambda bus, clock: TripleEMAStrategy(bus, clock),
    "Breakout (20-bar)":     lambda bus, clock: BreakoutStrategy(bus, clock),
    "Breakout (10-bar)":     lambda bus, clock: BreakoutStrategy(bus, clock, strategy_id="breakout-fast", period=10),
}

SYMBOLS = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "NIFTY"]
SEEDS = [42, 123, 456, 789, 1001, 2025, 3333, 5555, 7777, 9999]
N_BARS = 1000


def run_session(strategy_factory, seed: int) -> dict[str, Any]:
    session = PaperSession(
        symbols=SYMBOLS,
        n_bars=N_BARS,
        seed=seed,
        strategy_factory=strategy_factory,
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


def main():
    all_results: dict[str, list[dict]] = {}

    print("=" * 100)
    print("MULTI-STRATEGY BACKTESTING ENGINE")
    print(f"  Symbols: {', '.join(SYMBOLS)}")
    print(f"  Bars per symbol: {N_BARS}")
    print(f"  Seeds: {len(SEEDS)}")
    print(f"  Strategies: {len(STRATEGIES)}")
    print(f"  Total simulations: {len(STRATEGIES) * len(SEEDS)}")
    print("=" * 100)

    for name, factory in STRATEGIES.items():
        results = []
        for seed in SEEDS:
            try:
                summary = run_session(factory, seed)
                results.append(summary)
            except Exception as e:
                print(f"  WARN: {name} seed={seed} failed: {e}")
                results.append({
                    "total_pnl": 0, "fills": 0, "intents": 0,
                    "approved": 0, "rejected": 0,
                })
        all_results[name] = results
        avg_pnl = sum(r["total_pnl"] for r in results) / len(results)
        print(f"  {name:<30}  avg P&L = {avg_pnl:>+12,.2f}")

    # ── Compute aggregated metrics ──────────────────────────────────
    rankings = []
    for name, results in all_results.items():
        pnls = [r["total_pnl"] for r in results]
        fills_list = [r.get("fills", 0) for r in results]
        intents_list = [r.get("intents", 0) for r in results]
        rejected_list = [r.get("rejected", 0) for r in results]

        avg_pnl = sum(pnls) / len(pnls)
        std_pnl = math.sqrt(sum((p - avg_pnl) ** 2 for p in pnls) / max(len(pnls) - 1, 1))
        sharpe = avg_pnl / std_pnl if std_pnl > 0 else float("inf")
        win_sessions = sum(1 for p in pnls if p > 0)
        win_rate = win_sessions / len(pnls) * 100
        max_pnl = max(pnls)
        min_pnl = min(pnls)
        avg_fills = sum(fills_list) / len(fills_list)
        avg_intents = sum(intents_list) / len(intents_list)
        avg_rejected = sum(rejected_list) / len(rejected_list)
        approval_rate = (
            (avg_intents - avg_rejected) / avg_intents * 100
            if avg_intents > 0 else 0
        )

        rankings.append({
            "name": name,
            "avg_pnl": avg_pnl,
            "std_pnl": std_pnl,
            "sharpe": sharpe,
            "win_rate": win_rate,
            "win_sessions": win_sessions,
            "total_sessions": len(pnls),
            "max_pnl": max_pnl,
            "min_pnl": min_pnl,
            "avg_fills": avg_fills,
            "avg_intents": avg_intents,
            "approval_rate": approval_rate,
            "pnl_per_fill": avg_pnl / avg_fills if avg_fills > 0 else 0,
        })

    # Sort by Sharpe ratio (risk-adjusted returns)
    rankings.sort(key=lambda x: x["sharpe"], reverse=True)

    # ── Print results ───────────────────────────────────────────────
    print("\n" + "=" * 120)
    print("STRATEGY COMPARISON (ranked by Sharpe Ratio)")
    print("=" * 120)

    header = (
        f"{'Rank':>4}  {'Strategy':<30}  {'Avg P&L':>12}  {'Std Dev':>10}  "
        f"{'Sharpe':>8}  {'Win%':>6}  {'Wins':>5}  {'Fills':>6}  "
        f"{'P&L/Fill':>10}  {'Best':>12}  {'Worst':>12}  {'Appr%':>6}"
    )
    print(header)
    print("-" * 120)

    for rank, r in enumerate(rankings, 1):
        medal = ""
        if rank == 1:
            medal = " <<< BEST"
        elif rank == len(rankings):
            medal = " <<< WORST"

        line = (
            f"{rank:>4}  {r['name']:<30}  {r['avg_pnl']:>+12,.2f}  "
            f"{r['std_pnl']:>10,.2f}  {r['sharpe']:>8.3f}  "
            f"{r['win_rate']:>5.1f}%  {r['win_sessions']:>3}/{r['total_sessions']}  "
            f"{r['avg_fills']:>6.0f}  {r['pnl_per_fill']:>+10.2f}  "
            f"{r['max_pnl']:>+12,.2f}  {r['min_pnl']:>+12,.2f}  "
            f"{r['approval_rate']:>5.1f}%{medal}"
        )
        print(line)

    print("=" * 120)

    # ── Category analysis ───────────────────────────────────────────
    print("\nSTRATEGY CATEGORY ANALYSIS:")
    print("-" * 60)

    categories = {
        "Trend Following": ["SMA Crossover (10/30)", "SMA Crossover (5/20)", "SMA Crossover (20/50)",
                            "MACD (12/26/9)", "MACD Fast (8/17/9)", "Triple EMA (5/13/26)"],
        "Mean Reversion":  ["RSI TrendFilter (14)", "RSI TrendFilter (10)",
                            "BB TrendFilter (20, 2x)", "BB TrendFilter (20, 1.5x)",
                            "VWAP TrendFilter", "VWAP TrendFilter Agg"],
        "Breakout":        ["Breakout (20-bar)", "Breakout (10-bar)"],
    }

    rank_lookup = {r["name"]: r for r in rankings}
    for cat_name, members in categories.items():
        cat_pnls = []
        cat_sharpes = []
        for m in members:
            if m in rank_lookup:
                cat_pnls.append(rank_lookup[m]["avg_pnl"])
                cat_sharpes.append(rank_lookup[m]["sharpe"])
        if cat_pnls:
            avg_cat_pnl = sum(cat_pnls) / len(cat_pnls)
            avg_cat_sharpe = sum(cat_sharpes) / len(cat_sharpes)
            best_in_cat = max(members, key=lambda m: rank_lookup.get(m, {}).get("sharpe", 0))
            print(
                f"  {cat_name:<20}  Avg P&L: {avg_cat_pnl:>+10,.2f}  "
                f"Avg Sharpe: {avg_cat_sharpe:>6.3f}  "
                f"Best: {best_in_cat}"
            )

    # ── Per-seed heatmap ────────────────────────────────────────────
    print(f"\nPER-SEED P&L HEATMAP:")
    print(f"{'Strategy':<30}", end="")
    for seed in SEEDS:
        print(f"  {seed:>8}", end="")
    print()
    print("-" * (30 + len(SEEDS) * 10))

    for name in [r["name"] for r in rankings]:
        results = all_results[name]
        print(f"{name:<30}", end="")
        for r in results:
            pnl = r["total_pnl"]
            marker = "+" if pnl > 0 else "-"
            print(f"  {pnl:>+8,.0f}", end="")
        print()

    print("=" * 120)
    print("  CONCLUSION: The best strategy by risk-adjusted returns is:")
    best = rankings[0]
    print(f"  >>> {best['name']} <<<")
    print(f"      Sharpe: {best['sharpe']:.3f} | Avg P&L: {best['avg_pnl']:+,.2f} | Win Rate: {best['win_rate']:.0f}%")
    print("=" * 120)


if __name__ == "__main__":
    main()
