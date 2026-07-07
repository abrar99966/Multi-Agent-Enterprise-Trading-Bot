"""Signed model artifact: sign/verify round-trip, tamper detection, stable id."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.learning.artifact import (
    ArtifactIntegrityError,
    load_artifact,
    model_id,
    save_artifact,
    sign,
    verify_artifact,
)


def _artifact() -> dict:
    return {
        "schema": "etb-model-v1",
        "feature_names": ["a", "b"],
        "horizon": 5,
        "enter_threshold": 0.55,
        "exit_threshold": 0.45,
        "model_text": "tree...\n",
        "training": {"n_samples": 10},
    }


def test_sign_then_verify_round_trips() -> None:
    signed = sign(_artifact())
    assert verify_artifact(signed) is signed
    assert model_id(signed).startswith("model-")


def test_signature_is_content_derived_and_stable() -> None:
    a, b = sign(_artifact()), sign(_artifact())
    assert a["sha256"] == b["sha256"]  # deterministic over identical content
    assert model_id(a) == model_id(b)


def test_tamper_is_detected() -> None:
    signed = sign(_artifact())
    signed["enter_threshold"] = 0.99  # flip a field after signing
    with pytest.raises(ArtifactIntegrityError):
        verify_artifact(signed)


def test_missing_signature_rejected() -> None:
    with pytest.raises(ArtifactIntegrityError):
        verify_artifact(_artifact())  # no sha256


def test_save_load_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    mid = save_artifact(path, _artifact())
    loaded = load_artifact(path)
    assert model_id(loaded) == mid


def test_load_rejects_tampered_file(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    save_artifact(path, _artifact())
    text = path.read_text(encoding="utf-8").replace('"horizon": 5', '"horizon": 7')
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ArtifactIntegrityError):
        load_artifact(path)
