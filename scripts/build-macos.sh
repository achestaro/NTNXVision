#!/usr/bin/env bash
# One-command macOS build: venv + core install → PyInstaller sidecar → sidecar smoke test →
# npm ci → renderer/main build → electron-builder (.dmg + .zip) → app smoke test.
#
#   ./scripts/build-macos.sh                 # host architecture (arm64 or x64)
#   ./scripts/build-macos.sh --skip-tests    # skip pytest / lint
#   ./scripts/build-macos.sh --no-smoke      # skip launching the packaged app
#
# The sidecar is frozen by the venv's Python, so the output architecture is that Python's
# architecture (run under an x64 Python / Rosetta shell for an Intel build).
#
# Signing / notarization are env-driven and optional (see build/README.md):
#   CSC_LINK, CSC_KEY_PASSWORD      Developer ID Application .p12 (base64 or path)
#   APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD, APPLE_TEAM_ID   notarization
#   CSC_IDENTITY_AUTO_DISCOVERY=false   build unsigned even if a keychain identity exists
#
# Output: app/release/NTNXVision-<version>-mac-<arch>.dmg / .zip
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKIP_TESTS=0
SMOKE=1
for arg in "$@"; do
  case "$arg" in
    --skip-tests) SKIP_TESTS=1 ;;
    --no-smoke) SMOKE=0 ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }

[[ "$(uname -s)" == "Darwin" ]] || { echo "build-macos.sh must run on macOS" >&2; exit 1; }

# ── Python ────────────────────────────────────────────────────────────────────
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1 &&
     "$cand" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] < (3,14) else 1)'; then
    PY="$cand"; break
  fi
done
[[ -n "$PY" ]] || { echo "Need Python 3.10–3.13 (brew install python@3.13)" >&2; exit 1; }

step "Python core venv ($($PY -V))"
cd "$ROOT/core"
[[ -x .venv/bin/python ]] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -e ".[dev]"
ARCH="$(.venv/bin/python -c 'import platform; print(platform.machine())')"
case "$ARCH" in arm64|aarch64) EB_ARCH=--arm64 ;; x86_64) EB_ARCH=--x64 ;; *) echo "unsupported arch $ARCH" >&2; exit 1 ;; esac

if [[ $SKIP_TESTS -eq 0 ]]; then
  step "Core tests"
  .venv/bin/python -m pytest -q
fi

step "Freeze sidecar (PyInstaller, $ARCH)"
rm -rf dist/ntnxvision-sidecar build/ntnxvision-sidecar
.venv/bin/pyinstaller --noconfirm --log-level WARN build/ntnxvision-sidecar.spec

step "Sidecar smoke test"
.venv/bin/python "$ROOT/scripts/smoke-sidecar.py" dist/ntnxvision-sidecar/ntnxvision-sidecar

# ── Electron ──────────────────────────────────────────────────────────────────
command -v npm >/dev/null || { echo "Need Node.js 20+ (brew install node)" >&2; exit 1; }
step "npm ci (node $(node -v))"
cd "$ROOT/app"
npm ci --no-audit --no-fund

if [[ $SKIP_TESTS -eq 0 ]]; then
  step "App lint"
  npm run lint
fi

step "Third-party notices (this build's exact dependency set)"
npm run notices
cp "$ROOT/THIRD_PARTY_NOTICES.md" "$ROOT/core/dist/ntnxvision-sidecar/_internal/legal/"

step "Build renderer + main"
npm run build

step "electron-builder → app/release ($EB_ARCH)"
# An empty CSC_LINK makes electron-builder treat "" as a certificate path and abort.
[[ -z "${CSC_LINK:-}" ]] && unset CSC_LINK CSC_KEY_PASSWORD
npx electron-builder --mac "$EB_ARCH" --publish never

if [[ $SMOKE -eq 1 ]]; then
  step "Packaged app smoke test (--smoke)"
  APP="$(ls -d release/mac*/NTNXVision.app | head -1)"
  "$APP/Contents/MacOS/NTNXVision" --smoke
fi

step "Done"
ls -lh release/*.dmg release/*.zip
