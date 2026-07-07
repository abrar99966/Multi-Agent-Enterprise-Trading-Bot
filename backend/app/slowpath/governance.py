"""Agent lifecycle governance — Paperclip-inspired orchestration.

Adapted from Paperclip's agent control plane patterns for managing
slow-path analyst agents. Provides lifecycle management, resource
tracking, and governance controls without adopting their Node.js stack.

Key capabilities:
  - Agent lifecycle: register / pause / resume / terminate
  - Resource tracking: LLM token spend, invocation count, error rate
  - Health monitoring: automatic pause on error threshold breach
  - Rate limiting: per-agent invocation caps per time window

This governs SLOW PATH agents only. The deterministic fast path
(GBDT scoring, risk gateway) is unaffected by agent state changes.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

log = logging.getLogger(__name__)


class AgentStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    TERMINATED = "terminated"
    ERROR = "error"


@dataclass
class AgentMetrics:
    """Per-agent resource tracking."""
    invocations: int = 0
    errors: int = 0
    last_invocation: float = 0.0
    last_error: float = 0.0
    total_latency_ms: float = 0.0
    estimated_token_spend: int = 0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / max(self.invocations, 1)

    @property
    def error_rate(self) -> float:
        return self.errors / max(self.invocations, 1)

    def to_dict(self) -> dict:
        return {
            "invocations": self.invocations,
            "errors": self.errors,
            "error_rate": round(self.error_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "estimated_token_spend": self.estimated_token_spend,
            "last_invocation": self.last_invocation,
            "last_error": self.last_error,
        }


@dataclass
class AgentConfig:
    """Governance configuration for a registered agent."""
    max_errors_before_pause: int = 5
    max_invocations_per_hour: int = 60
    max_token_budget: int = 100_000  # lifetime token cap (0 = unlimited)
    cooldown_after_error_s: float = 30.0


@dataclass
class ManagedAgent:
    """A slow-path agent under governance control."""
    agent_id: str
    agent_type: str  # e.g. "fundamentals", "sentiment", "macro"
    status: AgentStatus = AgentStatus.ACTIVE
    config: AgentConfig = field(default_factory=AgentConfig)
    metrics: AgentMetrics = field(default_factory=AgentMetrics)
    registered_at: float = field(default_factory=time.monotonic)
    _invocation_timestamps: List[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "status": self.status.value,
            "metrics": self.metrics.to_dict(),
            "config": {
                "max_errors_before_pause": self.config.max_errors_before_pause,
                "max_invocations_per_hour": self.config.max_invocations_per_hour,
                "max_token_budget": self.config.max_token_budget,
            },
        }


class AgentGovernor:
    """Manages lifecycle and resources for slow-path agents.

    Usage::
        governor = AgentGovernor()
        governor.register("fundamentals-1", "fundamentals")
        if governor.can_invoke("fundamentals-1"):
            result = analyst.assess(headline, symbols)
            governor.record_invocation("fundamentals-1", latency_ms=350, tokens=500)
    """

    def __init__(self, on_status_change: Optional[Callable] = None):
        self._agents: Dict[str, ManagedAgent] = {}
        self._on_status_change = on_status_change

    def register(self, agent_id: str, agent_type: str,
                 config: Optional[AgentConfig] = None) -> ManagedAgent:
        if agent_id in self._agents:
            log.warning("Agent %s already registered, skipping", agent_id)
            return self._agents[agent_id]
        agent = ManagedAgent(
            agent_id=agent_id,
            agent_type=agent_type,
            config=config or AgentConfig(),
        )
        self._agents[agent_id] = agent
        log.info("Registered slow-path agent: %s (%s)", agent_id, agent_type)
        return agent

    def pause(self, agent_id: str, reason: str = "manual") -> bool:
        agent = self._agents.get(agent_id)
        if agent is None or agent.status == AgentStatus.TERMINATED:
            return False
        old = agent.status
        agent.status = AgentStatus.PAUSED
        log.info("Paused agent %s (was %s): %s", agent_id, old.value, reason)
        self._notify(agent_id, old, AgentStatus.PAUSED)
        return True

    def resume(self, agent_id: str) -> bool:
        agent = self._agents.get(agent_id)
        if agent is None or agent.status == AgentStatus.TERMINATED:
            return False
        old = agent.status
        agent.status = AgentStatus.ACTIVE
        log.info("Resumed agent %s", agent_id)
        self._notify(agent_id, old, AgentStatus.ACTIVE)
        return True

    def terminate(self, agent_id: str, reason: str = "manual") -> bool:
        agent = self._agents.get(agent_id)
        if agent is None:
            return False
        old = agent.status
        agent.status = AgentStatus.TERMINATED
        log.info("Terminated agent %s: %s", agent_id, reason)
        self._notify(agent_id, old, AgentStatus.TERMINATED)
        return True

    def can_invoke(self, agent_id: str) -> bool:
        """Check if an agent is allowed to be invoked right now."""
        agent = self._agents.get(agent_id)
        if agent is None or agent.status != AgentStatus.ACTIVE:
            return False
        now = time.monotonic()
        cfg = agent.config

        # Error cooldown
        if (agent.metrics.last_error > 0 and
                now - agent.metrics.last_error < cfg.cooldown_after_error_s):
            return False

        # Rate limit (invocations per hour)
        cutoff = now - 3600
        agent._invocation_timestamps = [
            t for t in agent._invocation_timestamps if t > cutoff
        ]
        if len(agent._invocation_timestamps) >= cfg.max_invocations_per_hour:
            return False

        # Token budget
        if (cfg.max_token_budget > 0 and
                agent.metrics.estimated_token_spend >= cfg.max_token_budget):
            self.pause(agent_id, reason="token budget exhausted")
            return False

        return True

    def record_invocation(self, agent_id: str, latency_ms: float = 0,
                          tokens: int = 0, error: bool = False) -> None:
        agent = self._agents.get(agent_id)
        if agent is None:
            return
        now = time.monotonic()
        agent.metrics.invocations += 1
        agent.metrics.last_invocation = now
        agent.metrics.total_latency_ms += latency_ms
        agent.metrics.estimated_token_spend += tokens
        agent._invocation_timestamps.append(now)

        if error:
            agent.metrics.errors += 1
            agent.metrics.last_error = now
            if agent.metrics.errors >= agent.config.max_errors_before_pause:
                self.pause(agent_id, reason=f"error threshold ({agent.config.max_errors_before_pause})")

    def get_agent(self, agent_id: str) -> Optional[ManagedAgent]:
        return self._agents.get(agent_id)

    def list_agents(self, status: Optional[AgentStatus] = None) -> List[ManagedAgent]:
        agents = list(self._agents.values())
        if status is not None:
            agents = [a for a in agents if a.status == status]
        return agents

    def dashboard_summary(self) -> Dict:
        """Summary for the governance dashboard."""
        agents = list(self._agents.values())
        return {
            "total_agents": len(agents),
            "active": sum(1 for a in agents if a.status == AgentStatus.ACTIVE),
            "paused": sum(1 for a in agents if a.status == AgentStatus.PAUSED),
            "terminated": sum(1 for a in agents if a.status == AgentStatus.TERMINATED),
            "total_invocations": sum(a.metrics.invocations for a in agents),
            "total_errors": sum(a.metrics.errors for a in agents),
            "total_token_spend": sum(a.metrics.estimated_token_spend for a in agents),
            "agents": [a.to_dict() for a in agents],
        }

    def reset_metrics(self, agent_id: str) -> bool:
        agent = self._agents.get(agent_id)
        if agent is None:
            return False
        agent.metrics = AgentMetrics()
        agent._invocation_timestamps.clear()
        return True

    def _notify(self, agent_id: str, old: AgentStatus, new: AgentStatus):
        if self._on_status_change:
            try:
                self._on_status_change(agent_id, old.value, new.value)
            except Exception:
                pass


# Module-level singleton
agent_governor = AgentGovernor()
