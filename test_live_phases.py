"""Live verification of all 5 phases against the running server."""
import urllib.request
import json
import sys

BASE = "http://127.0.0.1:8000"
results = []

def get(path):
    try:
        r = urllib.request.urlopen(BASE + path, timeout=10)
        return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()[:200]}, e.code
    except Exception as e:
        return {"error": str(e)[:200]}, 0

def post(path, data=None):
    try:
        body = json.dumps(data or {}).encode()
        req = urllib.request.Request(BASE + path, data=body, headers={"Content-Type": "application/json"})
        r = urllib.request.urlopen(req, timeout=10)
        return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()[:200]}, e.code
    except Exception as e:
        return {"error": str(e)[:200]}, 0

def check(phase, name, method, path, data=None, expect_key=None):
    if method == "GET":
        d, s = get(path)
    else:
        d, s = post(path, data)
    
    ok = s == 200
    if expect_key and ok:
        ok = expect_key in d if isinstance(d, dict) else False
    
    status = "PASS" if ok else "FAIL"
    results.append((phase, name, status, s))
    print(f"  [{status}] {method:4s} {path:45s} => {s}")
    if not ok and isinstance(d, dict):
        err = d.get("error", d.get("detail", ""))
        if err:
            print(f"         Error: {str(err)[:100]}")
    return d, s

# ===========================================================================
print("=" * 70)
print("PHASE 0: Truth & Safety — Core Application")
print("=" * 70)
check(0, "Root", "GET", "/", expect_key="status")
check(0, "Health", "GET", "/health")

# ===========================================================================
print()
print("=" * 70)
print("PHASE 1: Deterministic Decisions — Trades & Market Data")
print("=" * 70)
check(1, "Trade History", "GET", "/api/v1/trades/history")
check(1, "Recommendations", "GET", "/api/v1/trades/recommendations")
check(1, "Horizons", "GET", "/api/v1/trades/horizons")
check(1, "Market Providers", "GET", "/api/v1/market-data/providers")
check(1, "Watchlist", "GET", "/api/v1/market-data/watchlist")

# ===========================================================================
print()
print("=" * 70)
print("PHASE 2: Measurement & Parity — Performance & Risk")
print("=" * 70)
check(2, "Perf Stats", "GET", "/api/v1/performance/stats")
check(2, "Calibration", "GET", "/api/v1/performance/calibration")
check(2, "Health Score", "GET", "/api/v1/performance/health")
check(2, "Risk Limits", "GET", "/api/v1/risk/limits")
check(2, "RL Policy", "GET", "/api/v1/performance/rl-policy")

# ===========================================================================
print()
print("=" * 70)
print("PHASE 3: Slow Path — Learning & Strategies")
print("=" * 70)
check(3, "Strategies", "GET", "/api/v1/learning/strategies", expect_key="strategies")
check(3, "Learning Status", "GET", "/api/v1/learning/status")
check(3, "Universes", "GET", "/api/v1/learning/universes")
check(3, "Data Status", "GET", "/api/v1/learning/data/status")
check(3, "Screen", "GET", "/api/v1/learning/screen")
check(3, "Broker Accounts", "GET", "/api/v1/brokers/accounts")
check(3, "Brokers Supported", "GET", "/api/v1/brokers/supported")

# ===========================================================================
print()
print("=" * 70)
print("PHASE 4: Multi-Broker & Execution")
print("=" * 70)
check(4, "Phase4 Status", "GET", "/api/v1/phase4/status")
check(4, "SOR Status", "GET", "/api/v1/sor/status")
check(4, "SOR Brokers", "GET", "/api/v1/sor/brokers")
check(4, "SOR Failover", "GET", "/api/v1/sor/failover")
check(4, "Algos", "GET", "/api/v1/execution/algos")
check(4, "Recon Status", "GET", "/api/v1/reconciliation/status")
check(4, "Recon History", "GET", "/api/v1/reconciliation/history")
check(4, "Surveillance", "GET", "/api/v1/surveillance/summary")
check(4, "Alerts", "GET", "/api/v1/surveillance/alerts")
check(4, "Impact Estimate", "POST", "/api/v1/execution/impact-estimate",
       data={"symbol": "RELIANCE", "side": "BUY", "qty": 100, "price": 2500, "adv": 5000000})

