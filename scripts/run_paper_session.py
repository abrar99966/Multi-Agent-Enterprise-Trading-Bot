"""Run a synthetic paper-trading session end to end.

Generates deterministic bars, trades them through the full Phase 0
pipeline (strategy -> risk -> paper broker -> positions), journals every
event to a hash-chained JSONL file, then independently verifies the
chain. Exit code: 0 if the chain verifies, 1 if it does not.

Usage (from repo root):
    python scripts/run_paper_session.py --symbols RELIANCE,TCS \
        --bars 500 --seed 42 --journal data/journal/session.jsonl
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backend"))

from app.audit.chain import verify_journal  # noqa: E402
from app.engine.runner import PaperSession  # noqa: E402

_COUNT_KEYS = ("bars", "intents", "verdicts", "approved", "rejected", "orders", "fills")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a deterministic synthetic paper-trading session."
    )
    parser.add_argument(
        "--symbols",
        default="RELIANCE,TCS",
        help="comma-separated symbol list (default: RELIANCE,TCS)",
    )
    parser.add_argument(
        "--bars", type=int, default=500, help="bars per symbol (default: 500)"
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="synthetic data seed (default: 42)"
    )
    parser.add_argument(
        "--journal",
        default="data/journal/session.jsonl",
        help="journal output path (default: data/journal/session.jsonl)",
    )
    args = parser.parse_args(argv)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        parser.error("--symbols must name at least one symbol")
    journal_path = Path(args.journal)
    if journal_path.exists() and journal_path.stat().st_size > 0:
        # Journals are append-only; a reused path would chain a second
        # session onto the first and pollute any later replay.
        parser.error(
            f"journal {journal_path} already exists; delete it or pick a new path"
        )

    session = PaperSession(
        symbols, n_bars=args.bars, seed=args.seed, journal_path=journal_path
    )
    summary = session.run()

    print(f"paper session  symbols={','.join(symbols)} bars/symbol={args.bars} seed={args.seed}")
    print("-" * 56)
    for key in _COUNT_KEYS:
        print(f"  {key:<22} {summary[key]:>10}")
    print(f"  {'realized_pnl_total':<22} {summary['realized_pnl_total']:>10.2f}")
    print("  final positions / last prices:")
    for symbol in symbols:
        qty = summary["final_positions"][symbol]
        last = summary["last_prices"].get(symbol)
        last_text = f"{last:.4f}" if last is not None else "n/a"
        print(f"    {symbol:<12} qty={qty:>10.1f}  last={last_text}")
    print(f"  journal: {summary['journal_path']}")

    report = verify_journal(journal_path)
    status = "OK" if report.ok else "FAIL"
    detail = f" reason={report.reason}" if report.reason else ""
    print(f"chain verification: {status} ({report.records} records){detail}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
