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
| macOS   | `.dmg` (universal)      | Apple notarization (configure identity) |

## CI (future)

A GitHub Actions matrix (windows-latest + macos-latest) can run PyInstaller then
electron-builder per-OS and attach installers to a release. Not committed yet.
