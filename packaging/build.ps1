<#
.SYNOPSIS
    Builds Meshwright.exe and its Windows installer.

.DESCRIPTION
    Freezes the program with PyInstaller, proves the frozen copy works by running its own
    self-test with nothing but Windows on the PATH, then wraps it in an Inno Setup installer.

        powershell -ExecutionPolicy Bypass -File packaging\build.ps1
        powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Edition lite
        powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Fast

    -Edition full   (default) everything, including PyMeshLab and pymeshfix (both GPL-3)
    -Edition lite   the same program without those two engines
    -Fast           a quick, larger installer for testing the build itself; never publish one
    -SkipInstaller  stop after the frozen program and its self-test
    -NoWebView2     leave Microsoft's WebView2 installer out of the setup (not recommended)

    Output: dist\Meshwright\Meshwright.exe and dist\Meshwright-Setup-<version>[-lite].exe
#>
param(
    [ValidateSet('full', 'lite')][string]$Edition = 'full',
    [switch]$Fast,
    [switch]$SkipInstaller,
    [switch]$NoWebView2
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'

function Step($n, $text) { Write-Host "`n[$n] $text" -ForegroundColor Cyan }
function Fail($message) { Write-Host "`nBUILD FAILED: $message" -ForegroundColor Red; exit 1 }
function Run([string]$exe, [string[]]$arguments) {
    # Native programs write progress to stderr; that is not an error in itself, only their exit code is.
    $previous = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    # Out-Host matters: anything left in the pipeline becomes part of this function's return
    # value, so a program that printed a line would come back as ["wrote ...", 0] and "-ne 0"
    # would call a successful step a failure.
    & $exe @arguments | Out-Host
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    return $code
}

if (-not (Test-Path $py)) { Fail "There is no .venv here. Run install.bat first, then build." }
$version = (& $py -c "from engine.version import __version__; print(__version__)").Trim()
Write-Host "Meshwright $version - $Edition edition" -ForegroundColor Green

# ---------------------------------------------------------------- 1. tools
Step 1 'Checking the build tools'
$have = Run $py @('-c', 'import PyInstaller')
if ($have -ne 0) {
    Write-Host 'PyInstaller is not installed in .venv; installing it (nothing else is touched).'
    if ((Run $py @('-m', 'pip', 'install', '-r', 'requirements-build.txt')) -ne 0) { Fail 'Could not install PyInstaller.' }
}

# ---------------------------------------------------------------- 2. version resource
Step 2 'Stamping the version onto the program'
if ((Run $py @('packaging\prepare.py', 'version-info')) -ne 0) { Fail 'Could not write the version resource.' }

# ---------------------------------------------------------------- 3. freeze
Step 3 "Freezing the program (this is the slow part, several minutes)"
$env:MESHWRIGHT_EDITION = $Edition
$code = Run $py @('-m', 'PyInstaller', '--noconfirm', '--clean', '--distpath', 'dist', '--workpath', 'build\work', 'packaging\meshwright.spec')
if ($code -ne 0) { Fail "PyInstaller stopped with exit code $code." }
if (-not (Test-Path 'dist\Meshwright\Meshwright.exe')) { Fail 'PyInstaller finished but there is no Meshwright.exe.' }

# ---------------------------------------------------------------- 4. notices
Step 4 'Writing the third-party notices'
if ((Run $py @('packaging\prepare.py', 'notices', $Edition)) -ne 0) { Fail 'Could not write the notices.' }

# ---------------------------------------------------------------- 5. self-test
Step 5 'Testing the frozen program (nothing but Windows on PATH)'
$report = Join-Path $root 'build\frozen-selftest.json'
if (Test-Path $report) { [IO.File]::Delete($report) }
$savedPath = $env:PATH
try {
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $env:PYTHONHOME = $null; $env:PYTHONPATH = $null
    $test = Start-Process -FilePath 'dist\Meshwright\Meshwright.exe' -ArgumentList "--selftest-out `"$report`"" -Wait -PassThru
} finally {
    $env:PATH = $savedPath
}
if (-not (Test-Path $report)) { Fail "The program died before it could write a self-test report (exit code $($test.ExitCode))." }
$results = Get-Content $report -Raw | ConvertFrom-Json
foreach ($s in $results.steps) {
    $color = switch ($s.status) { 'passed' { 'Green' } 'skipped' { 'DarkGray' } default { 'Red' } }
    Write-Host ("  {0,-8} {1,-44} {2,6}s  {3}" -f $s.status, $s.name, $s.seconds, (($s.detail -split "`n")[0])) -ForegroundColor $color
}
if ((Run $py @('packaging\prepare.py', 'check', $report, $Edition)) -ne 0) { Fail 'The frozen program did not pass its own self-test.' }

if ($SkipInstaller) {
    Write-Host "`nDone. Run it: dist\Meshwright\Meshwright.exe" -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------- 6. installer
Step 6 'Building the installer'
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Host 'Inno Setup 6 is not installed, so no installer was built.' -ForegroundColor Yellow
    Write-Host 'Install it once (free) with:  winget install JRSoftware.InnoSetup   - then run this again.'
    Write-Host 'The program itself is ready: dist\Meshwright\Meshwright.exe'
    exit 0
}

$bootstrapper = Join-Path $root 'packaging\redist\MicrosoftEdgeWebview2Setup.exe'
if (-not $NoWebView2) {
    function Test-Microsoft($path) {
        if (-not (Test-Path $path)) { return $false }
        $sig = Get-AuthenticodeSignature -FilePath $path
        return ($sig.Status -eq 'Valid') -and ($sig.SignerCertificate.Subject -match 'O=Microsoft Corporation')
    }
    if (-not (Test-Microsoft $bootstrapper)) {
        Write-Host 'Downloading Microsoft''s WebView2 installer (2 MB)...'
        New-Item -ItemType Directory -Force -Path (Split-Path $bootstrapper) | Out-Null
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile $bootstrapper
        # It goes into an installer other people will run, so it must be exactly what Microsoft published.
        if (-not (Test-Microsoft $bootstrapper)) {
            [IO.File]::Delete($bootstrapper)
            Fail 'The downloaded WebView2 installer does not carry a valid Microsoft signature, so it was discarded.'
        }
    }
    Write-Host 'WebView2 installer present and signed by Microsoft.'
} elseif (Test-Path $bootstrapper) {
    [IO.File]::Move($bootstrapper, "$bootstrapper.disabled")   # keep it out of this build
}

$defines = @("/DAppVersion=$version", "/DEdition=$Edition")
if ($Fast) { $defines += '/DFast=1'; Write-Host 'FAST build: for testing only, do not publish.' -ForegroundColor Yellow }
if ((Run $iscc (@('/Q') + $defines + @('packaging\meshwright.iss'))) -ne 0) { Fail 'Inno Setup could not build the installer.' }

$suffix = if ($Edition -eq 'lite') { '-lite' } else { '' }
$setup = Join-Path $root "dist\Meshwright-Setup-$version$suffix.exe"
if (-not (Test-Path $setup)) { Fail "Inno Setup finished but $setup is missing." }
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash
Write-Host "`nBUILD OK" -ForegroundColor Green
Write-Host ("  Installer : {0}  ({1:N0} MB)" -f $setup, ((Get-Item $setup).Length / 1MB))
Write-Host "  SHA-256   : $hash"
Write-Host '  It is not code-signed, so Windows SmartScreen may warn on first run ("More info" > "Run anyway").'
