"""Train -> sign -> serve: determinism, validity, feature-order safety."""
from __future__ import annotations

import math

import pytest

from app.engine.inference import InferenceService
from app.features.fabric import FEATURE_NAMES, FeatureFabric
from app.learning.artifact import model_id, sign
from app.learning.train import train_from_synthetic
from app.marketdata.synthetic import generate_bars

_SYMS = ["RELIANCE", "TCS", "INFY"]
_START = 1_750_000_000_000_000_000


def test_training_is_deterministic() -> None:
    a = sign(train_from_synthetic(_SYMS, n_bars=400, seed=42, num_boost_round=40))
    b = sign(train_from_synthetic(_SYMS, n_bars=400, seed=42, num_boost_round=40))
    assert a["model_text"] == b["model_text"]
    assert model_id(a) == model_id(b)


def test_labels_are_non_degenerate() -> None:
    art = train_from_synthetic(_SYMS, n_bars=400, seed=42, num_boost_round=10)
    tr = art["training"]
    assert tr["n_pos"] > 0 and tr["n_neg"] > 0


def test_score_is_probability_with_attributions(inference_service: InferenceService) -> None:
    fab = FeatureFabric()
    feats = None
    for b in generate_bars("RELIANCE", 200, _START, seed=11):
        feats = fab.update(b)
    assert feats is not None
    r = inference_service.score(feats)
    assert 0.0 <= r.prob <= 1.0
    assert r.model_id == inference_service.model_id
    # SHAP contributions cover every feature plus the base term.
    assert set(FEATURE_NAMES) <= set(r.contributions)
    assert "_base" in r.contributions
    assert all(math.isfinite(v) for v in r.contributions.values())


def test_inference_is_deterministic(inference_service: InferenceService) -> None:
    fab = FeatureFabric()
    feats = None
    for b in generate_bars("TCS", 200, _START, seed=12):
        feats = fab.update(b)
    assert feats is not None
    assert inference_service.score(feats).prob == inference_service.score(feats).prob


def test_feature_order_mismatch_rejected() -> None:
    art = sign(train_from_synthetic(_SYMS, n_bars=400, seed=42, num_boost_round=10))
    art = dict(art)
    art["feature_names"] = list(reversed(FEATURE_NAMES))  # wrong order
    art = sign(art)  # re-sign so the integrity check passes; order check must still fail
    with pytest.raises(ValueError):
        InferenceService.from_artifact(art)
