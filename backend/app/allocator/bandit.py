"""Thompson-Sampling bandit capital allocator.

Formalises the strategy-tournament into a champion–challenger framework
(TARGET_ARCHITECTURE §7.4, Phase 5). Each *arm* is a (strategy, param_hash)
pair. The allocator maintains a Beta posterior per arm, updated by
discretised reward signals derived from risk-adjusted returns, and outputs
capital-fraction recommendations.

Key properties:
    * Exploration is principled (Thompson Sampling: sample from posteriors,
      allocate proportionally to sampled rank).
    * Champions earn capital through measured performance, not manual
      selection.
    * Promotion requires passing the formal §7.4 gate (minimum sample,
      Sharpe improvement, drawdown ceiling) — see ``gates.py``.
    * Fully serialisable: state can be persisted to JSON/DB for resume.
    * Deterministic when seeded: for paper-trading replay.

Capital fractions are normalised to sum to 1.0. A strategy with no
observations starts with a uniform prior Beta(1,1), so it gets a fair
share of exploration capital until the posterior sharpens.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Arm lifecycle
# ---------------------------------------------------------------------------

class ArmStatus(str, Enum):
    EXPLORATION = "exploration"       # receiving exploratory capital
    CHALLENGER  = "challenger"        # statistically promising, under observation
    CHAMPION    = "champion"          # promoted via §7.4 gate
    RETIRED     = "retired"           # disabled (performance decay)


# ---------------------------------------------------------------------------
# Arm state
# ---------------------------------------------------------------------------

@dataclass
class ArmState:
    """Per-arm state for a Thompson Sampling bandit."""

    arm_id: str                         # strategy_key::param_hash
    strategy_key: str
    param_hash: str
    params: Dict[str, Any]

    # Beta distribution posterior: alpha = successes + 1, beta = failures + 1
    alpha: float = 1.0
    beta_param: float = 1.0

    # Performance book
    n_observations: int = 0
    cumulative_return: float = 0.0
    cumulative_return_sq: float = 0.0   # for variance
    max_drawdown: float = 0.0
    peak_equity: float = 0.0
    current_equity: float = 0.0

    # Status
    status: ArmStatus = ArmStatus.EXPLORATION
    promoted_at: Optional[float] = None  # epoch ts
    retired_at: Optional[float] = None

    # Last capital fraction assigned
    capital_frac: float = 0.0

    @property
    def mean_return(self) -> float:
        if self.n_observations == 0:
            return 0.0
        return self.cumulative_return / self.n_observations

    @property
    def return_std(self) -> float:
        if self.n_observations < 2:
            return 0.0
        var = (
            self.cumulative_return_sq / self.n_observations
            - self.mean_return ** 2
        )
        return math.sqrt(max(0.0, var))

    @property
    def sharpe(self) -> float:
        """Annualised Sharpe (assumes hourly observations, ~2000 trading hours/year)."""
        if self.return_std < 1e-12:
            return 0.0
        return self.mean_return / self.return_std * math.sqrt(2000)

    @property
    def posterior_mean(self) -> float:
        return self.alpha / (self.alpha + self.beta_param)

    def sample(self, rng: random.Random) -> float:
        """Draw from Beta(alpha, beta) posterior."""
        return rng.betavariate(self.alpha, self.beta_param)

    def record_reward(self, reward: float) -> None:
        """Update the arm with a discretised reward ∈ [0, 1].

        reward > 0.5 ⟹ success (alpha ↑), else failure (beta ↑). The
        magnitude scales the update to reflect *how good* the reward was.
        """
        self.n_observations += 1
        self.cumulative_return += reward
        self.cumulative_return_sq += reward ** 2

        # Thompson update: discretise into success/failure with magnitude
        if reward > 0.5:
            self.alpha += reward
        else:
            self.beta_param += (1.0 - reward)

    def update_equity(self, pnl_delta: float) -> None:
        """Track equity curve for drawdown."""
        self.current_equity += pnl_delta
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity
        dd = (
            (self.peak_equity - self.current_equity) / max(self.peak_equity, 1e-9)
            if self.peak_equity > 0
            else 0.0
        )
        self.max_drawdown = max(self.max_drawdown, dd)


def _param_hash(params: Dict[str, Any]) -> str:
    raw = json.dumps(params, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class BanditConfig:
    """Tunable knobs for the allocator."""

    # Minimum capital fraction for any active arm (so challengers always
    # get some capital for exploration).
    min_frac: float = 0.02

    # Maximum fraction any single arm may hold (diversification guard).
    max_frac: float = 0.40

    # Arms with fewer than this many observations are NOT eligible for
    # champion promotion.
    min_observations_for_promotion: int = 100

    # Number of Thompson samples to draw when computing allocations.
    n_thompson_samples: int = 1000

    # Reward discretisation: how to convert a PnL/Sharpe signal to [0,1].
    # 'sharpe' = sigmoid of period Sharpe; 'return' = sigmoid of return.
    reward_mode: str = "sharpe"

    # Decay factor for stale arms: reduce alpha by this per period of
    # inactivity so abandoned arms don't hold phantom capital.
    inactivity_decay: float = 0.99

    # Maximum number of active (non-retired) arms.
    max_active_arms: int = 50


# ---------------------------------------------------------------------------
# Bandit Allocator
# ---------------------------------------------------------------------------

class BanditAllocator:
    """Thompson-Sampling capital allocator for the strategy tournament.

    Usage::

        allocator = BanditAllocator()

        # Register arms from the strategy registry
        for strategy_key, strategy in STRATEGIES.items():
            for combo in iter_combos(strategy):
                allocator.register_arm(strategy_key, combo)

        # After each evaluation period, record rewards
        allocator.record_reward(arm_id, reward_value)

        # Get capital allocations
        allocs = allocator.allocate()  # {arm_id: fraction}
    """

    def __init__(
        self,
        config: BanditConfig | None = None,
        seed: int | None = None,
    ) -> None:
        self._config = config or BanditConfig()
        self._rng = random.Random(seed)
        self._arms: Dict[str, ArmState] = {}
        self._champion_id: Optional[str] = None
        self._allocation_history: List[Dict[str, Any]] = []
        self._promotion_log: List[Dict[str, Any]] = []

    # -- Registration -------------------------------------------------------

    def register_arm(
        self,
        strategy_key: str,
        params: Dict[str, Any],
        status: ArmStatus = ArmStatus.EXPLORATION,
    ) -> str:
        """Register a new arm. Returns the arm_id."""
        ph = _param_hash(params)
        arm_id = f"{strategy_key}::{ph}"
        if arm_id in self._arms:
            return arm_id
        active_count = sum(
            1 for a in self._arms.values()
            if a.status not in (ArmStatus.RETIRED,)
        )
        if active_count >= self._config.max_active_arms:
            raise ValueError(
                f"Max active arms ({self._config.max_active_arms}) reached; "
                f"retire an arm before registering new ones."
            )
        self._arms[arm_id] = ArmState(
            arm_id=arm_id,
            strategy_key=strategy_key,
            param_hash=ph,
            params=dict(params),
            status=status,
        )
        return arm_id

    def retire_arm(self, arm_id: str) -> None:
        arm = self._arms.get(arm_id)
        if arm is None:
            return
        arm.status = ArmStatus.RETIRED
        arm.retired_at = time.time()
        arm.capital_frac = 0.0
        if self._champion_id == arm_id:
            self._champion_id = None

    # -- Reward ingestion ---------------------------------------------------

    def record_reward(self, arm_id: str, reward: float) -> None:
        """Record a reward observation for an arm.

        ``reward`` should already be normalised to [0, 1]:
        - 1.0 = best possible outcome
        - 0.0 = worst possible outcome

        Use ``normalise_sharpe()`` or ``normalise_return()`` helpers to
        convert raw performance signals.
        """
        arm = self._arms.get(arm_id)
        if arm is None or arm.status == ArmStatus.RETIRED:
            return
        arm.record_reward(max(0.0, min(1.0, reward)))

    def record_pnl(self, arm_id: str, pnl_delta: float) -> None:
        """Record a raw PnL delta and auto-convert to reward."""
        arm = self._arms.get(arm_id)
        if arm is None or arm.status == ArmStatus.RETIRED:
            return
        arm.update_equity(pnl_delta)

        # Auto-discretise PnL to [0, 1] via sigmoid
        if self._config.reward_mode == "sharpe":
            reward = self._sharpe_reward(arm)
        else:
            reward = self._return_reward(pnl_delta)
        arm.record_reward(max(0.0, min(1.0, reward)))

    @staticmethod
    def _sharpe_reward(arm: ArmState) -> float:
        """Sigmoid of rolling Sharpe ∈ (-∞,+∞) → [0, 1]."""
        # sharpe of 2.0 → ~0.88; sharpe of 0.0 → 0.5; sharpe of -2.0 → ~0.12
        return 1.0 / (1.0 + math.exp(-arm.sharpe))

    @staticmethod
    def _return_reward(pnl_delta: float) -> float:
        """Sigmoid of single-period return."""
        # Scale: 1% return → ~0.73
        return 1.0 / (1.0 + math.exp(-pnl_delta * 100))

    # -- Allocation ---------------------------------------------------------

    def allocate(self) -> Dict[str, float]:
        """Compute capital fractions via Thompson Sampling.

        Returns {arm_id: fraction} for all active arms. Fractions sum to 1.0.
        """
        active = [
            a for a in self._arms.values()
            if a.status != ArmStatus.RETIRED
        ]
        if not active:
            return {}

        cfg = self._config
        n = len(active)
        n_samples = cfg.n_thompson_samples

        # Draw samples and count wins (rank-1 finishes)
        win_counts: Dict[str, int] = {a.arm_id: 0 for a in active}
        for _ in range(n_samples):
            samples = [(a.sample(self._rng), a.arm_id) for a in active]
            samples.sort(reverse=True)
            winner = samples[0][1]
            win_counts[winner] += 1

        # Raw fractions from win counts
        raw: Dict[str, float] = {}
        for arm_id, wins in win_counts.items():
            raw[arm_id] = wins / n_samples

        # Apply min/max constraints while keeping the sum at exactly 1.0.
        # A naive clamp-then-renormalise inflates capped arms back above
        # max_frac (e.g. cap at 0.40, total 0.81 -> 0.40/0.81 = 0.49), breaking
        # the diversification guard precisely when one arm dominates. Instead:
        # clamp, then redistribute the imbalance among arms that still have
        # room, preserving both bounds (water-filling).
        allocs: Dict[str, float] = {
            a.arm_id: min(max(raw[a.arm_id], cfg.min_frac), cfg.max_frac)
            for a in active
        }
        for _ in range(2 * n):
            imbalance = sum(allocs.values()) - 1.0
            if abs(imbalance) < 1e-9:
                break
            if imbalance > 0:
                # Too much allocated: shrink arms above their floor.
                room = {k: v - cfg.min_frac for k, v in allocs.items() if v > cfg.min_frac + 1e-12}
            else:
                # Too little: grow arms below their cap.
                room = {k: cfg.max_frac - v for k, v in allocs.items() if v < cfg.max_frac - 1e-12}
            total_room = sum(room.values())
            if total_room <= 1e-12:
                # Constraints infeasible (e.g. n*max_frac < 1): plain renormalise.
                s = sum(allocs.values())
                if s > 0:
                    allocs = {k: v / s for k, v in allocs.items()}
                break
            shift = min(abs(imbalance), total_room)
            sign = -1.0 if imbalance > 0 else 1.0
            for k, r in room.items():
                allocs[k] += sign * shift * (r / total_room)

        # Update arms and record history
        for a in active:
            a.capital_frac = allocs.get(a.arm_id, 0.0)

        self._allocation_history.append({
            "ts": time.time(),
            "allocations": dict(allocs),
            "n_active": n,
        })

        return allocs

    # -- Champion/Challenger management -------------------------------------

    def evaluate_promotions(
        self,
        gate_check: Callable[["ArmState", Optional["ArmState"]], "PromotionDecision"] | None = None,
    ) -> List[Dict[str, Any]]:
        """Evaluate which arms should be promoted/demoted.

        If ``gate_check`` is provided, it is called for each candidate
        vs the current champion; otherwise uses the built-in gate.
        """
        from app.allocator.gates import default_gate_check

        check = gate_check or default_gate_check
        champion = self._arms.get(self._champion_id) if self._champion_id else None
        promotions: List[Dict[str, Any]] = []

        for arm in self._arms.values():
            if arm.status == ArmStatus.RETIRED:
                continue
            if arm.arm_id == self._champion_id:
                continue

            decision = check(arm, champion)
            if decision.promote:
                # Promote challenger → champion
                arm.status = ArmStatus.CHAMPION
                arm.promoted_at = time.time()
                if champion is not None:
                    champion.status = ArmStatus.CHALLENGER  # demote old champion
                self._champion_id = arm.arm_id

                entry = {
                    "ts": time.time(),
                    "new_champion": arm.arm_id,
                    "old_champion": champion.arm_id if champion else None,
                    "reason": decision.reason,
                    "sharpe_improvement": decision.sharpe_delta,
                    "n_observations": arm.n_observations,
                }
                self._promotion_log.append(entry)
                promotions.append(entry)
                champion = arm  # for subsequent evaluations in this loop

            elif (
                arm.status == ArmStatus.EXPLORATION
                and arm.n_observations >= self._config.min_observations_for_promotion // 2
                and arm.sharpe > 0.5  # promising enough to watch closely
            ):
                arm.status = ArmStatus.CHALLENGER

        return promotions

    # -- Decay / maintenance ------------------------------------------------

    def decay_inactive(self) -> None:
        """Apply decay to arms that haven't received rewards recently."""
        for arm in self._arms.values():
            if arm.status == ArmStatus.RETIRED:
                continue
            # If an arm's alpha+beta grew but no recent observations,
            # gently decay toward the prior
            if arm.n_observations > 0:
                arm.alpha *= self._config.inactivity_decay
                arm.beta_param *= self._config.inactivity_decay
                # Floor: don't decay below prior
                arm.alpha = max(arm.alpha, 1.0)
                arm.beta_param = max(arm.beta_param, 1.0)

    # -- Query interface ----------------------------------------------------

    @property
    def champion(self) -> Optional[ArmState]:
        if self._champion_id is None:
            return None
        return self._arms.get(self._champion_id)

    def arm(self, arm_id: str) -> Optional[ArmState]:
        return self._arms.get(arm_id)

    def active_arms(self) -> List[ArmState]:
        return [
            a for a in self._arms.values()
            if a.status != ArmStatus.RETIRED
        ]

    def leaderboard(self, top_n: int = 20) -> List[Dict[str, Any]]:
        """Top arms ranked by posterior mean."""
        active = self.active_arms()
        active.sort(key=lambda a: a.posterior_mean, reverse=True)
        return [
            {
                "rank": i + 1,
                "arm_id": a.arm_id,
                "strategy": a.strategy_key,
                "status": a.status.value,
                "posterior_mean": round(a.posterior_mean, 4),
                "sharpe": round(a.sharpe, 3),
                "n_obs": a.n_observations,
                "capital_frac": round(a.capital_frac, 4),
                "max_drawdown": round(a.max_drawdown, 4),
            }
            for i, a in enumerate(active[:top_n])
        ]

    def status_summary(self) -> Dict[str, Any]:
        active = self.active_arms()
        by_status = {}
        for a in active:
            by_status[a.status.value] = by_status.get(a.status.value, 0) + 1
        return {
            "total_arms": len(self._arms),
            "active_arms": len(active),
            "retired_arms": len(self._arms) - len(active),
            "by_status": by_status,
            "champion": self._champion_id,
            "champion_sharpe": round(self.champion.sharpe, 3) if self.champion else None,
            "total_promotions": len(self._promotion_log),
            "allocation_cycles": len(self._allocation_history),
        }

    def promotion_history(self) -> List[Dict[str, Any]]:
        return list(self._promotion_log)

    # -- Serialisation ------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "config": asdict(self._config),
            "champion_id": self._champion_id,
            "arms": {
                arm_id: {
                    "arm_id": a.arm_id,
                    "strategy_key": a.strategy_key,
                    "param_hash": a.param_hash,
                    "params": a.params,
                    "alpha": a.alpha,
                    "beta_param": a.beta_param,
                    "n_observations": a.n_observations,
                    "cumulative_return": a.cumulative_return,
                    "cumulative_return_sq": a.cumulative_return_sq,
                    "max_drawdown": a.max_drawdown,
                    "peak_equity": a.peak_equity,
                    "current_equity": a.current_equity,
                    "status": a.status.value,
                    "promoted_at": a.promoted_at,
                    "retired_at": a.retired_at,
                    "capital_frac": a.capital_frac,
                }
                for arm_id, a in self._arms.items()
            },
            "promotion_log": self._promotion_log,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], seed: int | None = None) -> "BanditAllocator":
        config = BanditConfig(**data.get("config", {}))
        alloc = cls(config=config, seed=seed)
        alloc._champion_id = data.get("champion_id")
        alloc._promotion_log = data.get("promotion_log", [])
        for arm_id, arm_data in data.get("arms", {}).items():
            arm = ArmState(
                arm_id=arm_data["arm_id"],
                strategy_key=arm_data["strategy_key"],
                param_hash=arm_data["param_hash"],
                params=arm_data["params"],
                alpha=arm_data["alpha"],
                beta_param=arm_data["beta_param"],
                n_observations=arm_data["n_observations"],
                cumulative_return=arm_data["cumulative_return"],
                cumulative_return_sq=arm_data["cumulative_return_sq"],
                max_drawdown=arm_data["max_drawdown"],
                peak_equity=arm_data["peak_equity"],
                current_equity=arm_data["current_equity"],
                status=ArmStatus(arm_data["status"]),
                promoted_at=arm_data.get("promoted_at"),
                retired_at=arm_data.get("retired_at"),
                capital_frac=arm_data.get("capital_frac", 0.0),
            )
            alloc._arms[arm_id] = arm
        return alloc


# -- Convenience -----------------------------------------------------------

def normalise_sharpe(sharpe: float) -> float:
    """Map Sharpe ratio ∈ ℝ to [0, 1] reward via sigmoid."""
    return 1.0 / (1.0 + math.exp(-sharpe))


def normalise_return(ret_pct: float) -> float:
    """Map single-period return (%) to [0, 1] reward."""
    return 1.0 / (1.0 + math.exp(-ret_pct))
