<#
.SYNOPSIS
  Bootstrap for the CORRECT Windows installer.

.DESCRIPTION
  1. Re-launches itself elevated if needed.
  2. Finds an all-users, 64-bit Python 3.11. If none is installed, downloads the
     pinned python.org 3.11.9 installer, verifies its SHA256, and installs it for
     all users (no PATH changes).
  3. Runs install_windows.py with that interpreter, passing all arguments through.
#>
$ErrorActionPreference = 'Stop'

$PythonVersion = '3.11.9'
$PythonUrl     = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
# SHA256 of python-3.11.9-amd64.exe (MD5 e8dcd502e34932eebcaf1be056d5cbcd matches python.org).
$PythonSha256  = '5ee42c4eee1e6b4464bb23722f90b45303f79442df63083f05322f1785f5fdde'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Host 'Requesting elevation...'
    $argList = @('-NoProfile', '-NoExit', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"") + ($args | ForEach-Object { "`"$_`"" })
    $p = Start-Process powershell.exe -Verb RunAs -ArgumentList $argList -Wait -PassThru
    exit $p.ExitCode
}

function Find-Python311 {
    # All-users installs register under HKLM. Per-user installs (HKCU) are ignored on
    # purpose: the service account could not read them.
    foreach ($key in @('HKLM:\SOFTWARE\Python\PythonCore\3.11\InstallPath')) {
        try {
            $exe = (Get-ItemProperty -Path $key -ErrorAction Stop).ExecutablePath
            if (-not $exe) { $exe = Join-Path (Get-ItemProperty -Path $key).'(default)' 'python.exe' }
            if ($exe -and (Test-Path $exe)) { return $exe }
        } catch { }
    }
    return $null
}

$python = Find-Python311
if (-not $python) {
    $dryRun = $args -contains '--dry-run'
    if ($dryRun) {
        Write-Host "[dry-run] Python 3.11 (all users) not found; would download and install $PythonUrl"
        exit 0
    }
    Write-Host "Python 3.11 (all users) not found. Downloading $PythonVersion from python.org..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $installer = Join-Path $env:TEMP "python-$PythonVersion-amd64.exe"
    Invoke-WebRequest -Uri $PythonUrl -OutFile $installer -UseBasicParsing
    $hash = (Get-FileHash -Algorithm SHA256 -Path $installer).Hash.ToLower()
    if ($hash -ne $PythonSha256) {
        Remove-Item $installer -Force
        throw "SHA256 mismatch for $installer (got $hash). Aborting."
    }
    Write-Host 'Installing Python for all users (this takes a minute)...'
    $p = Start-Process -FilePath $installer -Wait -PassThru -ArgumentList @(
        '/quiet', 'InstallAllUsers=1', 'PrependPath=0', 'Include_launcher=1',
        'Include_test=0', 'Include_tcltk=1', 'Include_pip=1', 'Shortcuts=0')
    Remove-Item $installer -Force
    if ($p.ExitCode -ne 0) { throw "Python installer exited with code $($p.ExitCode)" }
    $python = Find-Python311
    if (-not $python) { throw 'Python 3.11 still not found after install.' }
}

Write-Host "Using Python: $python"
& $python (Join-Path $PSScriptRoot 'install_windows.py') @args
exit $LASTEXITCODE
