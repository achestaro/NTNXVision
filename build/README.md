# Packaging (Windows MSI + macOS DMG/ZIP)

NTNXVision ships as one desktop app per OS: the Electron shell plus the Python core frozen
by PyInstaller into a **one-folder** sidecar, bundled as the `extraResource`
`resources/sidecar/`. Config lives in `app/package.json` → `build` and
`core/build/ntnxvision-sidecar.spec`.

## One-command builds

| OS | Command (from the repo root) | Output in `app/release/` |
|----|------------------------------|--------------------------|
| macOS | `./scripts/build-macos.sh` | `NTNXVision-<ver>-mac-<arm64\|x64>.dmg` and `.zip` |
| Windows | `powershell -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1` | `NTNXVision-<ver>-win-x64.msi`, `NTNXVision-<ver>-win-arm64.msi` |

Each script creates `core/.venv`, installs the core with `[dev]`, runs `pytest`, freezes the
sidecar, smoke-tests it (`scripts/smoke-sidecar.py`: `ping` plus an Excel/PDF/Word export of
a synthetic snapshot, which catches missing python-docx / fpdf2 / openpyxl data files), runs
`npm ci`, lint and build, calls electron-builder, and finally launches the packaged app with
`--smoke`. Flags: `--skip-tests` / `--no-smoke` (macOS), `-SkipTests` / `-NoSmoke` /
`-X64Only` / `-Nsis` (Windows).

Manual equivalent:

```bash
cd core && .venv/bin/pyinstaller --noconfirm build/ntnxvision-sidecar.spec  # → core/dist/ntnxvision-sidecar/
cd ../app && npm ci && npm run package:mac      # or: npm run package:win (Windows only)
```

`npm run package:win:nsis` builds the optional NSIS `.exe` (per-user) instead of the MSI.

## Architecture notes

* **Sidecar architecture = the Python that froze it.** PyInstaller can't cross-compile, so
  each macOS arch is built natively (CI: `macos-latest` arm64, `macos-15-intel` x64). No
  universal build: it would need universal2 wheels for every native dep.
* **Windows arm64 MSI** contains the arm64 Electron shell and keytar, but the **x64**
  sidecar (no arm64 Python on the CI runner); Windows 11 on ARM runs it under x64
  emulation. electron-builder 24's bundled WiX can't target arm64, so that MSI's own
  package architecture is x64 too. Both are expected.
* **keytar** is a native N-API module. It's unpacked from the asar
  (`asarUnpack: **/node_modules/keytar/**`), and electron-builder's `npmRebuild` fetches
  the prebuilt binary for each target arch at package time (`npm run rebuild:native` does it
  by hand). The `--smoke` run fails if keytar can't load.
* **Sidecar path** (`app/src/main/sidecar.ts`, `packagedSidecarPath`):
  `Contents/Resources/sidecar/ntnxvision-sidecar` (macOS) or
  `resources\sidecar\ntnxvision-sidecar.exe` (Windows). The execute bit is restored if it
  was lost, the console window is hidden on Windows, and `NTNXVISION_SIDECAR=<path>`
  overrides the path for debugging.

## Windows MSI behaviour

* **Per-machine install by default** (`msi.perMachine: true`). It goes to
  `C:\Program Files\NTNXVision` and needs elevation, which suits enterprise deployment
  (Intune, SCCM, GPO, `msiexec /i NTNXVision-…msi /qn`). Credentials stay per user in
  Windows Credential Manager, and snapshots stay in `%APPDATA%`. If you need a no-admin
  install, use the NSIS build (per-user).
* **Assisted UI** (`oneClick: false`): the user can pick the install folder. Start-menu
  and desktop shortcuts are always created. The app doesn't launch after install.
* **Upgrades:** the `upgradeCode` `9EE643E8-FE25-48DF-B14C-CEA7056CF80F` is pinned and
  **must never change**. A newer `version` in `app/package.json` installs as a major
  upgrade that removes the previous version. Installing the same version again does
  nothing, so bump the version for every release you ship. Downgrades are blocked.
