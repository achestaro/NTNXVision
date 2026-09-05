<#
.SYNOPSIS
    NTNXVision - one-shot Windows setup (mirror of scripts/setup-macos.sh).

.DESCRIPTION
    Creates the Python venv with a *supported* interpreter (3.10-3.13), installs only the
    runtime dependencies (fast, wheel-only - no mypy/ruff/pyinstaller), then installs the
    Electron app. Run from the repo root:

        powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1

    Add -Dev to also install the dev tooling (pytest/ruff/mypy/pyinstaller).
#>
[CmdletBinding()]
param([switch]$Dev)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

Write-Host "==> NTNXVision setup (repo: $Root)"

# --- 1. Pick a supported Python (3.10-3.13) ---------------------------------
# Prefer the py launcher, which can enumerate every installed interpreter.
$pyExe = $null
$pyArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($v in '3.13', '3.12', '3.11', '3.10') {
        & py "-$v" -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { $pyExe = 'py'; $pyArgs = @("-$v"); break }
    }
}
if (-not $pyExe -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $ver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
    $minor = [int]($ver.Split('.')[1])
    if ($ver.StartsWith('3.') -and $minor -ge 10 -and $minor -le 13) { $pyExe = 'python' }
}
if (-not $pyExe) {
    Write-Error @"
No supported Python found (need 3.10-3.13).
Install one, e.g.:  winget install Python.Python.3.12
(Python 3.14 is not yet supported - some deps lack wheels.)
"@
}
Write-Host "==> Using $(& $pyExe @pyArgs --version)"

# --- 2. Python core venv + runtime deps -------------------------------------
Set-Location (Join-Path $Root 'core')
& $pyExe @pyArgs -m venv .venv
$venvPy = Join-Path $Root 'core\.venv\Scripts\python.exe'
& $venvPy -m pip install --quiet --upgrade pip

if ($Dev) {
    Write-Host '==> Installing core with dev tooling...'
    & $venvPy -m pip install -e ".[dev]"
} else {
    Write-Host '==> Installing core (runtime only)...'
    & $venvPy -m pip install -e .
}
Write-Host '==> Core installed. Smoke test:'
& (Join-Path $Root 'core\.venv\Scripts\ntnxvision.exe') version

# --- 3. Electron app --------------------------------------------------------
Set-Location (Join-Path $Root 'app')
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Write-Host '==> Installing Electron app dependencies...'
    npm install
    Write-Host ''
    Write-Host '==> Done. Launch the UI with:'
    Write-Host '      cd app; npm run dev'
} else {
    Write-Warning 'npm not found - skipped the Electron app.'
    Write-Warning 'Install Node 20+ (winget install OpenJS.NodeJS.LTS), then: cd app; npm install'
    Write-Warning 'You can still use the CLI:  core\.venv\Scripts\ntnxvision.exe --help'
}
