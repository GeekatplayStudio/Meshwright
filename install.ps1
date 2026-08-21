Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  Meshwright Installer - Geekatplay Studio" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""

# Check Python
try {
    $pyVer = python --version
    Write-Host "[OK] Detected $pyVer" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python is not installed or not in PATH!" -ForegroundColor Red
    Write-Host "Please install Python 3.10+ from https://python.org" -ForegroundColor Yellow
    Exit 1
}

Write-Host "`n[1/3] Upgrading Pip..." -ForegroundColor Yellow
python -m pip install --upgrade pip

Write-Host "`n[2/3] Installing Python 3D Engine Dependencies..." -ForegroundColor Yellow
python -m pip install -r requirements.txt

Write-Host "`n[3/3] Installing Frontend UI Dependencies..." -ForegroundColor Yellow
npm install

Write-Host "`n========================================================" -ForegroundColor Green
Write-Host "  INSTALLATION COMPLETE SUCCESSFULLY!" -ForegroundColor Green
Write-Host "  Run .\start.ps1 or start.bat to launch the application." -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
