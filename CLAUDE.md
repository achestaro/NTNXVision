# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

NTNXVision — a cross-platform (Windows + macOS) inventory & documentation tool for Nutanix
Prism Central and its clusters (the "RVTools for Nutanix"). Electron/React UI shell driving a
Python core over local IPC; exports environment inventories to Excel, PDF, and a Word
Architecture Design Document. See `docs/ARCHITECTURE.md` for the full design (product→API
mapping, data model, roadmap) — read it before making structural changes.

## Repository layout

```
app/    Electron + React frontend (main process, renderer, IPC contract)
core/   Python core (api, collectors, models, store, exporters, engine)
docs/   Architecture & design documents (local only: gitignored, never committed; the repo is public)
build/  Packaging config (electron-builder + PyInstaller)
```

## Commands

### Python core (`core/`)
```bash
cd core
python -m venv .venv && .venv\Scripts\activate    # macOS: source .venv/bin/activate
pip install -e ".[dev]"
pytest                                    # run all tests
pytest tests/test_excel_export.py -k foo  # single test
ruff check .                              # lint
mypy ntnxvision_core                      # type check
ntnxvision --help                         # CLI (or: python -m ntnxvision_core --help)
ntnxvision --demo inventory --host pc01.demo.local --username admin --password demo
ntnxvision mock-server                    # serve the synthetic estate on 127.0.0.1
```

### Electron app (`app/`)
```bash
cd app
npm install          # prebuilt native keyring module, no compiler needed
npm run dev          # compiles main, starts Vite, launches Electron
npm run dev:demo     # the same, in demo mode (synthetic estate, no lab needed)
npm run demo         # build once and launch in demo mode
npm run e2e          # build, then drive the real app + sidecar end to end in demo mode
npm run screenshots  # renderer-only screenshots against the stub window.ntnx
npm run lint         # eslint
npm run build        # renderer + main → dist/
npm run package      # electron-builder → release/ (bundles the PyInstaller sidecar)
```

### Demo mode (no lab)
`NTNXVISION_DEMO=1` / `--demo` runs the real connection layer, collectors and exporters
against a local mock Nutanix API serving a synthetic estate (`pc01.demo.local`,
`pc02`, `pc03`, `pc04`, `pe01`, `ndb`, `selfsigned`, `down`, `large1..3`). Any non-empty password
except `wrong`. Each completed collection drifts that system one "week" so Compare has
changes. A first demo launch seeds saved targets and six backdated snapshots (`demo_seed`). Full host table and tests: `docs/DEVELOPMENT.md` §4.

### New machine / full dev setup
`docs/DEVELOPMENT.md` — prerequisites, `scripts/setup-macos.sh` / `scripts/setup-windows.ps1`,
pinned toolchain versions (`.python-version`, `.nvmrc`), and the per-user state that is
deliberately *not* in the repo (keychain credentials, snapshots, exports).

### Packaging
Versioning: semver in `app/package.json`, `core/pyproject.toml` and `ntnxvision_core.__version__`,
set together by `node scripts/bump-version.mjs patch|minor|major`. Every PR that changes shipped
code bumps it (patch = fixes, minor = features) and adds a `CHANGELOG.md` section; CI enforces both.

Freeze the Python core first (`pyinstaller build/ntnxvision-sidecar.spec` from `core/`,
requires `pip install -e ".[dev]"`), then `npm run package` from `app/`. See `build/README.md`.

## Architecture

**Electron + Python sidecar, not one language.** The Electron/React shell gives the
cross-platform point-and-click UX; the Python core owns both Nutanix data collection
(Python is Nutanix's lead SDK language) and the export engines (`openpyxl`, `python-docx`,
`fpdf2` have no equivalent in JS). They talk over local IPC (stdio JSON-RPC) — the sidecar
never opens an external port. The renderer never talks to Python directly; it only calls
the typed channels in `app/src/shared/ipc.ts`, which the main process relays to the sidecar.

### Python core (`core/ntnxvision_core/`)

- `api/` — connection layer: `connection.py` (Prism Central, v3 REST + version negotiation),
  `element_client.py` (direct Prism Element, v2.0 REST), `ndb_client.py` (Era API, separate
  base URL/auth), `errors.py`.
- `collectors/` — one module per Nutanix product/feature area (clusters, hosts, vms, storage,
  subnets, dataprotection, flow, nus, iam, xplay, ncm, licenses, ndb, element). Each collector
  takes a connection and populates the shared `Environment` model. Collector failures are
  isolated in `engine.py` — one broken collector never aborts the run; its error lands in
  `env.collection_notes` instead.
- `models/environment.py` — normalized, version-independent Pydantic data model. Both v3 and
  v4 API payloads map into the same schema (`Environment` → `PrismCentral`, `Cluster`, `Host`,
  `VM`, `DataProtection`, `FlowSecurity`, `Files`, `Objects`, `NDB`, `XPlay`, `NCM`, `IAM`,
  `Licensing`, …).
