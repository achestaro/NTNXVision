# NTNXVision

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> A modern, cross-platform (Windows + macOS) inventory & documentation tool for
> **Nutanix Prism Central** and its clusters: the *"RVTools for Nutanix,"* extended to
> cover the full Nutanix Cloud Platform.

NTNXVision connects to one or more Prism Central instances, inventories everything
configured across the Nutanix Cloud Platform: infrastructure (NCI), data protection & DR,
Flow networking & microsegmentation, X-Play playbooks, Nutanix Files/Objects (NUS), and
Nutanix Database Service (NDB). It exports the environment to **Excel**, **PDF**, and a
well-formatted **Word Architecture Design Document**.

## Architecture at a glance

- **UI shell:** Electron + React + TypeScript (modern, point-and-click, cross-platform)
- **Core:** Python, using the official Nutanix **v4 SDKs** with **v3 / v2 REST** fallback for
  features not yet in v4 (recovery plans, X-Play, some Flow rules)
- **Exports:** `openpyxl` (Excel), `fpdf2` (PDF), `python-docx` (Word ADD)
- **Safety:** read-only collection; credentials in the OS keychain; local-only IPC

The full design notes (Nutanix product → API mapping, data model, roadmap) are kept
privately and are not part of this repository.

## Repository layout

```
app/    Electron + React frontend (main process, renderer, IPC contract)
core/   Python core (api, collectors, models, store, exporters, engine)
build/  Packaging config (electron-builder + PyInstaller)
```

## Development status

**Phases 1–3 complete** (pending live-lab validation). Full Nutanix Cloud Platform
inventory (infrastructure, Data Protection/DR, Flow (security + VPC networking),
Files/Objects/Volumes, IAM, X-Play, NCM reports, and NDB), with a license/feature-usage
analysis and exports to **Excel**, **PDF**, and a **Word Architecture Design Document**,
driven from the Electron UI (with format picker and optional NDB collection).


## Getting started (dev)

New machine? The one-shot setup scripts are `scripts/setup-macos.sh` and
`scripts/setup-windows.ps1`. Short version:

```bash
git clone https://github.com/achestaro/NTNXVision.git && cd NTNXVision
./scripts/setup-macos.sh --dev                                          # macOS
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1 -Dev  # Windows
cd app && npm run dev
```

Toolchain versions are pinned in `.python-version` (3.13) and `.nvmrc` (22), the same
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
npm install          # prebuilt native keyring module, no compiler needed
npm run dev          # compiles main, starts Vite, launches Electron
```

## License

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

NTNXVision is released under the [MIT License](LICENSE), © 2026 Alberto Chestaro.

- [`NOTICE`](NOTICE): legal notice, covering no warranty, read-only operation, and the Nutanix
  trademark disclaimer.
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md): every third-party component shipped
  in the installers, with its license text. Generated: `cd app && npm run notices`.

Nutanix, Prism, AHV, AOS, Era/NDB, Flow, Files, Objects and related names and logos are
trademarks of Nutanix, Inc. NTNXVision is an independent project and is not affiliated
with, endorsed by, or sponsored by Nutanix, Inc.