* Manufacturer comes from `author`, the product name from `productName`, and the icon from
  `app/build/icon.ico`.

## Signing and notarization (all optional, env-driven)

Unsigned builds work. They just trigger Gatekeeper and SmartScreen warnings.

| Variable | Platform | Purpose |
|----------|----------|---------|
| `CSC_LINK`, `CSC_KEY_PASSWORD` | macOS | Developer ID Application `.p12` (base64 or path) |
| `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID` | macOS | notarization via `app/build/notarize.js` (afterSign hook) |
| `WIN_CSC_LINK`, `WIN_CSC_KEY_PASSWORD` (or `CSC_LINK`/`CSC_KEY_PASSWORD`) | Windows | Authenticode `.pfx` |
| `CSC_IDENTITY_AUTO_DISCOVERY=false` | both | ignore keychain identities, build unsigned |

The notarize hook **skips cleanly** when the three Apple variables aren't all set, or when the
app isn't signed with a Developer ID Application certificate (for example with the local
Apple Development identity). Hardened runtime is on, with entitlements in
`app/build/entitlements.mac.plist` (JIT, unsigned executable memory and library validation
off for the PyInstaller dylibs, network client). keytar needs no keychain entitlement
because the app isn't sandboxed.

**GitHub repo secrets** consumed by `.github/workflows/release.yml`: `MAC_CSC_LINK`,
`MAC_CSC_KEY_PASSWORD`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`,
`WIN_CSC_LINK`, `WIN_CSC_KEY_PASSWORD`.

Getting the Apple credentials (Apple Developer Program, $99/yr):
1. Create a **Developer ID Application** certificate and export it from Keychain Access as a
   `.p12`. Store `base64 -i cert.p12` as `MAC_CSC_LINK` and the password as
   `MAC_CSC_KEY_PASSWORD`.
2. Create an app-specific password at account.apple.com → Sign-In and Security, and find
   your Team ID under developer portal → Membership.

Until then, an unsigned, browser-downloaded DMG shows **"NTNXVision.app is damaged"**. Run
`xattr -cr /Applications/NTNXVision.app` to clear the quarantine flag.

## CI: `.github/workflows/release.yml`

Triggers: tags `v*`, `workflow_dispatch`, and pushes to `feature/packaging-msi`.

1. `checks` (ubuntu): `pytest` + `npm run lint`.
2. `build` matrix: `windows-latest` (MSI x64 + arm64), `macos-latest` (arm64),
   `macos-15-intel` (x64). Each job runs the platform build script, which includes the
   sidecar smoke test and the packaged-app `--smoke` launch. It then pings the sidecar
   *inside* the packaged app and uploads the artifacts `NTNXVision-windows-msi`,
   `NTNXVision-macos-arm64` and `NTNXVision-macos-x64`.
3. On a `v*` tag, all installers are attached to a **draft** GitHub Release. If an artifact
   upload fails (this repo has hit a stuck storage-quota error), the files go to a draft
   `ci-build-<n>` prerelease instead.

pip, npm, and the Electron and electron-builder (WiX) download caches are cached between runs.

## Smoke testing

* Sidecar: `python scripts/smoke-sidecar.py <path to ntnxvision-sidecar[.exe]> [--ping-only]`
* Packaged app: `NTNXVision --smoke` (or `NTNXVISION_SMOKE=1`). It loads keytar, pings the
  sidecar through the normal `SidecarClient`, prints a JSON line and exits 0 or 1 without
  opening a window. From source: `npm run build && npm run smoke`.

## Known limitations

* **MSI can only be built on Windows** (WiX runs natively; electron-builder's Wine path
  isn't supported here). On a Mac, use CI.
* DMG/ZIP for a given arch must be built on that arch. See above.
* No auto-update channel is configured (`--publish never`). Releases are draft-only.
* Icons in `app/build/` are placeholder art from `scripts/generate-icons.py`. Re-run it
  after changing the mark.
