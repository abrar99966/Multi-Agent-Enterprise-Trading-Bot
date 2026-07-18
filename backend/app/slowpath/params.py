"""Bounded parameter controller -- the slow path's only write interface.

Analysts emit ParameterChangeProposal events; this controller is the boundary
that decides what actually changes (docs/ARCHITECTURE.md section 6.2):

- Bounds: every parameter has [min, max] and a max step per change.
- Direction asymmetry: a proposal that moves a parameter in its conservative
  (risk-reducing) direction TIGHTENS and auto-applies within bounds; a
  proposal that loosens is HELD for human approval. A hallucinating analyst
  can only make the system more conservative, never more aggressive.
- Rate limit: at most N applied changes per parameter per window.
- Quorum: a parameter may require >= min_sources independent sources to agree
  (within the window) before a tightening applies -- so one noisy signal
  cannot swing capital.
- TTL: every applied change expires back to the human-set baseline unless
  renewed, so a stuck analyst cannot leave the system permanently drifted.
  Reverting to baseline is always safe (it is the approved default) and needs
  no approval.

Applied changes are published as ParameterChange on CTL_PARAMS; the risk
gateway consumes risk.* changes as effective-limit overrides. The controller
is deterministic: proposals and TTL expiry are driven by event time (bars),
never the wall clock.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.bus.base import EventBus
from app.core.clock import Clock
from app.core.events import (
    NS_PER_SEC,
    Event,
    ParameterChange,
    ParameterChangeProposal,
    Streams,
)


@dataclass(frozen=True)
class ControllableParam:
    name: str
    baseline: float
    min_value: float
    max_value: float
    conservative: str  # "down" (lower = safer) or "up"
    max_step_frac: float = 0.5  # max single change as a fraction of baseline
    min_sources: int = 1  # quorum: distinct sources to apply a tightening
    window_s: int = 3600
    max_changes_per_window: int = 20

    def is_tightening(self, current: float, proposed: float) -> bool:
        return proposed < current if self.conservative == "down" else proposed > current

    def more_conservative(self, a: float, b: float) -> float:
        if self.conservative == "down":
            return min(a, b)
        return max(a, b)


@dataclass
class _Override:
    value: float
    expiry_ts: int  # event-time ns; revert to baseline at/after this


@dataclass
class _QuorumState:
    # source -> (ts_ns, proposed_value) for tightenings awaiting quorum
    sources: Dict[str, tuple] = field(default_factory=dict)


class ParameterController:
    def __init__(
        self, bus: EventBus, clock: Clock, params: List[ControllableParam]
    ) -> None:
        self._bus = bus
        self._clock = clock
        self._params: Dict[str, ControllableParam] = {p.name: p for p in params}
        self._effective: Dict[str, float] = {p.name: p.baseline for p in params}
        self._overrides: Dict[str, _Override] = {}
        self._applied_ts: Dict[str, deque] = {n: deque() for n in self._params}
        self._quorum: Dict[str, _QuorumState] = {n: _QuorumState() for n in self._params}
        #: loosening proposals awaiting human approval (proposal_id -> proposal)
        self.pending_loosening: Dict[str, ParameterChangeProposal] = {}
        bus.subscribe(Streams.CTL_PARAM_PROPOSALS, self._on_proposal)
        bus.subscribe(Streams.MD_BARS, self._on_bar)

    # -- public accessors ---------------------------------------------

    def effective(self, name: str) -> Optional[float]:
        return self._effective.get(name)

    # -- proposal handling --------------------------------------------

    def _on_proposal(self, event: Event) -> None:
        proposal = ParameterChangeProposal.model_validate(event.payload)
        param = self._params.get(proposal.parameter)
        if param is None:
            return  # unknown parameter: ignore (analysts cannot invent params)
        now = self._clock.now_ns()
        proposed = _clamp(proposal.proposed_value, param.min_value, param.max_value)
        current = self._effective[param.name]
        if abs(proposed - current) > param.max_step_frac * param.baseline + 1e-9:
            return  # step too large: reject
        if not self._rate_ok(param, now):
            return  # too many recent changes: reject
        if param.is_tightening(current, proposed):
            self._handle_tightening(param, proposed, proposal, now)
        elif proposed != current:
            # Loosening: hold for human approval (never auto-applied).
            self.pending_loosening[proposal.proposal_id] = proposal

    def _handle_tightening(
        self,
        param: ControllableParam,
        proposed: float,
        proposal: ParameterChangeProposal,
        now: int,
    ) -> None:
        if param.min_sources > 1:
            q = self._quorum[param.name]
            self._prune_quorum(param, q, now)
            q.sources[proposal.source] = (now, proposed)
            if len(q.sources) < param.min_sources:
                return  # not enough independent sources yet -- hold
            # Apply the most conservative value among the agreeing sources.
            proposed = self._consensus(param, q)
            q.sources.clear()
        self._apply(param, proposed, proposal.source, proposal.ttl_s, proposal.rationale, now)

    def approve_loosening(self, proposal_id: str, approver: str = "human") -> bool:
        """Apply a held loosening proposal (an explicit human decision).
        Returns True if a pending proposal was applied."""
        proposal = self.pending_loosening.pop(proposal_id, None)
        if proposal is None:
            return False
        param = self._params[proposal.parameter]
        now = self._clock.now_ns()
        proposed = _clamp(proposal.proposed_value, param.min_value, param.max_value)
        self._apply(param, proposed, f"{proposal.source}+{approver}", proposal.ttl_s,
                    proposal.rationale, now)
        return True

    # -- TTL expiry ----------------------------------------------------

    def _on_bar(self, event: Event) -> None:
        now = event.ts_event
        for name in list(self._overrides):
            override = self._overrides[name]
            if now >= override.expiry_ts:
                baseline = self._params[name].baseline
                old = self._effective[name]
                self._effective[name] = baseline
                del self._overrides[name]
                self._publish(name, old, baseline, "ttl_expiry", None,
                              "override expired; reverted to baseline")

    # -- internals -----------------------------------------------------

    def _apply(
        self,
        param: ControllableParam,
        value: float,
        source: str,
        ttl_s: Optional[int],
        rationale: str,
        now: int,
    ) -> None:
        old = self._effective[param.name]
        if value == old:
            return
        self._effective[param.name] = value
        ttl = ttl_s if ttl_s is not None else param.window_s
        self._overrides[param.name] = _Override(value, now + ttl * NS_PER_SEC)
        self._applied_ts[param.name].append(now)
        self._publish(param.name, old, value, source, ttl_s, rationale)

    def _publish(
        self, name: str, old: float, new: float, source: str,
        ttl_s: Optional[int], rationale: str,
    ) -> None:
        self._bus.publish(
            Streams.CTL_PARAMS,
            ParameterChange(parameter=name, old_value=old, new_value=new,
                            source=source, ttl_s=ttl_s, rationale=rationale),
            ts_event=self._clock.now_ns(),
        )

    def _rate_ok(self, param: ControllableParam, now: int) -> bool:
        window = param.window_s * NS_PER_SEC
        dq = self._applied_ts[param.name]
        while dq and dq[0] <= now - window:
            dq.popleft()
        return len(dq) < param.max_changes_per_window

    def _prune_quorum(self, param: ControllableParam, q: _QuorumState, now: int) -> None:
        window = param.window_s * NS_PER_SEC
        for src in [s for s, (ts, _) in q.sources.items() if ts <= now - window]:
            del q.sources[src]

    @staticmethod
    def _consensus(param: ControllableParam, q: _QuorumState) -> float:
        values = [v for _, v in q.sources.values()]
        result = values[0]
        for v in values[1:]:
            result = param.more_conservative(result, v)
        return result


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def default_risk_params(
    baseline_position_qty: float = 20_000.0,
    baseline_gross: float = 2_000_000.0,
    baseline_notional: float = 500_000.0,
) -> List[ControllableParam]:
    """The risk limits the slow path may tighten. Names map to RiskLimits
    fields as risk.<field>; the gateway consumes these as overrides."""
    return [
        ControllableParam("risk.max_position_qty", baseline_position_qty, 0.0,
                          baseline_position_qty, "down"),
        ControllableParam("risk.max_gross_exposure", baseline_gross, 0.0,
                          baseline_gross, "down"),
        ControllableParam("risk.max_order_notional", baseline_notional, 0.0,
                          baseline_notional, "down"),
    ]
