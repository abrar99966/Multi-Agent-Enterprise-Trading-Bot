"""The OHLCV Bar type — kept dependency-free so the strategy/backtest layer can
import it without pulling in the broker SDKs that `historical.py` needs."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Bar:
    t: int           # epoch seconds
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0
