# Start the whole platform with one command:  .\start.ps1
#
#  1. This app (FastAPI backend + dashboards)  -> http://127.0.0.1:8000/dash
#  2. The classic Part-1 frontend, IF its code is on this machine.
#     Point ETB_LEGACY_UI_DIR at the folder containing its package.json
#     (set below or in your environment); it will be started with `npm run dev`
#     and the dashboard sidebar link flips from "offline" to live.
#
# The classic app's code is currently NOT in this workspace — until you copy
# it here (or set the path), only the backend starts and the sidebar shows
# the classic app as offline. Everything the classic app did via the API
# (brokers, recommendations, learning) is served by THIS backend either way.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# --- classic frontend ----------------------------------------------------------
# Lives in this repo at .\frontend (Next.js, port 3001). ETB_LEGACY_UI_DIR
# overrides if you ever move it.
$legacyDir = $env:ETB_LEGACY_UI_DIR
if (-not $legacyDir -and (Test-Path (Join-Path $root "frontend\package.json"))) {
    $legacyDir = Join-Path $root "frontend"
}
if ($legacyDir -and (Test-Path (Join-Path $legacyDir "package.json"))) {
    Write-Host "Starting classic app from $legacyDir ..." -ForegroundColor Cyan
    if (-not (Test-Path (Join-Path $legacyDir "node_modules"))) {
        Write-Host "  (first run: npm install)" -ForegroundColor DarkGray
        Push-Location $legacyDir; npm install; Pop-Location
    }
    Start-Process -FilePath "npm" -ArgumentList "run", "dev" -WorkingDirectory $legacyDir -WindowStyle Minimized
    $legacyUrl = if ($env:ETB_LEGACY_UI_URL) { $env:ETB_LEGACY_UI_URL } else { "http://localhost:3000" }
    Write-Host "  classic app -> $legacyUrl" -ForegroundColor Cyan
} elseif ($legacyDir) {
    Write-Host "ETB_LEGACY_UI_DIR set but no package.json found at $legacyDir - skipping classic app." -ForegroundColor Yellow
} else {
    Write-Host "Classic app not configured (set ETB_LEGACY_UI_DIR to its folder to auto-start it)." -ForegroundColor DarkGray
}

# --- this app ------------------------------------------------------------------
$py = Join-Path $root "venv\Scripts\python.exe"
Write-Host "Starting AI Trading Assistant -> http://127.0.0.1:8000/dash" -ForegroundColor Green
Start-Process "http://127.0.0.1:8000/dash"   # open the browser
& $py -m uvicorn app.main:app --app-dir backend --port 8000
