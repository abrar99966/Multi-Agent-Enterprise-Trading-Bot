"""Offline-RL execution agent — shadow-mode algo selector.

Learns from historical execution data (TCA fills) to recommend optimal
algo selection and urgency for future orders. Operates in *shadow mode*:
recommendations are logged and compared against actual execution, but
never control the live path.

Approach: Conservative Q-Learning (CQL) style — a tabular Q-table
discretised over (market-state, action) with a pessimism penalty on
unseen state–action pairs. This is intentionally simple: the Phase 5
spec calls for *research* in shadow, not production deployment. The
table is small enough to train in-process without GPU.

State space (discretised):
    - spread_bucket:      tight / normal / wide
    - volatility_bucket:  low / medium / high
    - volume_bucket:      low / normal / high
    - urgency_bucket:     low / medium / high
    - size_bucket:        small / medium / large (as % of ADV)

Action space:
    - algo: IS / VWAP / POV / ADAPTIVE
    - urgency: passive / normal / aggressive

Reward: -1 × realised implementation shortfall (bps). Lower IS = better.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# State & action discretisation
# ---------------------------------------------------------------------------

class SpreadBucket(str, Enum):
    TIGHT  = "tight"
    NORMAL = "normal"
    WIDE   = "wide"


class VolBucket(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"


class VolumeBucket(str, Enum):
    LOW    = "low"
    NORMAL = "normal"
    HIGH   = "high"


class UrgencyBucket(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"


class SizeBucket(str, Enum):
    SMALL  = "small"
    MEDIUM = "medium"
    LARGE  = "large"


class AlgoAction(str, Enum):
    IS       = "IS"
    VWAP     = "VWAP"
    POV      = "POV"
    ADAPTIVE = "ADAPTIVE"


class UrgencyAction(str, Enum):
    PASSIVE    = "passive"
    NORMAL     = "normal"
    AGGRESSIVE = "aggressive"


@dataclass(frozen=True)
class MarketState:
    """Discretised market microstructure state."""
    spread: SpreadBucket
    volatility: VolBucket
    volume: VolumeBucket
    urgency: UrgencyBucket
    size: SizeBucket

    def key(self) -> str:
        return f"{self.spread.value}|{self.volatility.value}|{self.volume.value}|{self.urgency.value}|{self.size.value}"


@dataclass(frozen=True)
class ExecAction:
    """Discretised execution action."""
    algo: AlgoAction
    urgency: UrgencyAction

    def key(self) -> str:
        return f"{self.algo.value}|{self.urgency.value}"


# All possible actions
ALL_ACTIONS: List[ExecAction] = [
    ExecAction(algo=a, urgency=u)
    for a in AlgoAction
    for u in UrgencyAction
]


# ---------------------------------------------------------------------------
# Feature discretisation helpers
# ---------------------------------------------------------------------------

def discretise_spread(spread_bps: float) -> SpreadBucket:
    if spread_bps < 3.0:
        return SpreadBucket.TIGHT
    elif spread_bps < 10.0:
        return SpreadBucket.NORMAL
    return SpreadBucket.WIDE


def discretise_volatility(daily_vol: float) -> VolBucket:
    if daily_vol < 0.01:
        return VolBucket.LOW
    elif daily_vol < 0.025:
        return VolBucket.MEDIUM
    return VolBucket.HIGH


def discretise_volume(volume_ratio: float) -> VolumeBucket:
    """volume_ratio = current volume / ADV."""
    if volume_ratio < 0.5:
        return VolumeBucket.LOW
    elif volume_ratio < 1.5:
        return VolumeBucket.NORMAL
    return VolumeBucket.HIGH


def discretise_urgency(urgency: float) -> UrgencyBucket:
    if urgency < 0.3:
        return UrgencyBucket.LOW
    elif urgency < 0.7:
        return UrgencyBucket.MEDIUM
    return UrgencyBucket.HIGH


def discretise_size(adv_pct: float) -> SizeBucket:
    """adv_pct = order_qty / avg_daily_volume × 100."""
    if adv_pct < 0.5:
        return SizeBucket.SMALL
    elif adv_pct < 5.0:
        return SizeBucket.MEDIUM
    return SizeBucket.LARGE


def make_state(
    spread_bps: float,
    daily_vol: float,
    volume_ratio: float,
    urgency: float,
    adv_pct: float,
) -> MarketState:
    return MarketState(
        spread=discretise_spread(spread_bps),
        volatility=discretise_volatility(daily_vol),
        volume=discretise_volume(volume_ratio),
        urgency=discretise_urgency(urgency),
        size=discretise_size(adv_pct),
    )


# ---------------------------------------------------------------------------
# Experience buffer
# ---------------------------------------------------------------------------

@dataclass
class Experience:
    """One transition from execution history."""
    state: MarketState
    action: ExecAction
    reward: float        # -IS (bps) — higher is better
    ts: float = 0.0      # wall-clock epoch
    symbol: str = ""
    fill_id: str = ""
    actual_is_bps: float = 0.0  # raw IS for logging


# ---------------------------------------------------------------------------
# CQL Agent
# ---------------------------------------------------------------------------

@dataclass
class CQLConfig:
    """Conservative Q-Learning hyperparameters."""
    learning_rate: float = 0.1
    discount: float = 0.0          # single-step, no future discounting
    cql_alpha: float = 1.0         # pessimism penalty coefficient
    min_visits_for_recommend: int = 5  # don't recommend until enough data
    initial_q: float = 0.0


class OfflineRLAgent:
    """Tabular CQL agent for execution algo selection.

    Shadow-mode only: call ``recommend()`` to get the agent's preferred
    action, but the live system uses the impact model's recommendation.
    Differences are logged for evaluation.
    """

    def __init__(self, config: CQLConfig | None = None) -> None:
        self._config = config or CQLConfig()
        self._q: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {a.key(): self._config.initial_q for a in ALL_ACTIONS}
        )
        self._visits: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {a.key(): 0 for a in ALL_ACTIONS}
        )
        self._experiences: List[Experience] = []
        self._shadow_log: List[Dict[str, Any]] = []
        self._total_updates: int = 0

    # -- Training -----------------------------------------------------------

    def ingest(self, exp: Experience) -> None:
        """Add an experience to the buffer."""
        self._experiences.append(exp)

    def train_batch(self, batch: List[Experience] | None = None) -> Dict[str, Any]:
        """Train on a batch of experiences (or the full buffer if None).

        Returns training stats.
        """
        data = batch or self._experiences
        if not data:
            return {"n_updates": 0}

        cfg = self._config
        n_updates = 0

        for exp in data:
            s_key = exp.state.key()
            a_key = exp.action.key()

            # Standard Q-update (single-step, no discounting)
            old_q = self._q[s_key][a_key]
            target = exp.reward
            new_q = old_q + cfg.learning_rate * (target - old_q)

            # CQL pessimism: penalise Q-values for actions NOT taken
            # This prevents overestimation of OOD actions
            for other_action in ALL_ACTIONS:
                other_key = other_action.key()
                if other_key != a_key:
                    # Push down unseen action values
                    self._q[s_key][other_key] -= (
                        cfg.learning_rate * cfg.cql_alpha
                        * max(0, self._q[s_key][other_key] - new_q)
                    )

            self._q[s_key][a_key] = new_q
            self._visits[s_key][a_key] = self._visits[s_key].get(a_key, 0) + 1
            n_updates += 1

        self._total_updates += n_updates
        return {
            "n_updates": n_updates,
            "n_states_visited": len(self._q),
            "total_experiences": len(self._experiences),
        }

    # -- Recommendation (shadow) --------------------------------------------

    def recommend(self, state: MarketState) -> Optional[ExecAction]:
        """Return the best action for a state, or None if insufficient data.

        This is a SHADOW recommendation — not wired to the live path.
        """
        s_key = state.key()
        if s_key not in self._q:
            return None

        # Check we have minimum visits for this state
        total_visits = sum(self._visits[s_key].values())
        if total_visits < self._config.min_visits_for_recommend:
            return None

        # Pick argmax Q
        best_key = max(self._q[s_key], key=self._q[s_key].get)  # type: ignore
        parts = best_key.split("|")
        return ExecAction(
            algo=AlgoAction(parts[0]),
            urgency=UrgencyAction(parts[1]),
        )

    def log_shadow_comparison(
        self,
        state: MarketState,
        actual_action: ExecAction,
        actual_is_bps: float,
        symbol: str = "",
    ) -> Dict[str, Any]:
        """Log a comparison between the agent's recommendation and actual.

        Returns the log entry.
        """
        recommended = self.recommend(state)
        entry = {
            "ts": time.time(),
            "symbol": symbol,
            "state": state.key(),
            "actual_action": actual_action.key(),
            "actual_is_bps": round(actual_is_bps, 2),
            "recommended_action": recommended.key() if recommended else None,
            "would_differ": (
                recommended is not None
                and recommended.key() != actual_action.key()
            ),
            "recommended_q": (
                round(self._q[state.key()][recommended.key()], 3)
                if recommended and state.key() in self._q
                else None
            ),
            "actual_q": (
                round(self._q[state.key()][actual_action.key()], 3)
                if state.key() in self._q
                else None
            ),
        }
        self._shadow_log.append(entry)
        return entry

    # -- Analytics ----------------------------------------------------------

    def shadow_stats(self) -> Dict[str, Any]:
        """Summary of shadow-mode performance."""
        if not self._shadow_log:
            return {"n_comparisons": 0}

        total = len(self._shadow_log)
        differs = sum(1 for e in self._shadow_log if e["would_differ"])
        would_improve = 0
        for entry in self._shadow_log:
            if entry["would_differ"] and entry["recommended_q"] is not None:
                if entry["actual_q"] is not None:
                    if entry["recommended_q"] > entry["actual_q"]:
                        would_improve += 1

        return {
            "n_comparisons": total,
            "n_would_differ": differs,
            "differ_rate": round(differs / total, 3) if total else 0,
            "n_would_improve": would_improve,
            "improve_rate": round(would_improve / total, 3) if total else 0,
            "total_states": len(self._q),
            "total_training_updates": self._total_updates,
            "buffer_size": len(self._experiences),
        }

    def q_table_summary(self) -> Dict[str, Any]:
        """Summary of Q-table: best action per state with enough visits."""
        summary = []
        for s_key in sorted(self._q.keys()):
            total_visits = sum(self._visits[s_key].values())
            if total_visits < self._config.min_visits_for_recommend:
                continue
            best_key = max(self._q[s_key], key=self._q[s_key].get)  # type: ignore
            summary.append({
                "state": s_key,
                "best_action": best_key,
                "q_value": round(self._q[s_key][best_key], 3),
                "visits": total_visits,
            })
        return {
            "n_states_with_policy": len(summary),
            "policies": summary[:50],  # cap output
        }

    # -- Serialisation ------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "config": {
                "learning_rate": self._config.learning_rate,
                "discount": self._config.discount,
                "cql_alpha": self._config.cql_alpha,
                "min_visits_for_recommend": self._config.min_visits_for_recommend,
                "initial_q": self._config.initial_q,
            },
            "q_table": dict(self._q),
            "visits": dict(self._visits),
            "total_updates": self._total_updates,
            "n_experiences": len(self._experiences),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OfflineRLAgent":
        config = CQLConfig(**data.get("config", {}))
        agent = cls(config=config)
        for s_key, actions in data.get("q_table", {}).items():
            agent._q[s_key] = dict(actions)
        for s_key, visits in data.get("visits", {}).items():
            agent._visits[s_key] = dict(visits)
        agent._total_updates = data.get("total_updates", 0)
        return agent
