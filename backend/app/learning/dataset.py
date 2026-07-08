"""Supervised dataset builder for the fast-path model.

For each decision bar t we record the feature vector computed from
``bars[t-W+1 : t+1]`` (the same bounded window the live fabric uses) and a
binary label: did the forward return over ``horizon`` bars exceed
``label_threshold``? Features read only bars up to t; the label reads bars
t..t+horizon, which exist in history -- so there is no lookahead leaking into
the features, only honest supervised targets.

The builder is deterministic: same bars in, same (X, y) out.

OBB augmentation (optional):
    Pass ``obb_aug_features`` — a dict mapping symbol → pre-fetched OBB
    feature dict (fundamentals, macro, sentiment) — to extend the feature
    vector with real financial context.  Build with:

        from app.learning.obb_features import obb_augmenter
        await obb_augmenter.prefetch_universe(list(bars_by_symbol))

    Or use the convenience wrapper ``build_dataset_with_obb()`` which handles
    the prefetch automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

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
    *,
    obb_aug_features: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dataset:
    """Pool labeled samples across symbols. Symbols iterate in sorted order
    for determinism.

    Args:
        bars_by_symbol:    Symbol → ordered list of EventBar.
        horizon:           Forward-return horizon for label generation.
        label_threshold:   Forward return must exceed this to be label=1.
        obb_aug_features:  Optional OBB-derived extra features keyed by symbol.
                           When provided, the feature vector is extended with
                           fundamentals (z-scored), macro (FRED) and news
                           sentiment columns.  Build with:
                           ``await obb_augmenter.prefetch_universe(symbols)``.
    """
    # Determine augmented feature names (extends FEATURE_NAMES if OBB provided)
    aug_names: List[str] = []
    if obb_aug_features:
        for _feats in obb_aug_features.values():
            if _feats:
                aug_names = [k for k in _feats if k not in FEATURE_NAMES]
                break

    all_feature_names = list(FEATURE_NAMES) + aug_names

    X: List[List[float]] = []
    y: List[int] = []

    for symbol in sorted(bars_by_symbol):
        bars = list(bars_by_symbol[symbol])
        ohlcv = [event_bar_to_ohlcv(b) for b in bars]
        closes = [b.c for b in ohlcv]
        n = len(ohlcv)

        # Per-symbol OBB extras: same snapshot for every bar of this symbol
        # (fundamentals / macro are slow-moving; sentiment is a current reading)
        sym_aug: Dict[str, float] = {}
        if obb_aug_features and symbol in obb_aug_features:
            raw = obb_aug_features[symbol] or {}
            sym_aug = {k: raw.get(k, 0.0) for k in aug_names}

        # t must be warm (>= MIN_BARS-1) and have a label bar at t+horizon.
        for t in range(MIN_BARS - 1, n - horizon):
            window = ohlcv[max(0, t + 1 - FABRIC_WINDOW) : t + 1]
            feats = compute_features(window)
            if feats is None:
                continue
            fwd = closes[t + horizon] / closes[t] - 1.0

            vec = to_vector(feats)
            if aug_names:
                vec = vec + [sym_aug.get(k, 0.0) for k in aug_names]

            X.append(vec)
            y.append(1 if fwd > label_threshold else 0)

    n_pos = sum(y)
    return Dataset(
        X=X, y=y,
        feature_names=all_feature_names,
        n_pos=n_pos,
        n_neg=len(y) - n_pos,
    )


async def build_dataset_with_obb(
    bars_by_symbol: Dict[str, Sequence[EventBar]],
    horizon: int = 5,
    label_threshold: float = 0.0,
) -> Dataset:
    """Convenience async wrapper: prefetches OBB data then builds the augmented dataset.

    Falls back to base features gracefully when OBB is unavailable.
    """
    import logging
    import math
    import time as _time

    log = logging.getLogger(__name__)
    symbols = list(bars_by_symbol.keys())
    obb_aug: Optional[Dict[str, Dict[str, float]]] = None

    try:
        from app.learning.obb_features import obb_augmenter, _BULL_WORDS, _BEAR_WORDS
        if obb_augmenter.available:
            await obb_augmenter.prefetch_universe(symbols)
            macro = obb_augmenter._macro_cache or {}
            obb_aug = {}
            cutoff = _time.time() - 3 * 86400
            for sym in symbols:
                fund = obb_augmenter._get_fundamentals(sym)
                articles = obb_augmenter._cache.get(f"news:{sym}") or []
                sent_score, sent_count = 0.0, 0.0
                if articles:
                    total = sum(
                        sum(1 for w in _BULL_WORDS if w in str(a.get("title","")).lower())
                        - sum(1 for w in _BEAR_WORDS if w in str(a.get("title","")).lower())
                        for a in articles
                    )
                    sent_score = max(-1.0, min(1.0, total / max(len(articles), 1)))
                    recent = sum(
                        1 for a in articles
                        if (ts := obb_augmenter._parse_ts(
                            a.get("published_utc") or a.get("date")
                        )) and ts >= cutoff
                    )
                    sent_count = math.log1p(recent)
                obb_aug[sym] = {
                    **fund, **macro,
                    "news_sentiment":    round(sent_score, 4),
                    "news_count_3d_log": round(sent_count, 4),
                }
    except Exception as exc:
        log.warning("OBB prefetch failed — building base dataset: %s", exc)
        obb_aug = None

    return build_dataset(bars_by_symbol, horizon, label_threshold,
                         obb_aug_features=obb_aug)
