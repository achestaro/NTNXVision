# Electron app (`app/src/`)

Root `CLAUDE.md` rules apply here (IPC contract in `shared/ipc.ts`, keychain-only credentials).
`npm install` uses a prebuilt native keyring module (no compiler needed). `npm test` runs the
plain-Node main-process tests (`tests/*.test.mjs`).

## Main process (`main/`)

- `main.ts`: entry, window lifecycle, typed IPC handlers relaying renderer calls to the sidecar
  (camelCase to snake_case), run tracking, emits `core:progress` / `core:runFinished`. Reads demo
  mode (`--demo` / `NTNXVISION_DEMO=1`) and the e2e switches (`NTNXVISION_USER_DATA`,
  `NTNXVISION_RENDERER=dist` / `--dist`, `NTNXVISION_E2E`).
- `sidecar.ts`: spawns and supervises the Python sidecar (dev: venv Python; packaged: frozen
  PyInstaller binary from `resources/sidecar`), passes demo mode through the environment.
  `smoke.ts`: the packaging `--smoke` check.
- `secrets.ts`: credentials via `@napi-rs/keyring` (Windows Credential Manager / macOS Keychain);
  never write credentials to disk in plaintext. Demo mode and tests use an in-memory store
  (`NTNXVISION_KEYCHAIN=memory`).
- `keychain.ts`: keeps entries written by keytar readable (same service; on Windows keytar's
  `service/account` target and UTF-8 blob).
- `integrity.ts`: HMAC (key in the keychain as `integrity:key`) over `schedules.json` /
  `email.json`. An outside edit disables schedules and mail until the user re-saves.
- `store.ts`: settings in `userData`. `targets.ts`: saved targets (no passwords; stable ids,
  keychain accounts `target:<id>`, one-time migration from `connections.json`). Collections only
  run against saved targets, managed on the Targets page.
- `mail.ts`: Settings > Email (`userData/email.json`; password in the keychain as `smtp:password`,
  never sent back to the renderer).
- `schedules.ts`: schedules store (`schedules.json`, no secrets) and run queue (anchor per
  schedule: no double runs, missed slots catch up once); plain Node so `npm test` covers it. Slot
  maths in `shared/schedule.ts` (local time, DST-safe).
- `scheduler.ts`: wires schedules to the sidecar (collect fresh with `unique_name`, then
  `send_email`), 30 s timer + powerMonitor resume, tray / menu bar (window close hides while a
  schedule is enabled), notifications, Start at login.
- `preload.ts`: exposes the `window.ntnx` bridge (typed by `NtnxApi` in `shared/ipc.ts`). The
  window is sandboxed and a sandboxed preload may only `require('electron')`, so it is bundled by
  esbuild into `dist/main/preload.bundle.js` (`npm run build:preload`).

## Security layer

- `security.ts`: navigation lock, `secureIpc`, permission deny-all, production menu, and the
  folders the renderer may open / reveal / export to. **Every** `ipcMain.handle` must go through
  `secureIpc` (`tests/security.test.mjs` enforces it).
- `validate.ts` / `requests.ts`: type-check IPC arguments. `pathpolicy.ts`: pure open / reveal /
  export path policy. `testhooks.ts`: gates the `NTNXVISION_*` test overrides to unpackaged apps.
  `sidecarEnv.ts`: the packaged sidecar's environment allowlist.
- The renderer CSP is injected per mode by the Vite plugin in `vite.config.ts`.

## Renderer and scripts

- `renderer/`: React UI on the Runway design system (mocks in `docs/design/runway-mocks/`).
  `App.tsx` + `shell/` (command bar, rail, Cmd+K palette, shortcuts), `lib/` (router, run state
  from progress events, settings, formatting), `components/` (Runway primitives), `views/` (one per
  page, plus the Export dialog).
- `app/scripts/`: `e2e-demo.mjs` (real app + sidecar in demo mode via Playwright `_electron`),
  `screenshot.mjs` + `stub-ntnx.js` (renderer against a stub bridge).
