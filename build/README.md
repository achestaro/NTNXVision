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

## macOS signing & notarization

Gatekeeper shows **"NTNXVision.app is damaged"** for any browser-downloaded build that
isn't Developer-ID-signed *and* notarized. Two ways out:

**Workaround (unsigned/dev builds):** clear the quarantine flag after download —
`xattr -cr /Applications/NTNXVision.app` (or on the .dmg before opening). Files copied
via AirDrop/USB/scp are never quarantined.

**Proper fix (requires Apple Developer Program, $99/yr, developer.apple.com/programs):**
1. In the developer portal create a **Developer ID Application** certificate; export it
   from Keychain Access as a `.p12` with a password.
2. Create an **app-specific password** for your Apple ID (account.apple.com → Sign-In and
   Security) and note your **Team ID** (developer portal → Membership).
3. Add GitHub repo secrets:
   - `MAC_CSC_LINK` — base64 of the .p12 (`base64 -i cert.p12 | pbcopy`)
   - `MAC_CSC_KEY_PASSWORD` — the .p12 password
   - `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`
4. Re-run the workflow. electron-builder signs with the hardened runtime
   (`app/build/entitlements.mac.plist`) and notarizes automatically when those env vars
   are present; the produced DMG then opens cleanly on any Mac.

Local builds sign with whatever identity is in your keychain (currently an
Apple Development cert — fine on this machine, still quarantine-blocked elsewhere).
