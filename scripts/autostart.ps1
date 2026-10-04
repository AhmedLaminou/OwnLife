# Start OwnLife quietly when you log in, so the evening reminders and the file
# sync work without you opening anything. It adds one shortcut to your Startup
# folder (shell:startup); -Off removes it.
#
#   pwsh -ExecutionPolicy Bypass -File .\scripts\autostart.ps1        turn on
#   pwsh -ExecutionPolicy Bypass -File .\scripts\autostart.ps1 -Off   turn off
#
# The shortcut runs  backend\.venv\Scripts\pythonw.exe -m app --background :
# no window, production mode, output in backend\data\logs\ownlife.log, and it
# does nothing if OwnLife is already running. Open http://127.0.0.1:8000 as usual.
param([switch]$Off)

$root = Split-Path -Parent $PSScriptRoot
$link = Join-Path ([Environment]::GetFolderPath("Startup")) "OwnLife.lnk"

if ($Off) {
    Remove-Item $link -ErrorAction SilentlyContinue
    Write-Host "OwnLife will no longer start at login."
    exit 0
}

$pythonw = Join-Path $root "backend\.venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    Write-Host "Not found: $pythonw — run scripts\setup.ps1 first." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $root "frontend\dist\index.html"))) {
    Write-Host "Building the frontend once (the background server serves it)…" -ForegroundColor Yellow
    Push-Location (Join-Path $root "frontend"); npm run build; Pop-Location
}

$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($link)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "-m app --background"
$shortcut.WorkingDirectory = Join-Path $root "backend"
$shortcut.Description = "OwnLife — your life ledger, running for the reminders and the file sync"
$shortcut.Save()

Write-Host "OwnLife will start at login, without a window: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "To start it now as well: double-click $link"
Write-Host "Logs: backend\data\logs\ownlife.log · to undo: autostart.ps1 -Off"
