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
- **Exports:** `openpyxl` (Excel), `weasyprint` (PDF), `python-docx` (Word ADD)
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

**Phase 1 (in progress):** connect → inventory clusters/hosts/VMs/storage/networking →
snapshot → Excel export, driven from the Electron UI.

See the roadmap in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#10-roadmap-phased).

## Getting started (dev)

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
npm install
npm run dev
```

## License

Proprietary — © Alberto Chestaro. Not affiliated with or endorsed by Nutanix, Inc.
