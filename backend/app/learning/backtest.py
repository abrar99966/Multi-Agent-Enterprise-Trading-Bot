"""Rule-based backtest engine.

Replays historical bars against a parameterised strategy and simulates the real
trade lifecycle: entry, stop-loss exit, take-profit exit, opposite-signal exit.
Computes honest metrics (win rate, Sharpe, max drawdown) WITHOUT look-ahead
bias — at bar `i`, the signal can only see bars[0..i].

Key honesty rules:
  • Entry/exit happens at bar[i+1]'s open, NOT bar[i]'s close. You can't
    actually trade on the close in live markets.
  • Apply a flat commission/slippage cost per trade (default 0.05%) so
    backtests aren't misleadingly optimistic.

The directional signal is supplied by a strategy from `strategies.py`, selected
via `params.strategy`. The same registry drives the live TechnicalAgent, so a
strategy backtests exactly the way it trades.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from .bar import Bar
# Re-export StrategyParams so existing imports (`from .backtest import StrategyParams`)
# keep working now that the param container lives alongside the strategies.
from .strategies import StrategyParams, get_strategy

log = logging.getLogger(__name__)


# ---- Trade + result types -------------------------------------------------------------

@dataclass
class SimTrade:
    side: str        # "long" or "short"
    entry_bar: int
    entry_t: int
    entry_px: float
    exit_bar: int = -1
    exit_t: int = 0
    exit_px: float = 0.0
    exit_reason: str = ""   # "tp" | "sl" | "opposite_signal" | "max_hold" | "end_of_data"
    pnl_pct: float = 0.0
    pnl_abs: float = 0.0


@dataclass
class BacktestResult:
    symbol: str
    interval: str
    bars_count: int
    params: dict
    strategy: str = "rsi_sma"
    trades: List[dict] = field(default_factory=list)
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    total_return_pct: float = 0.0
    sharpe: float = 0.0
    max_drawdown_pct: float = 0.0
    n_trades: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ---- Engine ---------------------------------------------------------------------------

def backtest(
    bars: List[Bar],
    symbol: str = "",
    interval: str = "30minute",
    params: Optional[StrategyParams] = None,
    fee_pct: float = 0.05,   # one-way commission/slippage cost
) -> BacktestResult:
    """Replay bars under `params` (which names the strategy) and return realistic metrics."""
    p = params or StrategyParams()
    signal_fn = get_strategy(p.strategy).signal_fn
    n = len(bars)

    # Warmup = the slowest indicator THIS strategy actually uses (not every field).
    # Including sma_slow(=200) for non-golden_cross strategies wrongly starved
    # thin-history symbols of trades across the board.
    common = max(p.rsi_period, p.sma_period, p.ema_slow, p.macd_slow + p.macd_signal,
                 p.bb_period, p.atr_period, p.breakout_period, p.vol_period)
    warmup = (max(common, p.sma_slow) if p.strategy == "golden_cross" else common) + 5
    if n < warmup:
        return BacktestResult(symbol=symbol, interval=interval, bars_count=n,
                              params=p.to_dict(), strategy=p.strategy)

    open_trade: Optional[SimTrade] = None
    trades: List[SimTrade] = []
    equity_curve = [1.0]     # cumulative return multiplier
    last_signal = "neutral"

    for i in range(n - 1):                # leave the last bar as 'next' for the final entry/exit
        sig = signal_fn(bars[: i + 1], p)
        next_bar = bars[i + 1]
        cur_bar = bars[i]

        # ---- manage open position
        if open_trade is not None:
            held = i - open_trade.entry_bar
            if open_trade.side == "long":
                ret = (cur_bar.c - open_trade.entry_px) / open_trade.entry_px * 100
            else:
                ret = (open_trade.entry_px - cur_bar.c) / open_trade.entry_px * 100
            exit_reason = None
            if ret <= -p.stop_loss_pct:
                exit_reason = "sl"
            elif ret >= p.take_profit_pct:
                exit_reason = "tp"
            elif held >= p.max_hold_bars:
                exit_reason = "max_hold"
            elif sig == "bearish" and open_trade.side == "long":
                exit_reason = "opposite_signal"
            elif sig == "bullish" and open_trade.side == "short":
                exit_reason = "opposite_signal"

            if exit_reason:
                # Exit at next bar open — apply fee on both legs (entry + exit)
                exit_px = next_bar.o or next_bar.c
                gross_pct = ((exit_px - open_trade.entry_px) / open_trade.entry_px * 100) \
                            if open_trade.side == "long" \
                            else ((open_trade.entry_px - exit_px) / open_trade.entry_px * 100)
                net_pct = gross_pct - 2 * fee_pct
                open_trade.exit_bar = i + 1
                open_trade.exit_t = next_bar.t
                open_trade.exit_px = exit_px
                open_trade.exit_reason = exit_reason
                open_trade.pnl_pct = net_pct
                open_trade.pnl_abs = exit_px - open_trade.entry_px if open_trade.side == "long" else open_trade.entry_px - exit_px
                trades.append(open_trade)
                equity_curve.append(equity_curve[-1] * (1 + net_pct / 100))
                open_trade = None

        # ---- enter new position on signal flip
        if open_trade is None and sig in ("bullish", "bearish") and sig != last_signal:
            side = "long" if sig == "bullish" else "short"
            open_trade = SimTrade(
                side=side,
                entry_bar=i + 1,
                entry_t=next_bar.t,
                entry_px=next_bar.o or next_bar.c,
            )
        last_signal = sig

    # Force-close any dangling position at the last close
    if open_trade is not None:
        last = bars[-1]
        gross = ((last.c - open_trade.entry_px) / open_trade.entry_px * 100) \
                if open_trade.side == "long" \
                else ((open_trade.entry_px - last.c) / open_trade.entry_px * 100)
        open_trade.exit_bar = n - 1
        open_trade.exit_t = last.t
        open_trade.exit_px = last.c
        open_trade.exit_reason = "end_of_data"
        open_trade.pnl_pct = gross - 2 * fee_pct
        open_trade.pnl_abs = (last.c - open_trade.entry_px) if open_trade.side == "long" else (open_trade.entry_px - last.c)
        trades.append(open_trade)
        equity_curve.append(equity_curve[-1] * (1 + open_trade.pnl_pct / 100))

    # ---- metrics
    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]
    win_rate = len(wins) / len(trades) if trades else 0.0
    avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0.0
    total_return = (equity_curve[-1] - 1.0) * 100

    # Sharpe: mean per-trade return / stdev, scaled by sqrt(N trades) — rough proxy
    if len(trades) > 1:
        returns = [t.pnl_pct for t in trades]
        mu = sum(returns) / len(returns)
        var = sum((r - mu) ** 2 for r in returns) / (len(returns) - 1)
        sd = math.sqrt(var) if var > 0 else 0
        sharpe = (mu / sd * math.sqrt(len(trades))) if sd > 0 else 0.0
    else:
        sharpe = 0.0

    # Max drawdown on equity curve
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        dd = (peak - v) / peak * 100
        max_dd = max(max_dd, dd)

    return BacktestResult(
        symbol=symbol,
        interval=interval,
        bars_count=n,
        params=p.to_dict(),
        strategy=p.strategy,
        trades=[asdict(t) for t in trades],
        win_rate=round(win_rate, 3),
        avg_win_pct=round(avg_win, 3),
        avg_loss_pct=round(avg_loss, 3),
        total_return_pct=round(total_return, 2),
        sharpe=round(sharpe, 3),
        max_drawdown_pct=round(max_dd, 2),
        n_trades=len(trades),
    )
