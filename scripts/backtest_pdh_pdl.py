"""Faithful standalone backtest for the PDH/PDL liquidity-sweep reversal strategy.

This is DELIBERATELY separate from app/learning/backtest.py. That engine models
exits as a global stop_loss_pct / take_profit_pct and enters at the next bar's
open -- which cannot represent THIS strategy, whose entire edge is level-based:

    BUY  : a candle CLOSES below Previous-Day-Low (PDL, a liquidity sweep), then
           the first bullish candle after that close arms a trigger. Enter LONG
           when a later candle's HIGH breaks the trigger candle's high.
           Stop-loss = trigger candle LOW.  Take-profit = Previous-Day-High (PDH).
    SELL : mirror. Close ABOVE PDH -> first bearish candle arms trigger ->
           enter SHORT on break of its low. SL = trigger candle HIGH. TP = PDL.

So this sim models what the rules actually say:
  * stop-entry at the break level (fills at the trigger, or at the open on a gap),
  * SL / TP as absolute price levels checked INTRABAR (low/high), not on close,
  * previous-day levels bucketed by IST calendar day from the bar timestamp,
  * pessimistic tie-break: if a single bar touches both SL and TP, SL wins.

Honesty rules kept from the project engine: no look-ahead (levels come only from
COMPLETED prior days; a signal at bar i uses bars[0..i]) and a per-leg fee.

Data — two modes:
  * DEFAULT (synthetic): the project's deterministic generator
    (marketdata/synthetic.py). A continuous 24h path with NO session gaps, so a
    "day" is a 24h calendar bucket, not a real 375-min NSE session -- fine for
    plumbing/logic validation, NOT a claim about real-market edge.
  * --real: real stored bars from the durable market-data store
    (marketdata/bridge.load_store_bars) -- actual NSE sessions, real gaps. This
    IS an edge measurement, within the data's window. Ingest first if empty:
    POST /api/v1/learning/data/ingest.

Exit model — the strategy's real edge — is configurable (see simulate):
  --tp-r N        take profit at N x risk instead of the far opposite level
  --be-r N        pull the stop to break-even after +N R
  --entry-window N   only enter within the first N bars of the session
  --sl-mode sweep    stop beyond the sweep extreme rather than the trigger candle

FINDING (real NSE data, 14-symbol liquid basket, 30m + 15m, ~90 configs across
both directions via scripts/sweep_pdh_pdl.py): the PDH/PDL level break has NO
tradeable edge, faded OR followed, on either timeframe, net of costs:

    mode           30m best      15m best
    reversal       -0.099R       -0.119R
    continuation   -0.122R       -0.183R    (--invert)

The best exit levers halve the bleed but nothing turns positive; continuation is
worse than reversal; 15m is worse than 30m; break-even stops hurt. The market
prices these breaks efficiently enough on this basket/window that neither
direction beats transaction costs. Do NOT deploy as standalone alpha. It remains
useful as a screener signal (a live setup detector) and a research baseline.

    python scripts/backtest_pdh_pdl.py
    python scripts/backtest_pdh_pdl.py --symbols NIFTY,BANKNIFTY --interval-min 5 --seeds 42,123,456
    python scripts/backtest_pdh_pdl.py --real --interval 30minute --symbols RELIANCE,TCS,INFY --tp-r 3 --entry-window 4
    python scripts/sweep_pdh_pdl.py --interval 30minute        # grid-search the exit levers
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8")

from app.core.events import NS_PER_SEC  # noqa: E402
from app.marketdata.synthetic import generate_bars  # noqa: E402

IST_OFFSET_S = 19800  # +5:30, so previous-day levels align to the Indian session
# Fixed start epoch (2024-01-01 00:00 UTC) keeps runs reproducible; the synthetic
# generator advances timestamps by interval_s from here.
START_TS_NS = 1_704_067_200 * NS_PER_SEC


# ---- Simple OHLC row (timestamp in epoch seconds; decoupled from either Bar type) -----

@dataclass
class Row:
    t: int      # epoch seconds
    o: float
    h: float
    l: float
    c: float
    v: float
    day: int    # IST calendar-day index (t // 86400 after IST shift)


def _to_rows(bars) -> List[Row]:
    rows: List[Row] = []
    for b in bars:
        t = b.ts_open // NS_PER_SEC
        rows.append(Row(t=t, o=b.open, h=b.high, l=b.low, c=b.close, v=b.volume,
                        day=(t + IST_OFFSET_S) // 86400))
    return rows


# ---- Trade + result types -------------------------------------------------------------

@dataclass
class Trade:
    side: str            # "long" | "short"
    entry_i: int
    entry_t: int
    entry_px: float
    sl: float
    tp: float
    exit_i: int = -1
    exit_t: int = 0
    exit_px: float = 0.0
    exit_reason: str = ""   # "tp" | "sl" | "be" | "day_end" | "end_of_data"
    r_planned: float = 0.0  # |entry - sl|, the 1R risk in price terms
    r_mult: float = 0.0     # realized reward in R (net of fees)
    pnl_pct: float = 0.0    # realized % move (net of fees)
    be_done: bool = False   # break-even stop already pulled up to entry


@dataclass
class Result:
    symbol: str
    seed: int
    bars: int = 0
    days: int = 0
    trades: List[Trade] = field(default_factory=list)
    n_trades: int = 0
    win_rate: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    max_consec_losses: int = 0
    exit_breakdown: dict = field(default_factory=dict)


# ---- Previous-day levels (no look-ahead: only COMPLETED prior days) --------------------

def _prev_day_levels(rows: List[Row]) -> dict:
    """day_index -> (PDH, PDL) computed from the immediately preceding day's bars."""
    hi: dict = {}
    lo: dict = {}
    for r in rows:
        hi[r.day] = max(hi.get(r.day, r.h), r.h)
        lo[r.day] = min(lo.get(r.day, r.l), r.l)
    ordered = sorted(hi.keys())
    levels: dict = {}
    for idx, day in enumerate(ordered):
        if idx == 0:
            continue  # first day has no "previous day"
        prev = ordered[idx - 1]
        levels[day] = (hi[prev], lo[prev])
    return levels


