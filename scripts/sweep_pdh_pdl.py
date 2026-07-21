"""Grid-search the PDH/PDL exit levers on REAL stored bars.

Finds the exit configuration (R-target take-profit, break-even, arm expiry,
one-trade-per-day, session entry window) that turns the strategy's expectancy
positive on actual NSE history — instead of guessing which "improvement" helps.

    python scripts/sweep_pdh_pdl.py
    python scripts/sweep_pdh_pdl.py --interval 30minute --top 15
"""
from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.stdout.reconfigure(encoding="utf-8")

from app.marketdata.bridge import load_store_bars  # noqa: E402
from backtest_pdh_pdl import simulate, _to_rows      # noqa: E402

# A liquid, sector-spread basket — the strategy has to work across names, not
# just curve-fit one.
BASKET = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN", "ICICIBANK", "ITC", "AXISBANK",
    "LT", "BHARTIARTL", "KOTAKBANK", "HINDUNILVR", "MARUTI", "SUNPHARMA",
]


def pooled(results):
    trs = [t for r in results for t in r.trades]
    if not trs:
        return None
    wins = [t for t in trs if t.r_mult > 0]
    losses = [t for t in trs if t.r_mult <= 0]
    gw = sum(t.r_mult for t in wins)
    gl = -sum(t.r_mult for t in losses)
    exits = {}
    for t in trs:
        exits[t.exit_reason] = exits.get(t.exit_reason, 0) + 1
    return {
        "trades": len(trs),
        "win": len(wins) / len(trs) * 100,
        "pf": (gw / gl) if gl > 0 else float("inf"),
        "exp": sum(t.r_mult for t in trs) / len(trs),
        "ret": sum(r.total_return_pct for r in results) / len(results),
        "dd": max(r.max_drawdown_pct for r in results),
        "exits": exits,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", default="30minute")
    ap.add_argument("--symbols", default=",".join(BASKET))
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--min-trades", type=int, default=120)
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    # Load once; reuse across every config.
    bars_by_sym = {}
    for s in symbols:
        try:
            bars_by_sym[s] = _to_rows(load_store_bars([s], interval=args.interval, last_n=None))
        except Exception as exc:
            print(f"skip {s}: {exc}")
    print(f"loaded {len(bars_by_sym)} symbols @ {args.interval}\n")

    # The grid. invert=False is the reversal (fade the sweep); invert=True is the
    # continuation (trade with the break) — the hypothesis under test here.
    grid = list(itertools.product(
        [False, True],               # invert (reversal vs continuation)
        ["trigger", "sweep"],        # sl_mode (reversal only; ignored when invert)
        [0.0, 1.5, 2.0, 3.0],        # tp_r
        [0.0, 1.0],                  # be_r
        [0, 4, 8],                   # entry_window
    ))

    rows_out = []
    for inv, slm, tp_r, be_r, win in grid:
        if inv and slm == "sweep":
            continue  # sl_mode does not apply to the continuation machine
        results = [
            simulate(rows, sym, seed=-1, tp_r=tp_r, be_r=be_r,
                     entry_window=win, sl_mode=slm, invert=inv)
            for sym, rows in bars_by_sym.items()
        ]
        p = pooled(results)
        if p is None or p["trades"] < args.min_trades:
            continue
        rows_out.append((inv, slm, tp_r, be_r, win, p))

    # Rank by expectancy, then PF.
    rows_out.sort(key=lambda x: (x[5]["exp"], x[5]["pf"]), reverse=True)

    hdr = f"{'mode':>6}{'sl':>8}{'tp_r':>5}{'be_r':>5}{'win':>4}{'trades':>7}{'win%':>7}{'PF':>7}{'exp(R)':>8}{'avgRet%':>8}{'maxDD%':>7}"
    print(hdr)
    print("-" * len(hdr))
    for inv, slm, tp_r, be_r, win, p in rows_out[:args.top]:
        pf = "inf" if p["pf"] == float("inf") else f"{p['pf']:.2f}"
        mode = "cont" if inv else "rev"
        print(f"{mode:>6}{slm:>8}{tp_r:>5.1f}{be_r:>5.1f}{win:>4}"
              f"{p['trades']:>7}{p['win']:>6.1f}%{pf:>7}{p['exp']:>+8.3f}{p['ret']:>+8.1f}{p['dd']:>7.1f}")

    print("-" * len(hdr))
    if rows_out:
        b = rows_out[0]
        print(f"\nBEST: mode={'continuation' if b[0] else 'reversal'} sl_mode={b[1]} "
              f"tp_r={b[2]} be_r={b[3]} entry_window={b[4]}")
        print(f"  exits {b[5]['exits']}")
    # Best of each mode for a clean head-to-head.
    for inv, name in ((False, "reversal"), (True, "continuation")):
        best = next((r for r in rows_out if r[0] == inv), None)
        if best:
            p = best[5]
            print(f"  best {name:<13}: exp={p['exp']:+.3f}R  PF={p['pf']:.2f}  win={p['win']:.1f}%  "
                  f"(tp_r={best[2]}, entry_window={best[4]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
