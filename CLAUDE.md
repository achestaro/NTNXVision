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
docs/   Architecture & design documents
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
```

### Electron app (`app/`)
```bash
cd app
npm install          # builds keytar (needs Xcode CLT on macOS)
npm run dev          # compiles main, starts Vite, launches Electron
npm run lint         # eslint
npm run build        # renderer + main → dist/
npm run package      # electron-builder → release/ (bundles the PyInstaller sidecar)
```

### Packaging
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
- `engine.py` — orchestrates connect → collect (per-collector, progress callback) → resolve
  cross-references → return `Environment`. Two entry points: `collect_environment` (Prism
  Central mode, all collectors + optional NDB step) and `collect_element_environment` (direct
  Prism Element mode, infrastructure-only — DR/Flow/Files/NDB/IAM are PC-scoped and unavailable
  in this mode).
- `store/snapshot.py` — persists a collected `Environment` to disk so exports/re-exports work
  fully offline without re-collecting.
- `exporters/` — `excel.py`, `pdf.py`, `word.py` share a pipeline: `Environment` (snapshot) →
  flattened rows/sections → format-specific renderer. Word is the ADD (Architecture Design
  Document) template — the differentiator over a plain data dump.
- `compat.py` — version compatibility checks (Prism Central `pc.2024.1`–`7.5.1.6`, AOS
  `6.5`–`7.5`). **Warn-but-proceed**: out-of-range versions surface as non-blocking warnings;
  collection always continues.
- `sidecar.py` — stdio JSON-RPC server implementing the same operations as the CLI
  (`__main__.py`), driven by the Electron main process.

### API version strategy

v4 SDKs are GA only from `pc.2024.3`/AOS 7.0, so the tool's floor (`pc.2024.1`) requires v3
REST as the base today — v3 works across the whole supported range and isn't deprecated until
Nutanix's Q4-2026 legacy-API sunset. Route by feature area: v4 SDK when available and
supported by the target PC version, else v3 REST, else v2.0 REST for cluster-local data not in
PC. NDB always uses its own Era REST API. When adding a collector, follow this same routing
rule rather than assuming v4 is always available.

### Electron app (`app/src/`)

- `main/main.ts` — main process entry, window lifecycle.
- `main/sidecar.ts` — spawns and supervises the Python sidecar process (dev: venv Python;
  packaged: frozen PyInstaller binary from `resources/sidecar`).
- `main/secrets.ts` — credential storage via `keytar` (Windows Credential Manager / macOS
  Keychain) — never write credentials to disk in plaintext.
- `main/preload.ts` — exposes the `window.ntnx` bridge (typed by `NtnxApi` in `shared/ipc.ts`).
- `shared/ipc.ts` — the single source of truth for the IPC contract (channels, request/result
  types, progress events) shared between main and renderer. Update this first when adding a
  new operation, then implement it in both the sidecar (Python) and the main process relay.
- `renderer/` — React UI: `App.tsx`, `views/ConnectView.tsx` (connection manager),
  `views/SnapshotsView.tsx` (snapshot browser/export).

### Safety invariants

- Collectors are **read-only** — GET/list calls only, never create/update/delete.
- Credentials go through the OS keychain, never plaintext to disk.
- IPC is local-only (stdio), no external listeners.