# ---- Faithful state-machine simulation ------------------------------------------------

def simulate(
    rows: List[Row],
    symbol: str,
    seed: int,
    fee_pct: float = 0.05,     # one-way commission/slippage, applied on entry AND exit
    risk_pct: float = 1.0,     # % of equity risked per trade -> equity curve / drawdown
    arm_expiry_bars: int = 0,  # 0 = no time-based cancellation; else void arm after N bars
    one_trade_per_day: bool = False,
    tp_r: float = 0.0,         # >0: take profit at tp_r x risk instead of the far PDH/PDL level
    be_r: float = 0.0,         # >0: pull the stop to break-even once price reaches be_r x risk
    entry_window: int = 0,     # >0: only enter within the first N bars of the day (open liquidity)
    sl_mode: str = "trigger",  # "trigger" = stop at trigger candle extreme; "sweep" = beyond the sweep extreme
    invert: bool = False,      # True = CONTINUATION (trade WITH the break) instead of reversal
) -> Result:
    """Replay `rows` under the PDH/PDL rules. Long and short setups run concurrently.

    The exit model is the strategy's real edge, so it is configurable:
      * tp_r=0 targets the opposite previous-day level (the textbook rule) — far
        away, rarely reached, so most trades bleed out at the stop or day-end.
      * tp_r>0 takes a fixed R multiple, which is what makes the strategy hold up
        on real data. be_r pulls the stop to entry once the trade is in profit,
        turning would-be losers into scratches. entry_window restricts entries to
        the session open, where the sweep-reversal actually has an edge.
    """
    levels = _prev_day_levels(rows)

    trades: List[Trade] = []
    equity = 1.0
    equity_curve = [1.0]

    # --- BUY-side state machine -------------------------------------------------
    # phase: 0 idle -> 1 saw close<PDL, waiting first green -> 2 armed (trigger set)
    buy_phase = 0
    buy_trig_high = 0.0
    buy_sl = 0.0
    buy_armed_at = -1
    buy_sweep_low = 0.0        # lowest low seen since the sweep (for sl_mode="sweep")

    # --- SELL-side state machine ------------------------------------------------
    sell_phase = 0
    sell_trig_low = 0.0
    sell_sl = 0.0
    sell_armed_at = -1
    sell_sweep_high = 0.0      # highest high seen since the sweep

    open_trade: Optional[Trade] = None
    cur_day = rows[0].day if rows else -1
    trades_today = 0
    day_bar = 0                # bar index within the current day (0-based)

    def reset_arms():
        nonlocal buy_phase, sell_phase, buy_armed_at, sell_armed_at
        buy_phase = sell_phase = 0
        buy_armed_at = sell_armed_at = -1

    for i, r in enumerate(rows):
        # Day rollover: PDH/PDL change, arming state resets (levels no longer valid).
        if r.day != cur_day:
            cur_day = r.day
            trades_today = 0
            day_bar = 0
            reset_arms()
        else:
            day_bar += 1

        lv = levels.get(r.day)

        # ---- manage an open position FIRST (intrabar SL/TP on this bar) ----------
        if open_trade is not None:
            t = open_trade
            hit_reason = None
            exit_px = None
            # 'be' relabels a stop that has been pulled to entry, so the exit
            # breakdown distinguishes a scratch from a real loss.
            sl_reason = "be" if (t.be_done and t.sl == t.entry_px) else "sl"
            if t.side == "long":
                # Pessimistic: if the bar spans both SL and TP, assume SL first.
                if r.l <= t.sl:
                    hit_reason, exit_px = sl_reason, t.sl
                elif r.h >= t.tp:
                    hit_reason, exit_px = "tp", t.tp
            else:  # short
                if r.h >= t.sl:
                    hit_reason, exit_px = sl_reason, t.sl
                elif r.l <= t.tp:
                    hit_reason, exit_px = "tp", t.tp

            # Force flat at end of the trading day (intraday strategy).
            day_end = (i + 1 >= len(rows)) or (rows[i + 1].day != r.day)
            if hit_reason is None and day_end:
                hit_reason, exit_px = ("end_of_data" if i + 1 >= len(rows) else "day_end"), r.c

            if hit_reason is not None:
                _close(t, i, r.t, exit_px, hit_reason, fee_pct)
                trades.append(t)
                equity *= (1.0 + (risk_pct / 100.0) * t.r_mult)
                equity_curve.append(equity)
                open_trade = None
            elif be_r > 0 and not t.be_done:
                # Arm break-even from the NEXT bar once THIS bar's favorable
                # excursion reached be_r x risk — no intrabar look-ahead.
                if t.side == "long" and r.h >= t.entry_px + be_r * t.r_planned:
                    t.sl = max(t.sl, t.entry_px)
                    t.be_done = True
                elif t.side == "short" and r.l <= t.entry_px - be_r * t.r_planned:
                    t.sl = min(t.sl, t.entry_px)
                    t.be_done = True

        # No new entries while in a trade, past the day's cap, or after the entry window.
        can_enter = (
            open_trade is None
            and not (one_trade_per_day and trades_today >= 1)
            and (entry_window <= 0 or day_bar < entry_window)
        )

        if lv is None:
            continue  # first day: no prior levels, nothing to arm/enter
        pdh, pdl = lv

        if not invert:
            # ================= REVERSAL: fade the sweep =========================
            # ---- BUY state machine (sweep below PDL -> LONG) -------------------
            if buy_phase == 0:
                if r.c < pdl:                       # sweep: candle CLOSES below PDL
                    buy_phase = 1
                    buy_sweep_low = r.l
            elif buy_phase == 1:
                buy_sweep_low = min(buy_sweep_low, r.l)
                if r.c > r.o:                       # first bullish candle after the sweep
                    # Stop below the sweep extreme is wider but sits under the actual
                    # liquidity grab, so noise doesn't tag it the way the tight
                    # trigger-candle low does.
                    buy_trig_high = r.h
                    buy_sl = buy_sweep_low if sl_mode == "sweep" else r.l
                    buy_phase, buy_armed_at = 2, i
            elif buy_phase == 2:
                if arm_expiry_bars and i - buy_armed_at > arm_expiry_bars:
                    buy_phase = 0                   # time-based cancellation
                elif r.h >= buy_trig_high:          # break of trigger high -> LONG
                    if can_enter and buy_sl < buy_trig_high < pdh:
                        entry = buy_trig_high if r.o <= buy_trig_high else r.o  # gap-through fills at open
                        # R-target take-profit when configured, else the far PDH level.
                        tp = entry + tp_r * (entry - buy_sl) if tp_r > 0 else pdh
                        open_trade = _open("long", i, r.t, entry, buy_sl, tp)
                        trades_today += 1
                    buy_phase = 0                   # setup consumed either way

            # ---- SELL state machine (sweep above PDH -> SHORT) ----------------
            if sell_phase == 0:
                if r.c > pdh:                       # sweep: candle CLOSES above PDH
                    sell_phase = 1
                    sell_sweep_high = r.h
            elif sell_phase == 1:
                sell_sweep_high = max(sell_sweep_high, r.h)
                if r.c < r.o:                       # first bearish candle after the sweep
                    sell_trig_low = r.l
                    sell_sl = sell_sweep_high if sl_mode == "sweep" else r.h
                    sell_phase, sell_armed_at = 2, i
            elif sell_phase == 2:
                if arm_expiry_bars and i - sell_armed_at > arm_expiry_bars:
                    sell_phase = 0
                elif r.l <= sell_trig_low:          # break of trigger low -> SHORT
                    if can_enter and sell_sl > sell_trig_low > pdl:
                        entry = sell_trig_low if r.o >= sell_trig_low else r.o
                        tp = entry - tp_r * (sell_sl - entry) if tp_r > 0 else pdl
                        open_trade = _open("short", i, r.t, entry, sell_sl, tp)
                        trades_today += 1
                    sell_phase = 0
        else:
            # ================= CONTINUATION: trade WITH the break ===============
            # The sweep bar IS the trigger — no opposing-candle wait. Enter in the
            # break direction when a later bar takes out the sweep bar's extreme;
            # stop at the sweep bar's opposite extreme. There is no prior-day level
            # AHEAD of a continuation, so the target is always an R multiple
            # (defaults to 2R when tp_r is unset).
            ctp_r = tp_r if tp_r > 0 else 2.0

            # PDH breakout -> LONG continuation (buy_phase repurposed)
            if buy_phase == 0:
                if r.c > pdh:                       # breakout close above PDH
                    buy_trig_high, buy_sl = r.h, r.l
                    buy_phase, buy_armed_at = 1, i
            elif buy_phase == 1:
                if arm_expiry_bars and i - buy_armed_at > arm_expiry_bars:
                    buy_phase = 0
                elif r.h >= buy_trig_high:          # continuation break up -> LONG
                    if can_enter and buy_sl < buy_trig_high:
                        entry = buy_trig_high if r.o <= buy_trig_high else r.o
                        tp = entry + ctp_r * (entry - buy_sl)
                        open_trade = _open("long", i, r.t, entry, buy_sl, tp)
                        trades_today += 1
                    buy_phase = 0

            # PDL breakdown -> SHORT continuation (sell_phase repurposed)
            if sell_phase == 0:
                if r.c < pdl:                       # breakdown close below PDL
                    sell_trig_low, sell_sl = r.l, r.h
                    sell_phase, sell_armed_at = 1, i
            elif sell_phase == 1:
                if arm_expiry_bars and i - sell_armed_at > arm_expiry_bars:
                    sell_phase = 0
                elif r.l <= sell_trig_low:          # continuation break down -> SHORT
                    if can_enter and sell_sl > sell_trig_low:
                        entry = sell_trig_low if r.o >= sell_trig_low else r.o
                        tp = entry - ctp_r * (sell_sl - entry)
                        open_trade = _open("short", i, r.t, entry, sell_sl, tp)
                        trades_today += 1
                    sell_phase = 0

    return _metrics(symbol, seed, rows, trades, equity_curve)


