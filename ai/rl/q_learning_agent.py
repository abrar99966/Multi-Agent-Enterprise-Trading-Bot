"""Self-learning confidence policy (Q-learning style, persisted).

Learns, per market STATE, whether the desk's calls tend to win or lose, and
nudges conviction accordingly. State = (horizon, macro regime, signal, news
sentiment). After each recommendation is graded (closed loop), the realized
outcome (+1 win / −1 loss) updates that state's running value. Live, the learned
value bends the raw confidence up or down — bounded, and only once a state has
enough samples, so it's a no-op until it has actually learned something.

Persisted to disk so learning survives restarts. This is the literal
"self-learning agent": outcomes → policy → better-sized future conviction.

OBB enrichment (optional):
    Call ``await rl_learning_agent.warm(symbols)`` at startup to pre-fetch real
    OBB news sentiment and FRED macro data. After that, use
    ``await rl_learning_agent.get_state_obb(symbol, agent_outputs)`` instead of
    ``get_state()`` to get enriched state keys (same format, more informative).
    Falls back to the original behaviour when OBB is not installed.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

log = logging.getLogger(__name__)

_QPATH = Path(__file__).resolve().parents[2] / "backend" / ".cache" / "rl_qtable.json"


class RLTrader:
    def __init__(self):
        self.alpha = 0.15          # EWMA step toward each new outcome
        self.k = 0.30              # how hard the learned value bends confidence
        self.lo, self.hi = 0.7, 1.3
        self.min_n = 3             # need this many outcomes before trusting a state
        self.full_n = 20           # sample size at which the nudge reaches full strength
        self.q: Dict[str, Dict[str, float]] = {}   # state -> {"v": mean_reward[-1..1], "n": count}
        self._load()

    def _load(self):
        try:
            if _QPATH.exists():
                self.q = json.loads(_QPATH.read_text())
        except Exception:
            self.q = {}

    def _save(self):
        try:
            _QPATH.parent.mkdir(parents=True, exist_ok=True)
            _QPATH.write_text(json.dumps(self.q))
        except Exception as exc:
            log.debug("RL q-table save failed: %s", exc)

    @staticmethod
    def state_of(horizon, regime, signal, sentiment) -> str:
        return f"{horizon or 'ST'}|{regime or 'neutral'}|{signal or 'neutral'}|{sentiment or 'neutral'}"

    def get_state(self, agent_outputs: Dict[str, Any]) -> str:
        """Derive the state key from a rec's agent outputs (used at grading time).

        Uses the values already present in agent_outputs (no I/O).
        Prefer ``get_state_obb()`` for richer OBB-derived state.
        """
        macro = agent_outputs.get("MacroEconomics") or {}
        he = agent_outputs.get("HorizonEngine") or {}
        tech = agent_outputs.get("TechnicalAnalysis") or {}
        news = agent_outputs.get("NewsIntelligence") or {}
        rationale = agent_outputs.get("rationale") or {}
        signal = he.get("signal") or tech.get("signal")
        return self.state_of(rationale.get("horizon"), macro.get("market_regime"), signal, news.get("sentiment"))

    async def warm(self, symbols: List[str]) -> None:
        """Pre-fetch OBB news sentiment + FRED macro for a symbol universe.

        Call at startup or before a grading batch.  Idempotent — re-fetches
        are throttled internally (TTL 5 min).  No-op if OBB not installed.
        """
        try:
            from app.learning.obb_rl_state import obb_rl_state
            await obb_rl_state.warm(symbols)
        except Exception as exc:
            log.debug("OBB warm skipped: %s", exc)

    async def get_state_obb(
        self,
        symbol: str,
        agent_outputs: Dict[str, Any],
    ) -> str:
        """OBB-enriched state key for a grading event.

        Same key format as ``get_state()`` so the q-table stays compatible.
        Sentiment comes from real OBB news; macro regime from FRED data.
        Falls back to ``get_state()`` when OBB is unavailable.
        """
        try:
            from app.learning.obb_rl_state import obb_rl_state
            return await obb_rl_state.state_for(symbol, agent_outputs)
        except Exception as exc:
            log.debug("OBB state_for failed, using fallback: %s", exc)
            return self.get_state(agent_outputs)

    def update(self, state: str, reward: float) -> None:
        """Fold a graded outcome (+1 win / −1 loss) into the state's value."""
        cell = self.q.get(state) or {"v": 0.0, "n": 0}
        cell["v"] = (1 - self.alpha) * cell["v"] + self.alpha * float(reward)
        cell["n"] = cell["n"] + 1
        self.q[state] = cell
        self._save()

    def multiplier(self, state: str) -> float:
        """Confidence multiplier for a state — 1.0 until enough samples, then bends
        toward winning states (>1) / away from losing ones (<1), bounded."""
        cell = self.q.get(state)
        if not cell or cell["n"] < self.min_n:
            return 1.0
        strength = min(1.0, cell["n"] / self.full_n)
        m = 1.0 + self.k * cell["v"] * strength
        return max(self.lo, min(self.hi, m))

    def policy_snapshot(self, limit: int = 50):
        """Sorted view of what's been learned, for the UI."""
        rows = [{"state": s, "value": round(c["v"], 3), "n": c["n"],
                 "multiplier": round(self.multiplier(s), 3)} for s, c in self.q.items()]
        rows.sort(key=lambda r: r["n"], reverse=True)
        return rows[:limit]


rl_learning_agent = RLTrader()
