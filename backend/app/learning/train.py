"""Deterministic offline training for the fast-path GBDT.

Trains a LightGBM binary classifier (P(forward return > threshold)) on the
pooled feature dataset and emits a signed artifact (learning/artifact.py).

Determinism: ``seed`` fixed, ``num_threads=1``, ``deterministic=True``,
``force_row_wise=True`` -- so the same bars produce a byte-identical booster,
which is what lets a replayed session reproduce identical decisions. Training
runs offline; nothing here is on the decision path.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import lightgbm as lgb
import numpy as np

from app.core.events import Bar as EventBar
from app.features.fabric import FABRIC_WINDOW, MIN_BARS
from app.learning.dataset import build_dataset
from app.marketdata.synthetic import generate_bars

_DEFAULT_LGB_PARAMS: Dict[str, Any] = {
    "objective": "binary",
    "num_leaves": 15,
    "min_data_in_leaf": 30,
    "learning_rate": 0.05,
    "feature_fraction": 1.0,
    "bagging_fraction": 1.0,
    "max_depth": 4,
    "seed": 42,
    "num_threads": 1,
    "deterministic": True,
    "force_row_wise": True,
    "verbose": -1,
}


def train_artifact(
    bars_by_symbol: Dict[str, Sequence[EventBar]],
    horizon: int = 5,
    label_threshold: float = 0.0,
    enter_threshold: float = 0.55,
    exit_threshold: float = 0.45,
    num_boost_round: int = 80,
    seed: int = 42,
    lgb_params: Optional[Dict[str, Any]] = None,
    provenance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the dataset, train the booster, return an UNSIGNED artifact dict
    (caller signs via artifact.sign / save_artifact)."""
    ds = build_dataset(bars_by_symbol, horizon=horizon, label_threshold=label_threshold)
    if not ds.X:
        raise ValueError("empty training set; need more bars than the warmup window")
    if ds.n_pos == 0 or ds.n_neg == 0:
        raise ValueError(
            f"degenerate labels (pos={ds.n_pos}, neg={ds.n_neg}); "
            "adjust horizon/threshold or training data"
        )

    params = dict(lgb_params or _DEFAULT_LGB_PARAMS)
    params["seed"] = seed
    params.setdefault("num_threads", 1)
    params.setdefault("deterministic", True)
    params.setdefault("force_row_wise", True)
    dtrain = lgb.Dataset(
        np.asarray(ds.X, dtype=np.float64),
        label=np.asarray(ds.y, dtype=np.int32),
        feature_name=ds.feature_names,
    )
    booster = lgb.train(params, dtrain, num_boost_round=num_boost_round)
    model_text = booster.model_to_string()

    artifact: Dict[str, Any] = {
        "schema": "etb-model-v1",
        "feature_names": ds.feature_names,
        "fabric": {"min_bars": MIN_BARS, "window": FABRIC_WINDOW},
        "horizon": horizon,
        "label_threshold": label_threshold,
        "enter_threshold": enter_threshold,
        "exit_threshold": exit_threshold,
        "lgb_params": params,
        "num_boost_round": num_boost_round,
        "training": {
            "symbols": sorted(bars_by_symbol),
            "n_samples": len(ds.y),
            "n_pos": ds.n_pos,
            "n_neg": ds.n_neg,
            "seed": seed,
            **(provenance or {}),
        },
        "model_text": model_text,
    }
    return artifact


def train_from_synthetic(
    symbols: List[str],
    n_bars: int = 800,
    seed: int = 42,
    start_ts_ns: int = 1_700_000_000 * 1_000_000_000,
    interval_s: int = 60,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Convenience: train on deterministic synthetic bars (used by tests and
    the train CLI's default). Distinct default start_ts from the paper session
    so training data is not literally the evaluation data."""
    bars_by_symbol = {
        sym: generate_bars(sym, n_bars, start_ts_ns, interval_s=interval_s, seed=seed)
        for sym in symbols
    }
    provenance = {"source": "synthetic", "n_bars": n_bars}
    return train_artifact(bars_by_symbol, seed=seed, provenance=provenance, **kwargs)
