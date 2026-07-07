"""Shared fixtures for Phase 1 model tests.

Trains the fast-path GBDT once per test session (deterministic synthetic
data) and exposes the signed artifact path plus a loaded InferenceService.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.engine.inference import InferenceService
from app.learning.artifact import save_artifact
from app.learning.train import train_from_synthetic

_TRAIN_SYMBOLS = ["RELIANCE", "TCS", "INFY"]


@pytest.fixture(scope="session")
def model_artifact_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    artifact = train_from_synthetic(
        _TRAIN_SYMBOLS, n_bars=700, seed=42, num_boost_round=60
    )
    path = tmp_path_factory.mktemp("model") / "gbdt-test.json"
    save_artifact(path, artifact)
    return path


@pytest.fixture(scope="session")
def inference_service(model_artifact_path: Path) -> InferenceService:
    return InferenceService.from_path(model_artifact_path)