def _open(side: str, i: int, t: int, entry: float, sl: float, tp: float) -> Trade:
    return Trade(side=side, entry_i=i, entry_t=t, entry_px=entry, sl=sl, tp=tp,
                 r_planned=abs(entry - sl))


def _close(t: Trade, i: int, ts: int, exit_px: float, reason: str, fee_pct: float):
    t.exit_i, t.exit_t, t.exit_px, t.exit_reason = i, ts, exit_px, reason
    if t.side == "long":
        gross_pct = (exit_px - t.entry_px) / t.entry_px * 100
    else:
        gross_pct = (t.entry_px - exit_px) / t.entry_px * 100
    t.pnl_pct = gross_pct - 2 * fee_pct
    # R multiple: realized price move / planned 1R risk. Fee converted to R terms.
    if t.r_planned > 0:
        gross_r = (exit_px - t.entry_px) / t.r_planned if t.side == "long" \
            else (t.entry_px - exit_px) / t.r_planned
        fee_r = (2 * fee_pct / 100 * t.entry_px) / t.r_planned
        t.r_mult = gross_r - fee_r


def _metrics(symbol, seed, rows, trades, equity_curve) -> Result:
    res = Result(symbol=symbol, seed=seed, bars=len(rows),
                 days=len({r.day for r in rows}), trades=trades, n_trades=len(trades))
    if not trades:
        return res

    wins = [t for t in trades if t.r_mult > 0]
    losses = [t for t in trades if t.r_mult <= 0]
    res.win_rate = round(len(wins) / len(trades), 3)
    res.avg_win_r = round(sum(t.r_mult for t in wins) / len(wins), 3) if wins else 0.0
    res.avg_loss_r = round(sum(t.r_mult for t in losses) / len(losses), 3) if losses else 0.0

    gross_win = sum(t.r_mult for t in wins)
    gross_loss = -sum(t.r_mult for t in losses)
    res.profit_factor = round(gross_win / gross_loss, 3) if gross_loss > 0 else float("inf")
    res.expectancy_r = round(sum(t.r_mult for t in trades) / len(trades), 3)
    res.total_return_pct = round((equity_curve[-1] - 1.0) * 100, 2)

    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak * 100)
    res.max_drawdown_pct = round(max_dd, 2)

    streak = worst = 0
    for t in trades:
        streak = streak + 1 if t.r_mult <= 0 else 0
        worst = max(worst, streak)
    res.max_consec_losses = worst

    br: dict = {}
    for t in trades:
        br[t.exit_reason] = br.get(t.exit_reason, 0) + 1
    res.exit_breakdown = br
    return res


