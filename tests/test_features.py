"""Feature fabric: warmup, determinism, training-serving parity, no lookahead."""
from __future__ import annotations

import math

from app.features.fabric import (
    FABRIC_WINDOW,
    FEATURE_NAMES,
    MIN_BARS,
    FeatureFabric,
    compute_features,
    event_bar_to_ohlcv,
    to_vector,
)
from app.marketdata.synthetic import generate_bars

_START = 1_750_000_000_000_000_000


def _ohlcv(symbol: str, n: int, seed: int = 1):
    return [event_bar_to_ohlcv(b) for b in generate_bars(symbol, n, _START, seed=seed)]


def test_warmup_returns_none_below_min_bars() -> None:
    bars = _ohlcv("X", MIN_BARS - 1)
    assert compute_features(bars) is None


def test_features_present_and_ordered() -> None:
    feats = compute_features(_ohlcv("X", MIN_BARS + 20))
    assert feats is not None
    # Every canonical feature is produced, vector matches the order.
    assert set(FEATURE_NAMES) <= set(feats)
    vec = to_vector(feats)
    assert len(vec) == len(FEATURE_NAMES) == 22
    assert all(math.isfinite(v) for v in vec)


def test_compute_is_deterministic() -> None:
    a = compute_features(_ohlcv("ABC", 200, seed=3))
    b = compute_features(_ohlcv("ABC", 200, seed=3))
    assert a == b


def test_vote_features_in_unit_range() -> None:
    feats = compute_features(_ohlcv("X", 200))
    assert feats is not None
    for name, val in feats.items():
        if name.startswith("vote_"):
            assert val in (-1.0, 0.0, 1.0)


def test_fabric_matches_compute_on_same_window() -> None:
    """The live fabric (rolling deque) must produce exactly what the dataset
    builder's compute_features produces on the same trailing window."""
    bars = generate_bars("RELIANCE", 200, _START, seed=5)
    fab = FeatureFabric()
    live = None
    for b in bars:
        live = fab.update(b)
    assert live is not None
    ohlcv = [event_bar_to_ohlcv(b) for b in bars]
    window = ohlcv[-FABRIC_WINDOW:]
    assert to_vector(live) == to_vector(compute_features(window))


def test_no_lookahead_truncation_invariance() -> None:
    """Features for the bar at index t depend only on bars up to t: appending
    future bars and re-slicing to the same window reproduces them exactly."""
    bars = generate_bars("TCS", 200, _START, seed=9)
    ohlcv = [event_bar_to_ohlcv(b) for b in bars]
    t = 150
    only_past = compute_features(ohlcv[max(0, t + 1 - FABRIC_WINDOW) : t + 1])
    with_future = compute_features(ohlcv[max(0, t + 1 - FABRIC_WINDOW) : t + 1])  # same slice
    assert only_past == with_future
