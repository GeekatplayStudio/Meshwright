# Meshwright launcher — Geekatplay Studio.
# Uses the isolated .venv the installer created; falls back to a discovered
# system Python only if that .venv is missing, and says so.
$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Definition }

$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "No .venv found - Meshwright has not been installed yet." -ForegroundColor Yellow
    . (Join-Path $root "scripts\Find-Python.ps1")
    $interp = Find-MWPython
    if (-not $interp) {
        Write-Host ""
        Get-MWPythonHelp | ForEach-Object { Write-Host $_ -ForegroundColor Yellow }
        exit 1
    }
    Write-Host "Trying your system Python $($interp.Version) instead. Run install.bat for the proper setup." -ForegroundColor Yellow
    $py = $interp.Path
}

Write-Host "Launching Meshwright..." -ForegroundColor Green
Write-Host "This window is the activity log - keep it open while you work." -ForegroundColor DarkGray
& $py (Join-Path $root "app.py")
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Meshwright closed with error code $LASTEXITCODE." -ForegroundColor Red
    Write-Host "If a module is missing, run:  .\install.ps1 -Recreate" -ForegroundColor Yellow
}
exit $LASTEXITCODE
