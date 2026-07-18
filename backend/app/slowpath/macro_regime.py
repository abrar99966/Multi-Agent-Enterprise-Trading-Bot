"""Macro regime analyst (slow path, off-replay).

Reads real macro data -- the US Treasury yield curve (no key) and, when a FRED
key is configured, the equity-implied-vol index (VIXCLS) -- and, on a macro
STRESS reading, proposes a bounded TIGHTENING of gross exposure.

Why this is safe by construction (docs/ARCHITECTURE.md sections 5-6):
  * It only ever proposes tightenings, which auto-apply. A misread can only make
    the system MORE conservative -- the fail-safe direction. It can never loosen
    and never emit an order.
  * It is OFF the deterministic replay path. Like LLMAnalyst, it hits the network
    and reads the real clock, so it is invoked explicitly (a schedule / an
    operator action) via ``poll_and_propose`` -- NEVER inside the bus dispatch
    loop -- and it is not wired into engine/runner.py's deterministic assembly.
  * It extends SlowPathAgent, so any bug is swallowed and counted; the fast path
    is unaffected.

This is the macro counterpart to the price-derived RegimeClassifier
(slowpath/regime.py): that one is a pure function of the bar stream (stays
replay-deterministic); this one adds an independent, external macro signal that
an LLM regime call or a large capital shift can be cross-checked against.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from app.bus.base import EventBus
from app.core.clock import Clock
from app.core.events import ParameterChangeProposal, Streams
from app.services.macro_data import MacroDataAdapter, macro_data
from app.slowpath.base import SlowPathAgent

log = logging.getLogger(__name__)

# VIXCLS levels: >30 elevated (stress), >40 crisis. Curve inversion is stress.
_VIX_STRESS = 30.0
_VIX_CRISIS = 40.0
# Tightening factor per macro regime (fraction of baseline gross exposure).
# Capped at a 50% single-step cut so a proposal applies from baseline in one
# poll -- the ParameterController rejects steps larger than max_step_frac (0.5).
# Deeper crisis cuts ratchet further over successive polls.
_MACRO_SIZING = {"stress": 0.6, "crisis": 0.5}


def classify_macro_regime(
    spread_10y_2y: Optional[float], vix: Optional[float]
) -> Optional[str]:
    """Combine the yield-curve spread and VIX into {None, stress, crisis}.

    Pure function (no I/O) so it is unit-testable offline. Returns the MORE
    severe of the two signals; None means 'no macro stress detected'.
    """
    severity = 0  # 0 = calm, 2 = stress, 3 = crisis
    if vix is not None:
        if vix >= _VIX_CRISIS:
            severity = max(severity, 3)
        elif vix >= _VIX_STRESS:
            severity = max(severity, 2)
    if spread_10y_2y is not None and spread_10y_2y < 0.0:
        # An inverted curve is a stress precursor; a deep inversion, crisis-level.
        severity = max(severity, 3 if spread_10y_2y <= -0.5 else 2)
    if severity >= 3:
        return "crisis"
    if severity >= 2:
        return "stress"
    return None


class MacroRegimeAnalyst(SlowPathAgent):
    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        adapter: Optional[MacroDataAdapter] = None,
        source: str = "macro-regime",
        baseline_gross: float = 2_000_000.0,
        ttl_s: int = 43_200,  # 12h -- macro moves slowly; decays back to baseline
    ) -> None:
        super().__init__(bus)
        self._clock = clock
        self._data = adapter or macro_data
        self._source = source
        self._baseline_gross = baseline_gross
        self._ttl_s = ttl_s
        self.macro_regime: Optional[str] = None

    async def poll_and_propose(self) -> Optional[ParameterChangeProposal]:
        """Pull macro data, classify, and on stress publish ONE tightening
        proposal. Non-deterministic + networked: never call inside the bus loop.
        Returns the proposal, or None when macro is calm / data is unavailable."""
        try:
            point = await self._data.latest_yield_curve()
            spread = point.spread_10y_2y if point else None
            vix = await self._data.latest_value("VIXCLS")  # [] -> None without a key
        except Exception:  # best-effort: a macro outage must change nothing
            self.errors += 1
            return None

        regime = classify_macro_regime(spread, vix)
        self.macro_regime = regime
        if regime is None:
            return None

        factor = _MACRO_SIZING[regime]
        proposed = self._baseline_gross * factor
        now = self._clock.now_ns()
        evidence: List[str] = [f"macro_regime={regime}"]
        if spread is not None:
            evidence.append(f"10y_2y_spread={spread:.2f}")
        if vix is not None:
            evidence.append(f"vix={vix:.1f}")
        proposal = ParameterChangeProposal(
            proposal_id=f"{self._source}:risk.max_gross_exposure:{now}",
            parameter="risk.max_gross_exposure",
            proposed_value=proposed,
            source=self._source,
            ttl_s=self._ttl_s,
            rationale=f"macro {regime}: tighten gross exposure to {factor:.0%} of baseline",
            evidence=evidence,
        )
        self._bus.publish(Streams.CTL_PARAM_PROPOSALS, proposal, ts_event=now)
        return proposal
