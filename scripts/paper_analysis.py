"""Run multiple paper trading sessions with different seeds and symbols
to build a comprehensive win probability analysis."""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.stdout.reconfigure(encoding='utf-8')

from app.engine.runner import PaperSession
from app.risk.limits import RiskLimits

SYMBOLS = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "NIFTY"]
N_BARS = 1000
SEEDS = [42, 123, 456, 789, 1001, 2025, 3333, 5555, 7777, 9999]

results = []

print("=" * 80)
print("MULTI-SEED PAPER TRADING ANALYSIS")
print(f"Symbols: {', '.join(SYMBOLS)}")
print(f"Bars per symbol: {N_BARS} | Total sessions: {len(SEEDS)}")
print("=" * 80)

for seed in SEEDS:
    session = PaperSession(
        SYMBOLS,
        n_bars=N_BARS,
        seed=seed,
        journal_path=None,  # no journaling for speed
        enable_tca=True,
        enable_slow_path=True,
    )
    summary = session.run()
    
    # Calculate win/loss from fill events
    buys = sells = 0
    buy_pnl = sell_pnl = 0.0
    
    for event in session.bus.events:
        if event.stream == "exec.fills":
            fill = event.payload
            side = fill.get("side", "")
            if side == "BUY":
                buys += 1
            elif side == "SELL":
                sells += 1

    # Calculate unrealized PnL from positions
    unrealized_pnl = 0.0
    for sym in SYMBOLS:
        qty, avg_cost = session.tracker.position(sym)
        last = summary["last_prices"].get(sym, 0)
        if qty != 0 and last > 0:
            unrealized_pnl += qty * (last - avg_cost)
    
    total_pnl = summary["realized_pnl_total"] + unrealized_pnl
    
    tca_summary = summary.get("tca", {})
    
    result = {
        "seed": seed,
        "intents": summary["intents"],
        "approved": summary["approved"],
        "rejected": summary["rejected"],
        "fills": summary["fills"],
        "realized_pnl": round(summary["realized_pnl_total"], 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "tca_avg_slippage_bps": tca_summary.get("avg_slippage_bps"),
        "tca_fill_rate": tca_summary.get("fill_rate_pct"),
    }
    results.append(result)
    
    pnl_emoji = "+" if total_pnl >= 0 else ""
    print(f"  Seed {seed:>5}: {summary['fills']:>3} fills | "
          f"Realized: {pnl_emoji}{summary['realized_pnl_total']:>10.2f} | "
          f"Unrealized: {pnl_emoji if unrealized_pnl >= 0 else ''}{unrealized_pnl:>10.2f} | "
          f"Total: {pnl_emoji if total_pnl >= 0 else ''}{total_pnl:>10.2f}")

print("\n" + "=" * 80)
print("AGGREGATE STATISTICS")
print("=" * 80)

total_sessions = len(results)
winning_sessions = sum(1 for r in results if r["total_pnl"] > 0)
losing_sessions = total_sessions - winning_sessions

total_fills = sum(r["fills"] for r in results)
total_realized = sum(r["realized_pnl"] for r in results)
total_unrealized = sum(r["unrealized_pnl"] for r in results)
total_all_pnl = sum(r["total_pnl"] for r in results)

avg_pnl = total_all_pnl / total_sessions
max_pnl = max(r["total_pnl"] for r in results)
min_pnl = min(r["total_pnl"] for r in results)
avg_fills = total_fills / total_sessions

profitable_pnls = [r["total_pnl"] for r in results if r["total_pnl"] > 0]
losing_pnls = [r["total_pnl"] for r in results if r["total_pnl"] <= 0]

avg_win = sum(profitable_pnls) / len(profitable_pnls) if profitable_pnls else 0
avg_loss = sum(losing_pnls) / len(losing_pnls) if losing_pnls else 0

print(f"\n  Sessions run:          {total_sessions}")
print(f"  Winning sessions:     {winning_sessions} ({winning_sessions/total_sessions*100:.0f}%)")
print(f"  Losing sessions:      {losing_sessions} ({losing_sessions/total_sessions*100:.0f}%)")
print(f"\n  Win probability:      {winning_sessions/total_sessions*100:.1f}%")
print(f"\n  Total fills:          {total_fills}")
print(f"  Avg fills/session:    {avg_fills:.1f}")
print(f"\n  Total realized P&L:   {'+' if total_realized >= 0 else ''}{total_realized:>12.2f}")
print(f"  Total unrealized P&L: {'+' if total_unrealized >= 0 else ''}{total_unrealized:>12.2f}")
print(f"  TOTAL P&L:            {'+' if total_all_pnl >= 0 else ''}{total_all_pnl:>12.2f}")
print(f"\n  Avg P&L per session:  {'+' if avg_pnl >= 0 else ''}{avg_pnl:>12.2f}")
print(f"  Best session:         +{max_pnl:>12.2f} (seed {[r['seed'] for r in results if r['total_pnl']==max_pnl][0]})")
print(f"  Worst session:        {'+' if min_pnl >= 0 else ''}{min_pnl:>12.2f} (seed {[r['seed'] for r in results if r['total_pnl']==min_pnl][0]})")
print(f"\n  Avg winning session:  +{avg_win:>12.2f}")
if avg_loss:
    print(f"  Avg losing session:   {avg_loss:>12.2f}")
    if avg_loss != 0:
        print(f"  Win/Loss ratio:       {abs(avg_win/avg_loss):>12.2f}")
else:
    print(f"  Avg losing session:   N/A (no losing sessions)")

# Sharpe-like metric
import statistics
pnls = [r["total_pnl"] for r in results]
if len(pnls) > 1:
    pnl_std = statistics.stdev(pnls)
    sharpe_like = avg_pnl / pnl_std if pnl_std > 0 else float('inf')
    print(f"\n  P&L Std Dev:          {pnl_std:>12.2f}")
    print(f"  Sharpe-like ratio:    {sharpe_like:>12.3f}")

print("\n" + "=" * 80)
print("PER-SEED BREAKDOWN")
print("=" * 80)
print(f"  {'Seed':>6} | {'Fills':>5} | {'Realized':>12} | {'Unrealized':>12} | {'Total P&L':>12} | {'Result':>8}")
print(f"  {'-'*6} | {'-'*5} | {'-'*12} | {'-'*12} | {'-'*12} | {'-'*8}")
for r in results:
    tag = "WIN" if r["total_pnl"] > 0 else "LOSS"
    color = tag
    print(f"  {r['seed']:>6} | {r['fills']:>5} | {r['realized_pnl']:>12.2f} | "
          f"{r['unrealized_pnl']:>12.2f} | {r['total_pnl']:>12.2f} | {tag:>8}")

print("\n" + "=" * 80)
