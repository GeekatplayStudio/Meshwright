# Meshwright installer — Geekatplay Studio, Vladimir Chopine
# Installs into an isolated virtual environment (.venv) so Meshwright can never
# disturb the Python packages you use for anything else.
param([switch]$Global)

$ErrorActionPreference = "Stop"
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  Meshwright Installer - Geekatplay Studio" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

try {
    $pyVer = python --version
    Write-Host "[OK] Detected $pyVer" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python is not installed or not in PATH!" -ForegroundColor Red
    Write-Host "Please install Python 3.10+ from https://python.org" -ForegroundColor Yellow
    Exit 1
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
$venv = Join-Path $root ".venv"
$py = "python"

if ($Global) {
    Write-Host "`n[!] -Global was given: installing into your system Python." -ForegroundColor Yellow
    Write-Host "    This can change package versions other projects rely on (numpy, scipy...)." -ForegroundColor Yellow
} else {
    if (-not (Test-Path $venv)) {
        Write-Host "`n[1/4] Creating an isolated environment in .venv ..." -ForegroundColor Yellow
        python -m venv $venv
    } else {
        Write-Host "`n[1/4] Reusing the existing .venv ..." -ForegroundColor Yellow
    }
    $py = Join-Path $venv "Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Write-Host "[ERROR] Could not create the virtual environment." -ForegroundColor Red
        Exit 1
    }
}

Write-Host "`n[2/4] Upgrading pip ..." -ForegroundColor Yellow
& $py -m pip install --upgrade pip --quiet

Write-Host "`n[3/4] Installing the 3D engine dependencies ..." -ForegroundColor Yellow
& $py -m pip install -r (Join-Path $root "requirements.txt")

Write-Host "`n[4/4] Installing the viewport libraries (optional, needs Node.js) ..." -ForegroundColor Yellow
if (Get-Command npm -ErrorAction SilentlyContinue) {
    npm install --silent
} else {
    Write-Host "    Node.js not found - skipping. The vendored Three.js files in ui/vendor are already in the repo." -ForegroundColor DarkGray
}

Write-Host "`n========================================================" -ForegroundColor Green
Write-Host "  INSTALLATION COMPLETE" -ForegroundColor Green
if (-not $Global) {
    Write-Host "  Installed into .venv - your other Python projects are untouched." -ForegroundColor Green
}
Write-Host "  Run .\start.ps1 or start.bat to launch Meshwright." -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
