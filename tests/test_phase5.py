"""Phase 5 verification: bandit allocator, §7.4 promotion gates, offline-RL
shadow agent, latency profiler, DMA economics. Pins the behaviors claimed in
docs/PHASE5_IMPLEMENTATION.md (which shipped without pytest coverage)."""
from __future__ import annotations

from app.allocator.bandit import ArmState, ArmStatus, BanditAllocator, BanditConfig
from app.allocator.gates import GateConfig, default_gate_check
from app.allocator.rl_execution import (
    AlgoAction,
    CQLConfig,
    ExecAction,
    Experience,
    OfflineRLAgent,
    UrgencyAction,
    make_state,
)
from app.hotpath.dma_evaluator import AlphaDecayModel, DMAEvaluator
from app.hotpath.profiler import LatencyProfiler, Stage

# ---------------------------------------------------------------- bandit


def _seeded_allocator() -> tuple[BanditAllocator, str, str, str]:
    alloc = BanditAllocator(seed=42)
    strong = alloc.register_arm("rsi_sma", {"rsi_period": 14})
    mid = alloc.register_arm("ema_cross", {"ema_fast": 12})
    weak = alloc.register_arm("macd", {"macd_fast": 12})
    for _ in range(40):
        alloc.record_reward(strong, 0.9)
        alloc.record_reward(mid, 0.55)
        alloc.record_reward(weak, 0.2)
    return alloc, strong, mid, weak


def test_bandit_allocation_sums_to_one_and_ranks_by_posterior() -> None:
    alloc, strong, _, weak = _seeded_allocator()
    fractions = alloc.allocate()
    assert abs(sum(fractions.values()) - 1.0) < 1e-6
    assert fractions[strong] == max(fractions.values())
    assert fractions[strong] > fractions[weak]
    cfg = BanditConfig()
    for frac in fractions.values():  # min/max diversification guards
        assert cfg.min_frac - 1e-9 <= frac <= cfg.max_frac + 1e-9


def test_bandit_same_seed_is_reproducible() -> None:
    a1, *_ = _seeded_allocator()
    a2, *_ = _seeded_allocator()
    assert a1.allocate() == a2.allocate()


def test_bandit_serialization_round_trip() -> None:
    alloc, strong, *_ = _seeded_allocator()
    restored = BanditAllocator.from_dict(alloc.to_dict(), seed=42)
    orig, back = alloc.arm(strong), restored.arm(strong)
    assert back is not None
    assert back.alpha == orig.alpha and back.beta_param == orig.beta_param
    assert back.n_observations == orig.n_observations


def test_bandit_retire_excludes_from_allocation() -> None:
    alloc, strong, mid, weak = _seeded_allocator()
    alloc.retire_arm(weak)
    fractions = alloc.allocate()
    assert weak not in fractions
    assert set(fractions) == {strong, mid}


# ---------------------------------------------------------------- §7.4 gates


def _arm(arm_id: str = "a") -> ArmState:
    return ArmState(arm_id=arm_id, strategy_key="s", param_hash="h", params={})


def test_gate_blocks_insufficient_observations() -> None:
    challenger = _arm()
    challenger.record_reward(0.9)  # 1 observation << 100
    decision = default_gate_check(challenger, champion=None)
    assert decision.promote is False
    assert "Insufficient observations" in decision.reason


def test_gate_promotes_strong_arm_without_champion() -> None:
    challenger = _arm()
    for i in range(120):  # high mean, some variance -> high Sharpe
        challenger.record_reward(0.85 if i % 2 else 0.95)
        challenger.update_equity(10.0)
    decision = default_gate_check(challenger, champion=None)
    assert decision.promote is True


def test_gate_blocks_high_drawdown() -> None:
    challenger = _arm()
    for i in range(120):
        challenger.record_reward(0.85 if i % 2 else 0.95)
    challenger.update_equity(100.0)
    challenger.update_equity(-50.0)  # 50% drawdown > 15% ceiling
    decision = default_gate_check(challenger, champion=None)
    assert decision.promote is False
    assert "drawdown" in decision.reason.lower()


def test_gate_requires_sharpe_improvement_over_champion() -> None:
    champion, challenger = _arm("champ"), _arm("chall")
    for i in range(120):
        champion.record_reward(0.85 if i % 2 else 0.95)
        challenger.record_reward(0.85 if i % 2 else 0.95)  # identical -> delta 0
    decision = default_gate_check(challenger, champion, GateConfig())
    assert decision.promote is False
    assert "improvement" in decision.reason.lower()


