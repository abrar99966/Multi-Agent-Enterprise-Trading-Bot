"""Versioned, hash-verified model artifact.

A trained model ships as a single JSON file: the LightGBM booster text plus
all metadata needed to reproduce its decisions (feature order, thresholds,
horizon, training provenance). A SHA-256 over the canonical bytes of every
field except ``sha256`` itself is the artifact's signature and its identity:
``model_id = "model-" + sha256[:12]``.

The loader recomputes the digest and refuses a tampered or corrupt artifact,
exactly as the audit journal does (core/hashing.py). True cryptographic
signing with a writer-held key is Phase 1+; hash-verification gives integrity
and a stable, content-derived model id now.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from app.core.hashing import canonical_json

import hashlib


class ArtifactIntegrityError(Exception):
    """The artifact's recomputed digest does not match its stored signature."""


def _digest(artifact: Dict[str, Any]) -> str:
    body = {k: v for k, v in artifact.items() if k != "sha256"}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def sign(artifact: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy with ``sha256`` set to the digest of all other fields."""
    signed = {k: v for k, v in artifact.items() if k != "sha256"}
    signed["sha256"] = _digest(signed)
    return signed


def model_id(artifact: Dict[str, Any]) -> str:
    return "model-" + artifact["sha256"][:12]


def save_artifact(path: Path, artifact: Dict[str, Any]) -> str:
    """Sign and write the artifact as pretty JSON. Returns the model_id."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    signed = sign(artifact)
    path.write_text(json.dumps(signed, indent=2, sort_keys=True), encoding="utf-8")
    return model_id(signed)


def load_artifact(path: Path) -> Dict[str, Any]:
    """Load and verify an artifact; raise ArtifactIntegrityError on mismatch."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return verify_artifact(data)


def verify_artifact(data: Dict[str, Any]) -> Dict[str, Any]:
    stored = data.get("sha256")
    if not isinstance(stored, str):
        raise ArtifactIntegrityError("artifact has no sha256 signature")
    recomputed = _digest(data)
    if recomputed != stored:
        raise ArtifactIntegrityError(
            f"artifact digest mismatch: stored {stored!r}, recomputed {recomputed!r}"
        )
    return data


@dataclass(frozen=True)
class ModelSpec:
    """Decoded view of the decision-relevant artifact fields."""

    model_id: str
    feature_names: list[str]
    horizon: int
    enter_threshold: float
    exit_threshold: float
    model_text: str

    @classmethod
    def from_artifact(cls, artifact: Dict[str, Any]) -> "ModelSpec":
        return cls(
            model_id=model_id(artifact),
            feature_names=list(artifact["feature_names"]),
            horizon=int(artifact["horizon"]),
            enter_threshold=float(artifact["enter_threshold"]),
            exit_threshold=float(artifact["exit_threshold"]),
            model_text=artifact["model_text"],
        )
