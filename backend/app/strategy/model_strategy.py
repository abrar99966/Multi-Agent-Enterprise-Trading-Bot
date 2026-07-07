"""GBDT-driven fast-path strategy (Phase 1).

Replaces the SMA-crossover reference (and the banned LLM-in-the-loop path)
with a deterministic model: each bar updates the feature fabric, the inference
service scores P(up over horizon), and a long-only state machine with
hysteresis emits intents -- BUY when prob >= enter_threshold while flat, SELL
(close) when prob <= exit_threshold while long.

Every intent carries the model_id and its top SHAP feature attributions, so a
decision is explainable from the journal alone. Deterministic: fabric and
inference are pure functions of the bar stream and the fixed artifact, so a
replay reproduces identical intents. Same optimistic flat/long bookkeeping
caveat as MomentumStrategy (state flips on emission; gateway/broker stay
authoritative).
"""
from __future__ import annotations

from typing import Dict

from app.bus.base import EventBus
from app.core.clock import Clock
from app.core.events import Bar, Event, OrderIntent, OrderType, Side, Streams
from app.engine.inference import InferenceService
from app.features.fabric import FeatureFabric


class ModelStrategy:
    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        inference: InferenceService,
        strategy_id: str = "gbdt-v1",
        qty: float = 100.0,
        top_k_attributions: int = 5,
    ) -> None:
        self._bus = bus
        self._clock = clock
        self._inference = inference
        self.strategy_id = strategy_id
        self._qty = float(qty)
        self._k = top_k_attributions
        self._fabric = FeatureFabric()
        self._long: set[str] = set()
        bus.subscribe(Streams.MD_BARS, self._on_bar)

    def is_long(self, symbol: str) -> bool:
        return symbol in self._long

    def _on_bar(self, event: Event) -> None:
        payload = event.decode()
        if not isinstance(payload, Bar):
            return
        feats = self._fabric.update(payload)
        if feats is None:  # warming up
            return
        result = self._inference.score(feats)
        prob = result.prob
        is_long = payload.symbol in self._long
        if not is_long and prob >= self._inference.enter_threshold:
            self._long.add(payload.symbol)
            self._emit(payload, Side.BUY, "model_entry", prob, result.contributions)
        elif is_long and prob <= self._inference.exit_threshold:
            self._long.discard(payload.symbol)
            self._emit(payload, Side.SELL, "model_exit", prob, result.contributions)

    def _emit(
        self,
        bar: Bar,
        side: Side,
        reason: str,
        prob: float,
        contributions: Dict[str, float],
    ) -> None:
        top = dict(
            sorted(contributions.items(), key=lambda kv: abs(kv[1]), reverse=True)[
                : self._k
            ]
        )
        top["prob"] = prob
        intent = OrderIntent(
            intent_id=f"{self.strategy_id}:{bar.symbol}:{bar.ts_open}",
            strategy_id=self.strategy_id,
            model_id=self._inference.model_id,
            symbol=bar.symbol,
            side=side,
            qty=self._qty,
            order_type=OrderType.MARKET,
            ts_signal=bar.ts_close,
            reason=reason,
            attributions=top,
        )
        self._bus.publish(Streams.SIGNAL_INTENTS, intent, ts_event=self._clock.now_ns())
