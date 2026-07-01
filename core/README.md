# ntnxvision-core (NTNXVision core)

The Python core of **NTNXVision** — the Nutanix Prism Central inventory & documentation
engine. The importable package is `ntnxvision_core`. Provides the API layer (v4 SDK
preferred, v3/v2 REST fallback), collectors, normalized data model, snapshot store, and
exporters (Excel now; PDF + Word later).

It runs two ways:

- **CLI** — `ntnxvision inventory --host pc.example.com --username admin`
- **Sidecar** — `ntnxvision sidecar` (stdio JSON-RPC, driven by the Electron app)

See the top-level [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) for the full design.

## Dev setup

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate     macOS:  source .venv/bin/activate
pip install -e ".[dev]"
pytest
ntnxvision --help          # or:  python -m ntnxvision_core --help
```
