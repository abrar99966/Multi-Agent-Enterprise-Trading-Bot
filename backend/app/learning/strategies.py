"""Strategy library + registry — the "tournament" field.

Each strategy is a pure signal function `(bars_so_far, params) -> signal` where
signal ∈ {"bullish", "bearish", "neutral"}. The backtest engine and the live
TechnicalAgent BOTH dispatch through this registry, so a strategy is guaranteed
to behave identically in backtest and in production (no drift between the two).

Honesty rule shared with the engine: a signal at bar `i` may only look at
bars[0..i]. None of these functions peek at future bars.

Param container `StrategyParams` carries every field any strategy might need
(plus the shared exit params the engine reads). A given strategy only reads the
fields it cares about; the grid tuner only varies those fields. Old
`tuned_params.json` files written before strategies existed have no `strategy`
key and therefore default to "rsi_sma" — fully backward compatible.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Callable, Dict, List, Optional

from .bar import Bar

Signal = str  # "bullish" | "bearish" | "neutral"


# ---- Param container (shared across all strategies) -----------------------------------

@dataclass
class StrategyParams:
    # Which strategy this param set drives. Defaults to the legacy RSI+SMA rule
    # so pre-existing tuned_params.json entries keep working unchanged.
    strategy: str = "rsi_sma"

    # --- Shared exit / risk management (read by the engine, not the signal) ---
    stop_loss_pct: float = 2.0
    take_profit_pct: float = 5.0
    max_hold_bars: int = 24

    # --- rsi_sma ---
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    sma_period: int = 20

    # --- ema_cross ---
    ema_fast: int = 12
    ema_slow: int = 26

    # --- macd ---
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # --- bollinger ---
    bb_period: int = 20
    bb_std: float = 2.0

    # --- supertrend ---
    atr_period: int = 10
    st_multiplier: float = 3.0

    # --- breakout (Donchian channel) ---
    breakout_period: int = 20

    # --- volume_breakout (Donchian + volume confirmation) ---
    vol_period: int = 20
    vol_mult: float = 1.5

    # --- golden_cross (fast/slow SMA crossover) ---
    sma_fast: int = 50
    sma_slow: int = 200

    def to_dict(self) -> dict:
        return asdict(self)


# ---- Indicator helpers (self-contained — no external TA dependency) -------------------

def rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """Wilder's RSI. None if too few points."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - (100 / (1 + rs))


def sma(values: List[float], n: int) -> Optional[float]:
    if len(values) < n or n <= 0:
        return None
    return sum(values[-n:]) / n


