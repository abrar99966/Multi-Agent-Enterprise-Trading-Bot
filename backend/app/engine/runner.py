"""Paper-trading session assembly.

PaperSession wires the full Phase 0 pipeline over a MemoryBus driven by
a SimClock:

    synthetic bars -> MomentumStrategy -> RiskGateway -> PaperBroker
                                                        -> PositionTracker

with an optional hash-chained journal teed off every publish.
replay_from_journal() rebuilds an identical session from the journaled
bars alone -- the determinism property the whole platform rests on:
feeding the recorded md.bars through fresh components must reproduce
the exact same intents, verdicts, orders and fills.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from app.bus.base import EventBus
from app.core.clock import Clock
from app.bus.journal import JournalReader, JournalWriter
from app.bus.memory import MemoryBus
from app.core.clock import SimClock
from app.core.events import NS_PER_SEC, Bar, RiskVerdict, Streams
from app.marketdata.replay import ReplaySource
from app.marketdata.synthetic import generate_bars
from app.oms.positions import PositionTracker
from app.paper.broker import PaperBroker
from app.risk.approver import AutoApprover
from app.risk.gateway import RiskGateway
from app.risk.limits import RiskLimits
from app.risk.tiers import TierPolicy
from app.slowpath.params import ParameterController, default_risk_params
from app.slowpath.regime import RegimeClassifier
from app.strategy.momentum import MomentumStrategy
from app.tca.engine import TcaEngine

_DEFAULT_START_TS_NS = 1_750_000_000 * NS_PER_SEC

# Builds the decision strategy given the bus and clock. Defaults to the
# SMA-crossover reference; pass a factory wrapping ModelStrategy (with a loaded
# InferenceService) to run the GBDT fast path. The SAME factory must be used
# for run and replay, or the determinism contract is void.
StrategyFactory = Callable[[EventBus, Clock], Any]


def _default_strategy_factory(bus: EventBus, clock: Clock) -> Any:
    return MomentumStrategy(bus, clock)


class PaperSession:
    """One-shot paper session: construct, run() once, inspect.

    Public attributes after construction: symbols, clock, bus, strategy,
    gateway, broker, tracker, journal_path; ``summary`` is set by run().
    """

    def __init__(
        self,
        symbols: list[str],
        n_bars: int = 500,
        seed: int = 42,
        journal_path: Path | None = None,
        limits: RiskLimits | None = None,
        start_ts_ns: int = _DEFAULT_START_TS_NS,
        interval_s: int = 60,
        strategy_factory: Optional[StrategyFactory] = None,
        tier_policy: Optional[TierPolicy] = None,
        auto_release_max_tier: int = 1,
        approver_max_tier: int = 3,
        enable_tca: bool = True,
        enable_slow_path: bool = False,
        bars: Optional[list[Bar]] = None,
    ) -> None:
        """``bars`` overrides the synthetic generator: pass real history (e.g.
        from marketdata.bridge.load_store_bars) and the identical deterministic
        pipeline -- journal, risk gateway, TCA, replay -- runs on it. When
        given, ``n_bars``/``seed`` are ignored and the clock starts at the
        earliest bar."""
        if bars is None:
            bars = []
            for symbol in symbols:
                bars.extend(
                    generate_bars(
                        symbol, n_bars, start_ts_ns, interval_s=interval_s, seed=seed
                    )
                )
        else:
            bars = list(bars)
            start_ts_ns = min((b.ts_open for b in bars), default=start_ts_ns)
        self._wire(
            list(symbols), bars, journal_path, limits, start_ts_ns, strategy_factory,
            tier_policy, auto_release_max_tier, approver_max_tier, enable_tca,
            enable_slow_path,
        )

    def _wire(
        self,
        symbols: list[str],
        bars: list[Bar],
        journal_path: Path | None,
        limits: RiskLimits | None,
        clock_start_ns: int,
        strategy_factory: Optional[StrategyFactory] = None,
        tier_policy: Optional[TierPolicy] = None,
        auto_release_max_tier: int = 1,
        approver_max_tier: int = 3,
        enable_tca: bool = True,
        enable_slow_path: bool = False,
    ) -> None:
        """Build clock, bus and components around a fixed bar list.

        Construction order fixes subscription order, and therefore the
        dispatch order of same-event subscribers -- part of the
        determinism contract, do not reorder. run() and replay must wire
        with identical config or determinism is void.
        """
        self.symbols = symbols
        self.journal_path = Path(journal_path) if journal_path is not None else None
        self.clock = SimClock(clock_start_ns)
        self._journal = (
            JournalWriter(self.journal_path) if self.journal_path is not None else None
        )
        self.bus = MemoryBus(self.clock, journal=self._journal)
        factory = strategy_factory or _default_strategy_factory
        self.strategy = factory(self.bus, self.clock)
        self.gateway = RiskGateway(
            self.bus,
            self.clock,
            limits if limits is not None else RiskLimits(),
            policy=tier_policy,
            auto_release_max_tier=auto_release_max_tier,
        )
        # Stands in for the human/dashboard so Tier 2/3 intents still release in
        # the headless harness (deterministic).
        self.approver = AutoApprover(self.bus, self.clock, max_tier=approver_max_tier)
        self.broker = PaperBroker(self.bus, self.clock)
        self.tracker = PositionTracker(self.bus)
        self.tca = TcaEngine(self.bus) if enable_tca else None
        # Slow path (opt-in): bounded parameter control + regime classifier.
        # The gateway already subscribes CTL_PARAMS, so tightenings constrain it.
        self.param_controller: ParameterController | None = None
        self.regime: RegimeClassifier | None = None
        if enable_slow_path:
            active = limits if limits is not None else RiskLimits()
            self.param_controller = ParameterController(
                self.bus,
                self.clock,
                default_risk_params(
                    active.max_position_qty, active.max_gross_exposure,
                    active.max_order_notional,
                ),
            )
            self.regime = RegimeClassifier(
                self.bus, self.clock,
                baseline_gross=active.max_gross_exposure,
                baseline_position_qty=active.max_position_qty,
            )
        self._source = ReplaySource(self.bus, self.clock, bars)
        self.summary: dict[str, Any] | None = None

    def run(self) -> dict[str, Any]:
        """Replay all bars through the pipeline; close the journal; build
        and return the summary. May only be called once per session."""
        if self.summary is not None:
            raise RuntimeError("PaperSession.run() may only be called once")
        try:
            self._source.run()
        finally:
            if self._journal is not None:
                self._journal.close()
        self.summary = self._build_summary()
        return self.summary

    def _build_summary(self) -> dict[str, Any]:
        bars = intents = verdicts = approved = rejected = orders = fills = 0
        approval_requests = 0
        tier_counts = {1: 0, 2: 0, 3: 0}
        last_prices: dict[str, float] = {}
        for event in self.bus.events:
            if event.stream == Streams.MD_BARS:
                bars += 1
                bar = Bar.model_validate(event.payload)
                last_prices[bar.symbol] = bar.close
            elif event.stream == Streams.SIGNAL_INTENTS:
                intents += 1
            elif event.stream == Streams.RISK_VERDICTS:
                verdicts += 1
                verdict = RiskVerdict.model_validate(event.payload)
                if verdict.approved:
                    approved += 1
                    tier_counts[verdict.tier] = tier_counts.get(verdict.tier, 0) + 1
                else:
                    rejected += 1
            elif event.stream == Streams.CTL_APPROVAL_REQUESTS:
                approval_requests += 1
            elif event.stream == Streams.EXEC_ORDERS:
                orders += 1
            elif event.stream == Streams.EXEC_FILLS:
                fills += 1
        summary = {
            "bars": bars,
            "intents": intents,
            "verdicts": verdicts,
            "approved": approved,
            "rejected": rejected,
            "tier_counts": tier_counts,
            "approval_requests": approval_requests,
            "orders": orders,
            "fills": fills,
            "realized_pnl_total": self.tracker.total_realized_pnl(),
            "final_positions": {
                symbol: self.tracker.position(symbol)[0] for symbol in self.symbols
            },
            "journal_path": (
                str(self.journal_path) if self.journal_path is not None else None
            ),
            "last_prices": last_prices,
        }
        if self.tca is not None:
            summary["tca"] = self.tca.summary()
        return summary

    @classmethod
    def replay_from_journal(
        cls,
        journal_path: Path,
        symbols: list[str] | None = None,
        limits: RiskLimits | None = None,
        strategy_factory: Optional[StrategyFactory] = None,
        tier_policy: Optional[TierPolicy] = None,
        auto_release_max_tier: int = 1,
        approver_max_tier: int = 3,
        enable_tca: bool = True,
        enable_slow_path: bool = False,
    ) -> "PaperSession":
        """Replay the journaled md.bars through a FRESH bus and fresh
        components (chain-verified read; no journal on the replay bus)
        and run to completion. Returns the run session: ``.summary`` has
        the same shape as run()'s, ``.bus.events`` the replayed stream.

        Pass the SAME tier/approver/factory/slow-path config the original run
        used, or the replay will not reproduce it."""
        bars = [
            payload
            for _, payload in JournalReader(Path(journal_path)).payloads(
                Streams.MD_BARS
            )
            if isinstance(payload, Bar)
        ]
        if symbols is None:
            symbols = sorted({bar.symbol for bar in bars})
        start_ns = min((bar.ts_open for bar in bars), default=0)
        session = object.__new__(cls)
        session._wire(
            list(symbols), bars, None, limits, start_ns, strategy_factory,
            tier_policy, auto_release_max_tier, approver_max_tier, enable_tca,
            enable_slow_path,
        )
        session.run()
        return session