# ===========================================================================
print()
print("=" * 70)
print("PHASE 5: Learning & Speed")
print("=" * 70)
check(5, "Phase5 Status", "GET", "/api/v1/phase5/status")
check(5, "Allocator Status", "GET", "/api/v1/allocator/status")
check(5, "Leaderboard", "GET", "/api/v1/allocator/leaderboard")
check(5, "Promotions", "GET", "/api/v1/allocator/promotions")

# Register an arm and test flow
d, s = check(5, "Register Arm", "POST", "/api/v1/allocator/arms",
             data={"strategy_key": "rsi_sma", "params": {"period": 14}})
if s == 200:
    arm_id = d.get("arm_id", "")
    check(5, "Record Reward", "POST", "/api/v1/allocator/reward",
          data={"arm_id": arm_id, "reward": 0.75})
    check(5, "Record PnL", "POST", "/api/v1/allocator/pnl",
          data={"arm_id": arm_id, "pnl_delta": 0.005})
    check(5, "Allocate", "POST", "/api/v1/allocator/allocate")
    check(5, "Eval Promotions", "POST", "/api/v1/allocator/evaluate-promotions")

check(5, "RL Shadow Stats", "GET", "/api/v1/rl-shadow/stats")
check(5, "RL Q-Table", "GET", "/api/v1/rl-shadow/q-table")
check(5, "RL Recommend", "POST", "/api/v1/rl-shadow/recommend",
       data={"symbol": "RELIANCE", "spread_bps": 5.0, "daily_vol": 0.02,
             "volume_ratio": 1.0, "urgency": 0.5, "adv_pct": 1.0})
check(5, "RL Train", "POST", "/api/v1/rl-shadow/train")

check(5, "Profiler Report", "GET", "/api/v1/profiler/report")
check(5, "Profiler Stage", "GET", "/api/v1/profiler/stage/inference")
check(5, "Phase5 Target", "GET", "/api/v1/profiler/phase5-target")

check(5, "DMA Tiers", "GET", "/api/v1/dma/tiers")
check(5, "DMA Evaluate", "POST", "/api/v1/dma/evaluate",
       data={"alpha_0_bps": 5.0, "decay_rate_per_ms": 0.005})
check(5, "DMA Sensitivity", "POST", "/api/v1/dma/sensitivity")

# ===========================================================================
# SUMMARY
# ===========================================================================
print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)

by_phase = {}
for phase, name, status, code in results:
    by_phase.setdefault(phase, []).append(status)

total_pass = 0
total_fail = 0
phase_names = {
    0: "Truth & Safety",
    1: "Deterministic Decisions",
    2: "Measurement & Parity",
    3: "Slow Path & Feeds",
    4: "Multi-Broker & Execution",
    5: "Learning & Speed",
}

for phase in sorted(by_phase.keys()):
    statuses = by_phase[phase]
    passed = statuses.count("PASS")
    failed = statuses.count("FAIL")
    total_pass += passed
    total_fail += failed
    icon = "PASS" if failed == 0 else "PARTIAL"
    print(f"  Phase {phase}: {phase_names[phase]:30s} {passed}/{len(statuses)} endpoints  [{icon}]")

print()
print(f"  Total: {total_pass}/{total_pass + total_fail} endpoints passing")
print()

if total_fail == 0:
    print("  ALL PHASES OPERATIONAL")
else:
    print(f"  {total_fail} endpoint(s) need attention")

sys.exit(0 if total_fail == 0 else 1)
