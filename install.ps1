<#
  Meshwright installer — Geekatplay Studio, Vladimir Chopine

  Installs into an isolated virtual environment (.venv) inside the project folder,
  so Meshwright can never disturb the Python packages you use for anything else.

  Usage
    .\install.ps1                  normal install
    .\install.ps1 -Check           only report what is on this machine, change nothing
    .\install.ps1 -Recreate        throw the old .venv away and build a fresh one
    .\install.ps1 -NoOptional      core only, skip the extra mesh engines
    .\install.ps1 -Python "C:\Path\to\python.exe"   use this interpreter
    .\install.ps1 -Global          install into your system Python (not recommended)
#>
param(
    [switch]$Global,
    [string]$Python = "",
    [switch]$Recreate,
    [switch]$NoOptional,
    [switch]$Check
)

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Definition }
$logFile = Join-Path $root "install-log.txt"
$venv = Join-Path $root ".venv"

. (Join-Path $root "scripts\Find-Python.ps1")

# ---------------------------------------------------------------- output helpers
try {
    "Meshwright install log - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" |
        Set-Content -Path $logFile -Encoding utf8
} catch { }

function Note([string]$text) {
    try { Add-Content -Path $logFile -Value $text -Encoding utf8 } catch { }
}
function Say([string]$text, [string]$color = "Gray") {
    Write-Host $text -ForegroundColor $color
    Note $text
}
function Fail([string[]]$lines) {
    Write-Host ""
    Say "========================================================" Red
    Say "  INSTALLATION STOPPED" Red
    Say "========================================================" Red
    foreach ($l in $lines) { Say $l Yellow }
    Say ""
    Say "Full log: $logFile" DarkGray
    exit 1
}

Say "========================================================" Cyan
Say "  Meshwright Installer - Geekatplay Studio" Cyan
Say "========================================================" Cyan
Note "PowerShell $($PSVersionTable.PSVersion) on $([System.Environment]::OSVersion.VersionString)"
Note "Project folder: $root"
Say ""

# ---------------------------------------------------------------- 0. sanity
# Double-clicking install.bat from inside the downloaded ZIP runs it out of a
# temporary folder Windows deletes afterwards. Everything "works" and then the
# program is gone, so stop before installing anything.
if ($root -match '(?i)\\AppData\\Local\\Temp\\' -or $root -match '(?i)\\Temp\\(Rar\$|7z|Temp1_)') {
    Fail @("Meshwright is running from a temporary folder:",
           "  $root",
           "",
           "That happens when install.bat is started from inside the downloaded ZIP.",
           "Windows unpacks a throwaway copy that it deletes later.",
           "",
           "Right-click the ZIP file, choose 'Extract All...', pick a normal folder",
           "such as C:\Meshwright, and run install.bat from there.")
}

$probeFile = Join-Path $root ".meshwright-write-test"
try {
    New-Item -Path $probeFile -ItemType File -Force | Out-Null
    Remove-Item $probeFile -Force
} catch {
    Fail @("This folder cannot be written to:",
           "  $root",
           "",
           "Meshwright installs into a .venv folder next to install.bat, so it needs",
           "write access. Move the folder somewhere like C:\Meshwright (not Program",
           "Files, and ideally not a OneDrive folder) and run install.bat again.")
}

# ---------------------------------------------------------------- 1. find Python
Say "[1/5] Looking for Python ..." Yellow

if ($Python) {
    $interp = Find-MWPython -Explicit $Python
    if (-not $interp) {
        Fail (@("'$Python' is not a working Python 3.10+ interpreter.", "") + (Get-MWPythonHelp))
    }
} else {
    $interp = Find-MWPython
}

if (-not $interp) {
    $seen = Get-MWPythonCandidatePaths
    Note "Candidates examined: $($seen -join '; ')"
    Fail (@("No usable Python was found on this computer.", "") + (Get-MWPythonHelp))
}

Say "      Using Python $($interp.Version)  -  $($interp.Path)" Green
Note "64-bit: $($interp.Is64Bit); store build: $($interp.IsStoreApp)"

