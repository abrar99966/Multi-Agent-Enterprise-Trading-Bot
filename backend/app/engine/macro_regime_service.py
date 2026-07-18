"""Continuous macro-regime background service.

Hosts the MacroRegimeAnalyst on a long-lived bus so its tightening proposals are
auto-published and actually applied -- unlike the ephemeral per-round paper
sessions, which throw their bus away after each run.

What it owns (an isolated slow-path control plane):
  * a LiveClock (real wall time -- this is OFF the deterministic replay path),
  * a MemoryBus with NO journal (proposals here are advisory control state, not
    the trade audit log),
  * a ParameterController (the slow path's only write interface -- bounds,
    direction asymmetry, rate limit, TTL), and
  * the MacroRegimeAnalyst bound to that bus.

Each poll:
  1. ``analyst.poll_and_propose()`` fetches live macro data (US Treasury yield
     curve; FRED VIX when keyed) and, on stress, publishes a TIGHTENING proposal.
  2. a heartbeat Bar is published so the controller's TTL-expiry check runs on
     real time -- when macro returns to calm, applied overrides decay back to the
     human-set baseline (reverting to baseline is always safe, needs no approval).
  3. the bus is drained, applying whatever survived the controller's bounds.

Safety (docs/ARCHITECTURE.md sections 5-6): the analyst only ever proposes
tightenings, so a misread can only make the system MORE conservative. The
controller is the sole boundary; nothing here can loosen a limit or emit an
order. The service is opt-in (started via REST), never auto-run -- consistent
with the free/opt-in mandate (it makes outbound network calls).

The controller here is driven by a LiveClock rather than a bar stream, which is
correct for a standalone control plane: it is not part of any replay, so
determinism-by-event-time does not apply. A live trading session that wants these
effective limits reads ``effective_limits()`` at construction / on change.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.bus.memory import MemoryBus
from app.core.clock import LiveClock
from app.core.events import Bar, Streams
from app.risk.limits import RiskLimits
from app.services.macro_data import YieldCurvePoint
from app.slowpath.macro_regime import MacroRegimeAnalyst
from app.slowpath.params import ParameterController, default_risk_params

logger = logging.getLogger(__name__)

_HEARTBEAT_SYMBOL = "__macro_heartbeat__"
POLL_INTERVAL_SECONDS = 900  # 15 min -- macro moves slowly


class MacroRegimeService:
    """Manages the always-on macro regime analyst + its parameter controller."""

    def __init__(
        self,
        poll_interval_s: int = POLL_INTERVAL_SECONDS,
        analyst_ttl_s: int = 43_200,
    ) -> None:
        self._poll_interval = poll_interval_s
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._started_at: Optional[datetime] = None
        self._last_poll_at: Optional[datetime] = None
        self._polls = 0
        self._proposals_published = 0

        limits = RiskLimits()
        self._baselines = {
            "risk.max_position_qty": limits.max_position_qty,
            "risk.max_gross_exposure": limits.max_gross_exposure,
            "risk.max_order_notional": limits.max_order_notional,
        }
        self.clock = LiveClock()
        self.bus = MemoryBus(self.clock)  # no journal: advisory control state
        self.controller = ParameterController(
            self.bus,
            self.clock,
            default_risk_params(
                limits.max_position_qty,
                limits.max_gross_exposure,
                limits.max_order_notional,
            ),
        )
        self.analyst = MacroRegimeAnalyst(
            self.bus,
            self.clock,
            baseline_gross=limits.max_gross_exposure,
            ttl_s=analyst_ttl_s,
        )

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._started_at = datetime.now(timezone.utc)
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Macro regime service started (poll every %ds)", self._poll_interval)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("Macro regime service stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.poll_once()
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                logger.info("Macro regime loop cancelled")
                break
            except Exception as exc:  # never let the loop die
                logger.error("Macro regime poll failed: %s", exc, exc_info=True)
                await asyncio.sleep(30)

    # -- one poll cycle ------------------------------------------------

    async def poll_once(self) -> Dict[str, Any]:
        """Run a single macro poll: propose (on stress), tick TTL, apply, and
        return the resulting status. Safe to call directly (REST / tests)."""
        proposal = await self.analyst.poll_and_propose()
        if proposal is not None:
            self._proposals_published += 1
        # Heartbeat drives the controller's real-time TTL expiry so calm macro
        # decays applied overrides back to baseline.
        now = self.clock.now_ns()
        self.bus.publish(
            Streams.MD_BARS,
            Bar(symbol=_HEARTBEAT_SYMBOL, ts_open=now, interval_s=1,
                open=0.0, high=0.0, low=0.0, close=0.0, volume=0.0),
            ts_event=now,
        )
        self.bus.run_until_idle()
        self._polls += 1
        self._last_poll_at = datetime.now(timezone.utc)
        return self.status

    async def simulate_poll(self, spread: Optional[float],
                            vix: Optional[float]) -> Dict[str, Any]:
        """What-if: run ONE poll against a SYNTHETIC macro reading (given 10Y-2Y
        spread and/or VIX) through the real bus + controller, then restore the
        live data source. Applies a genuine tightening so the effect on effective
        limits is observable -- for demos/ops, not a data source. The applied
        override still decays via TTL / a later real poll.

        A spread is modeled as a yield-curve point y10-y2 == spread (y2 anchored
        at 0), so ``spread < 0`` is an inverted curve.
        """
        class _Sim:
            async def latest_yield_curve(self_):
                if spread is None:
                    return None
                return YieldCurvePoint(date="SIMULATED", y2=0.0, y10=spread)

            async def latest_value(self_, series_id: str):
                return vix

        original = self.analyst._data
        self.analyst._data = _Sim()
        try:
            status = await self.poll_once()
        finally:
            self.analyst._data = original  # always restore the live source
        status["simulated"] = {"spread_10y_2y": spread, "vix": vix}
        return status

    # -- read side -----------------------------------------------------

    def effective_limits(self) -> Dict[str, float]:
        """Current effective risk limits after macro tightenings. A live trading
        session reads these to inherit macro-driven constraints."""
        return {
            name: self.controller.effective(name) or self._baselines[name]
            for name in self._baselines
        }

    @property
    def status(self) -> Dict[str, Any]:
        effective = self.effective_limits()
        limits = {
            name: {
                "baseline": self._baselines[name],
                "effective": effective[name],
                "tightened": effective[name] < self._baselines[name],
            }
            for name in self._baselines
        }
        return {
            "running": self._running,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_poll_at": self._last_poll_at.isoformat() if self._last_poll_at else None,
            "poll_interval_s": self._poll_interval,
            "polls": self._polls,
            "proposals_published": self._proposals_published,
            "macro_regime": self.analyst.macro_regime,
            "analyst_errors": self.analyst.errors,
            "limits": limits,
        }


# Singleton instance (mirrors engine/paper_trading_service.py).
macro_regime_service = MacroRegimeService()
