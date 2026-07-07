"""Supervised dataset builder for the fast-path model.

For each decision bar t we record the feature vector computed from
``bars[t-W+1 : t+1]`` (the same bounded window the live fabric uses) and a
binary label: did the forward return over ``horizon`` bars exceed
``label_threshold``? Features read only bars up to t; the label reads bars
t..t+horizon, which exist in history -- so there is no lookahead leaking into
the features, only honest supervised targets.

The builder is deterministic: same bars in, same (X, y) out.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence

from app.core.events import Bar as EventBar
from app.features.fabric import (
    FABRIC_WINDOW,
    FEATURE_NAMES,
    MIN_BARS,
    compute_features,
    event_bar_to_ohlcv,
    to_vector,
)


@dataclass
class Dataset:
    X: List[List[float]]
    y: List[int]
    feature_names: List[str]
    n_pos: int
    n_neg: int


def build_dataset(
    bars_by_symbol: Dict[str, Sequence[EventBar]],
    horizon: int = 5,
    label_threshold: float = 0.0,
) -> Dataset:
    """Pool labeled samples across symbols. Symbols iterate in sorted order
    for determinism."""
    X: List[List[float]] = []
    y: List[int] = []
    for symbol in sorted(bars_by_symbol):
        bars = list(bars_by_symbol[symbol])
        ohlcv = [event_bar_to_ohlcv(b) for b in bars]
        closes = [b.c for b in ohlcv]
        n = len(ohlcv)
        # t must be warm (>= MIN_BARS-1) and have a label bar at t+horizon.
        for t in range(MIN_BARS - 1, n - horizon):
            window = ohlcv[max(0, t + 1 - FABRIC_WINDOW) : t + 1]
            feats = compute_features(window)
            if feats is None:
                continue
            fwd = closes[t + horizon] / closes[t] - 1.0
            X.append(to_vector(feats))
            y.append(1 if fwd > label_threshold else 0)
    n_pos = sum(y)
    return Dataset(
        X=X, y=y, feature_names=list(FEATURE_NAMES), n_pos=n_pos, n_neg=len(y) - n_pos
    )
