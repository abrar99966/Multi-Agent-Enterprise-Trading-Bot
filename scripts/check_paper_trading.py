"""Quick script to check paper trading status."""
import urllib.request
import json

url = "http://127.0.0.1:8000/api/v1/paper-trading/status"
data = json.loads(urllib.request.urlopen(url).read())

print(f"Rounds: {data['rounds_completed']}  |  Sessions: {data['total_sessions']}  |  Running: {data['running']}")
print(f"Started: {data['started_at']}")
print()
print(f"{'Strategy':<30}  {'Avg P&L':>10}  {'Sharpe':>8}  {'Win%':>7}  {'Runs':>5}")
print("-" * 70)
for s in data["strategy_rankings"]:
    print(f"  {s['name']:<28}  {s['avg_pnl']:>+10.2f}  {s['sharpe']:>8.3f}  {s['win_rate']:>5.1f}%  {s['sessions']:>5}")
print()
print(f"Recent trades:")
for r in data["recent_results"][-5:]:
    print(f"  {r['strategy']:<28}  P&L: {r['pnl']:>+10.2f}  Fills: {r['fills']}")
