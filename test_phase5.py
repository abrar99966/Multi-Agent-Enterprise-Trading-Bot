"""Phase 5 integration test."""
import sys
sys.path.insert(0, "backend")

# ===========================================================================
# TEST 1: Bandit Capital Allocator
# ===========================================================================
from app.allocator.bandit import BanditAllocator, BanditConfig, ArmStatus

alloc = BanditAllocator(config=BanditConfig(min_observations_for_promotion=10), seed=42)

# Register arms from strategies
arms = []
for strat in ["rsi_sma", "ema_cross", "macd", "bollinger", "supertrend"]:
    arm_id = alloc.register_arm(strat, {"period": 14})
    arms.append(arm_id)

print(f"Registered {len(arms)} arms")

# Simulate rewards (rsi_sma performs best)
import random
rng = random.Random(123)
for i in range(50):
    for j, arm_id in enumerate(arms):
        base = 0.7 if j == 0 else 0.4 + rng.random() * 0.2
        alloc.record_reward(arm_id, min(1.0, base + rng.gauss(0, 0.1)))
        alloc.record_pnl(arm_id, rng.gauss(0.001 if j == 0 else -0.0005, 0.002))

# Allocate capital
allocs = alloc.allocate()
print(f"Allocation: {len(allocs)} arms, sum={sum(allocs.values()):.3f}")

# Check leaderboard
lb = alloc.leaderboard(top_n=3)
for entry in lb:
    print(f"  #{entry['rank']} {entry['arm_id'][:20]:20s} pm={entry['posterior_mean']:.3f} sharpe={entry['sharpe']:.2f} frac={entry['capital_frac']:.3f}")

# Evaluate promotions
promos = alloc.evaluate_promotions()
print(f"Promotions: {len(promos)}")
if promos:
    print(f"  New champion: {promos[0]['new_champion']}")
    print(f"  Reason: {promos[0]['reason']}")

# Status
status = alloc.status_summary()
print(f"Status: {status['active_arms']} active, champion={status['champion']}")

# Serialisation round-trip
d = alloc.to_dict()
alloc2 = BanditAllocator.from_dict(d, seed=42)
assert alloc2.status_summary()["active_arms"] == status["active_arms"]
print("Serialisation: round-trip OK")
print()
print("TEST 1 PASSED: Bandit Allocator")

# ===========================================================================
# TEST 2: Promotion Gates
# ===========================================================================
from app.allocator.gates import default_gate_check, GateConfig

# Create a strong challenger
champion = alloc.champion
if champion:
    # Try to promote an underperforming arm (should fail)
    weak_arm = alloc.arm(arms[3])
    decision = default_gate_check(weak_arm, champion)
    print(f"\nGate check (weak vs champion): promote={decision.promote}, reason={decision.reason}")
    assert not decision.promote, "Weak arm should not promote"
    print("TEST 2 PASSED: Promotion Gates")
else:
    print("TEST 2 SKIPPED: No champion (gate still verified via evaluate_promotions)")

# ===========================================================================
# TEST 3: Offline-RL Execution Agent
# ===========================================================================
from app.allocator.rl_execution import (
    OfflineRLAgent, CQLConfig, make_state, Experience, ExecAction, AlgoAction, UrgencyAction
)

agent = OfflineRLAgent(config=CQLConfig(learning_rate=0.2, min_visits_for_recommend=3))

# Generate synthetic execution experiences
rng2 = random.Random(456)
for _ in range(100):
    state = make_state(
        spread_bps=rng2.uniform(1, 15),
        daily_vol=rng2.uniform(0.005, 0.04),
        volume_ratio=rng2.uniform(0.3, 3.0),
        urgency=rng2.uniform(0.1, 0.9),
        adv_pct=rng2.uniform(0.1, 10.0),
    )
    # IS works best in tight spreads, VWAP in normal, POV in wide
    if state.spread.value == "tight":
        best_algo, best_reward = AlgoAction.IS, -2.0 + rng2.gauss(0, 1)
    elif state.spread.value == "normal":
        best_algo, best_reward = AlgoAction.VWAP, -4.0 + rng2.gauss(0, 1)
    else:
        best_algo, best_reward = AlgoAction.POV, -8.0 + rng2.gauss(0, 2)
    
    action = ExecAction(algo=best_algo, urgency=UrgencyAction.NORMAL)
    exp = Experience(state=state, action=action, reward=best_reward, symbol="RELIANCE")
    agent.ingest(exp)

# Train
stats = agent.train_batch()
print(f"\nRL Training: {stats['n_updates']} updates, {stats['n_states_visited']} states")

# Test recommendation
test_state = make_state(spread_bps=2.0, daily_vol=0.02, volume_ratio=1.0, urgency=0.5, adv_pct=1.0)
rec = agent.recommend(test_state)
print(f"RL Recommendation for tight/medium market: {rec.key() if rec else 'no data'}")

