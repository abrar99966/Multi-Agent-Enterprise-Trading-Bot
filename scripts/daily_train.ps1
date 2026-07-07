# Daily maintenance for the trading desk — keeps the bot learning.
#
# What it does (against the running backend on :8000):
#   1. Tops up any thin daily history (deepens symbols with < 400 bars).
#   2. Retrains the walk-forward tournament on the liquid core (indexes + Nifty50)
#      so tuned_params.json stays fresh with out-of-sample track records.
#   3. The closed-loop grading, RL policy, and confidence calibration update
#      themselves automatically as recommendations mature (no action needed).
#
# Schedule it once with Windows Task Scheduler (runs every day at 18:30):
#   schtasks /Create /SC DAILY /TN "TradingDeskDailyTrain" /TR ^
#     "powershell -ExecutionPolicy Bypass -File `"C:\Workspace\enterprise-trading-bot-Part 2\scripts\daily_train.ps1`"" /ST 18:30
#
# Tune TRAIN_PRESET to 'stored' for a full-market (slow) retrain, or keep the
# liquid core for a fast daily refresh.

$ErrorActionPreference = "Stop"
$API = "http://127.0.0.1:8000"
$TRAIN_PRESET = "indexes_plus_nifty50"   # or 'nifty50', 'all_nse', 'stored'
$LOOKBACK = 1825                          # ~5 years of daily bars

function Wait-Idle($what, $statusUrl, $maxMin = 90) {
  $deadline = (Get-Date).AddMinutes($maxMin)
  do {
    Start-Sleep -Seconds 20
    try { $s = Invoke-RestMethod $statusUrl -TimeoutSec 20 } catch { continue }
    $p = $s.state.progress
    Write-Host ("[{0}] {1} {2}/{3} {4}" -f $what, $s.running, $p.done, $p.total, $p.current_symbol)
  } while ($s.running -and (Get-Date) -lt $deadline)
}

Write-Host "=== Daily train $(Get-Date -Format s) ==="

# 1) Top up thin daily history (only symbols with < 400 bars, gentle throttle)
$ingest = @{ preset='all_nse'; interval='day'; lookback_days=$LOOKBACK; min_bars=400; skip_existing=$true; throttle=0.8 } | ConvertTo-Json
try {
  Invoke-RestMethod "$API/api/v1/learning/data/ingest" -Method Post -Body $ingest -ContentType "application/json" -TimeoutSec 30 | Out-Null
  Write-Host "ingest top-up kicked"
  Wait-Idle "ingest" "$API/api/v1/learning/data/status" 120
} catch { Write-Host "ingest skipped/busy: $($_.Exception.Message)" }

# 2) Retrain the tournament (walk-forward, OOS-selected)
$train = @{ preset=$TRAIN_PRESET; interval='day'; lookback_days=$LOOKBACK } | ConvertTo-Json
try {
  Invoke-RestMethod "$API/api/v1/learning/train" -Method Post -Body $train -ContentType "application/json" -TimeoutSec 30 | Out-Null
  Write-Host "train kicked ($TRAIN_PRESET)"
  Wait-Idle "train" "$API/api/v1/learning/status" 90
} catch { Write-Host "train skipped/busy: $($_.Exception.Message)" }

# 3) Trigger grading so matured recs feed RL + calibration
try { Invoke-RestMethod "$API/api/v1/performance/stats" -TimeoutSec 30 | Out-Null; Write-Host "grading triggered" } catch {}

Write-Host "=== Daily train complete $(Get-Date -Format s) ==="
