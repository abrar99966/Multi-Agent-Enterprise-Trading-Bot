"""Backtest on REAL market history from the durable bar store.

Bridges the legacy bar store (~2.3M real NSE OHLCV rows) into the
event-sourced pipeline: the same deterministic journaled stack that runs on
synthetic data -- GBDT/momentum strategy, risk gateway, autonomy tiers, TCA,
hash-chained journal -- now driven by actual bars. Results land in
data/journal/<name>.jsonl + data/tca/<name>.db and appear in the dashboards
at /dash immediately (no server restart needed).

    python scripts/run_real_backtest.py --symbols RELIANCE,TCS --interval day --last-n 500
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.audit.chain import verify_journal  # noqa: E402
from app.engine.runner import PaperSession  # noqa: E402
from app.marketdata.bridge import load_store_bars  # noqa: E402
from app.tca.store import SqliteTcaStore  # noqa: E402

_ARTIFACT = REPO_ROOT / "backend" / "app" / "models" / "artifacts" / "gbdt-v1.json"


def _strategy_factory():
    if not _ARTIFACT.is_file():
        return None, "momentum-v0 (SMA reference; no model artifact)"
    from app.engine.inference import InferenceService
    from app.strategy.model_strategy import ModelStrategy

    inference = InferenceService.from_path(_ARTIFACT)
    return (lambda bus, clock: ModelStrategy(bus, clock, inference)), inference.model_id


def main() -> int:
    p = argparse.ArgumentParser(description="Event-pipeline backtest on real stored bars.")
    p.add_argument("--symbols", default="RELIANCE,TCS")
    p.add_argument("--interval", default="day", help="bar store interval label (day, 30minute, ...)")
    p.add_argument("--last-n", type=int, default=500, help="most recent N bars per symbol")
    p.add_argument("--name", default="real", help="output name: data/journal/<name>.jsonl")
    p.add_argument("--momentum", action="store_true", help="force SMA strategy even if a model artifact exists")
    args = p.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    journal = REPO_ROOT / "data" / "journal" / f"{args.name}.jsonl"
    tca_db = REPO_ROOT / "data" / "tca" / f"{args.name}.db"
    for path in (journal, journal.with_suffix(".jsonl.head"), tca_db):
        if path.exists():
            path.unlink()

    bars = load_store_bars(symbols, interval=args.interval, last_n=args.last_n)
    factory, model_desc = (None, "momentum-v0 (forced)") if args.momentum else _strategy_factory()

    session = PaperSession(symbols, journal_path=journal, strategy_factory=factory, bars=bars)
    s = session.run()

    with SqliteTcaStore(tca_db) as store:
        n_tca = store.insert(session.tca.results())
    chain = verify_journal(journal)

    print(f"real backtest  symbols={','.join(symbols)} interval={args.interval} "
          f"bars={s['bars']} ({args.last_n}/symbol requested)")
    print(f"strategy  {model_desc}")
    print("-" * 60)
    print(f"  intents/approved/orders/fills : {s['intents']}/{s['approved']}/{s['orders']}/{s['fills']}")
    print(f"  tiers T1/T2/T3                : {s['tier_counts'][1]}/{s['tier_counts'][2]}/{s['tier_counts'][3]}")
    print(f"  realized PnL                  : {s['realized_pnl_total']:.2f}")
    if s.get("tca", {}).get("n_fills"):
        t = s["tca"]
        print(f"  TCA delay/exec/fees/total bps : {t['delay_bps']:.2f}/{t['execution_bps']:.2f}/"
              f"{t['fees_bps']:.2f}/{t['total_is_bps']:.2f}")
    print(f"  TCA rows persisted            : {n_tca} -> {tca_db}")
    print(f"  journal chain                 : {'OK' if chain.ok else 'FAIL'} ({chain.records} records)")
    print(f"  dashboards                    : http://127.0.0.1:8000/dash (journal {journal.name})")
    return 0 if chain.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
