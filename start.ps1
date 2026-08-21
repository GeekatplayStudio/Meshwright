# Meshwright launcher — uses the isolated .venv when present.
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
Write-Host "Launching Meshwright..." -ForegroundColor Green
& $py (Join-Path $root "app.py")
