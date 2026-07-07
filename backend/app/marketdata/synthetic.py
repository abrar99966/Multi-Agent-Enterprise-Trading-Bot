"""Deterministic synthetic OHLCV generator.

Produces a regime-switching price path -- sine-modulated drift plus
gaussian noise -- tuned so SMA(10)/SMA(30) crossovers occur several
times per 500 bars, giving Phase 0 strategies something to trade in
tests and demos without any market dependency.
"""
from __future__ import annotations

import math
import random
import zlib

from app.core.events import NS_PER_SEC, Bar

_DRIFT_AMPLITUDE = 0.0015  # peak drift per bar (~0.15%)
_DRIFT_PERIOD_BARS = 80.0
_NOISE_SIGMA = 0.004  # ~0.4% per bar
_WICK_SIGMA = 0.001


def generate_bars(
    symbol: str,
    n: int,
    start_ts_ns: int,
    interval_s: int = 60,
    seed: int = 42,
    base_price: float = 100.0,
) -> list[Bar]:
    """Deterministic: identical args always yield an identical list.

    The RNG is seeded with ``seed + crc32(symbol)`` (crc32, not builtin
    hash(), which is randomized per process) so each symbol gets its
    own reproducible path. OHLC invariants hold by construction:
    high >= max(open, close), low <= min(open, close), volume > 0, and
    each bar opens at the previous bar's close.
    """
    rng = random.Random(seed + zlib.crc32(symbol.encode("utf-8")))
    bars: list[Bar] = []
    open_price = base_price
    for k in range(n):
        drift = _DRIFT_AMPLITUDE * math.sin(2.0 * math.pi * k / _DRIFT_PERIOD_BARS)
        ret = drift + rng.gauss(0.0, _NOISE_SIGMA)
        close_price = open_price * (1.0 + ret)
        high = max(open_price, close_price) * (1.0 + abs(rng.gauss(0.0, _WICK_SIGMA)))
        low = min(open_price, close_price) * (1.0 - abs(rng.gauss(0.0, _WICK_SIGMA)))
        volume = 1_000.0 * math.exp(rng.gauss(0.0, 0.5))
        bars.append(
            Bar(
                symbol=symbol,
                ts_open=start_ts_ns + k * interval_s * NS_PER_SEC,
                interval_s=interval_s,
                open=open_price,
                high=high,
                low=low,
                close=close_price,
                volume=volume,
            )
        )
        open_price = close_price
    return bars