if (-not $interp.Is64Bit) {
    Say "      [!] This is a 32-bit Python. Several mesh engines only ship 64-bit" Yellow
    Say "          builds, so parts of Meshwright will be unavailable." Yellow
}
if (-not $interp.Preferred) {
    Say "      [!] Python $($interp.Version) is outside the tested range (3.10 - 3.13)." Yellow
    Say "          Meshwright should still run, but some mesh engines have no ready-made" Yellow
    Say "          package for it yet and will be skipped." Yellow
}

# ---------------------------------------------------------------- -Check only
if ($Check) {
    Say ""
    Say "[Check] Interpreters found on this machine:" Yellow
    foreach ($p in Get-MWPythonCandidatePaths) {
        $i = Test-MWPython -Path $p
        if ($i) { Say ("      {0,-10} {1}" -f $i.Version, $i.Path) }
        else { Say ("      {0,-10} {1}" -f "unusable", $p) DarkGray }
    }
    $vpyCheck = Join-Path $venv "Scripts\python.exe"
    Say ""
    if (Test-Path $vpyCheck) {
        Say "[Check] Packages in the existing .venv:" Yellow
        $r = Invoke-MWNative -Exe $vpyCheck -Arguments @((Join-Path $root "scripts\check_install.py")) -TimeoutSeconds 180
        Write-Host $r.Output
        Note $r.Output
    } else {
        Say "[Check] No .venv yet - run install.bat to create one." DarkGray
    }
    Say ""
    Say "Report saved to: $logFile" DarkGray
    exit 0
}

# ---------------------------------------------------------------- 2. environment
$py = $interp.Path

if ($Global) {
    Say ""
    Say "[2/5] -Global was given: installing into your system Python." Yellow
    Say "      This can change package versions other projects rely on (numpy, scipy...)." Yellow
} else {
    Say ""
    if ($Recreate -and (Test-Path $venv)) {
        Say "[2/5] Removing the old .venv ..." Yellow
        try {
            Remove-Item $venv -Recurse -Force
        } catch {
            Fail @("Could not delete $venv.",
                   "Close Meshwright and any terminal using it, then run install.bat again.")
        }
    }
    if (Test-Path (Join-Path $venv "Scripts\python.exe")) {
        Say "[2/5] Reusing the existing .venv ..." Yellow
    } else {
        Say "[2/5] Creating an isolated environment in .venv ..." Yellow
        $r = Invoke-MWNative -Exe $py -Arguments @('-m', 'venv', $venv) -TimeoutSeconds 300
        Note $r.Output
        if ($r.ExitCode -ne 0) {
            Fail @("Could not create the virtual environment in:",
                   "  $venv",
                   "",
                   "Python reported:",
                   $r.Output.Trim(),
                   "",
                   "Usual causes:",
                   "  * The project folder is read-only, or OneDrive is syncing it right now.",
                   "    Move Meshwright to a plain local folder such as C:\Meshwright and retry.",
                   "  * Antivirus blocked copying python.exe into .venv.",
                   "  * A half-finished .venv is in the way - run:  install.bat -Recreate")
        }
    }
    $py = Join-Path $venv "Scripts\python.exe"
    if (-not (Test-Path $py)) {
        Fail @("The environment was created but $py is missing.",
               "Run  install.bat -Recreate  to build it again from scratch.")
    }
}

Note "Installing with: $py"

# ---------------------------------------------------------------- 3. pip
Say ""
Say "[3/5] Updating pip ..." Yellow
& $py -m pip install --upgrade pip --disable-pip-version-check --quiet
if ($LASTEXITCODE -ne 0) {
    Say "      Could not update pip - continuing with the version already installed." DarkYellow
    Note "pip self-update failed with exit code $LASTEXITCODE"
}