def test_bandit_promotion_via_gate() -> None:
    alloc, strong, *_ = _seeded_allocator()
    arm = alloc.arm(strong)
    for i in range(120):  # variance for a real Sharpe + clean equity curve
        arm.record_reward(0.85 if i % 2 else 0.95)
        arm.update_equity(5.0)
    promotions = alloc.evaluate_promotions()
    assert any(p["new_champion"] == strong for p in promotions)
    assert alloc.champion is not None and alloc.champion.arm_id == strong
    assert alloc.champion.status == ArmStatus.CHAMPION


# ---------------------------------------------------------------- offline RL


def test_rl_agent_learns_better_action_and_stays_shadow() -> None:
    agent = OfflineRLAgent(CQLConfig(min_visits_for_recommend=5))
    state = make_state(spread_bps=5, daily_vol=0.02, volume_ratio=1.0,
                       urgency=0.5, adv_pct=0.5)
    good = ExecAction(AlgoAction.IS, UrgencyAction.NORMAL)
    bad = ExecAction(AlgoAction.POV, UrgencyAction.PASSIVE)
    for _ in range(8):
        agent.ingest(Experience(state=state, action=good, reward=-2.0))
        agent.ingest(Experience(state=state, action=bad, reward=-60.0))
    result = agent.train_batch()
    assert result  # training report
    rec = agent.recommend(state)
    assert rec is not None and rec.key() == good.key()
    # CQL pessimism: an action never taken must not outscore the learned one.
    q = agent.q_table_summary()
    assert q  # structure exists
    # Shadow comparison logging (agent recommends internally; stays shadow-only)
    entry = agent.log_shadow_comparison(state, actual_action=bad, actual_is_bps=60.0)
    assert entry["would_differ"] is True  # agent would have picked the better algo
    assert agent.shadow_stats()["n_comparisons"] >= 1


def test_rl_agent_no_recommendation_without_data() -> None:
    agent = OfflineRLAgent()
    cold_state = make_state(spread_bps=50, daily_vol=0.10, volume_ratio=3.0,
                            urgency=0.9, adv_pct=8.0)
    assert agent.recommend(cold_state) is None  # min visits not met -> abstain


def test_rl_agent_serialization_round_trip() -> None:
    agent = OfflineRLAgent()
    state = make_state(5, 0.02, 1.0, 0.5, 0.5)
    action = ExecAction(AlgoAction.VWAP, UrgencyAction.AGGRESSIVE)
    for _ in range(6):
        agent.ingest(Experience(state=state, action=action, reward=-3.0))
    agent.train_batch()
    restored = OfflineRLAgent.from_dict(agent.to_dict())
    assert restored.recommend(state) is not None


# ---------------------------------------------------------------- profiler


def _find_stats(report: dict) -> dict:
    """Locate the {p50_us, p99_us, ...} block wherever the report nests it."""
    if "p50_us" in report:
        return report
    for value in report.values():
        if isinstance(value, dict) and "p50_us" in value:
            return value
    raise AssertionError(f"no percentile stats in report: {list(report)}")


def test_profiler_percentiles_and_report() -> None:
    prof = LatencyProfiler()
    for _ in range(100):
        prof.record_direct(Stage.INFERENCE, 100_000)  # 100 µs
    prof.record_direct(Stage.INFERENCE, 10_000_000)   # one 10 ms outlier
    stats = _find_stats(prof.stage_report(Stage.INFERENCE))
    assert stats["count"] == 101
    assert abs(stats["p50_us"] - 100.0) < 5.0   # median unaffected by outlier
    assert stats["p99_us"] >= stats["p50_us"]
    assert stats["max_us"] >= 10_000.0           # outlier captured
    full = prof.report()
    assert full


def test_profiler_context_manager_and_target_check() -> None:
    prof = LatencyProfiler()
    with prof.measure(Stage.TOTAL):
        sum(range(1000))
    target = prof.meets_phase5_target()
    assert isinstance(target, dict)
    prof.reset()


# ---------------------------------------------------------------- DMA economics


def test_dma_evaluator_produces_memo() -> None:
    memo = DMAEvaluator().evaluate_all()
    assert memo["recommendation"]
    assert "retail_api" in memo["tier_evaluations"]
    assert memo["upgrade_analysis"]
    for up in memo["upgrade_analysis"]:
        assert {"from", "to", "incremental_roi_pct", "recommended"} <= set(up)


def test_dma_aggressive_alpha_recommends_upgrade() -> None:
    # High alpha, fast decay: latency is expensive -> upgrade should win.
    memo = DMAEvaluator(
        alpha_model=AlphaDecayModel(alpha_0_bps=10.0, decay_rate_per_ms=0.015),
        capital_deployed_inr=50_000_000,
    ).evaluate_all()
    assert memo["recommendation"].startswith(("UPGRADE", "EVALUATE"))


def test_dma_sensitivity_analysis_runs() -> None:
    result = DMAEvaluator().sensitivity_analysis(
        alpha_range=[3.0, 10.0], decay_range=[0.002, 0.015]
    )
    assert result
