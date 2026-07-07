"""Run a paper/backtest session, report TCA + autonomy tiers, and verify that
the backtest IS a replay of the journal (the parity property, §16 Phase 2).

    python scripts/backtest_report.py --symbols RELIANCE,TCS --bars 500 \
        --journal data/journal/bt.jsonl --tca data/tca/bt.db

If a trained model artifact exists it drives the session (GBDT fast path);
otherwise the SMA reference strategy is used.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.audit.chain import verify_journal  # noqa: E402
from app.engine.inference import InferenceService  # noqa: E402
from app.engine.runner import PaperSession  # noqa: E402
from app.strategy.model_strategy import ModelStrategy  # noqa: E402
from app.tca.store import SqliteTcaStore  # noqa: E402

_DEFAULT_ARTIFACT = REPO_ROOT / "backend" / "app" / "models" / "artifacts" / "gbdt-v1.json"


def _strategy_factory(artifact_path: Path):
    if not artifact_path.exists():
        return None, "momentum-v0 (SMA reference; no model artifact found)"
    inference = InferenceService.from_path(artifact_path)
    return (lambda bus, clock: ModelStrategy(bus, clock, inference)), inference.model_id


def main() -> int:
    p = argparse.ArgumentParser(description="Backtest + TCA + replay-parity report.")
    p.add_argument("--symbols", default="RELIANCE,TCS")
    p.add_argument("--bars", type=int, default=500)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--journal", default=str(REPO_ROOT / "data" / "journal" / "bt.jsonl"))
    p.add_argument("--tca", default=str(REPO_ROOT / "data" / "tca" / "bt.db"))
    p.add_argument("--artifact", default=str(_DEFAULT_ARTIFACT))
    args = p.parse_args()

    journal = Path(args.journal)
    if journal.exists():
        journal.unlink()
    head = journal.with_suffix(journal.suffix + ".head")
    if head.exists():
        head.unlink()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    factory, model_desc = _strategy_factory(Path(args.artifact))

    session = PaperSession(
        symbols, n_bars=args.bars, seed=args.seed, journal_path=journal,
        strategy_factory=factory,
    )
    s = session.run()

    print(f"backtest  symbols={','.join(symbols)} bars/symbol={args.bars} seed={args.seed}")
    print(f"strategy  {model_desc}")
    print("-" * 60)
    print(f"  bars/intents/approved/orders/fills : "
          f"{s['bars']}/{s['intents']}/{s['approved']}/{s['orders']}/{s['fills']}")
    print(f"  autonomy tiers (approved)          : "
          f"T1={s['tier_counts'][1]} T2={s['tier_counts'][2]} T3={s['tier_counts'][3]} "
          f"(approval_requests={s['approval_requests']})")
    print(f"  realized PnL                       : {s['realized_pnl_total']:.2f}")

    tca = s.get("tca", {})
    if tca.get("n_fills"):
        print("  TCA (notional-weighted bps):")
        print(f"    delay={tca['delay_bps']:.2f}  execution={tca['execution_bps']:.2f}  "
              f"fees={tca['fees_bps']:.2f}  total_IS={tca['total_is_bps']:.2f}")
        for h in (1, 5, 30):
            if f"markout_{h}_bps" in tca:
                print(f"    markout +{h} bar: {tca[f'markout_{h}_bps']:.2f} bps "
                      f"(n={int(tca[f'markout_{h}_n'])})")

    # Persist per-fill TCA.
    with SqliteTcaStore(Path(args.tca)) as store:
        n = store.insert(session.tca.results())
    print(f"  TCA rows persisted                 : {n} -> {args.tca}")

    # Audit chain.
    chain = verify_journal(journal)
    print(f"  journal chain                      : {'OK' if chain.ok else 'FAIL'} "
          f"({chain.records} records)")

    # Backtest == replay: feed the journal back through the same code path.
    replay = PaperSession.replay_from_journal(journal, strategy_factory=factory)
    same = (
        [e.payload for e in session.bus.events if e.stream == "signal.intents"]
        == [e.payload for e in replay.bus.events if e.stream == "signal.intents"]
        and [e.payload for e in session.bus.events if e.stream == "exec.fills"]
        == [e.payload for e in replay.bus.events if e.stream == "exec.fills"]
        and session.summary["tca"] == replay.summary["tca"]
    )
    print(f"  replay determinism (backtest==live): {'PASS' if same else 'FAIL'}")

    return 0 if (chain.ok and same) else 1


if __name__ == "__main__":
    raise SystemExit(main())
