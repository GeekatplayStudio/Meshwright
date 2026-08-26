# Meshwright — Python discovery helpers (dot-source this file).
# Geekatplay Studio, Vladimir Chopine
#
# Windows ships a fake "python.exe" in WindowsApps that only prints
#   "Python was not found; run without arguments to install from the Microsoft Store"
# and exits with an error code. It is on PATH by default, so naive installers
# think Python is present and then fail two steps later. Everything here probes
# a candidate by actually running it, so stubs, broken installs and 32-bit or
# too-old interpreters are all filtered out before we try to build a .venv.

$script:MW_MinPython       = [Version]"3.10.0"
$script:MW_PreferredMax    = [Version]"3.13.99"   # newest series the pinned wheels are known to cover

function ConvertTo-MWArgLine {
    <#  Start-Process does not quote array arguments, so do it here. #>
    param([string[]]$Arguments = @())
    $parts = @()
    foreach ($a in $Arguments) {
        if ($a -match '[\s"]') { $parts += '"' + ($a -replace '"', '\"') + '"' }
        else { $parts += $a }
    }
    return ($parts -join ' ')
}

function Invoke-MWNative {
    <#  Run a native executable with a hard timeout, capture stdout+stderr and the
        exit code, and never throw. A half-installed interpreter that hangs must
        not hang the installer with it. #>
    param([Parameter(Mandatory)][string]$Exe, [string[]]$Arguments = @(), [int]$TimeoutSeconds = 30)

    $outFile = [System.IO.Path]::GetTempFileName()
    $errFile = [System.IO.Path]::GetTempFileName()
    try {
        $splat = @{ FilePath = $Exe; NoNewWindow = $true; PassThru = $true;
                    RedirectStandardOutput = $outFile; RedirectStandardError = $errFile;
                    ErrorAction = 'Stop' }
        $line = ConvertTo-MWArgLine -Arguments $Arguments
        if ($line) { $splat['ArgumentList'] = $line }
        $proc = Start-Process @splat
        # Touching the handle keeps ExitCode readable after the process ends;
        # without it Windows PowerShell hands back an empty exit code.
        $null = $proc.Handle
    } catch {
        Remove-Item $outFile, $errFile -ErrorAction SilentlyContinue
        return [pscustomobject]@{ Output = $_.Exception.Message; ExitCode = -1; TimedOut = $false }
    }

    $finished = $proc.WaitForExit($TimeoutSeconds * 1000)
    if (-not $finished) {
        try { $proc.Kill() } catch { }
        Remove-Item $outFile, $errFile -ErrorAction SilentlyContinue
        return [pscustomobject]@{ Output = "Timed out after $TimeoutSeconds s"; ExitCode = -2; TimedOut = $true }
    }

    # WaitForExit(ms) can leave ExitCode unpopulated; the parameterless call
    # after it settles the process object.
    try { $proc.WaitForExit() } catch { }
    try { $proc.Refresh() } catch { }
    $code = -1
    try { $code = [int]$proc.ExitCode } catch { }

    $text = ''
    foreach ($f in @($outFile, $errFile)) {
        $content = Get-Content $f -Raw -ErrorAction SilentlyContinue
        if ($content) { $text += $content }
    }
    Remove-Item $outFile, $errFile -ErrorAction SilentlyContinue
    return [pscustomobject]@{ Output = $text; ExitCode = $code; TimedOut = $false }
}

function Test-MWPython {
    <#  Probe one candidate path. Returns $null unless it is a real interpreter. #>
    param([Parameter(Mandatory)][string]$Path)

    # Only ever execute something that claims to be a Python. Probing an
    # arbitrary path would launch whatever program it points at.
    $leaf = ''
    try { $leaf = [System.IO.Path]::GetFileName($Path) } catch { return $null }
    if (-not $leaf -or $leaf -notmatch '^(?i)python[0-9.]*(\.exe)?$') { return $null }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }

    $probe = "import sys, importlib.util as u; print('MWPY|' + '|'.join([str(sys.version_info[0]), str(sys.version_info[1]), str(sys.version_info[2]), sys.executable, str(int(sys.maxsize > 2**32)), str(int(bool(u.find_spec('venv')) and bool(u.find_spec('ensurepip'))))]))"
    $r = Invoke-MWNative -Exe $Path -Arguments @('-c', $probe)
    if ($r.ExitCode -ne 0) { return $null }

    $line = ($r.Output -split "`n" | Where-Object { $_ -like 'MWPY|*' } | Select-Object -First 1)
    if (-not $line) { return $null }
    $f = $line.Trim() -split '\|'
    if ($f.Count -lt 7) { return $null }

    $version = [Version]("{0}.{1}.{2}" -f $f[1], $f[2], $f[3])
    return [pscustomobject]@{
        Path       = $f[4]
        Version    = $version
        Is64Bit    = ($f[5] -eq '1')
        HasVenv    = ($f[6] -eq '1')
        Supported  = ($version -ge $script:MW_MinPython)
        Preferred  = ($version -ge $script:MW_MinPython -and $version -le $script:MW_PreferredMax)
        IsStoreApp = ($f[4] -like '*\WindowsApps\*')
    }
}