def ema_series(values: List[float], n: int) -> List[float]:
    """Full EMA series (same length as input, seeded with the first value)."""
    if not values or n <= 0:
        return []
    k = 2.0 / (n + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def ema(values: List[float], n: int) -> Optional[float]:
    if len(values) < n or n <= 0:
        return None
    return ema_series(values, n)[-1]


def macd_lines(closes: List[float], fast: int, slow: int, signal: int):
    """Return (macd_line_last, signal_line_last) or (None, None) if insufficient data."""
    if len(closes) < slow + signal:
        return None, None
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = ema_series(macd_line, signal)
    return macd_line[-1], signal_line[-1]


def bollinger(closes: List[float], period: int, std_mult: float):
    """Return (mid, upper, lower) or (None, None, None)."""
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid = sum(window) / period
    var = sum((c - mid) ** 2 for c in window) / period
    sd = var ** 0.5
    return mid, mid + std_mult * sd, mid - std_mult * sd


def atr_series(bars: List[Bar], period: int) -> List[float]:
    """Wilder ATR series. Uses close as a stand-in when h/l are missing/zero."""
    if not bars:
        return []
    trs: List[float] = []
    for i, b in enumerate(bars):
        hi = b.h if b.h else b.c
        lo = b.l if b.l else b.c
        if i == 0:
            trs.append(max(0.0, hi - lo))
        else:
            prev_c = bars[i - 1].c
            trs.append(max(hi - lo, abs(hi - prev_c), abs(lo - prev_c)))
    if len(trs) < period:
        return []
    out = [sum(trs[:period]) / period]
    for tr in trs[period:]:
        out.append((out[-1] * (period - 1) + tr) / period)
    return out


def supertrend_dir(bars: List[Bar], period: int, mult: float) -> Optional[str]:
    """Current Supertrend direction: 'up', 'down', or None if insufficient data."""
    atr = atr_series(bars, period)
    if not atr:
        return None
    # ATR series starts at index `period-1` of bars; align by slicing bars.
    aligned = bars[period - 1:]
    if len(aligned) != len(atr) or not aligned:
        # Defensive: lengths should match; bail to neutral if not.
        n = min(len(aligned), len(atr))
        aligned, atr = aligned[-n:], atr[-n:]
        if not aligned:
            return None
    final_upper = final_lower = None
    direction = "up"
    for idx, b in enumerate(aligned):
        hi = b.h if b.h else b.c
        lo = b.l if b.l else b.c
        mid = (hi + lo) / 2.0
        basic_upper = mid + mult * atr[idx]
        basic_lower = mid - mult * atr[idx]
        if final_upper is None:
            final_upper, final_lower = basic_upper, basic_lower
            direction = "up" if b.c >= mid else "down"
            prev_c = b.c
            continue
        final_upper = basic_upper if (basic_upper < final_upper or prev_c > final_upper) else final_upper
        final_lower = basic_lower if (basic_lower > final_lower or prev_c < final_lower) else final_lower
        if b.c > final_upper:
            direction = "up"
        elif b.c < final_lower:
            direction = "down"
        # else: carry previous direction
        prev_c = b.c
    return direction


# ---- Signal functions -----------------------------------------------------------------

def _rsi_sma_signal(bars: List[Bar], p: StrategyParams) -> Signal:
    """Legacy mean-reversion + trend filter (kept identical to the original engine)."""
    closes = [b.c for b in bars]
    if not closes:
        return "neutral"
    r = rsi(closes, p.rsi_period)
    s = sma(closes, p.sma_period) or sma(closes, max(5, len(closes) // 2))
    if r is None or s is None:
        return "neutral"
    last = closes[-1]
    dist_pct = (last - s) / s * 100 if s else 0
    trend = "uptrend" if dist_pct > 0.5 else ("downtrend" if dist_pct < -0.5 else "sideways")
    if r >= p.rsi_overbought:
        return "bearish"
    if r <= p.rsi_oversold:
        return "bullish"
    if trend == "uptrend" and r > 55:
        return "bullish"
    if trend == "downtrend" and r < 45:
        return "bearish"
    return "neutral"


def _ema_cross_signal(bars: List[Bar], p: StrategyParams) -> Signal:
    """Trend-following: fast EMA above slow EMA ⇒ bullish, below ⇒ bearish."""
    closes = [b.c for b in bars]
    fast = ema(closes, p.ema_fast)
    slow = ema(closes, p.ema_slow)
    if fast is None or slow is None:
        return "neutral"
    if fast > slow:
        return "bullish"
    if fast < slow:
        return "bearish"
    return "neutral"


def _macd_signal(bars: List[Bar], p: StrategyParams) -> Signal:
    """Trend/momentum: MACD line above its signal line ⇒ bullish."""
    closes = [b.c for b in bars]
    m, s = macd_lines(closes, p.macd_fast, p.macd_slow, p.macd_signal)
    if m is None or s is None:
        return "neutral"
    if m > s:
        return "bullish"
    if m < s:
        return "bearish"
    return "neutral"


def _bollinger_signal(bars: List[Bar], p: StrategyParams) -> Signal:
    """Mean-reversion: tag of lower band ⇒ bullish bounce, upper band ⇒ bearish fade."""
    closes = [b.c for b in bars]
    mid, upper, lower = bollinger(closes, p.bb_period, p.bb_std)
    if mid is None:
        return "neutral"
    last = closes[-1]
    if last <= lower:
        return "bullish"
    if last >= upper:
        return "bearish"
    return "neutral"


def _supertrend_signal(bars: List[Bar], p: StrategyParams) -> Signal:
    """Trend-following: Supertrend up ⇒ bullish, down ⇒ bearish."""
    d = supertrend_dir(bars, p.atr_period, p.st_multiplier)
    if d == "up":
        return "bullish"
    if d == "down":
        return "bearish"
    return "neutral"


def _breakout_signal(bars: List[Bar], p: StrategyParams) -> Signal:
    """Momentum (Donchian): close breaks the prior N-bar high ⇒ bullish, prior low ⇒ bearish."""
    n = p.breakout_period
    if len(bars) < n + 1:
        return "neutral"
    prior = bars[-(n + 1):-1]            # the N bars BEFORE the current one (no look-ahead)
    hh = max((b.h if b.h else b.c) for b in prior)
    ll = min((b.l if b.l else b.c) for b in prior)
    last = bars[-1].c
    if last >= hh:
        return "bullish"
    if last <= ll:
        return "bearish"
    return "neutral"


def _volume_breakout_signal(bars: List[Bar], p: StrategyParams) -> Signal:
    """Donchian breakout that only fires when volume confirms the move.

    A breakout on thin volume is noise; a breakout on a volume spike
    (last bar volume ≥ vol_mult × the average of the prior window) is the
    classic screener setup. Falls back to neutral when volume data is missing.
    """
    n = p.breakout_period
    if len(bars) < n + 1:
        return "neutral"
    prior = bars[-(n + 1):-1]            # N bars BEFORE the current one (no look-ahead)
    hh = max((b.h if b.h else b.c) for b in prior)
    ll = min((b.l if b.l else b.c) for b in prior)
    last = bars[-1]
    vols = [b.v for b in prior if b.v]
    if len(vols) < max(2, p.vol_period // 2) or not last.v:
        return "neutral"                # not enough volume info to confirm
    avg_v = sum(vols[-p.vol_period:]) / min(len(vols), p.vol_period)
    if avg_v <= 0 or last.v < p.vol_mult * avg_v:
        return "neutral"                # no volume spike → ignore the breakout
    if last.c >= hh:
        return "bullish"
    if last.c <= ll:
        return "bearish"
    return "neutral"


def _golden_cross_signal(bars: List[Bar], p: StrategyParams) -> Signal:
    """Trend regime: fast SMA above slow SMA ⇒ bullish (golden cross), below ⇒ bearish (death cross)."""
    closes = [b.c for b in bars]
    f = sma(closes, p.sma_fast)
    s = sma(closes, p.sma_slow)
    if f is None or s is None:
        return "neutral"
    if f > s:
        return "bullish"
    if f < s:
        return "bearish"
    return "neutral"


def _engulfing_signal(bars: List[Bar], p: StrategyParams) -> Signal:
    """Candlestick reversal: bullish/bearish engulfing on the latest two bars.

    Bullish engulf = a down candle followed by an up candle whose body fully
    engulfs it; bearish engulf is the mirror. A staple of every chart screener.
    """
    if len(bars) < 2:
        return "neutral"
    prev, cur = bars[-2], bars[-1]
    po, pc = (prev.o if prev.o else prev.c), prev.c
    co, cc = (cur.o if cur.o else cur.c), cur.c
    bull = pc < po and cc > co and cc >= po and co <= pc
    bear = pc > po and cc < co and cc <= po and co >= pc
    if bull:
        return "bullish"
    if bear:
        return "bearish"
    return "neutral"


# +5:30, so previous-day levels bucket to the Indian session, not UTC midnight.
_IST_OFFSET_S = 19800


def _pdh_pdl_signal(bars: List[Bar], p: StrategyParams) -> Signal:
    """PDH/PDL liquidity-sweep reversal — is a setup ARMED and TRIGGERED on the
    latest bar?

    A long fires when price swept below the Previous-Day-Low (a candle CLOSED
    below PDL), the first bullish candle after that sweep armed a trigger at its
    high, and the latest bar's HIGH breaks that trigger. Short is the mirror
    around PDH. Returns the direction only on the bar the break happens, else
    neutral. No look-ahead: PDH/PDL come from the COMPLETED prior day, and the
    state machine only reads bars up to and including the last.

    This is the SCREENER view of the strategy — "is a sweep-reversal live right
    now?". The faithful path-dependent backtest (stop-entry fills, level-based
    SL/TP checked intrabar) lives in scripts/backtest_pdh_pdl.py, because the
    tournament's global stop_loss_pct / take_profit_pct exits cannot represent
    a strategy whose exits ARE the previous-day levels.
    """
    if len(bars) < 3:
        return "neutral"

    def day_of(b: Bar) -> int:
        return (b.t + _IST_OFFSET_S) // 86400

    cur_day = day_of(bars[-1])
    prev_day = cur_day - 1

    # Previous-day levels from bars actually tagged to the prior IST day. A gap
    # (no bars yesterday) means no reference levels -> no setup.
    prev_bars = [b for b in bars if day_of(b) == prev_day]
    if not prev_bars:
        return "neutral"
    pdh = max((b.h if b.h else b.c) for b in prev_bars)
    pdl = min((b.l if b.l else b.c) for b in prev_bars)
    if pdh <= pdl:
        return "neutral"

    today = [b for b in bars if day_of(b) == cur_day]
    if len(today) < 2:
        return "neutral"

    # Replay today's bars; the signal is whatever triggers ON the final bar.
    buy_phase = sell_phase = 0        # 0 idle · 1 swept · 2 armed
    buy_trig = sell_trig = 0.0
    last_i = len(today) - 1

    for i, r in enumerate(today):
        o = r.o if r.o else r.c
        fired = None

        # long side
        if buy_phase == 0:
            if r.c < pdl:
                buy_phase = 1
        elif buy_phase == 1:
            if r.c > o:                # first bullish candle after the sweep
                buy_trig, buy_phase = r.h, 2
        elif buy_phase == 2:
            if r.h >= buy_trig and buy_trig < pdh:
                fired = "bullish"
                buy_phase = 0

        # short side
        if sell_phase == 0:
            if r.c > pdh:
                sell_phase = 1
        elif sell_phase == 1:
            if r.c < o:                # first bearish candle after the sweep
                sell_trig, sell_phase = r.l, 2
        elif sell_phase == 2:
            if r.l <= sell_trig and sell_trig > pdl:
                fired = fired or "bearish"
                sell_phase = 0

        if i == last_i and fired:
            return fired

    return "neutral"


# ---- Registry -------------------------------------------------------------------------

@dataclass
class Strategy:
    key: str
    label: str
    description: str
    signal_fn: Callable[[List[Bar], StrategyParams], Signal]
    grid: Dict[str, List]                       # field -> candidate values (cartesian)
    valid: Optional[Callable[[dict], bool]] = None   # optional combo filter


DEFAULT_STRATEGY = "rsi_sma"

STRATEGIES: Dict[str, Strategy] = {
    "rsi_sma": Strategy(
        key="rsi_sma", label="RSI + SMA (mean-reversion)",
        description="Buys oversold / sells overbought with an SMA trend filter.",
        signal_fn=_rsi_sma_signal,
        grid={
            "rsi_oversold":   [25.0, 30.0, 35.0],
            "rsi_overbought": [65.0, 70.0, 75.0],
            "sma_period":     [10, 20, 50],
        },
    ),
    "ema_cross": Strategy(
        key="ema_cross", label="EMA crossover (trend)",
        description="Long while the fast EMA is above the slow EMA, short below.",
        signal_fn=_ema_cross_signal,
        grid={
            "ema_fast": [9, 12, 20],
            "ema_slow": [26, 50, 100],
        },
        valid=lambda c: c["ema_fast"] < c["ema_slow"],
    ),
    "macd": Strategy(
        key="macd", label="MACD (momentum)",
        description="Long when MACD is above its signal line, short below.",
        signal_fn=_macd_signal,
        grid={
            "macd_fast":   [8, 12],
            "macd_slow":   [21, 26],
            "macd_signal": [9],
        },
        valid=lambda c: c["macd_fast"] < c["macd_slow"],
    ),
    "bollinger": Strategy(
        key="bollinger", label="Bollinger Bands (mean-reversion)",
        description="Fades band touches — buy the lower band, sell the upper.",
        signal_fn=_bollinger_signal,
        grid={
            "bb_period": [14, 20],
            "bb_std":    [1.5, 2.0, 2.5],
        },
    ),
    "supertrend": Strategy(
        key="supertrend", label="Supertrend (trend)",
        description="ATR-based trend follower — very popular for Indian intraday/F&O.",
        signal_fn=_supertrend_signal,
        grid={
            "atr_period":    [7, 10, 14],
            "st_multiplier": [2.0, 3.0],
        },
    ),
    "breakout": Strategy(
        key="breakout", label="Donchian breakout (momentum)",
        description="Buys N-bar highs, sells N-bar lows — classic breakout/turtle style.",
        signal_fn=_breakout_signal,
        grid={
            "breakout_period": [10, 20, 55],
        },
    ),
    "volume_breakout": Strategy(
        key="volume_breakout", label="Volume breakout (screener)",
        description="N-bar breakout confirmed by a volume spike — the staple Chartink-style scan.",
        signal_fn=_volume_breakout_signal,
        grid={
            "breakout_period": [10, 20],
            "vol_mult":        [1.5, 2.0],
        },
    ),
    "golden_cross": Strategy(
        key="golden_cross", label="Golden/Death cross (regime)",
        description="Fast SMA over slow SMA ⇒ bullish regime; under ⇒ bearish. Classic 50/200 screen.",
        signal_fn=_golden_cross_signal,
        grid={
            "sma_fast": [20, 50],
            "sma_slow": [100, 200],
        },
        valid=lambda c: c["sma_fast"] < c["sma_slow"],
    ),
    "engulfing": Strategy(
        key="engulfing", label="Engulfing candle (reversal)",
        description="Bullish/bearish engulfing candlestick pattern — a common reversal screen.",
        signal_fn=_engulfing_signal,
        grid={},   # pattern-only — no tunable params (single combo)
    ),
    "pdh_pdl": Strategy(
        key="pdh_pdl", label="PDH/PDL sweep reversal (levels)",
        description=("Liquidity sweep of the previous day's high/low, then reversal on the "
                     "break of the first opposing candle. Screener signal; faithful "
                     "level-based backtest is scripts/backtest_pdh_pdl.py."),
        signal_fn=_pdh_pdl_signal,
        grid={},   # level-based, no tunable indicator params
    ),
}


def get_strategy(key: str) -> Strategy:
    """Look up a strategy, falling back to the default if the key is unknown."""
    return STRATEGIES.get(key or DEFAULT_STRATEGY, STRATEGIES[DEFAULT_STRATEGY])


def list_strategies() -> List[dict]:
    """Surfaced via API for the UI."""
    return [
        {"key": s.key, "label": s.label, "description": s.description,
         "grid_size": _grid_size(s)}
        for s in STRATEGIES.values()
    ]


def _grid_size(s: Strategy) -> int:
    from itertools import product
    keys = list(s.grid.keys())
    count = 0
    for combo in product(*[s.grid[k] for k in keys]):
        d = dict(zip(keys, combo))
        if s.valid is None or s.valid(d):
            count += 1
    return count


def iter_combos(s: Strategy):
    """Yield every valid param combo for a strategy as a plain dict."""
    from itertools import product
    keys = list(s.grid.keys())
    for combo in product(*[s.grid[k] for k in keys]):
        d = dict(zip(keys, combo))
        if s.valid is None or s.valid(d):
            yield d
