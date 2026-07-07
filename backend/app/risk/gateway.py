"""Risk gateway v0 -- THE safety boundary.

Only this component may publish to Streams.EXEC_ORDERS. Every
OrderIntent is evaluated against the full check list -- never
short-circuited, because the complete list is the audit record -- and
answered with a RiskVerdict; approved intents are released as Orders.

Fail-closed philosophy: an exception inside any check produces a failed
'internal_error' check and a rejection. The intent handler never raises
and never approves on error. All time comes from the injected Clock
(event time), read once per intent, so live decisions replay exactly.

KNOWN LIMITATION (Phase 1 -- docs/PHASE0_REVIEW.md F1): the position and
exposure views update from FILLS only, not from approvals. Between
approving an intent and its fill landing, position_limit and
gross_exposure see pre-trade state, so a burst of intents can be
approved past the position limit before any fill arrives. Harmless under
the Phase 0 long-only single-unit strategy plus the per-minute rate cap,
but Phase 1 must reserve working (approved-not-filled) qty/notional and
release it on fill/cancel/reject/expiry.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable

from app.bus.base import EventBus
from app.core.clock import Clock
from app.core.events import (
    NS_PER_SEC,
    ApprovalDecision,
    ApprovalRequest,
    Bar,
    Event,
    Fill,
    KillSwitch,
    Order,
    OrderIntent,
    OrderStatus,
    OrderUpdate,
    RiskCheck,
    RiskVerdict,
    Side,
    Streams,
    Tick,
)
from app.risk.limits import RiskLimits
from app.risk.tiers import TierPolicy

_RATE_WINDOW_NS = 60 * NS_PER_SEC
_NS_PER_MS = 1_000_000
_PLATFORM_SCOPE = "*"
_QTY_EPS = 1e-9
_TERMINAL_STATUSES = frozenset(
    {
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }
)

CheckFn = Callable[[OrderIntent, int], RiskCheck]


class RiskGateway:
    """Subscribes to intents, fills, market data and kill switches;
    maintains its own position and last-price view (independent of the
    OMS tracker by design) and adjudicates every intent."""

    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        limits: RiskLimits,
        policy: TierPolicy | None = None,
        auto_release_max_tier: int = 1,
    ) -> None:
        self._bus = bus
        self._clock = clock
        self._limits = limits
        self._policy = policy or TierPolicy()
        self._auto_release_max_tier = auto_release_max_tier
        self._positions: dict[str, float] = {}  # symbol -> signed filled qty
        self._last_price: dict[str, float] = {}  # symbol -> last trade/close
        self._kill_levels: dict[str, int] = {}  # scope -> engaged level
        self._approvals: dict[str, deque[int]] = {}  # strategy -> approval ts_ns
        # F1: working (approved-but-unfilled) exposure reservation.
        self._working: dict[str, float] = {}  # symbol -> reserved signed qty
        self._reservations: dict[str, tuple[str, float]] = {}  # order_id -> (sym, remaining signed)
        # Tier 2/3 intents awaiting an approval decision before release.
        self._pending_approval: dict[str, OrderIntent] = {}  # intent_id -> intent
        # Slow-path limit overrides (risk.<field> -> effective value), applied
        # by the ParameterController. Empty => baseline limits in force.
        self._limit_overrides: dict[str, float] = {}
        bus.subscribe(Streams.SIGNAL_INTENTS, self._on_intent)
        bus.subscribe(Streams.EXEC_FILLS, self._on_fill)
        bus.subscribe(Streams.EXEC_ORDER_UPDATES, self._on_order_update)
        bus.subscribe(Streams.CTL_APPROVAL_DECISIONS, self._on_approval_decision)
        bus.subscribe(Streams.CTL_PARAMS, self._on_param_change)
        bus.subscribe(Streams.MD_BARS, self._on_bar)
        bus.subscribe(Streams.MD_TICKS, self._on_tick)
        bus.subscribe(Streams.CTL_KILL, self._on_kill)

    # -- public state accessors ---------------------------------------

    def position(self, symbol: str) -> float:
        return self._positions.get(symbol, 0.0)

    def working(self, symbol: str) -> float:
        """Reserved signed qty for approved orders not yet filled/terminal."""
        return self._working.get(symbol, 0.0)

    def _held(self, symbol: str) -> float:
        """Filled position plus working reservation -- the exposure a new
        intent must respect (F1)."""
        return self._positions.get(symbol, 0.0) + self._working.get(symbol, 0.0)

    def approvals_count(self, strategy_id: str) -> int:
        """Approvals for this strategy inside the trailing 60s window,
        measured in event time."""
        approvals = self._approvals.get(strategy_id)
        if not approvals:
            return 0
        self._prune(approvals, self._clock.now_ns())
        return len(approvals)

    # -- state-tracking subscriptions ----------------------------------

    def _on_bar(self, event: Event) -> None:
        bar = Bar.model_validate(event.payload)
        self._last_price[bar.symbol] = bar.close

    def _on_tick(self, event: Event) -> None:
        tick = Tick.model_validate(event.payload)
        self._last_price[tick.symbol] = tick.ltp

    def _on_fill(self, event: Event) -> None:
        fill = Fill.model_validate(event.payload)
        signed = fill.qty if fill.side is Side.BUY else -fill.qty
        self._positions[fill.symbol] = self._positions.get(fill.symbol, 0.0) + signed
        # The filled portion is now real position, not working reservation.
        self._reduce_reservation(fill.order_id, signed)

    def _on_order_update(self, event: Event) -> None:
        """Release any remaining reservation when an order reaches a terminal
        state (so a cancel/reject/expiry frees the working exposure)."""
        update = OrderUpdate.model_validate(event.payload)
        if update.status in _TERMINAL_STATUSES:
            res = self._reservations.pop(update.order_id, None)
            if res is not None:
                symbol, remaining = res
                self._release(symbol, remaining)

    def _reduce_reservation(self, order_id: str, filled_signed: float) -> None:
        res = self._reservations.get(order_id)
        if res is None:
            return
        symbol, remaining = res
        new_remaining = remaining - filled_signed
        self._release(symbol, filled_signed)
        if abs(new_remaining) < _QTY_EPS:
            del self._reservations[order_id]
        else:
            self._reservations[order_id] = (symbol, new_remaining)

    def _release(self, symbol: str, signed: float) -> None:
        remaining = self._working.get(symbol, 0.0) - signed
        if abs(remaining) < _QTY_EPS:
            self._working[symbol] = 0.0
        else:
            self._working[symbol] = remaining

    def _on_param_change(self, event: Event) -> None:
        """Apply a slow-path effective-limit override for risk.<field>. The
        controller has already enforced bounds/direction/TTL; the gateway just
        consumes the resulting effective value."""
        from app.core.events import ParameterChange

        change = ParameterChange.model_validate(event.payload)
        if change.parameter.startswith("risk."):
            field = change.parameter[len("risk.") :]
            if hasattr(self._limits, field):
                self._limit_overrides[field] = change.new_value

    def _effective_limit(self, field: str) -> float:
        """Slow-path override if present, else the baseline limit."""
        if field in self._limit_overrides:
            return self._limit_overrides[field]
        return getattr(self._limits, field)

    def _on_kill(self, event: Event) -> None:
        """engaged=True raises the level held at that scope; engaged=False
        at the same-or-higher level clears that scope."""
        switch = KillSwitch.model_validate(event.payload)
        current = self._kill_levels.get(switch.scope, 0)
        if switch.engaged:
            if switch.level > current:
                self._kill_levels[switch.scope] = switch.level
        elif current and switch.level >= current:
            del self._kill_levels[switch.scope]

    # -- intent adjudication -------------------------------------------

    def _on_intent(self, event: Event) -> None:
        now = self._clock.now_ns()
        try:
            intent = OrderIntent.model_validate(event.payload)
        except Exception as exc:  # malformed intent: reject, never raise
            bad = RiskCheck(
                name="internal_error",
                passed=False,
                detail=f"intent decode failed: {type(exc).__name__}: {exc}",
            )
            self._publish_verdict(str(event.payload.get("intent_id") or ""), False, 3, [bad], now)
            return

        check_fns: tuple[CheckFn, ...] = (
            self._check_kill_switch,
            self._check_market_data,
            self._check_signal_age,
            self._check_order_qty,
            self._check_order_notional,
            self._check_price_collar,
            self._check_position_limit,
            self._check_gross_exposure,
            self._check_rate_limit,
        )
        checks = [self._run_check(fn, intent, now) for fn in check_fns]
        approved = all(check.passed for check in checks)
        tier = 3
        reasons: list[str] = []
        if approved:
            # Record before publishing so cascading intents see this approval.
            self._approvals.setdefault(intent.strategy_id, deque()).append(now)
            tier, reasons = self._classify_tier(intent)
        self._publish_verdict(intent.intent_id, approved, tier, checks, now)
        if not approved:
            return
        if tier <= self._auto_release_max_tier:
            self._release_order(intent, now)
        else:
            # Hold the order until an approval decision arrives. No reservation
            # is taken until release, so a held intent ties up no exposure.
            self._pending_approval[intent.intent_id] = intent
            self._bus.publish(
                Streams.CTL_APPROVAL_REQUESTS,
                ApprovalRequest(
                    intent_id=intent.intent_id,
                    strategy_id=intent.strategy_id,
                    symbol=intent.symbol,
                    side=intent.side,
                    qty=intent.qty,
                    tier=tier,
                    reasons=reasons,
                    ts=now,
                ),
                ts_event=now,
            )

    def _on_approval_decision(self, event: Event) -> None:
        """Release (or drop) an intent that was held for approval."""
        decision = ApprovalDecision.model_validate(event.payload)
        intent = self._pending_approval.pop(decision.intent_id, None)
        if intent is not None and decision.approved:
            self._release_order(intent, self._clock.now_ns())

    def _release_order(self, intent: OrderIntent, now: int) -> None:
        """Reserve working exposure and publish the order. The sole path to
        Streams.EXEC_ORDERS."""
        order = Order(
            order_id=f"ord-{intent.intent_id}",
            intent_id=intent.intent_id,
            strategy_id=intent.strategy_id,
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.qty,
            order_type=intent.order_type,
            limit_price=intent.limit_price,
        )
        signed = self._signed_qty(intent)
        self._reservations[order.order_id] = (intent.symbol, signed)
        self._working[intent.symbol] = self._working.get(intent.symbol, 0.0) + signed
        self._bus.publish(Streams.EXEC_ORDERS, order, ts_event=now)

    def _classify_tier(self, intent: OrderIntent) -> tuple[int, list[str]]:
        ref_price = self._ref_price(intent)
        if ref_price is None:  # approved => priced; defensive fallback
            return 3, ["no_reference_price"]
        proj_pos = self._held(intent.symbol) + self._signed_qty(intent)
        proj_gross = self._projected_gross(intent)
        return self._policy.classify(
            intent, ref_price, self._limits, proj_pos, proj_gross
        )

    def _ref_price(self, intent: OrderIntent) -> float | None:
        if intent.limit_price is not None:
            return intent.limit_price
        return self._last_price.get(intent.symbol)

    def _projected_gross(self, intent: OrderIntent) -> float:
        """Gross exposure (filled + working + this order), priced. Sorted
        iteration keeps it replay-deterministic (Phase 0 finding F7)."""
        symbols = sorted(set(self._positions) | set(self._working) | {intent.symbol})
        projected = {sym: self._held(sym) for sym in symbols}
        projected[intent.symbol] = projected.get(intent.symbol, 0.0) + self._signed_qty(intent)
        gross = 0.0
        for sym, qty in projected.items():
            if qty == 0.0:
                continue
            price = self._last_price.get(sym)
            if price is not None:
                gross += abs(qty * price)
        return gross

    def _publish_verdict(
        self, intent_id: str, approved: bool, tier: int, checks: list[RiskCheck], now: int
    ) -> None:
        reject_reason: str | None = None
        if not approved:
            first = next(check for check in checks if not check.passed)
            reject_reason = f"{first.name}: {first.detail}" if first.detail else first.name
        verdict = RiskVerdict(
            intent_id=intent_id,
            approved=approved,
            tier=tier,
            checks=checks,
            reject_reason=reject_reason,
        )
        self._bus.publish(Streams.RISK_VERDICTS, verdict, ts_event=now)

    @staticmethod
    def _run_check(fn: CheckFn, intent: OrderIntent, now: int) -> RiskCheck:
        try:
            return fn(intent, now)
        except Exception as exc:  # fail closed, never raise past the handler
            return RiskCheck(
                name="internal_error",
                passed=False,
                detail=f"{getattr(fn, '__name__', 'check')}: {type(exc).__name__}: {exc}",
            )

    # -- individual checks (all always run; order is the audit order) ---

    def _check_kill_switch(self, intent: OrderIntent, now: int) -> RiskCheck:
        for scope, level in self._kill_levels.items():
            if scope == _PLATFORM_SCOPE or scope == intent.strategy_id:
                return RiskCheck(
                    name="kill_switch",
                    passed=False,
                    detail=f"engaged level {level} scope {scope!r}",
                )
        return RiskCheck(name="kill_switch", passed=True)

    def _check_market_data(self, intent: OrderIntent, now: int) -> RiskCheck:
        last = self._last_price.get(intent.symbol)
        if last is None:
            return RiskCheck(name="market_data", passed=False, detail="no market data")
        return RiskCheck(name="market_data", passed=True, detail=f"last={last}")

    def _check_signal_age(self, intent: OrderIntent, now: int) -> RiskCheck:
        age_ns = now - intent.ts_signal
        limit_ns = self._limits.max_signal_age_ms * _NS_PER_MS
        return RiskCheck(
            name="signal_age",
            passed=age_ns <= limit_ns,
            detail=f"age_ms={age_ns / _NS_PER_MS} max_ms={self._limits.max_signal_age_ms}",
        )

    def _check_order_qty(self, intent: OrderIntent, now: int) -> RiskCheck:
        max_qty = self._effective_limit("max_order_qty")
        return RiskCheck(
            name="order_qty",
            passed=intent.qty <= max_qty,
            detail=f"qty={intent.qty} max={max_qty}",
        )

    def _check_order_notional(self, intent: OrderIntent, now: int) -> RiskCheck:
        if intent.limit_price is not None:
            ref_price = intent.limit_price
        else:
            ref_price = self._last_price.get(intent.symbol)
        if ref_price is None:
            return RiskCheck(name="order_notional", passed=False, detail="no reference price")
        notional = intent.qty * ref_price
        max_notional = self._effective_limit("max_order_notional")
        return RiskCheck(
            name="order_notional",
            passed=notional <= max_notional,
            detail=f"notional={notional} max={max_notional}",
        )

    def _check_price_collar(self, intent: OrderIntent, now: int) -> RiskCheck:
        if intent.limit_price is None:
            return RiskCheck(name="price_collar", passed=True, detail="market order")
        last = self._last_price.get(intent.symbol)
        if last is None or last <= 0.0:
            return RiskCheck(name="price_collar", passed=False, detail="no market data")
        deviation_pct = abs(intent.limit_price - last) / last * 100.0
        return RiskCheck(
            name="price_collar",
            passed=deviation_pct <= self._limits.price_collar_pct,
            detail=f"deviation_pct={deviation_pct} max_pct={self._limits.price_collar_pct}",
        )

    def _check_position_limit(self, intent: OrderIntent, now: int) -> RiskCheck:
        """Filled position + working reservation + this order (F1)."""
        projected = self._held(intent.symbol) + self._signed_qty(intent)
        max_pos = self._effective_limit("max_position_qty")
        return RiskCheck(
            name="position_limit",
            passed=abs(projected) <= max_pos,
            detail=f"projected={projected} max={max_pos}",
        )

    def _check_gross_exposure(self, intent: OrderIntent, now: int) -> RiskCheck:
        """Gross exposure (filled + working) with this order applied. A held
        symbol without a known price cannot be valued -> fail closed."""
        # Sorted, not set-iteration: a deterministic summation order keeps the
        # gross value bit-identical on replay (Phase 0 finding F7).
        symbols = sorted(set(self._positions) | set(self._working) | {intent.symbol})
        projected = {sym: self._held(sym) for sym in symbols}
        projected[intent.symbol] = projected.get(intent.symbol, 0.0) + self._signed_qty(intent)
        gross = 0.0
        for symbol, qty in projected.items():
            if qty == 0.0:
                continue
            price = self._last_price.get(symbol)
            if price is None:
                return RiskCheck(
                    name="gross_exposure",
                    passed=False,
                    detail=f"no price for held symbol {symbol!r}",
                )
            gross += abs(qty * price)
        max_gross = self._effective_limit("max_gross_exposure")
        return RiskCheck(
            name="gross_exposure",
            passed=gross <= max_gross,
            detail=f"gross={gross} max={max_gross}",
        )

    def _check_rate_limit(self, intent: OrderIntent, now: int) -> RiskCheck:
        """Approvals for this strategy with ts in (now - 60s, now], event
        time only. The current intent is not yet counted."""
        approvals = self._approvals.get(intent.strategy_id)
        count = 0
        if approvals:
            self._prune(approvals, now)
            count = len(approvals)
        return RiskCheck(
            name="rate_limit",
            passed=count < self._limits.max_orders_per_min_per_strategy,
            detail=f"approvals_60s={count} max={self._limits.max_orders_per_min_per_strategy}",
        )

    @staticmethod
    def _signed_qty(intent: OrderIntent) -> float:
        return intent.qty if intent.side is Side.BUY else -intent.qty

    @staticmethod
    def _prune(approvals: deque[int], now: int) -> None:
        cutoff = now - _RATE_WINDOW_NS
        while approvals and approvals[0] <= cutoff:
            approvals.popleft()
