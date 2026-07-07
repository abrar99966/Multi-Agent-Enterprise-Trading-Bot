"""Train the fast-path GBDT and write a signed artifact.

Phase 0/1 uses deterministic synthetic bars by default so the whole pipeline
runs with zero external data. Example:

    python scripts/train_model.py --symbols RELIANCE,TCS,INFY --bars 800 \
        --out backend/app/models/artifacts/gbdt-v1.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.learning.artifact import save_artifact  # noqa: E402
from app.learning.train import train_from_synthetic  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Train the fast-path GBDT model.")
    p.add_argument("--symbols", default="RELIANCE,TCS,INFY")
    p.add_argument("--bars", type=int, default=800)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--horizon", type=int, default=5)
    p.add_argument("--rounds", type=int, default=80)
    p.add_argument(
        "--out",
        default=str(REPO_ROOT / "backend" / "app" / "models" / "artifacts" / "gbdt-v1.json"),
    )
    args = p.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    artifact = train_from_synthetic(
        symbols,
        n_bars=args.bars,
        seed=args.seed,
        horizon=args.horizon,
        num_boost_round=args.rounds,
    )
    mid = save_artifact(Path(args.out), artifact)
    tr = artifact["training"]
    print(f"trained {mid}")
    print(f"  symbols    : {','.join(symbols)}")
    print(f"  samples    : {tr['n_samples']} (pos={tr['n_pos']} neg={tr['n_neg']})")
    print(f"  features   : {len(artifact['feature_names'])}")
    print(f"  horizon    : {artifact['horizon']} bars")
    print(f"  thresholds : enter>={artifact['enter_threshold']} exit<={artifact['exit_threshold']}")
    print(f"  written    : {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
