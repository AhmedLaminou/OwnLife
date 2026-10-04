# Development: backend with auto-reload (:8000) + Vite with hot reload (:5173), in two windows.
#   pwsh -ExecutionPolicy Bypass -File .\scripts\dev.ps1
$root = Split-Path -Parent $PSScriptRoot
$shell = if (Get-Command pwsh -ErrorAction SilentlyContinue) { "pwsh" } else { "powershell" }

Start-Process $shell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\backend'; .\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --reload --port 8000"
Start-Process $shell -ArgumentList "-NoExit", "-Command", "Set-Location '$root\frontend'; npm run dev"

Start-Sleep -Seconds 6
Start-Process "http://localhost:5173"
