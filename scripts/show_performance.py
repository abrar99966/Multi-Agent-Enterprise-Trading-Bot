"""Fetch and display all performance data from the live API."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.stdout.reconfigure(encoding='utf-8')
import httpx

BASE = "http://127.0.0.1:8000"

# Performance stats
r = httpx.get(f"{BASE}/api/v1/performance/stats?days=7&grade_now=false")
perf = r.json()

# Agent dashboard
r2 = httpx.get(f"{BASE}/api/v1/slowpath/dashboard")
agents = r2.json()

print("=" * 80)
print("LIVE PERFORMANCE REPORT")
print("=" * 80)

print(f"\n  Graded signals:       {perf.get('graded_count', 0)}")
print(f"  Hit Rate (1h):        {(perf.get('hit_rate_1h') or 0) * 100:.1f}%")
print(f"  Hit Rate (24h):       {(perf.get('hit_rate_24h') or 0) * 100:.1f}%")
print(f"  Avg Win Move (1h):    +{perf.get('avg_correct_move_pct_1h', 0):.3f}%")
print(f"  Avg Loss Move (1h):   -{perf.get('avg_wrong_move_pct_1h', 0):.3f}%")
print(f"  Expectancy (1h):      {perf.get('expectancy_1h', 0):.3f}%")

print("\n  PER-SYMBOL BREAKDOWN:")
print(f"  {'Symbol':>12} | {'Total':>5} | {'Hit Rate':>8} | {'Avg Move':>8}")
print(f"  {'-'*12} | {'-'*5} | {'-'*8} | {'-'*8}")
for sym, data in (perf.get("per_symbol") or {}).items():
    hr = data.get("hit_rate_1h", 0)
    print(f"  {sym:>12} | {data['total']:>5} | {hr*100:>7.1f}% | {data.get('avg_move_pct',0):>+7.3f}%")

print(f"\n  RECENT SIGNALS (last 10):")
print(f"  {'Symbol':>10} {'Side':>4} {'Entry':>10} {'After 1h':>10} {'Move%':>8} {'Result':>6} {'Conf':>6}")
print(f"  {'-'*10} {'-'*4} {'-'*10} {'-'*10} {'-'*8} {'-'*6} {'-'*6}")
for rec in (perf.get("recent") or [])[:10]:
    tag = "WIN" if rec.get("correct_1h") else "LOSS"
    after = rec.get("price_after_1h") or 0
    move = rec.get("actual_move_pct_1h") or 0
    conf = rec.get("confidence") or 0
    print(f"  {rec['symbol']:>10} {rec['side']:>4} {rec['entry_price']:>10.2f} {after:>10.2f} {move:>+7.3f}% {tag:>6} {conf:>5.3f}")

print(f"\n  INTELLIGENCE AGENTS:")
for agent in agents.get("agents", []):
    name = agent.get("agent_id", "?")
    status = agent.get("status", "?")
    invocations = agent.get("metrics", {}).get("invocations", 0)
    errors = agent.get("metrics", {}).get("errors", 0)
    print(f"    {name:<30} status={status:<8} invocations={invocations} errors={errors}")

provider = agents.get("provider", "?")
print(f"  LLM Provider: {provider}")
print("=" * 80)
