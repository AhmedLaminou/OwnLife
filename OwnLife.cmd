@echo off
rem Double-click to start OwnLife on http://127.0.0.1:8000
where pwsh >nul 2>nul && (pwsh -NoLogo -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1") || (powershell -NoLogo -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1")
