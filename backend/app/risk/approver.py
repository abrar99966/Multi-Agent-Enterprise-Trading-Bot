"""Approval routing for held (Tier 2/3) intents.

In production, an ApprovalRequest goes to the dashboard: Tier 2 auto-executes
on a timeout unless a human vetoes; Tier 3 needs an explicit human approval
(docs/TARGET_ARCHITECTURE.md section 8.4). For deterministic backtests and the
paper harness there is no human, so AutoApprover stands in: it approves every
request up to ``max_tier`` and rejects above it. Deterministic -- it reacts
synchronously to each request with no clock or randomness -- so sessions still
replay bit-identically.

The gateway's own ``auto_release_max_tier`` (default 1) is the real autonomy
ceiling; the approver only decides what happens to intents the gateway chose
NOT to release on its own.
"""
from __future__ import annotations

from app.bus.base import EventBus
from app.core.clock import Clock
from app.core.events import ApprovalDecision, ApprovalRequest, Event, Streams


class AutoApprover:
    def __init__(
        self,
        bus: EventBus,
        clock: Clock,
        max_tier: int = 3,
        approver_id: str = "auto-approver",
    ) -> None:
        self._bus = bus
        self._clock = clock
        self._max_tier = max_tier
        self._approver_id = approver_id
        self.approved = 0
        self.rejected = 0
        bus.subscribe(Streams.CTL_APPROVAL_REQUESTS, self._on_request)

    def _on_request(self, event: Event) -> None:
        req = ApprovalRequest.model_validate(event.payload)
        approve = req.tier <= self._max_tier
        if approve:
            self.approved += 1
        else:
            self.rejected += 1
        self._bus.publish(
            Streams.CTL_APPROVAL_DECISIONS,
            ApprovalDecision(
                intent_id=req.intent_id,
                approved=approve,
                approver=self._approver_id,
                ts=self._clock.now_ns(),
            ),
            ts_event=self._clock.now_ns(),
        )