- `areas.py` — the collection-area catalogue (ids shared with the UI, progress events and
  run metadata); `runinfo.py` — `RunTracker` (per-area status / API / objects / duration,
  typed progress events) and the run metadata stored on the snapshot (`Environment.run`).
- `cancel.py` — `CancelToken` / `RunCancelled` (a `BaseException`, so collector isolation
  can't swallow it); the HTTP clients check the token before every request.
- `engine.py` — orchestrates connect → collect (per-collector, progress callback) → resolve
  cross-references → return `Environment`. Takes `areas=` (only those collectors run),
  `cancel=` and `tracker=`. Two entry points: `collect_environment` (Prism
  Central mode, all collectors + optional NDB step) and `collect_element_environment` (direct
  Prism Element mode, infrastructure-only — DR/Flow/Files/NDB/IAM are PC-scoped and unavailable
  in this mode). `combine_environments` merges several PCs' runs into one combined
  `Environment` (multi-PC mode, driven by the sidecar's `inventory_multi`): list fields are
  concatenated, each object stamped with its source PC in `source_pc`, and per-PC identity /
  sizing / counts kept in `source_environments`. A combined env has **no** `pc_config` — there
  is no single management plane — so exporters must branch on `env.is_combined` rather than
  render one PC's sizing as the estate's, and should attribute rows via `exporters/sourcing.py`.
- `store/snapshot.py` — persists a collected `Environment` to disk (atomically) so
  exports/re-exports work fully offline, plus a small `.ntnxmeta.json` index per snapshot
  (summary, stats, run metadata, export history) so listing never parses big files, and an
  LRU `EnvironmentCache`.
- `inventory.py` — server-side filter/sort/page + facets over a snapshot, and object
  detail with related objects; `compare.py` — UUID-based diff engine (field-level,
  grouped rows, summary figures) and comparison export; `overview.py` — Overview figures,
  coverage and API routing from the snapshot index; `connectivity.py` — read-only
  `test_connection` for PC / PE / NDB with outcome classification; `capacity.py` — per-cluster
  CPU / storage capacity and oversubscription (`X.X : 1`, full and N-1), the one place that
  math lives (see `docs/ARCHITECTURE.md` §6).
- `synthetic/` — deterministic synthetic estates (`estate.py`), v4/v3/v2.0/Era payload
  renderers (`render.py`), the mock Nutanix API (`mockapi.py`, HTTPS front in `server.py`,
  TLS material minted at runtime by `certgen.py`) and demo mode (`demo.py`, `profiles.py`,
  routed through `api/transport.py`).
- `exporters/` — `excel.py`, `pdf.py`, `word.py` share a pipeline: `Environment` (snapshot) →
  flattened rows/sections → format-specific renderer. Word is the ADD (Architecture Design
  Document) template — the differentiator over a plain data dump. `tables.py` holds the
  per-type column specs shared by the Excel sheets and the Inventory grid (change a column
  there, not in one view). Column-order rule: the Cluster column comes first in every
  table (grid, Excel, PDF, Word), enforced by `TableSpec` and `content.make_table`; only
  the VM tables opt out, and a combined run's "Prism Central" column follows Cluster
  (`tests/test_column_order.py`). `compare_pdf.py` renders the Compare page's PDF export
  with the inventory PDF's renderer. `ExportOptions` carries PDF/Word section selection, the Word
  template (built-in or a customer `.dotx`) and the cover's prepared-for / author.
- `mailer.py` — SMTP (stdlib `smtplib`) for the sidecar's `test_smtp` / `send_email`:
  message building, None / STARTTLS / SSL-TLS, error classification, attachment size guard
  and optional zip. Outbound SMTP is the only network use besides Nutanix APIs, and only
  when the user configured a server; demo mode only sends to 127.0.0.1.
  `synthetic/smtpsink.py` (`ntnxvision smtp-sink`) is a 127.0.0.1-only test sink.
- `compat.py` — version compatibility checks (Prism Central `pc.2024.1`–`7.5.1.6`, AOS
  `6.5`–`7.5`). **Warn-but-proceed**: out-of-range versions surface as non-blocking warnings;
  collection always continues.
- `sidecar.py` — stdio JSON-RPC server implementing the same operations as the CLI
  (`__main__.py`), driven by the Electron main process. Concurrent: `ping`/`list_areas`/
  `cancel_run` inline, each collection run on its own thread (one at a time; a second is
  refused with -32002), everything else on a worker pool, stdout writes locked. Multi-PC
  targets collect in parallel. A cancelled run saves nothing (-32800); once it starts
  writing its snapshot it is committed. Contract: `docs/IPC_CONTRACT.md`.

### API version strategy

v4 SDKs are GA only from `pc.2024.3`/AOS 7.0, so the tool's floor (`pc.2024.1`) requires v3
REST as the base today — v3 works across the whole supported range and isn't deprecated until
Nutanix's Q4-2026 legacy-API sunset. Route by feature area: v4 SDK when available and
supported by the target PC version, else v3 REST, else v2.0 REST for cluster-local data not in
PC. NDB always uses its own Era REST API. When adding a collector, follow this same routing
rule rather than assuming v4 is always available.

### Electron app (`app/src/`)

- `main/main.ts` — main process entry, window lifecycle, and the typed IPC handlers that
  relay renderer calls to the sidecar (camelCase → snake_case), track runs and emit
  `core:progress` / `core:runFinished`. Demo mode (`--demo` / `NTNXVISION_DEMO=1`) and the
  e2e switches (`NTNXVISION_USER_DATA`, `NTNXVISION_RENDERER=dist` / `--dist`,
  `NTNXVISION_E2E`) are read here.
- `main/sidecar.ts` — spawns and supervises the Python sidecar process (dev: venv Python;
  packaged: frozen PyInstaller binary from `resources/sidecar`); passes demo mode through
  the environment. `main/smoke.ts` — the packaging `--smoke` check.
- `main/secrets.ts` — credential storage via `@napi-rs/keyring` (Windows Credential Manager / macOS
  Keychain) — never write credentials to disk in plaintext. Demo mode and tests use an
  in-memory store instead (`NTNXVISION_KEYCHAIN=memory`). `main/store.ts` — settings
  in `userData`; `main/targets.ts` — saved targets (no passwords; stable ids, keychain
  accounts `target:<id>`, one-time migration from `connections.json`; `npm test`).
  Collections only run against saved targets, managed on the Targets page.
  `main/keychain.ts` keeps the entries keytar wrote readable (same service; Windows keeps
  keytar's `service/account` target and UTF-8 blob). `main/integrity.ts` — HMAC (key in the
  keychain as `integrity:key`) over `schedules.json` / `email.json`; an outside edit disables
  schedules and mail until the user re-saves.
- `main/mail.ts` — Settings > Email (userData `email.json`; password in the keychain as
  `smtp:password`, never back to the renderer). `main/schedules.ts` — schedules store
  (`schedules.json`, no secrets) and the run queue (anchor per schedule: no double runs,
  missed slots catch up once), plain Node for `npm test`; slot maths in
  `shared/schedule.ts` (local time, DST-safe). `main/scheduler.ts` — wires them to the
  sidecar (collect fresh with `unique_name`, then `send_email`), a 30 s timer +
  powerMonitor resume, the tray / menu bar (window close hides while a schedule is
  enabled), notifications and Start at login.
- `main/preload.ts` — exposes the `window.ntnx` bridge (typed by `NtnxApi` in `shared/ipc.ts`).
  Bundled by esbuild into `dist/main/preload.bundle.js` (`npm run build:preload`) because the
  window is sandboxed: a sandboxed preload may only `require('electron')`.
- `main/security.ts` — the hardening layer: navigation lock, `secureIpc` (**every**
  `ipcMain.handle` must go through it; `tests/security.test.mjs` enforces that), permission
  deny-all, production menu, and the folders the renderer may open/reveal/export to.
  `validate.ts` / `requests.ts` type-check IPC arguments, `pathpolicy.ts` is the pure
  open/reveal/export path policy, `testhooks.ts` gates the `NTNXVISION_*` test overrides to
  unpackaged apps, `sidecarEnv.ts` is the packaged sidecar's environment allowlist. The
  renderer CSP is injected per mode by the Vite plugin in `vite.config.ts`.
- `shared/ipc.ts` — the single source of truth for the IPC contract (channels, request/result
  types, progress events) shared between main and renderer. Update this first when adding a
  new operation, then implement it in both the sidecar (Python) and the main process relay,
  and document the sidecar side in `docs/IPC_CONTRACT.md`.
- `renderer/` — React UI on the Runway design system (mocks in `docs/design/runway-mocks/`).
  `App.tsx` + `shell/` (command bar, rail, ⌘K palette, shortcuts), `lib/` (router, run
  state from progress events, settings, formatting), `components/` (Runway primitives),
  and `views/`: Overview, Targets, New collection, Multiple PCs, Collecting, Results, Inventory
  (grid + detail pane), Snapshots, Compare, Settings, plus the Export dialog.
- `scripts/` — `e2e-demo.mjs` (real app + sidecar in demo mode, Playwright `_electron`),
  `screenshot.mjs` + `stub-ntnx.js` (renderer against a stub bridge).

### Safety invariants

- Collectors are **read-only** — GET/list calls only, never create/update/delete.
- Credentials go through the OS keychain, never plaintext to disk.
- IPC is local-only (stdio), no external listeners. Demo mode's mock Nutanix API is the
  only socket the sidecar opens: 127.0.0.1 only, opt-in, synthetic data only.
- Collect only what the area selection asks for; a cancelled run writes nothing.
- The repository is public: never commit real lab data (hostnames, IPs, usernames, emails,
  screenshots, snapshots, exported reports) or credentials. Use example values
  (`example.lab`, 192.0.2.x / 198.51.100.x) in tests; private material belongs in `docs/`.