function Get-MWPythonCandidatePaths {
    <#  Every place a Python may hide on a Windows box, best guesses first. #>
    $paths = New-Object System.Collections.Generic.List[string]
    $add = {
        param($p)
        if (-not $p) { return }
        $p = $p.Trim()
        # A candidate has to look like a path we could execute; the py launcher and
        # the registry both hand back junk on some machines.
        if ($p -match '[
	*?<>|"]' -or $p -notmatch '(?i)python[0-9.]*\.exe$') { return }
        if (-not $paths.Contains($p)) { $paths.Add($p) }
    }

    # 1. The py launcher knows about every registered install.
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($v in @('-3.13', '-3.12', '-3.11', '-3.10', '-3')) {
            $r = Invoke-MWNative -Exe $launcher.Source -Arguments @($v, '-c', 'import sys;print(sys.executable)')
            if ($r.ExitCode -eq 0) { & $add ($r.Output.Trim()) }
        }
    }

    # 2. Whatever is on PATH (stubs get filtered out by the probe).
    foreach ($name in @('python', 'python3')) {
        Get-Command $name -All -ErrorAction SilentlyContinue |
            ForEach-Object { if ($_.Source) { & $add $_.Source } }
    }

    # 3. The registry keys written by the python.org installer.
    foreach ($hive in @('HKLM:\SOFTWARE\Python\PythonCore',
                        'HKLM:\SOFTWARE\WOW6432Node\Python\PythonCore',
                        'HKCU:\SOFTWARE\Python\PythonCore')) {
        Get-ChildItem $hive -ErrorAction SilentlyContinue | ForEach-Object {
            $ip = Get-ItemProperty (Join-Path $_.PSPath 'InstallPath') -ErrorAction SilentlyContinue
            if ($ip) {
                if ($ip.ExecutablePath) { & $add $ip.ExecutablePath }
                $dir = $ip.'(default)'
                if ($dir) { & $add (Join-Path $dir 'python.exe') }
            }
        }
    }

    # 4. Common install folders, including conda and uv-managed interpreters.
    $globs = @(
        "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
        "$env:ProgramFiles\Python3*\python.exe",
        "${env:ProgramFiles(x86)}\Python3*\python.exe",
        "C:\Python3*\python.exe",
        "$env:USERPROFILE\anaconda3\python.exe",
        "$env:USERPROFILE\miniconda3\python.exe",
        "$env:USERPROFILE\AppData\Local\Programs\miniforge3\python.exe",
        "$env:APPDATA\uv\python\cpython-*\python.exe"
    )
    foreach ($g in $globs) {
        Get-ChildItem $g -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            ForEach-Object { & $add $_.FullName }
    }

    return $paths
}

function Find-MWPython {
    <#  Returns the best usable interpreter, or $null. Pass -Explicit to test one
        path only (the -Python argument of install.ps1).  #>
    param([string]$Explicit = "")

    if ($Explicit) {
        $p = $Explicit
        if (Test-Path $p -PathType Container) { $p = Join-Path $p 'python.exe' }
        $found = Test-MWPython -Path $p
        if ($found) { return $found }
        return $null
    }

    $found = @()
    foreach ($p in Get-MWPythonCandidatePaths) {
        $info = Test-MWPython -Path $p
        if ($info -and $info.Supported -and $info.HasVenv) { $found += $info }
    }
    if (-not $found) { return $null }

    # One entry per real executable, then: known-good series first, 64-bit first,
    # newest version first, plain installs ahead of the Store build.
    $found = $found | Sort-Object -Property Path -Unique
    $ranked = $found | Sort-Object `
        @{ Expression = { if ($_.Preferred) { 0 } else { 1 } } }, `
        @{ Expression = { if ($_.Is64Bit) { 0 } else { 1 } } }, `
        @{ Expression = { if ($_.IsStoreApp) { 1 } else { 0 } } }, `
        @{ Expression = { $_.Version }; Descending = $true }
    return $ranked[0]
}

function Get-MWPythonHelp {
    <#  The message shown when no usable interpreter exists. #>
    return @(
        "Meshwright needs Python 3.10 or newer (3.12 is the version we test against).",
        "",
        "  1. Download it from  https://www.python.org/downloads/windows/",
        "  2. In the installer, tick  [x] Add python.exe to PATH  before pressing Install.",
        "  3. Close this window, open a NEW one, and run install.bat again.",
        "",
        "If Windows printed 'Python was not found; run without arguments to install from",
        "the Microsoft Store', that is a placeholder shortcut, not Python. Turn it off in",
        "Settings > Apps > Advanced app settings > App execution aliases (switch off both",
        "python.exe and python3.exe), or simply install Python from the link above.",
        "",
        "Already installed somewhere unusual? Point the installer straight at it:",
        "  powershell -ExecutionPolicy Bypass -File install.ps1 -Python ""C:\Path\to\python.exe"""
    )
}
