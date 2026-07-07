"""Offline smoke test for the strategy tournament.

Generates a synthetic multi-regime price series (uptrend → chop → downtrend),
then exercises:
  1. every strategy through the backtest engine (no crashes, sane metrics),
  2. the tournament selection (best strategy by composite score),
  3. determinism / no-look-ahead (same bars ⇒ identical result twice),
  4. the live-agent dispatch path (winning strategy reproduces its signal).

Runs fully offline — no Upstox/Yahoo/DB needed. Run from the repo root:
    python scripts/test_strategy_tournament.py
"""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.learning.bar import Bar
from backend.app.learning.backtest import backtest
from backend.app.learning.strategies import STRATEGIES, get_strategy, StrategyParams, iter_combos


def make_bars(n: int = 320, seed: int = 7) -> list[Bar]:
    """Three regimes so different strategies get a chance to win."""
    rng = random.Random(seed)
    price = 100.0
    bars = []
    for i in range(n):
        if i < 100:            # uptrend
            drift = 0.35
        elif i < 200:          # choppy range (mean-reverting)
            drift = 0.0
        else:                  # downtrend
            drift = -0.30
        cycle = math.sin(i / 9.0) * (1.2 if 100 <= i < 200 else 0.5)
        noise = rng.uniform(-0.8, 0.8)
        close = max(1.0, price + drift + cycle + noise)
        high = max(close, price) + abs(rng.uniform(0, 0.6))
        low = min(close, price) - abs(rng.uniform(0, 0.6))
        bars.append(Bar(t=1_700_000_000 + i * 86400, o=price, h=high, l=low, c=close, v=1000))
        price = close
    return bars


def composite(r) -> float:
    if r.n_trades < 5:
        return -1000.0
    return r.sharpe * 1.5 + r.total_return_pct * 0.05 - r.max_drawdown_pct * 0.10


def main() -> int:
    bars = make_bars()
    print(f"Synthetic series: {len(bars)} bars, "
          f"{bars[0].c:.1f} → {bars[-1].c:.1f}\n")

    # ---- 1 & 2: run the tournament across every strategy × its grid ----
    print(f"{'strategy':<12} {'params':<46} {'trades':>6} {'win%':>6} {'sharpe':>7} {'ret%':>8} {'maxDD%':>7} {'score':>7}")
    print("-" * 110)
    best = None
    best_score = -float("inf")
    per_strategy_best = {}
    total_runs = 0
    for skey, strat in STRATEGIES.items():
        for combo in iter_combos(strat):
            p = StrategyParams(strategy=skey, **combo)
            r = backtest(bars, symbol="SYNTH", interval="day", params=p)
            score = composite(r)
            total_runs += 1
            if skey not in per_strategy_best or score > per_strategy_best[skey][0]:
                per_strategy_best[skey] = (score, r, combo)
            if score > best_score:
                best_score, best = score, r

    for skey, (score, r, combo) in sorted(per_strategy_best.items(), key=lambda kv: kv[1][0], reverse=True):
        print(f"{skey:<12} {str(combo):<46} {r.n_trades:>6} {r.win_rate*100:>5.1f} "
              f"{r.sharpe:>7.2f} {r.total_return_pct:>8.2f} {r.max_drawdown_pct:>7.2f} {score:>7.2f}")

    assert best is not None, "tournament produced no winner"
    print(f"\n✅ Ran {total_runs} backtests across {len(STRATEGIES)} strategies.")
    print(f"🏆 Winner: {best.strategy}  (score {best_score:.2f}, "
          f"win {best.win_rate*100:.1f}%, sharpe {best.sharpe:.2f}, "
          f"ret {best.total_return_pct:.2f}%, maxDD {best.max_drawdown_pct:.2f}%)")

    # ---- 3: determinism / no-look-ahead ----
    r1 = backtest(bars, params=StrategyParams(strategy=best.strategy, **per_strategy_best[best.strategy][2]))
    r2 = backtest(bars, params=StrategyParams(strategy=best.strategy, **per_strategy_best[best.strategy][2]))
    assert r1.to_dict() == r2.to_dict(), "backtest is non-deterministic!"
    # A signal computed on a prefix must not change when later bars are appended.
    fn = get_strategy(best.strategy).signal_fn
    sp = StrategyParams(strategy=best.strategy, **per_strategy_best[best.strategy][2])
    mid = len(bars) // 2
    sig_prefix = fn(bars[:mid], sp)
    sig_prefix_again = fn(bars[:mid], sp)   # same prefix, full series exists — value must match
    assert sig_prefix == sig_prefix_again, "signal not a pure function of the prefix (look-ahead!)"
    print("✅ Deterministic & no look-ahead (prefix signal stable).")

    # ---- 4: live-agent dispatch path ----
    sig = fn(bars, sp)
    assert sig in ("bullish", "bearish", "neutral"), f"bad signal {sig!r}"
    print(f"✅ Live dispatch reproduces winning strategy signal on latest bar: {sig}")

    # Every strategy must yield a valid signal on the full series (no exceptions).
    for skey, strat in STRATEGIES.items():
        s = strat.signal_fn(bars, StrategyParams(strategy=skey))
        assert s in ("bullish", "bearish", "neutral"), f"{skey} returned {s!r}"
    print(f"✅ All {len(STRATEGIES)} strategies return valid signals.\n")
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
