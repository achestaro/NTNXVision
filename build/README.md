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
a synthetic snapshot, which catches missing python-docx / fpdf2 / openpyxl data files, and
`--tls-check`, which fails when verified TLS has no trust store), runs
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
* **Windows arm64 MSI** contains the arm64 Electron shell and @napi-rs/keyring, but the **x64**
  sidecar (no arm64 Python on the CI runner); Windows 11 on ARM runs it under x64
  emulation. electron-builder 24's bundled WiX can't target arm64, so that MSI's own
  package architecture is x64 too. Both are expected.
* **@napi-rs/keyring** (credential storage, replaced the archived keytar) is a native N-API
  module with prebuilt per-platform binaries (`@napi-rs/keyring-<platform>-<arch>`, no
  compiler needed). They are unpacked from the asar (`asarUnpack: **/node_modules/@napi-rs/keyring*/**`).
  It reads and writes the same OS entries keytar did (service `NTNXVision`), so saved
  passwords keep working after an upgrade; see `app/src/main/keychain.ts`. npm installs only the
  host's binary package, so `scripts/build-windows.ps1` also installs the x64 and arm64 Windows ones
  (`npm install --no-save --force`) before packaging. The `--smoke`
  run fails if it can't load.
* **Sidecar path** (`app/src/main/sidecar.ts`, `packagedSidecarPath`):
  `Contents/Resources/sidecar/ntnxvision-sidecar` (macOS) or
  `resources\sidecar\ntnxvision-sidecar.exe` (Windows). The execute bit is restored if it
  was lost, the console window is hidden on Windows, and `NTNXVISION_SIDECAR=<path>`
  overrides the path for debugging in an unpackaged app only (a packaged app ignores every
  `NTNXVISION_*` test override except demo mode, and starts the sidecar with an allowlisted
  environment).

## File association (`.ntnxsnap`)

`build.fileAssociations` in `app/package.json` registers portable snapshot files
(`.ntnxsnap`): electron-builder writes `CFBundleDocumentTypes` into the macOS
`Info.plist` and a ProgId with an `open` verb (`"%1"`) into the MSI. Both use the app
icon. Double-clicking a file starts NTNXVision, or hands the file to the running copy,
and shows the import preview: macOS delivers `open-file`, Windows passes the path on the
command line (first launch) or through `second-instance` (the app holds a single-instance
lock per profile). The optional NSIS build is per-user (`nsis.perMachine: false`), where
electron-builder doesn't register associations: users pick NTNXVision under "Open with"
once, or use Import or drag and drop.

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

## Hardening: fuses and entitlements

* **Electron fuses** (`build.electronFuses` in `app/package.json`): `runAsNode`,
  `enableNodeOptionsEnvironmentVariable` and `enableNodeCliInspectArguments` off,
  `enableEmbeddedAsarIntegrityValidation`, `onlyLoadAppFromAsar` and `enableCookieEncryption`
  on. Verify a build with `npx @electron/fuses read --app release/mac-arm64/NTNXVision.app`.
  `grantFileProtocolExtraPrivileges` is expected off once the renderer loads over `app://`;
  CI (the "Verify Electron fuses" step) fails if it is on or any other fuse differs.
  The packaged app therefore cannot be started as plain Node, and `--smoke` does not need
  that; development runs use the unpackaged Electron, which has the default fuses.
* **macOS entitlements**: `entitlements.mac.plist` (main executable) and
  `entitlements.inherit.plist` (helpers) grant only `allow-jit`. The frozen sidecar is
  re-signed by `app/build/sign-sidecar.js` (an `afterSign` step, before notarization) with
  `entitlements.sidecar.plist`, which is empty on purpose (R2-08): every Mach-O in the bundle
  is signed with the same Team ID, so neither `disable-library-validation` nor
  `allow-unsigned-executable-memory` is needed (verified with a signed build and
  `scripts/smoke-sidecar.py`). Without a signing certificate the hook only seals the app
  ad-hoc, because flipping fuses leaves an unsigned arm64 app unlaunchable.

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
`app/build/entitlements.mac.plist` (JIT only) and, for the sidecar, the empty
`app/build/entitlements.sidecar.plist`. The keychain library needs no keychain entitlement
because the app isn't sandboxed.

**GitHub Environment secrets** consumed by `.github/workflows/release.yml`: `MAC_CSC_LINK`,
`MAC_CSC_KEY_PASSWORD`, `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`,
`WIN_CSC_LINK`, `WIN_CSC_KEY_PASSWORD`. Store them as secrets of an Environment named
`release`, not as repository secrets: the package jobs attach that environment only on `v*`
tag runs, so pull requests and manual (`workflow_dispatch`) runs from any branch get no
signing secrets and always build unsigned.

**One-time setup by the repo owner** (CI cannot create it): Settings > Environments > New
environment `release`; under Deployment branches and tags add a **tag** rule `v*`; optionally
add Required reviewers so each release build needs an approval; move the seven secrets above
into it and delete any repository-level copies (a repository secret of the same name would
still reach every run). Until the environment exists, the first tag run creates it with no
secrets and fails at the "signing secret is missing" check.

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
  (both modes include the trust store check below)
* Sidecar trust store: `ntnxvision-sidecar --tls-check` prints a JSON summary of what verified
  TLS trusts and exits 0 only when it is usable (macOS / Windows: the OS trust store through
  `truststore`, with at least one root; elsewhere the bundled certifi CAs). No network use.
  The frozen OpenSSL's own CA directory points at the build machine's Python install, so
  every verifying client builds its context through `ntnxvision_core/tlsctx.py`, never
  `ssl.create_default_context()`.
* Packaged app: `NTNXVision --smoke` (or `NTNXVISION_SMOKE=1`). It loads the keyring module, pings the
  sidecar through the normal `SidecarClient`, prints a JSON line and exits 0 or 1 without
  opening a window. From source: `npm run build && npm run smoke`.

## Security notes

* **Windows**: Credential Manager entries (saved target and SMTP passwords) have no per-app
  ACL. Any process running as the same Windows user can read them. NTNXVision cannot prevent
  that. Run it on a dedicated admin workstation account, not on a shared or daily-driver profile.
* **macOS**: the keychain item's access control is bound to the signed NTNXVision app, so
  another app asking for the item triggers a prompt. Unsigned local builds do not get that
  protection.
* Schedules and email settings (`schedules.json`, `email.json`) are protected against edits
  outside the app by an HMAC whose key lives in the OS keychain (`integrity:key`). A mismatch
  disables schedules and mail sending until the user reviews and re-saves them.
* On macOS/Linux the app's data folders are created owner-only (0700) and the files 0600.
  On Windows they rely on the per-user ACLs of `%APPDATA%`.

## Known limitations

* **MSI can only be built on Windows** (WiX runs natively; electron-builder's Wine path
  isn't supported here). On a Mac, use CI.
* DMG/ZIP for a given arch must be built on that arch. See above.
* No auto-update channel is configured (`--publish never`). Releases are draft-only.
* Icons in `app/build/` (and the tray icons and favicon) are generated from `app/build/art/*.svg`
  by `npm run icons` (in `app/`); edit the SVGs, re-run on macOS (for `icon.icns`) and commit the outputs.

## Legal notices

`LICENSE`, `NOTICE` and `THIRD_PARTY_NOTICES.md` ship in `resources/` (electron-builder
`extraResources`) and in the sidecar under `_internal/legal/`; the build scripts regenerate
`THIRD_PARTY_NOTICES.md` (`npm run notices`) right before electron-builder runs. fpdf2 is
collected as plain `.py` files (LGPL-3.0 replaceability). See `docs/LICENSING.md`.
