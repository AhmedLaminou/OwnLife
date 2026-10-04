# One-time setup (and after pulling new code): Python packages, frontend packages, build.
#   pwsh -ExecutionPolicy Bypass -File .\scripts\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Write-Host "== Backend" -ForegroundColor Cyan
Set-Location "$root\backend"
if (-not (Test-Path .venv)) { python -m venv .venv }
if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv pip install --python .venv\Scripts\python.exe -r requirements.txt
} else {
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Created backend\.env from .env.example — put your OpenRouter key in it." -ForegroundColor Yellow
}

Write-Host "== Frontend" -ForegroundColor Cyan
Set-Location "$root\frontend"
npm install --no-fund --no-audit
npm run build

Write-Host "== Check" -ForegroundColor Cyan
Set-Location "$root\backend"
.\.venv\Scripts\python.exe -m app.doctor
