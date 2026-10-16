<#
.SYNOPSIS
  One-command Windows build: venv + core install -> PyInstaller sidecar -> sidecar smoke
  test -> npm ci -> renderer/main build -> electron-builder MSI -> app smoke test.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1
  powershell -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1 -SkipTests -Nsis

.NOTES
  Signing is env-driven and optional (see build/README.md):
    WIN_CSC_LINK / CSC_LINK          Authenticode .pfx (base64 or path)
    WIN_CSC_KEY_PASSWORD / CSC_KEY_PASSWORD
  electron-builder downloads the WiX toolset itself on first MSI build (needs internet).
  Output: app\release\NTNXVision-<version>-win-x64.msi (and -arm64.msi unless -X64Only)
#>
[CmdletBinding()]
param(
  [switch]$SkipTests,  # skip pytest + eslint
  [switch]$NoSmoke,    # skip launching the packaged app with --smoke
  [switch]$X64Only,    # build only the x64 MSI
  [switch]$Nsis        # additionally build the NSIS .exe installer
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Magenta }
# Simple (non-advanced) function on purpose: native flags like -m / --x64 land in $args
# untouched instead of being parsed as PowerShell parameters.
function Invoke-Checked {
  $exe = $args[0]
  $rest = @($args | Select-Object -Skip 1)
  & $exe @rest
  if ($LASTEXITCODE -ne 0) { throw "$exe $($rest -join ' ') failed with exit code $LASTEXITCODE" }
}

# -- Python ---------------------------------------------------------------------
$py = $null
foreach ($v in '3.13', '3.12', '3.11', '3.10') {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    & py "-$v" -c "import sys" 2>$null
    if ($LASTEXITCODE -eq 0) { $py = @('py', "-$v"); break }
  }
}
if (-not $py) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) {
    & python -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,14) else 1)"
    if ($LASTEXITCODE -eq 0) { $py = @('python') }
  }
}
if (-not $py) { throw 'Need Python 3.10-3.13 (winget install Python.Python.3.13)' }

Step "Python core venv"
Set-Location (Join-Path $Root 'core')
$venvPy = Join-Path $PWD '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
  $pyArgs = @($py | Select-Object -Skip 1) + @('-m', 'venv', '.venv')
  Invoke-Checked $py[0] @pyArgs
}
Invoke-Checked $venvPy -m pip install --quiet --upgrade pip
Invoke-Checked $venvPy -m pip install --quiet -e '.[dev]'

if (-not $SkipTests) {
  Step 'Core tests'
  Invoke-Checked $venvPy -m pytest -q
}

Step 'Freeze sidecar (PyInstaller)'
Remove-Item -Recurse -Force dist\ntnxvision-sidecar, build\ntnxvision-sidecar -ErrorAction SilentlyContinue
Invoke-Checked (Join-Path $PWD '.venv\Scripts\pyinstaller.exe') --noconfirm --log-level WARN build\ntnxvision-sidecar.spec

Step 'Sidecar smoke test'
Invoke-Checked $venvPy (Join-Path $Root 'scripts\smoke-sidecar.py') dist\ntnxvision-sidecar\ntnxvision-sidecar.exe

# -- Electron -------------------------------------------------------------------
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'Need Node.js 20+ (winget install OpenJS.NodeJS.LTS)' }
Step "npm ci (node $(node -v))"
Set-Location (Join-Path $Root 'app')
Invoke-Checked npm ci --no-audit --no-fund

if (-not $SkipTests) {
  Step 'App lint'
  Invoke-Checked npm run lint
}

Step 'Build renderer + main'
Invoke-Checked npm run build

# An empty CSC_LINK makes electron-builder treat "" as a certificate path and abort.
foreach ($n in 'CSC_LINK', 'WIN_CSC_LINK') {
  if ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($n))) {
    Remove-Item "Env:$n" -ErrorAction SilentlyContinue
  }
}

$targets = @('msi')
if ($Nsis) { $targets += 'nsis' }
$archs = if ($X64Only) { @('--x64') } else { @('--x64', '--arm64') }
Step "electron-builder -> app\release ($($targets -join ', '); $($archs -join ' '))"
Invoke-Checked npx electron-builder --win @targets @archs --publish never

if (-not $NoSmoke) {
  Step 'Packaged app smoke test (--smoke)'
  $exe = Join-Path $PWD 'release\win-unpacked\NTNXVision.exe'
  $p = Start-Process -FilePath $exe -ArgumentList '--smoke' -Wait -PassThru -NoNewWindow
  if ($p.ExitCode -ne 0) { throw "Packaged app smoke test failed (exit $($p.ExitCode))" }
  Write-Host 'packaged app smoke test passed'
}

Step 'Done'
Get-ChildItem release\*.msi, release\*.exe -ErrorAction SilentlyContinue | Format-Table Name, Length
