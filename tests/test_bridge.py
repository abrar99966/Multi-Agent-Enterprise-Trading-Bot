"""Bar-store bridge: legacy Bar -> core Bar conversion + PaperSession on
externally supplied (real) bars. No dependency on the real database."""
from __future__ import annotations

import pytest

from app.core.events import NS_PER_SEC, Streams
from app.engine.runner import PaperSession
from app.learning.bar import Bar as LegacyBar
from app.marketdata.bridge import INTERVAL_SECONDS, to_core_bar
from app.marketdata.synthetic import generate_bars

_T = 1_700_000_000  # epoch seconds


def test_conversion_maps_fields_and_units() -> None:
    legacy = LegacyBar(t=_T, o=100.0, h=105.0, l=99.0, c=103.0, v=12_345.0)
    bar = to_core_bar("reliance", legacy, "day")
    assert bar.symbol == "RELIANCE"
    assert bar.ts_open == _T * NS_PER_SEC
    assert bar.interval_s == 86_400
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (100, 105, 99, 103, 12_345)
    assert to_core_bar("X", legacy, "30minute").interval_s == 1800


def test_conversion_tolerates_missing_ohlc_and_volume() -> None:
    # Index rows sometimes carry only close; o/h/l zero and v zero.
    legacy = LegacyBar(t=_T, o=0.0, h=0.0, l=0.0, c=250.0, v=0.0)
    bar = to_core_bar("NIFTY", legacy, "day")
    assert bar.open == bar.close == 250.0
    assert bar.high >= bar.open and bar.low <= bar.open
    assert bar.volume == 0.0


def test_unknown_interval_raises() -> None:
    legacy = LegacyBar(t=_T, o=1, h=1, l=1, c=1, v=1)
    with pytest.raises(ValueError, match="unknown legacy interval"):
        to_core_bar("X", legacy, "fortnight")


def test_paper_session_runs_on_supplied_bars(tmp_path) -> None:
    """The `bars` override drives the identical pipeline: journaled, risk-gated,
    deterministic. Reuse the synthetic generator as a stand-in for store bars."""
    bars = generate_bars("RELIANCE", 300, 1_750_000_000_000_000_000, seed=21)
    journal = tmp_path / "supplied.jsonl"
    session = PaperSession(["RELIANCE"], journal_path=journal, bars=bars)
    summary = session.run()
    assert summary["bars"] == 300
    assert summary["intents"] > 0  # crossovers occur on this seed
    # Replay parity must hold for supplied bars exactly as for generated ones.
    replay = PaperSession.replay_from_journal(journal)
    for stream in (Streams.SIGNAL_INTENTS, Streams.EXEC_FILLS):
        orig = [e.payload for e in session.bus.events if e.stream == stream]
        repl = [e.payload for e in replay.bus.events if e.stream == stream]
        assert orig == repl
