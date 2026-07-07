"""ParameterController: bounds, direction asymmetry, rate limit, quorum, TTL."""
from __future__ import annotations

from app.core.clock import SimClock
from app.core.events import NS_PER_SEC, ParameterChange, ParameterChangeProposal, Streams
from app.slowpath.params import ControllableParam, ParameterController
from tests.helpers import SyncTestBus

_T = 1_750_000_000_000_000_000


def _param(**kw) -> ControllableParam:
    base = dict(name="risk.max_gross_exposure", baseline=1_000_000.0, min_value=0.0,
                max_value=1_000_000.0, conservative="down", max_step_frac=1.0)
    base.update(kw)
    return ControllableParam(**base)


def _setup(param: ControllableParam, start: int = _T):
    clock = SimClock(start)
    bus = SyncTestBus(clock)
    ctrl = ParameterController(bus, clock, [param])
    return bus, clock, ctrl


def _propose(bus, pid, value, source="a", ttl_s=3600):
    bus.publish(
        Streams.CTL_PARAM_PROPOSALS,
        ParameterChangeProposal(proposal_id=pid, parameter="risk.max_gross_exposure",
                                proposed_value=value, source=source, ttl_s=ttl_s),
        ts_event=_T,
    )


def _changes(bus) -> list[ParameterChange]:
    return [ParameterChange.model_validate(e.payload) for e in bus.stream(Streams.CTL_PARAMS)]


def test_tightening_auto_applies() -> None:
    bus, _, ctrl = _setup(_param())
    _propose(bus, "p1", 500_000.0)  # lower the cap = tighten
    assert ctrl.effective("risk.max_gross_exposure") == 500_000.0
    changes = _changes(bus)
    assert len(changes) == 1 and changes[0].new_value == 500_000.0


def test_loosening_is_held_for_approval() -> None:
    bus, _, ctrl = _setup(_param())
    _propose(bus, "p1", 800_000.0)  # tighten first
    _propose(bus, "p2", 950_000.0)  # raise the cap = loosen -> held
    assert ctrl.effective("risk.max_gross_exposure") == 800_000.0  # unchanged
    assert "p2" in ctrl.pending_loosening
    # Human approves -> now applied.
    assert ctrl.approve_loosening("p2") is True
    assert ctrl.effective("risk.max_gross_exposure") == 950_000.0


def test_value_clamped_to_bounds() -> None:
    bus, _, ctrl = _setup(_param(min_value=100_000.0))
    _propose(bus, "p1", 10_000.0)  # below min -> clamp to 100k
    assert ctrl.effective("risk.max_gross_exposure") == 100_000.0


def test_step_too_large_rejected() -> None:
    bus, _, ctrl = _setup(_param(max_step_frac=0.1))  # max step 100k
    _propose(bus, "p1", 500_000.0)  # 500k jump > 100k -> rejected
    assert ctrl.effective("risk.max_gross_exposure") == 1_000_000.0
    assert _changes(bus) == []


def test_rate_limit_blocks_excess_changes() -> None:
    bus, _, ctrl = _setup(_param(max_changes_per_window=2))
    _propose(bus, "p1", 900_000.0)
    _propose(bus, "p2", 800_000.0)
    _propose(bus, "p3", 700_000.0)  # third within window -> rejected
    assert ctrl.effective("risk.max_gross_exposure") == 800_000.0
    assert len(_changes(bus)) == 2


def test_quorum_requires_two_sources() -> None:
    bus, _, ctrl = _setup(_param(min_sources=2))
    _propose(bus, "p1", 600_000.0, source="regime")  # one source -> held
    assert ctrl.effective("risk.max_gross_exposure") == 1_000_000.0
    _propose(bus, "p2", 500_000.0, source="llm")  # second distinct source -> apply
    # Applies the more conservative (lower) of the agreeing values.
    assert ctrl.effective("risk.max_gross_exposure") == 500_000.0


def test_ttl_reverts_to_baseline_on_bar() -> None:
    bus, clock, ctrl = _setup(_param())
    _propose(bus, "p1", 400_000.0, ttl_s=60)
    assert ctrl.effective("risk.max_gross_exposure") == 400_000.0
    # A bar after the TTL expiry reverts to baseline (event-time driven).
    from app.core.events import Bar
    expired_ts = _T + 61 * NS_PER_SEC
    clock.advance_to(expired_ts)
    bus.publish(
        Streams.MD_BARS,
        Bar(symbol="X", ts_open=expired_ts, interval_s=60, open=1, high=1, low=1,
            close=1, volume=1),
        ts_event=expired_ts,
    )
    assert ctrl.effective("risk.max_gross_exposure") == 1_000_000.0
    reverts = [c for c in _changes(bus) if c.source == "ttl_expiry"]
    assert len(reverts) == 1 and reverts[0].new_value == 1_000_000.0


def test_unknown_parameter_ignored() -> None:
    bus, _, ctrl = _setup(_param())
    bus.publish(
        Streams.CTL_PARAM_PROPOSALS,
        ParameterChangeProposal(proposal_id="p1", parameter="risk.bogus",
                                proposed_value=1.0, source="a"),
        ts_event=_T,
    )
    assert _changes(bus) == []