# ---- Runner + reporting ---------------------------------------------------------------

def run_one(symbol: str, seed: int, n_bars: int, interval_min: int, **kw) -> Result:
    bars = generate_bars(symbol=symbol, n=n_bars, start_ts_ns=START_TS_NS,
                         interval_s=interval_min * 60, seed=seed, base_price=100.0)
    return simulate(_to_rows(bars), symbol, seed, **kw)


def run_one_real(symbol: str, interval: str, last_n: Optional[int], **kw) -> Result:
    """Same simulation over REAL stored bars from the durable market-data store.

    This is the honest test: actual NSE sessions, real gaps, real volatility. The
    docstring's promise made concrete. seed is recorded as -1 (no RNG involved).
    """
    from app.marketdata.bridge import load_store_bars

    bars = load_store_bars([symbol], interval=interval, last_n=last_n)
    return simulate(_to_rows(bars), symbol, seed=-1, **kw)


def main() -> int:
    p = argparse.ArgumentParser(description="Faithful PDH/PDL sweep-reversal backtest (synthetic data).")
    p.add_argument("--symbols", default="NIFTY,BANKNIFTY,RELIANCE")
    p.add_argument("--seeds", default="42,123,456,789,1001")
    p.add_argument("--n-bars", type=int, default=2880, help="bars per run (2880 = 10 days of 5-min)")
    p.add_argument("--interval-min", type=int, default=5)
    p.add_argument("--fee-pct", type=float, default=0.05, help="one-way fee/slippage %%")
    p.add_argument("--risk-pct", type=float, default=1.0, help="%% equity risked per trade")
    p.add_argument("--arm-expiry-bars", type=int, default=0, help="void an armed setup after N bars (0=off)")
    p.add_argument("--one-trade-per-day", action="store_true")
    p.add_argument("--tp-r", type=float, default=0.0, help="take profit at N x risk (0=target the far PDH/PDL level)")
    p.add_argument("--be-r", type=float, default=0.0, help="pull stop to break-even after price reaches N x risk (0=off)")
    p.add_argument("--entry-window", type=int, default=0, help="only enter within the first N bars of the day (0=off)")
    p.add_argument("--sl-mode", default="trigger", choices=["trigger", "sweep"],
                   help="stop at the trigger candle extreme, or beyond the sweep extreme")
    p.add_argument("--invert", action="store_true",
                   help="CONTINUATION: trade WITH the level break instead of fading it")
    p.add_argument("--real", action="store_true",
                   help="use REAL stored bars (marketdata store) instead of synthetic")
    p.add_argument("--interval", default="30minute",
                   help="stored-bar interval for --real (e.g. 30minute, day)")
    p.add_argument("--last-n", type=int, default=None,
                   help="keep only the most recent N stored bars/symbol for --real (default all)")
    args = p.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    kw = dict(fee_pct=args.fee_pct, risk_pct=args.risk_pct,
              arm_expiry_bars=args.arm_expiry_bars, one_trade_per_day=args.one_trade_per_day,
              tp_r=args.tp_r, be_r=args.be_r, entry_window=args.entry_window, sl_mode=args.sl_mode,
              invert=args.invert)

    print("=" * 100)
    print("PDH/PDL LIQUIDITY-SWEEP REVERSAL  —  faithful level-based backtest")
    if args.real:
        print(f"  data=REAL stored bars  symbols={','.join(symbols)}  "
              f"interval={args.interval}  last_n={args.last_n or 'all'}  "
              f"fee={args.fee_pct}%  risk={args.risk_pct}%")
        print(f"  NOTE: actual NSE sessions — this IS a real-market result, within the data's window.")
    else:
        print(f"  data=synthetic  symbols={','.join(symbols)}  seeds={len(seeds)}  "
              f"bars/run={args.n_bars}  interval={args.interval_min}m  fee={args.fee_pct}%  risk={args.risk_pct}%")
        print(f"  NOTE: synthetic 24h path, no session gaps — logic/plumbing test, NOT a real-edge claim.")
    print("=" * 100)

    results: List[Result] = []
    for sym in symbols:
        if args.real:
            try:
                results.append(run_one_real(sym, args.interval, args.last_n, **kw))
            except ValueError as exc:
                print(f"  SKIP {sym}: {exc}")
        else:
            for seed in seeds:
                results.append(run_one(sym, seed, args.n_bars, args.interval_min, **kw))

    hdr = (f"{'Symbol':<12}{'Seed':>6}{'Trades':>8}{'Win%':>7}{'AvgW(R)':>9}{'AvgL(R)':>9}"
           f"{'PF':>7}{'Exp(R)':>8}{'Ret%':>9}{'MaxDD%':>8}{'MaxLoss':>8}")
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        pf = "inf" if r.profit_factor == float("inf") else f"{r.profit_factor:>6.2f}"
        print(f"{r.symbol:<12}{r.seed:>6}{r.n_trades:>8}{r.win_rate * 100:>6.1f}%"
              f"{r.avg_win_r:>9.2f}{r.avg_loss_r:>9.2f}{pf:>7}{r.expectancy_r:>8.2f}"
              f"{r.total_return_pct:>8.1f}%{r.max_drawdown_pct:>7.1f}%{r.max_consec_losses:>8}")

    # ---- aggregate ----
    n_tr = sum(r.n_trades for r in results)
    print("-" * len(hdr))
    if n_tr == 0:
        print("  0 trades across all runs. Likely the synthetic path never sweeps a full "
              "prior-day level, or warmup/day-count too small. Try --n-bars 5760 or a wider seed set.")
        return 0

    all_tr = [t for r in results for t in r.trades]
    wins = [t for t in all_tr if t.r_mult > 0]
    losses = [t for t in all_tr if t.r_mult <= 0]
    gw = sum(t.r_mult for t in wins)
    gl = -sum(t.r_mult for t in losses)
    exp = sum(t.r_mult for t in all_tr) / len(all_tr)
    br: dict = {}
    for t in all_tr:
        br[t.exit_reason] = br.get(t.exit_reason, 0) + 1
    print(f"  POOLED  trades={len(all_tr)}  win%={len(wins) / len(all_tr) * 100:.1f}  "
          f"PF={'inf' if gl == 0 else round(gw / gl, 2)}  expectancy={exp:+.3f}R  "
          f"avgTrades/run={n_tr / len(results):.1f}")
    print(f"  exits   {br}")
    print("=" * 100)
    tail = ("This is REAL data — the expectancy above is a genuine (in-window) edge measurement."
            if args.real else
            "Synthetic result validates the ENGINE, not the market — rerun with --real for an edge read.")
    print("  READ THIS: expectancy is in R (multiples of the candle-low risk). +0.1R over many "
          f"trades = edge; <=0R = none. {tail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
