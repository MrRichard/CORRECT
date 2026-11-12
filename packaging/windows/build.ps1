<#
.SYNOPSIS
  Build the CORRECT Windows installer (CORRECT-Setup-<version>.exe).

.DESCRIPTION
  1. Downloads the pinned python-build-standalone CPython (runtime.lock) and verifies its SHA256.
  2. Installs requirements.txt into that private runtime (no system Python involved).
  3. Places pythonservice.exe + pywin32 DLLs next to python.exe so the service can start.
  4. Stages the CORRECT source under app\ and links it in with a relative .pth file.
  5. Compiles packaging\windows\correct.iss with Inno Setup 6 (ISCC.exe).

  Run on Windows from the repo root or anywhere:
      powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
      powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -SkipInstaller   # stage only

.OUTPUTS
  build\windows\stage\{runtime,app}   staged files
  dist\CORRECT-Setup-<version>.exe    installer
#>
param(
    [string]$OutDir = "",
    [switch]$SkipInstaller
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is much faster without it

$Root    = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Build   = Join-Path $Root 'build\windows'
$Cache   = Join-Path $Build 'cache'
$Stage   = Join-Path $Build 'stage'
$Runtime = Join-Path $Stage 'runtime'
$App     = Join-Path $Stage 'app'
if (-not $OutDir) { $OutDir = Join-Path $Root 'dist' }

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Invoke-Checked {
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}

# ── version + runtime pin ────────────────────────────────────────────────────
$init = Get-Content (Join-Path $Root 'netrt_core\__init__.py') -Raw
if ($init -notmatch '__version__\s*=\s*"([^"]+)"') { throw 'cannot read netrt_core.__version__' }
$Version = $Matches[1]

$lock = @{}
Get-Content (Join-Path $PSScriptRoot 'runtime.lock') | Where-Object { $_ -match '^\s*[A-Z0-9_]+=' } | ForEach-Object {
    $k, $v = $_ -split '=', 2; $lock[$k.Trim()] = $v.Trim()
}
foreach ($key in @('PBS_RELEASE', 'PYTHON_VERSION', 'ASSET', 'SHA256')) {
    if (-not $lock[$key]) { throw "runtime.lock is missing $key" }
}
Write-Host "CORRECT $Version  |  Python $($lock.PYTHON_VERSION) (python-build-standalone $($lock.PBS_RELEASE))"

# ── 1. runtime ──────────────────────────────────────────────────────────────
Step 'Fetching Python runtime'
New-Item -ItemType Directory -Force -Path $Cache | Out-Null
$archive = Join-Path $Cache $lock.ASSET
if (-not (Test-Path $archive)) {
    $url = "https://github.com/astral-sh/python-build-standalone/releases/download/$($lock.PBS_RELEASE)/$($lock.ASSET -replace '\+', '%2B')"
    Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing
}
$hash = (Get-FileHash -Algorithm SHA256 $archive).Hash.ToLower()
if ($hash -ne $lock.SHA256.ToLower()) {
    Remove-Item $archive -Force
    throw "SHA256 mismatch for $($lock.ASSET): got $hash, expected $($lock.SHA256)"
}

if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
$extract = Join-Path $Build 'extract'
if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
New-Item -ItemType Directory -Force -Path $extract | Out-Null
Invoke-Checked 'tar.exe' @('-xzf', $archive, '-C', $extract)
Move-Item (Join-Path $extract 'python') $Runtime
Remove-Item $extract -Recurse -Force
$py = Join-Path $Runtime 'python.exe'

# ── 2. dependencies ─────────────────────────────────────────────────────────
Step 'Installing Python dependencies into the runtime'
Invoke-Checked $py @('-m', 'ensurepip', '--upgrade')
Invoke-Checked $py @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-warn-script-location',
                     '--no-compile', '-r', (Join-Path $Root 'requirements.txt'))

# ── 3. pywin32 service host ─────────────────────────────────────────────────
Step 'Placing pythonservice.exe next to python.exe'
$sp = Join-Path $Runtime 'Lib\site-packages'
Copy-Item (Join-Path $sp 'win32\pythonservice.exe') $Runtime -Force
Get-ChildItem (Join-Path $sp 'pywin32_system32') -Filter '*.dll' | Copy-Item -Destination $Runtime -Force

# ── 4. application source ───────────────────────────────────────────────────
Step 'Staging CORRECT application files'
New-Item -ItemType Directory -Force -Path $App | Out-Null
foreach ($dir in @('netrt_core', 'correct_admin')) {
    Copy-Item (Join-Path $Root $dir) (Join-Path $App $dir) -Recurse
}
# (config.yaml is NOT shipped: the app writes one from correct_admin\templates on first launch.)
foreach ($file in @('main.py', 'DicomAnonymizer.py', 'README.md')) {
    Copy-Item (Join-Path $Root $file) $App
}
Copy-Item (Join-Path $Root 'docs\LICENSE') (Join-Path $App 'LICENSE.txt')
Copy-Item (Join-Path $Root 'docs\DISCLAIMER.md') (Join-Path $App 'DISCLAIMER.md')
# Relative .pth: resolved against site-packages, so the install folder can live anywhere.
Set-Content -Path (Join-Path $sp 'correct.pth') -Value '..\..\..\app' -Encoding ascii

# ── 5. slim down ────────────────────────────────────────────────────────────
Step 'Removing files not needed at runtime'
Get-ChildItem $Stage -Recurse -Force -Include '*.pdb', '__pycache__' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
foreach ($p in @('Lib\test', 'Lib\idlelib\idle_test', 'Lib\tkinter\test')) {
    $full = Join-Path $Runtime $p
    if (Test-Path $full) { Remove-Item $full -Recurse -Force }
}

# ── smoke check ─────────────────────────────────────────────────────────────
# (No double quotes in inline Python: Windows PowerShell 5.1 mangles them for native commands.)
Step 'Smoke-testing the staged runtime'
# Run from the stage folder so CORRECT must come from app\ via correct.pth, not the repo checkout.
Push-Location $Stage
Invoke-Checked $py @('-c', @'
import sys, tkinter, cv2, numpy, scipy, pydicom, pynetdicom, highdicom, rt_utils, ruamel.yaml
import win32serviceutil, servicemanager, win32security
import os, netrt_core.app, correct_admin.config_ui, correct_admin.service_control, correct_admin.winservice
from netrt_core.platform_paths import install_info
assert os.sep + 'stage' + os.sep + 'app' + os.sep in netrt_core.app.__file__, netrt_core.app.__file__
print('runtime OK:', sys.version.split()[0], '| app from', netrt_core.app.__file__)
'@)
Pop-Location

if ($SkipInstaller) { Write-Host "Staged at $Stage (installer skipped)"; exit 0 }

# ── 6. installer ────────────────────────────────────────────────────────────
Step 'Compiling installer with Inno Setup'
$iscc = (Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    foreach ($c in @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                     "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe")) {
        if (Test-Path $c) { $iscc = $c; break }
    }
}
if (-not $iscc) { throw 'ISCC.exe (Inno Setup 6) not found. Install it: winget install JRSoftware.InnoSetup' }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Invoke-Checked $iscc @("/DAppVersion=$Version", "/DStageDir=$Stage", "/DOutputDir=$OutDir",
                       (Join-Path $PSScriptRoot 'correct.iss'))
Write-Host "Built $(Join-Path $OutDir "CORRECT-Setup-$Version.exe")" -ForegroundColor Green
