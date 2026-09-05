#!/usr/bin/env bash
# NTNXVision — one-shot macOS setup.
#
# Creates the Python venv with a *supported* interpreter (3.10–3.13), installs only the
# runtime dependencies (fast, wheel-only — no mypy/ruff/pyinstaller), then installs the
# Electron app. Run from the repo root:
#
#     ./scripts/setup-macos.sh
#
# Add --dev to also install the dev tooling (pytest/ruff/mypy/pyinstaller).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV=0
[[ "${1:-}" == "--dev" ]] && DEV=1

echo "==> NTNXVision setup (repo: $ROOT)"

# ─── 1. Pick a supported Python (3.10–3.13) ────────────────────────────
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    ver="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    major="${ver%%.*}"; minor="${ver##*.}"
    if [[ "$major" == "3" && "$minor" -ge 10 && "$minor" -le 13 ]]; then
      PY="$cand"; break
    fi
  fi
done

if [[ -z "$PY" ]]; then
  echo "ERROR: No supported Python found (need 3.10–3.13)."
  echo "       Install one, e.g.:  brew install python@3.12"
  echo "       (Python 3.14 is not yet supported — some deps lack wheels.)"
  exit 1
fi
echo "==> Using $("$PY" --version) ($PY)"

# ─── 2. Python core venv + runtime deps ────────────────────────────────
cd "$ROOT/core"
"$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip

if [[ "$DEV" == "1" ]]; then
  echo "==> Installing core with dev tooling…"
  pip install -e ".[dev]"
else
  echo "==> Installing core (runtime only)…"
  pip install -e .
fi
echo "==> Core installed. Smoke test:"
ntnxvision version
deactivate

# ─── 3. Electron app ───────────────────────────────────────────────────
cd "$ROOT/app"
if command -v npm >/dev/null 2>&1; then
  echo "==> Installing Electron app dependencies…"
  npm install
  echo ""
  echo "==> Done. Launch the UI with:"
  echo "      cd app && npm run dev"
else
  echo "WARNING: npm not found — skipped the Electron app."
  echo "         Install Node 18+ (brew install node), then: cd app && npm install"
  echo "         You can still use the CLI:  cd core && source .venv/bin/activate && ntnxvision --help"
fi
