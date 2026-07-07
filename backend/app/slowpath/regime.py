"""Statistical regime classifier (deterministic slow-path analyst).

Classifies the market into {trend, chop, stress, crisis} from realized
volatility, and on a shift to a more severe regime emits ParameterChange
PROPOSALS that TIGHTEN risk limits (cut gross exposure and position caps).
It only ever proposes tightenings, so everything it emits auto-applies and a
misfire can only make the system more conservative -- the fail-safe direction.

This is the deterministic counterpart the design pairs with the LLM analyst
(section 6.3 quorum): a pure function of the bar stream, no clock, no RNG, so
it stays replay-deterministic and is the statistical signal an LLM regime call
must agree with before large capital shifts. Extends SlowPathAgent, so a bug
here is swallowed and the fast path is unaffected.
"""
from __future__ import annotations

from collections import deque
from statistics import median, pstdev
from typing import Dict, List, Optional

from app.bus.base import EventBus
from app.core.clock import Clock
from app.core.events import Bar, Event, ParameterChangeProposal, Streams
from app.slowpath.base import SlowPathAgent

_SEVERITY = {"chop": 0, "trend": 1, "stress": 2, "crisis": 3}

# Tightening factor applied to risk limits per regime (fraction of baseline).
_REGIME_SIZING = {"stress": 0.5, "crisis": 0.25}
_TIGHTEN_PARAMS = ("risk.max_gross_exposure", "risk.max_position_qty")


def classify_regime(
    returns: List[float], short: int = 10, long: int = 60,
    stress_ratio: float = 2.0, crisis_ratio: float = 4.0, trend_strength: float = 1.0,
) -> Optional[str]:
    """Regime from the volatility ratio (recent vs baseline). None until warm."""
    if len(returns) < long:
        return None
    recent = returns[-short:]
    short_vol = pstdev(recent)
    long_vol = pstdev(returns[-long:])
    if long_vol <= 0:
        return "chop"
    ratio = short_vol / long_vol
    if ratio >= crisis_ratio:
        return "crisis"
    if ratio >= stress_ratio:
        return "stress"
    mean_recent = sum(recent) / len(recent)
    if short_vol > 0 and abs(mean_recent) / short_vol >= trend_strength:
        return "trend"
    return "chop"


class RegimeClassifier(SlowPathAgent):
    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        source: str = "regime-classifier",
        baseline_gross: float = 2_000_000.0,
        baseline_position_qty: float = 20_000.0,
        ttl_s: int = 86_400,
        short: int = 10,
        long: int = 60,
    ) -> None:
        super().__init__(bus)
        self._clock = clock
        self._source = source
        self._baselines = {
            "risk.max_gross_exposure": baseline_gross,
            "risk.max_position_qty": baseline_position_qty,
        }
        self._ttl_s = ttl_s
        self._short = short
        self._long = long
        self._closes: Dict[str, deque] = {}
        self.market_regime = "chop"
        self.subscribe(Streams.MD_BARS, self._on_bar)

    def _on_bar(self, event: Event) -> None:
        bar = Bar.model_validate(event.payload)
        closes = self._closes.setdefault(bar.symbol, deque(maxlen=self._long + 1))
        closes.append(bar.close)
        regime = self._market_regime_estimate()
        if regime is None:
            return
        if _SEVERITY[regime] > _SEVERITY[self.market_regime]:
            self._propose_tightening(regime, bar.ts_open)
        self.market_regime = regime

    def _market_regime_estimate(self) -> Optional[str]:
        """Most severe per-symbol regime across tracked symbols."""
        worst: Optional[str] = None
        for closes in self._closes.values():
            prices = list(closes)
            if len(prices) < self._long + 1:
                continue
            rets = [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices))]
            regime = classify_regime(rets, self._short, self._long)
            if regime is not None and (worst is None or _SEVERITY[regime] > _SEVERITY[worst]):
                worst = regime
        return worst

    def _propose_tightening(self, regime: str, ts: int) -> None:
        factor = _REGIME_SIZING.get(regime)
        if factor is None:
            return
        for param in _TIGHTEN_PARAMS:
            proposed = self._baselines[param] * factor
            self._bus.publish(
                Streams.CTL_PARAM_PROPOSALS,
                ParameterChangeProposal(
                    proposal_id=f"{self._source}:{param}:{ts}",
                    parameter=param,
                    proposed_value=proposed,
                    source=self._source,
                    ttl_s=self._ttl_s,
                    rationale=f"regime shift to {regime}: tighten {param} to {factor:.0%} of baseline",
                    evidence=[f"market_regime={regime}"],
                ),
                ts_event=self._clock.now_ns(),
            )