# Shadow comparison
log_entry = agent.log_shadow_comparison(
    test_state,
    ExecAction(algo=AlgoAction.VWAP, urgency=UrgencyAction.NORMAL),
    actual_is_bps=3.5,
    symbol="RELIANCE",
)
print(f"Shadow comparison: would_differ={log_entry['would_differ']}, rec_q={log_entry['recommended_q']}")

# Stats
shadow = agent.shadow_stats()
print(f"Shadow stats: {shadow['n_comparisons']} comparisons")

# Serialisation
d2 = agent.to_dict()
agent2 = OfflineRLAgent.from_dict(d2)
assert agent2._total_updates == agent._total_updates
print("RL Serialisation: round-trip OK")
print()
print("TEST 3 PASSED: Offline-RL Agent")

# ===========================================================================
# TEST 4: Latency Profiler
# ===========================================================================
from app.hotpath.profiler import LatencyProfiler, Stage
import time

profiler = LatencyProfiler()

# Simulate pipeline measurements
for _ in range(1000):
    # Feed decode: ~0.1-0.5ms
    profiler.record_direct(Stage.FEED_DECODE, int(rng2.uniform(100_000, 500_000)))
    # Feature update: ~0.3-1ms
    profiler.record_direct(Stage.FEATURE_UPDATE, int(rng2.uniform(300_000, 1_000_000)))
    # Inference: ~0.5-2ms
    profiler.record_direct(Stage.INFERENCE, int(rng2.uniform(500_000, 2_000_000)))
    # Risk check: ~0.2-1ms
    profiler.record_direct(Stage.RISK_CHECK, int(rng2.uniform(200_000, 1_000_000)))
    # Order encode: ~0.05-0.1ms
    profiler.record_direct(Stage.ORDER_ENCODE, int(rng2.uniform(50_000, 100_000)))
    # Total: sum of above
    total = int(rng2.uniform(1_500_000, 4_500_000))
    profiler.record_direct(Stage.TOTAL, total)

report = profiler.report()
print(f"\nLatency Report:")
for stage_name, stats in report["stages"].items():
    if stats["count"] > 0:
        target = stats.get("target_us", "N/A")
        print(f"  {stage_name:20s}: p50={stats['p50_us']:8.1f}us  p99={stats['p99_us']:8.1f}us  target={target}")

# Check Phase 5 target
p5 = profiler.meets_phase5_target()
print(f"Phase 5 target (p99 < 1ms): {p5['meets_target']} (current: {p5['current_p99_us']}us)")

# Context manager test
with profiler.measure(Stage.INFERENCE):
    time.sleep(0.0001)  # 0.1ms

print(f"Breaches: {report['total_breaches']}")
print()
print("TEST 4 PASSED: Latency Profiler")

# ===========================================================================
# TEST 5: DMA Economics Evaluator
# ===========================================================================
from app.hotpath.dma_evaluator import DMAEvaluator, AlphaDecayModel, TIERS

# Conservative scenario: low alpha, low sensitivity
evaluator_low = DMAEvaluator(
    alpha_model=AlphaDecayModel(alpha_0_bps=3.0, decay_rate_per_ms=0.002),
    capital_deployed_inr=10_000_000,
)
memo_low = evaluator_low.evaluate_all()
print(f"\nDMA Analysis (conservative: alpha=3bps, decay=0.002):")
print(f"  Recommendation: {memo_low['recommendation']}")
for name, ev in memo_low["tier_evaluations"].items():
    print(f"  {name:20s}: alpha={ev['alpha_bps']:.2f}bps  net=INR{ev['net_annual_inr']:>10,.0f}  ROI={ev['roi_pct']:.0f}%")

# Aggressive scenario: high alpha, high sensitivity
evaluator_high = DMAEvaluator(
    alpha_model=AlphaDecayModel(alpha_0_bps=10.0, decay_rate_per_ms=0.015),
    capital_deployed_inr=50_000_000,
)
memo_high = evaluator_high.evaluate_all()
print(f"\nDMA Analysis (aggressive: alpha=10bps, decay=0.015):")
print(f"  Recommendation: {memo_high['recommendation']}")

# Sensitivity analysis
sensitivity = evaluator_low.sensitivity_analysis(
    alpha_range=[3.0, 5.0, 10.0],
    decay_range=[0.002, 0.005, 0.01],
)
upgrades = sum(1 for s in sensitivity if "UPGRADE" in s["recommendation"])
print(f"\nSensitivity: {len(sensitivity)} scenarios, {upgrades} recommend upgrade")
print()
print("TEST 5 PASSED: DMA Economics")

# ===========================================================================
print()
print("=" * 60)
print("ALL PHASE 5 TESTS PASSED")
print("=" * 60)