# ---------------------------------------------------------------- 4. required
Say ""
Say "[4/5] Installing the required packages ..." Yellow
& $py -m pip install --disable-pip-version-check -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    $why = @("The required packages could not be installed.",
             "",
             "Look at the pip messages above. The usual causes are:",
             "  * No internet connection, or a company proxy/firewall blocking pypi.org.",
             "  * Antivirus quarantined a download - simply try again.")
    if (-not $interp.Preferred) {
        $why += "  * Python $($interp.Version) is newer than these packages support yet -"
        $why += "    install Python 3.12 from python.org, then run:  install.bat -Recreate"
    }
    Fail $why
}

# ---------------------------------------------------------------- 5. engines
$optionalFile = Join-Path $root "requirements-optional.txt"
$skipped = @()
Say ""
if ($NoOptional) {
    Say "[5/5] Skipping the optional mesh engines (-NoOptional was given)." Yellow
} elseif (-not (Test-Path $optionalFile)) {
    Say "[5/5] No optional package list found - skipping." DarkGray
} else {
    Say "[5/5] Installing the optional mesh engines ..." Yellow
    Say "      Installed one at a time on purpose: if a package has no build for your" DarkGray
    Say "      Python, that engine is skipped instead of failing the whole install." DarkGray
    # Strip trailing comments: pip rejects "pymeshlab>=2023.12   # repair" as one spec.
    $specs = Get-Content $optionalFile |
             ForEach-Object { ($_ -replace '\s+#.*$', '').Trim() } |
             Where-Object { $_ -and -not $_.StartsWith("#") }
    foreach ($spec in $specs) {
        $name = ($spec -split '[<>=!~;\[]')[0].Trim()
        Write-Host ("      {0,-22} " -f $name) -NoNewline
        $r = Invoke-MWNative -Exe $py -TimeoutSeconds 900 -Arguments @(
            '-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', $spec)
        Note "pip install $spec -> exit $($r.ExitCode)"
        Note $r.Output
        if ($r.ExitCode -eq 0) {
            Write-Host "installed" -ForegroundColor Green
        } else {
            Write-Host "skipped" -ForegroundColor DarkYellow
            $skipped += $name
        }
    }
}

# ---------------------------------------------------------------- verify
Say ""
Say "Checking the installation ..." Yellow
$checkResult = Invoke-MWNative -Exe $py -Arguments @((Join-Path $root "scripts\check_install.py")) -TimeoutSeconds 180
Write-Host $checkResult.Output
Note $checkResult.Output
if ($checkResult.ExitCode -ne 0) {
    Fail @("Meshwright cannot start yet - see the report above.",
           "Run  install.bat -Recreate  to build the environment again from scratch.")
}

# ---------------------------------------------------------------- node (optional)
$npm = Get-Command npm -ErrorAction SilentlyContinue
if ($npm) {
    Say "Refreshing the viewport libraries with npm (optional) ..." DarkGray
    $r = Invoke-MWNative -Exe $npm.Source -Arguments @('install', '--silent') -TimeoutSeconds 600
    Note "npm install -> exit $($r.ExitCode)"
    Note $r.Output
} else {
    Note "npm not found - using the vendored Three.js files in ui/vendor."
}

# ---------------------------------------------------------------- done
Say ""
Say "========================================================" Green
Say "  INSTALLATION COMPLETE" Green
Say "========================================================" Green
if (-not $Global) {
    Say "  Everything went into .venv - your other Python projects are untouched." Green
}
if ($skipped.Count -gt 0) {
    Say "  Engines skipped: $($skipped -join ', ')" Yellow
    Say "  Meshwright runs without them; those repair or retopology methods are just" Yellow
    Say "  not offered. Python 3.12 gets the fullest set of engines." Yellow
}
Say "  Start Meshwright with  start.bat  (or .\start.ps1)." Green
Say ""
Say "  Meshwright does not ship with a 3D model - it works on your own files." Cyan
Say "  Press 'Open model', drop a file on the window, or click 'Load demo model'" Cyan
Say "  in the empty viewport to try it on a built-in test object." Cyan
Say "========================================================" Green
Note "Finished successfully."
