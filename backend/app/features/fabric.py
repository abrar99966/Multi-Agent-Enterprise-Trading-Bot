"""Feature fabric -- the single, deterministic feature definition shared by
offline training and live decisioning (training-serving parity).

Design choice (Phase 1): features are built from the SAME no-lookahead
indicator helpers and the SAME 9-strategy tournament that
``learning/strategies.py`` already drives. So the GBDT fast path literally
learns how to weight the existing strategy tournament plus a handful of raw
indicator context features -- one indicator codebase, no train/live skew,
and no skew between the tournament and the model. This formalizes the
tournament into the deterministic fast path (docs/ARCHITECTURE.md
section 7: "learn which strategy to trust").

Causality: ``compute_features(bars)`` at index t reads only ``bars[:t+1]``.
The dataset builder slides this over history; the live FeatureFabric feeds
it a per-symbol rolling window. Identical math either way. No wall clock,
no RNG -- the whole fabric is a pure function of the bar sequence, so it
preserves the platform's replay-determinism contract.
"""
from __future__ import annotations

from collections import deque
from statistics import pstdev
from typing import Dict, List, Optional

from app.core.events import Bar as EventBar
from app.learning.bar import Bar as OhlcvBar
from app.learning.strategies import (
    STRATEGIES,
    StrategyParams,
    bollinger,
    ema,
    macd_lines,
    rsi,
    sma,
)
from app.core.events import NS_PER_SEC

# Minimum bars before any feature is emitted: the largest lookback below
# (golden_cross slow SMA = 50, plus headroom for return/vol windows).
MIN_BARS = 60

# Bounded window the live fabric and the dataset builder BOTH slide. Path-
# dependent indicators (EMA, MACD, Wilder ATR/RSI) seed from the window's
# first bar, so training and serving must use the identical window length or
# their values drift -- this constant is the single source of that length.
FABRIC_WINDOW = MIN_BARS + 5

# Bounded-window params for the 9 tournament votes used as features. Kept
# small enough that MIN_BARS stays modest while preserving each strategy's
# character (golden_cross uses a 20/50 "fast" cross, not 50/200).
_VOTE_PARAMS: Dict[str, StrategyParams] = {
    "rsi_sma": StrategyParams(strategy="rsi_sma", rsi_period=14, sma_period=20),
    "ema_cross": StrategyParams(strategy="ema_cross", ema_fast=12, ema_slow=26),
    "macd": StrategyParams(strategy="macd", macd_fast=12, macd_slow=26, macd_signal=9),
    "bollinger": StrategyParams(strategy="bollinger", bb_period=20, bb_std=2.0),
    "supertrend": StrategyParams(strategy="supertrend", atr_period=14, st_multiplier=3.0),
    "breakout": StrategyParams(strategy="breakout", breakout_period=20),
    "volume_breakout": StrategyParams(
        strategy="volume_breakout", breakout_period=20, vol_mult=1.5, vol_period=20
    ),
    "golden_cross": StrategyParams(strategy="golden_cross", sma_fast=20, sma_slow=50),
    "engulfing": StrategyParams(strategy="engulfing"),
}

_VOTE_KEYS = list(_VOTE_PARAMS)  # fixed order
_SIGNAL_VALUE = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}

# Numeric context features, in fixed order.
_NUMERIC_NAMES = [
    "rsi_14_z",       # (RSI-50)/50 in [-1,1]
    "sma_ratio_20",   # close/SMA20 - 1
    "ema_cross",      # EMA12/EMA26 - 1
    "macd_hist_pct",  # (macd-signal)/close
    "bb_pos",         # position within Bollinger band, ~[-1,1]+
    "atr_pct_14",     # ATR14/close
    "ret_1",          # one-bar return
    "mom_10",         # 10-bar return
    "vol_20",         # stdev of 1-bar returns over 20
    "donchian_pos_20",  # (close-LL)/(HH-LL) over prior 20, in [0,1]
    "vol_accel_20",   # volume/SMA(volume,20) - 1
    "dist_high_20",   # close/max(high,20) - 1  (<=0)
    "dist_low_20",    # close/min(low,20) - 1   (>=0)
]

#: Canonical feature order. The model artifact pins this; inference asserts it.
FEATURE_NAMES: List[str] = _NUMERIC_NAMES + [f"vote_{k}" for k in _VOTE_KEYS]


