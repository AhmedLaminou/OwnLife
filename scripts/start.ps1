# Everyday start: one server on http://127.0.0.1:8000 serving the API and the built frontend.
#   pwsh -ExecutionPolicy Bypass -File .\scripts\start.ps1      (or double-click OwnLife.cmd)
$root = Split-Path -Parent $PSScriptRoot
Set-Location "$root\backend"

if (-not (Test-Path "$root\frontend\dist\index.html")) {
    Write-Host "Frontend not built yet: building it once…" -ForegroundColor Yellow
    Push-Location "$root\frontend"; npm run build; Pop-Location
}

# Open the browser once the server answers.
Start-Job -ScriptBlock {
    for ($i = 0; $i -lt 60; $i++) {
        try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 "http://127.0.0.1:8000/api/system/health" | Out-Null; break } catch { Start-Sleep -Milliseconds 500 }
    }
    Start-Process "http://127.0.0.1:8000"
} | Out-Null

# production = no auto-reload (steadier for daily use). Ctrl+C stops it.
$env:ENVIRONMENT = "production"
.\.venv\Scripts\python.exe -m app
