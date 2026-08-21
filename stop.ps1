Write-Host "Stopping Meshwright..." -ForegroundColor Yellow
Get-Process | Where-Object { $_.MainWindowTitle -like "*Meshwright*" } | Stop-Process -Force -ErrorAction SilentlyContinue
Write-Host "Application stopped." -ForegroundColor Green