def event_bar_to_ohlcv(bar: EventBar) -> OhlcvBar:
    """Adapt a core.events.Bar (ns timestamps) to the legacy OHLCV Bar
    (epoch seconds) the indicator helpers operate on."""
    return OhlcvBar(
        t=bar.ts_open // NS_PER_SEC,
        o=bar.open,
        h=bar.high,
        l=bar.low,
        c=bar.close,
        v=bar.volume,
    )


def _atr14(bars: List[OhlcvBar]) -> Optional[float]:
    # Local Wilder ATR(14); avoids importing atr_series for one value.
    from app.learning.strategies import atr_series

    series = atr_series(bars, 14)
    return series[-1] if series else None


def compute_features(bars: List[OhlcvBar]) -> Optional[Dict[str, float]]:
    """Feature dict for the LAST bar in ``bars``, or None if not warm
    (fewer than MIN_BARS) or a core indicator is undefined.

    ``bars`` must be chronologically ordered; only ``bars[:]`` up to and
    including the decision bar is read (no lookahead)."""
    if len(bars) < MIN_BARS:
        return None
    closes = [b.c for b in bars]
    last = closes[-1]
    if last <= 0:
        return None

    sma20 = sma(closes, 20)
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line, macd_sig = macd_lines(closes, 12, 26, 9)
    mid, upper, lower = bollinger(closes, 20, 2.0)
    rsi14 = rsi(closes, 14)
    atr14 = _atr14(bars)
    if None in (sma20, ema12, ema26, macd_line, macd_sig, mid, rsi14, atr14):
        return None

    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    vol20 = pstdev(rets[-20:]) if len(rets) >= 20 else 0.0
    band = (upper - mid) or 1.0
    prior20 = bars[-21:-1]  # the 20 bars BEFORE the decision bar (no lookahead)
    hh = max(b.h for b in prior20)
    ll = min(b.l for b in prior20)
    chan = (hh - ll) or 1.0
    high20 = max(b.h for b in bars[-20:])
    low20 = min(b.l for b in bars[-20:])
    vols = [b.v for b in bars[-20:]]
    avg_v = sum(vols) / len(vols) if vols else 0.0

    feats: Dict[str, float] = {
        "rsi_14_z": (rsi14 - 50.0) / 50.0,
        "sma_ratio_20": last / sma20 - 1.0,
        "ema_cross": ema12 / ema26 - 1.0,
        "macd_hist_pct": (macd_line - macd_sig) / last,
        "bb_pos": (last - mid) / band,
        "atr_pct_14": atr14 / last,
        "ret_1": rets[-1],
        "mom_10": last / closes[-11] - 1.0,
        "vol_20": vol20,
        "donchian_pos_20": (last - ll) / chan,
        "vol_accel_20": (bars[-1].v / avg_v - 1.0) if avg_v > 0 else 0.0,
        "dist_high_20": last / high20 - 1.0,
        "dist_low_20": last / low20 - 1.0,
    }
    for key in _VOTE_KEYS:
        signal = STRATEGIES[key].signal_fn(bars, _VOTE_PARAMS[key])
        feats[f"vote_{key}"] = _SIGNAL_VALUE.get(signal, 0.0)
    return feats


def to_vector(feats: Dict[str, float]) -> List[float]:
    """Feature dict -> ordered vector in FEATURE_NAMES order."""
    return [feats[name] for name in FEATURE_NAMES]


class FeatureFabric:
    """Live, per-symbol feature computation over a rolling window. Holds the
    minimum bars needed; ``update`` returns the current feature dict (or None
    while warming up). Deterministic: a pure function of the bars fed in."""

    def __init__(self, window: int = FABRIC_WINDOW) -> None:
        # Keep a little more than MIN_BARS so windowed features are stable.
        self._window = max(window, MIN_BARS + 1)
        self._bars: Dict[str, deque[OhlcvBar]] = {}

    def update(self, bar: EventBar) -> Optional[Dict[str, float]]:
        dq = self._bars.setdefault(bar.symbol, deque(maxlen=self._window))
        dq.append(event_bar_to_ohlcv(bar))
        return compute_features(list(dq))

    def features(self, symbol: str) -> Optional[Dict[str, float]]:
        dq = self._bars.get(symbol)
        return compute_features(list(dq)) if dq else None
