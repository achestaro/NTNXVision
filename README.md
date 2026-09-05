# NTNXVision

> A modern, cross-platform (Windows + macOS) inventory & documentation tool for
> **Nutanix Prism Central** and its clusters — the *"RVTools for Nutanix,"* extended to
> cover the full Nutanix Cloud Platform.

NTNXVision connects to one or more Prism Central instances, inventories everything
configured across the Nutanix Cloud Platform — infrastructure (NCI), data protection & DR,
Flow networking & microsegmentation, X-Play playbooks, Nutanix Files/Objects (NUS), and
Nutanix Database Service (NDB) — and exports the environment to **Excel**, **PDF**, and a
well-formatted **Word Architecture Design Document**.

## Architecture at a glance

- **UI shell:** Electron + React + TypeScript (modern, point-and-click, cross-platform)
- **Core:** Python — official Nutanix **v4 SDKs** with **v3 / v2 REST** fallback for
  features not yet in v4 (recovery plans, X-Play, some Flow rules)
- **Exports:** `openpyxl` (Excel), `fpdf2` (PDF), `python-docx` (Word ADD)
- **Safety:** read-only collection; credentials in the OS keychain; local-only IPC

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design, the Nutanix
product → API mapping, and the roadmap.

## Repository layout

```
app/    Electron + React frontend (main process, renderer, IPC contract)
core/   Python core (api, collectors, models, store, exporters, engine)
docs/   Architecture & design documents
build/  Packaging config (electron-builder + PyInstaller)
```

## Development status

**Phases 1–3 complete** (pending live-lab validation). Full Nutanix Cloud Platform
inventory — infrastructure, Data Protection/DR, Flow (security + VPC networking),
Files/Objects/Volumes, IAM, X-Play, NCM reports, and NDB — with a license/feature-usage
analysis and exports to **Excel**, **PDF**, and a **Word Architecture Design Document**,
driven from the Electron UI (with format picker and optional NDB collection).

See the roadmap in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#10-roadmap-phased).

## Getting started (dev)

New machine? [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) is the full setup guide for both
Windows and macOS (prerequisites, one-shot setup scripts, packaging, and what state is
*not* in the repo). Short version:

```bash
git clone https://github.com/achestaro/NTNXVision.git && cd NTNXVision
./scripts/setup-macos.sh --dev                                          # macOS
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1 -Dev  # Windows
cd app && npm run dev
```

Toolchain versions are pinned in `.python-version` (3.13) and `.nvmrc` (22) — the same
versions CI builds with.

### Python core
```bash
cd core
python -m venv .venv
# Windows:  .venv\Scripts\activate    macOS:  source .venv/bin/activate
pip install -e ".[dev]"
python -m ntnxvision_core --help
```

### Electron app
```bash
cd app
npm install          # builds keytar (needs Xcode CLT on macOS)
npm run dev          # compiles main, starts Vite, launches Electron
```

> **Validating on macOS?** See [`docs/RUNNING-ON-MACOS.md`](docs/RUNNING-ON-MACOS.md) for a
> step-by-step setup + run guide (prerequisites, CLI smoke test, UI walkthrough,
> troubleshooting).

## License

Proprietary — © Alberto Chestaro. Not affiliated with or endorsed by Nutanix, Inc.
