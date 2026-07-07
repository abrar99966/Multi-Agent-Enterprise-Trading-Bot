"""Smart Order Router (SOR) v1.

Phase 4 of the Institutional Target-State Architecture.

Routes orders to the optimal broker based on:
  1. Symbol region matching (IN → Indian brokers, US/GLOBAL → IBKR/Alpaca)
  2. Broker health score (connectivity, recent error rate, latency)
  3. Cost optimization (commission tiers, expected impact)
  4. Failover: if primary broker is degraded, route to backup
  5. Split routing: large orders can be split across brokers

This replaces the simple region-based picker in execution_router.py with
a scoring-based router that supports multi-broker failover.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


class BrokerHealth(str, Enum):
    GREEN = "GREEN"      # Normal operation
    YELLOW = "YELLOW"    # Degraded (elevated errors/latency)
    RED = "RED"          # Down or circuit-breaker tripped
    UNKNOWN = "UNKNOWN"  # No recent data


@dataclass
class BrokerMetrics:
    """Real-time broker health metrics for routing decisions."""
    slug: str
    name: str
    region: str

    # Health
    health: BrokerHealth = BrokerHealth.UNKNOWN
    last_success_ts: float = 0.0      # Unix timestamp of last successful operation
    last_error_ts: float = 0.0        # Unix timestamp of last error
    last_error_msg: str = ""

    # Sliding window stats (configurable period)
    orders_sent: int = 0
    orders_filled: int = 0
    orders_rejected: int = 0
    error_count: int = 0
    error_rate: float = 0.0           # errors / total in window (0-1)

    # Latency (milliseconds)
    avg_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0

    # Cost
    commission_per_order: float = 0.0  # Estimated fixed cost
    commission_bps: float = 0.0        # Variable cost in bps

    # Capabilities
    supports_market_data: bool = False
    supports_algo: bool = False
    is_live: bool = False
    is_connected: bool = False

    # Circuit breaker
    circuit_open_until: float = 0.0    # If > now, circuit is open

    @property
    def is_available(self) -> bool:
        """Is this broker available for routing?"""
        if not self.is_live or not self.is_connected:
            return False
        if self.health == BrokerHealth.RED:
            return False
        if self.circuit_open_until > time.time():
            return False
        return True


@dataclass
class RouteDecision:
    """Result of a routing decision."""
    primary_broker: str               # Selected broker slug
    backup_broker: Optional[str]      # Failover broker slug
    score: float                      # Primary broker score (0-100)
    reason: str                       # Human-readable explanation
    should_split: bool = False        # Whether to split across brokers
    split_plan: List[Dict[str, Any]] = field(default_factory=list)

    # For audit
    all_scores: Dict[str, float] = field(default_factory=dict)
    disqualified: Dict[str, str] = field(default_factory=dict)


# -- Scoring weights ---------------------------------------------------

@dataclass(frozen=True)
class SORWeights:
    """Tunable weights for the broker scoring function."""
    health: float = 40.0       # Weight for health/availability (0-100)
    latency: float = 20.0      # Weight for latency (lower is better)
    cost: float = 15.0         # Weight for transaction costs
    fill_rate: float = 15.0    # Weight for fill rate history
    recency: float = 10.0      # Weight for last successful interaction

    @property
    def total(self) -> float:
        return self.health + self.latency + self.cost + self.fill_rate + self.recency


# -- Circuit Breaker ---------------------------------------------------

@dataclass
class CircuitBreakerConfig:
    """Per-broker circuit breaker settings."""
    error_threshold: int = 5       # Errors before tripping
    window_seconds: float = 300.0  # Sliding window for error counting
    cooldown_seconds: float = 60.0 # Time to wait before retrying
    half_open_attempts: int = 1    # Probe attempts in half-open state


class SmartOrderRouter:
    """Multi-broker Smart Order Router with failover and health monitoring.

    Usage:
        sor = SmartOrderRouter()
        sor.register_broker("dhan", "Dhan", "IN", is_live=True, is_connected=True)
        sor.register_broker("ibkr", "IBKR", "GLOBAL", is_live=True, is_connected=True)

        decision = sor.route("RELIANCE", "IN")
        # → RouteDecision(primary_broker="dhan", backup_broker=None, ...)

        decision = sor.route("AAPL", "US")
        # → RouteDecision(primary_broker="ibkr", ...)
    """

    def __init__(
        self,
        weights: Optional[SORWeights] = None,
        cb_config: Optional[CircuitBreakerConfig] = None,
    ):
        self._weights = weights or SORWeights()
        self._cb_config = cb_config or CircuitBreakerConfig()
        self._brokers: Dict[str, BrokerMetrics] = {}

        # Error windows for circuit breaker
        self._error_windows: Dict[str, List[float]] = {}  # slug → [error_ts, ...]

    # -- Broker registration -----------------------------------------------

    def register_broker(
        self,
        slug: str,
        name: str,
        region: str,
        is_live: bool = False,
        is_connected: bool = False,
        supports_market_data: bool = False,
        commission_bps: float = 0.0,
    ) -> None:
        """Register or update a broker's metadata."""
        if slug in self._brokers:
            m = self._brokers[slug]
            m.is_live = is_live
            m.is_connected = is_connected
            m.supports_market_data = supports_market_data
            m.commission_bps = commission_bps
        else:
            self._brokers[slug] = BrokerMetrics(
                slug=slug,
                name=name,
                region=region,
                is_live=is_live,
                is_connected=is_connected,
                supports_market_data=supports_market_data,
                commission_bps=commission_bps,
            )

    def update_health(self, slug: str, health: BrokerHealth) -> None:
        if slug in self._brokers:
            self._brokers[slug].health = health

    # -- Event recording ---------------------------------------------------

    def record_success(self, slug: str, latency_ms: float = 0.0) -> None:
        """Record a successful broker interaction (fill, ack, etc.)."""
        m = self._brokers.get(slug)
        if m is None:
            return
        m.last_success_ts = time.time()
        m.orders_sent += 1
        m.orders_filled += 1

        # Exponential moving average for latency
        if m.avg_latency_ms == 0:
            m.avg_latency_ms = latency_ms
        else:
            m.avg_latency_ms = 0.9 * m.avg_latency_ms + 0.1 * latency_ms
        m.p99_latency_ms = max(m.p99_latency_ms * 0.99, latency_ms)

        self._update_health(slug)

    def record_error(self, slug: str, error_msg: str = "") -> None:
        """Record a broker error. May trip the circuit breaker."""
        m = self._brokers.get(slug)
        if m is None:
            return
        now = time.time()
        m.last_error_ts = now
        m.last_error_msg = error_msg[:200]
        m.error_count += 1
        m.orders_sent += 1

        # Track error window for circuit breaker
        window = self._error_windows.setdefault(slug, [])
        window.append(now)
        # Prune old entries
        cutoff = now - self._cb_config.window_seconds
        self._error_windows[slug] = [t for t in window if t > cutoff]

        # Check circuit breaker
        if len(self._error_windows[slug]) >= self._cb_config.error_threshold:
            m.circuit_open_until = now + self._cb_config.cooldown_seconds
            m.health = BrokerHealth.RED
            log.warning(
                "SOR: circuit breaker tripped for %s — %d errors in %.0fs window",
                slug, len(self._error_windows[slug]),
                self._cb_config.window_seconds,
            )

        self._update_health(slug)

    def record_rejection(self, slug: str) -> None:
        """Record a broker rejection (order rejected, not a system error)."""
        m = self._brokers.get(slug)
        if m is None:
            return
        m.orders_sent += 1
        m.orders_rejected += 1
        self._update_health(slug)

    def _update_health(self, slug: str) -> None:
        """Recompute health status from recent metrics."""
        m = self._brokers.get(slug)
        if m is None:
            return

        # Circuit breaker overrides
        if m.circuit_open_until > time.time():
            m.health = BrokerHealth.RED
            return

        # Error rate
        total = m.orders_sent
        if total > 0:
            m.error_rate = m.error_count / total
        else:
            m.error_rate = 0.0

        # Health classification
        if not m.is_connected:
            m.health = BrokerHealth.RED
        elif m.error_rate > 0.3:
            m.health = BrokerHealth.RED
        elif m.error_rate > 0.1 or m.avg_latency_ms > 5000:
            m.health = BrokerHealth.YELLOW
        elif m.last_success_ts > 0:
            m.health = BrokerHealth.GREEN
        else:
            m.health = BrokerHealth.UNKNOWN

    # -- Routing decision ---------------------------------------------------

    def route(
        self,
        symbol: str,
        target_region: str,
        qty: float = 0.0,
        prefer_broker: Optional[str] = None,
    ) -> RouteDecision:
        """Select the best broker for this order.

        Args:
            symbol: instrument symbol
            target_region: "IN", "US", or "GLOBAL"
            qty: order quantity (for split decision)
            prefer_broker: optional preference (if healthy, gets a bonus)

        Returns:
            RouteDecision with primary, backup, and audit trail.
        """
        candidates = self._eligible_brokers(target_region)
        if not candidates:
            return RouteDecision(
                primary_broker="",
                backup_broker=None,
                score=0.0,
                reason=f"No eligible brokers for region {target_region}",
                disqualified={
                    s: self._disqualify_reason(s, target_region)
                    for s in self._brokers
                },
            )

        # Score each candidate
        scores: Dict[str, float] = {}
        for slug in candidates:
            scores[slug] = self._score_broker(slug, prefer_broker)

        # Sort by score descending
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        primary = ranked[0][0]
        primary_score = ranked[0][1]
        backup = ranked[1][0] if len(ranked) > 1 else None

        reason = self._explain_choice(primary, primary_score, scores)

        # Check if we should split across brokers
        should_split = False
        split_plan: List[Dict[str, Any]] = []
        if qty > 0 and backup and len(ranked) >= 2:
            should_split, split_plan = self._check_split(
                primary, backup, qty, scores
            )

        return RouteDecision(
            primary_broker=primary,
            backup_broker=backup,
            score=primary_score,
            reason=reason,
            should_split=should_split,
            split_plan=split_plan,
            all_scores=scores,
            disqualified={
                s: self._disqualify_reason(s, target_region)
                for s in self._brokers
                if s not in candidates
            },
        )

    def _eligible_brokers(self, target_region: str) -> List[str]:
        """Return brokers eligible for this region."""
        eligible = []
        for slug, m in self._brokers.items():
            if not m.is_available:
                continue
            # Region matching
            if target_region == "GLOBAL":
                eligible.append(slug)
            elif m.region == target_region:
                eligible.append(slug)
            elif m.region == "GLOBAL":
                eligible.append(slug)  # Global brokers serve all regions
        return eligible

    def _score_broker(self, slug: str, prefer: Optional[str] = None) -> float:
        """Score a broker 0-100 for routing priority."""
        m = self._brokers[slug]
        w = self._weights
        score = 0.0

        # Health score (0 to w.health)
        health_scores = {
            BrokerHealth.GREEN: 1.0,
            BrokerHealth.YELLOW: 0.5,
            BrokerHealth.UNKNOWN: 0.3,
            BrokerHealth.RED: 0.0,
        }
        score += w.health * health_scores.get(m.health, 0.0)

        # Latency score (lower is better, 0 to w.latency)
        if m.avg_latency_ms <= 0:
            latency_score = 0.5  # Unknown
        elif m.avg_latency_ms < 100:
            latency_score = 1.0
        elif m.avg_latency_ms < 500:
            latency_score = 0.8
        elif m.avg_latency_ms < 2000:
            latency_score = 0.5
        else:
            latency_score = 0.2
        score += w.latency * latency_score

        # Cost score (lower is better, 0 to w.cost)
        if m.commission_bps <= 0:
            cost_score = 0.8
        elif m.commission_bps < 3:
            cost_score = 1.0
        elif m.commission_bps < 10:
            cost_score = 0.7
        else:
            cost_score = 0.4
        score += w.cost * cost_score

        # Fill rate score (0 to w.fill_rate)
        if m.orders_sent == 0:
            fill_score = 0.5  # Unknown
        else:
            fill_rate = m.orders_filled / m.orders_sent
            fill_score = fill_rate
        score += w.fill_rate * fill_score

        # Recency score (0 to w.recency)
        now = time.time()
        since_success = now - m.last_success_ts if m.last_success_ts > 0 else 3600
        if since_success < 60:
            recency_score = 1.0
        elif since_success < 300:
            recency_score = 0.8
        elif since_success < 3600:
            recency_score = 0.5
        else:
            recency_score = 0.2
        score += w.recency * recency_score

        # Preference bonus (up to 5 points)
        if prefer and slug == prefer:
            score += 5.0

        # Normalize to 0-100
        return round(min(100.0, score / w.total * 100), 1)

    def _check_split(
        self,
        primary: str,
        backup: str,
        qty: float,
        scores: Dict[str, float],
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """Decide whether to split the order across brokers.

        Split if: both brokers are healthy AND the order is large enough
        to benefit from reduced market impact.
        """
        m_primary = self._brokers[primary]
        m_backup = self._brokers[backup]

        # Only split if both are GREEN
        if m_primary.health != BrokerHealth.GREEN or m_backup.health != BrokerHealth.GREEN:
            return False, []

        # Only split if score gap is small (both are viable)
        score_gap = scores[primary] - scores[backup]
        if score_gap > 20:  # Primary is clearly better
            return False, []

        # Split 60/40 in favor of primary
        primary_qty = round(qty * 0.6)
        backup_qty = round(qty * 0.4)

        if primary_qty < 1 or backup_qty < 1:
            return False, []

        plan = [
            {"broker": primary, "qty": primary_qty, "pct": 60},
            {"broker": backup, "qty": backup_qty, "pct": 40},
        ]
        return True, plan

    def _explain_choice(
        self, chosen: str, score: float, all_scores: Dict[str, float]
    ) -> str:
        m = self._brokers[chosen]
        parts = [f"{m.name} selected (score={score:.1f})"]
        if m.health == BrokerHealth.GREEN:
            parts.append("health=GREEN")
        elif m.health == BrokerHealth.YELLOW:
            parts.append("health=YELLOW (degraded)")
        if m.avg_latency_ms > 0:
            parts.append(f"latency={m.avg_latency_ms:.0f}ms")
        return "; ".join(parts)

    def _disqualify_reason(self, slug: str, target_region: str) -> str:
        m = self._brokers.get(slug)
        if m is None:
            return "unknown broker"
        reasons = []
        if not m.is_live:
            reasons.append("not live")
        if not m.is_connected:
            reasons.append("not connected")
        if m.health == BrokerHealth.RED:
            reasons.append("health=RED")
        if m.circuit_open_until > time.time():
            reasons.append("circuit breaker open")
        if target_region != "GLOBAL" and m.region != target_region and m.region != "GLOBAL":
            reasons.append(f"region mismatch ({m.region} vs {target_region})")
        return "; ".join(reasons) if reasons else "eligible"

    # -- Status & introspection --------------------------------------------

    def broker_status(self) -> List[Dict[str, Any]]:
        """Return health status of all registered brokers."""
        return [
            {
                "slug": m.slug,
                "name": m.name,
                "region": m.region,
                "health": m.health.value,
                "is_available": m.is_available,
                "is_connected": m.is_connected,
                "error_rate": round(m.error_rate, 3),
                "avg_latency_ms": round(m.avg_latency_ms, 1),
                "orders_sent": m.orders_sent,
                "orders_filled": m.orders_filled,
                "last_success": m.last_success_ts,
                "last_error": m.last_error_msg,
                "circuit_breaker": m.circuit_open_until > time.time(),
            }
            for m in self._brokers.values()
        ]

    def failover_status(self) -> Dict[str, Any]:
        """Summary of failover readiness."""
        available_in = [
            m.slug for m in self._brokers.values()
            if m.is_available and m.region == "IN"
        ]
        available_global = [
            m.slug for m in self._brokers.values()
            if m.is_available and m.region in ("US", "GLOBAL")
        ]
        return {
            "india_brokers_available": len(available_in),
            "india_brokers": available_in,
            "global_brokers_available": len(available_global),
            "global_brokers": available_global,
            "total_brokers_registered": len(self._brokers),
            "total_available": sum(1 for m in self._brokers.values() if m.is_available),
            "any_circuit_open": any(
                m.circuit_open_until > time.time() for m in self._brokers.values()
            ),
        }
