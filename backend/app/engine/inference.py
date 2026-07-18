"""Deterministic model serving for the fast path.

Loads a hash-verified artifact, then scores a feature dict into a probability
plus per-feature SHAP contributions (LightGBM ``pred_contrib``) for
per-decision explainability. No wall clock, no RNG, no I/O on the hot path --
given the artifact and the features, ``score`` is a pure function, so it
preserves replay determinism. An LLM never sits here (or anywhere on the
decision path); this is GBDT only (docs/ARCHITECTURE.md section 5).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import lightgbm as lgb
import numpy as np

from app.features.fabric import FEATURE_NAMES
from app.learning.artifact import ModelSpec, load_artifact, verify_artifact


@dataclass(frozen=True)
class ScoreResult:
    prob: float
    model_id: str
    contributions: Dict[str, float]  # per-feature SHAP, + "_base" expected value


class InferenceService:
    """Wraps one signed artifact. Construct via from_artifact / from_path."""

    def __init__(self, spec: ModelSpec) -> None:
        if spec.feature_names != FEATURE_NAMES:
            raise ValueError(
                "artifact feature order does not match the current fabric; "
                "retrain against this code"
            )
        self._spec = spec
        self._booster = lgb.Booster(model_str=spec.model_text)
        self._booster.params["num_threads"] = 1

    @classmethod
    def from_path(cls, path: Path) -> "InferenceService":
        return cls(ModelSpec.from_artifact(load_artifact(path)))

    @classmethod
    def from_artifact(cls, artifact: Dict[str, Any]) -> "InferenceService":
        return cls(ModelSpec.from_artifact(verify_artifact(artifact)))

    @property
    def model_id(self) -> str:
        return self._spec.model_id

    @property
    def enter_threshold(self) -> float:
        return self._spec.enter_threshold

    @property
    def exit_threshold(self) -> float:
        return self._spec.exit_threshold

    def score(self, feats: Dict[str, float]) -> ScoreResult:
        vec = np.asarray([[feats[name] for name in FEATURE_NAMES]], dtype=np.float64)
        prob = float(self._booster.predict(vec)[0])
        contrib = self._booster.predict(vec, pred_contrib=True)[0]
        # pred_contrib has one column per feature plus a trailing base value.
        attributions = {name: float(contrib[i]) for i, name in enumerate(FEATURE_NAMES)}
        attributions["_base"] = float(contrib[-1])
        return ScoreResult(prob=prob, model_id=self._spec.model_id, contributions=attributions)
