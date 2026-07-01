# Packaging (Windows + macOS)

NTNXVision ships as a single desktop app per OS: the Electron shell with the Python core
frozen (PyInstaller) and bundled as an `extraResource`.

## 1. Freeze the Python core → sidecar binary

```bash
cd core
pip install -e ".[dev]"          # includes pyinstaller
pyinstaller build/ntnxvision-sidecar.spec
# → core/dist/ntnxvision-sidecar/ntnxvision-sidecar(.exe)
```

## 2. Build & package the Electron app

```bash
cd app
npm install
npm run package                  # electron-builder → release/
```

`electron-builder` (config in `app/package.json` → `build`) copies
`core/dist/ntnxvision-sidecar` into the app under `resources/sidecar`. At runtime the
main process launches the frozen binary in packaged builds, or the dev venv Python otherwise.

## Targets

| OS      | Output | Signing |
|---------|--------|---------|
| Windows | NSIS installer (`.exe`) | Authenticode (configure cert) |
| macOS   | `.dmg` (arm64)          | Apple notarization (configure identity) |

Local packaged output lands in `app/release/` (git-ignored).

## CI

`.github/workflows/build-installers.yml` runs a windows-latest + macos-latest matrix:
PyInstaller freeze → sidecar ping smoke test → `npm run package`, and uploads the
installers as artifacts (`NTNXVision-windows-installer`, `NTNXVision-macos-dmg`).
Trigger manually (`gh workflow run build-installers.yml`) or push a `v*` tag.
CI builds are unsigned (`CSC_IDENTITY_AUTO_DISCOVERY=false`); configure certs later.
