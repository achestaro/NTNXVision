# CLAUDE.md

NTNXVision: a Windows + macOS inventory and documentation tool for Nutanix Prism Central and
its clusters ("RVTools for Nutanix"). An Electron/React shell drives a Python core; exports to
Excel, PDF and a Word Architecture Design Document (ADD).

- `docs/` is local only (gitignored, never committed; the repo is public) and may be absent in
  a clone. When present: `docs/ARCHITECTURE.md` (design, product to API mapping, data model;
  read before structural changes), `docs/DEVELOPMENT.md` (new-machine setup via `scripts/setup-macos.sh` /
  `scripts/setup-windows.ps1`, pinned `.python-version` / `.nvmrc`, demo host table),
  `docs/IPC_CONTRACT.md`.
- Area guides load on demand: `core/CLAUDE.md` (Python core), `app/CLAUDE.md` (Electron app).

## Commands

```bash
# core/ (venv: python -m venv .venv; pip install -e ".[dev]")
pytest            ruff check .            mypy ntnxvision_core
ntnxvision --demo inventory --host pc01.demo.local --username admin --password demo
ntnxvision mock-server           # synthetic estate on 127.0.0.1
# app/
npm run dev:demo     # dev app in demo mode (no lab needed)
npm run demo         # build once, launch in demo mode
npm run e2e          # build, drive the real app + sidecar end to end in demo mode
npm run screenshots  # renderer only, against the stub window.ntnx
npm test             # main-process unit tests (node --test)
```

**Demo mode** (`NTNXVISION_DEMO=1` / `--demo`): real connection layer, collectors and exporters
against a local mock Nutanix API. Hosts: `pc01.demo.local`, `pc02`, `pc03`, `pc04`, `pe01`,
`ndb`, `selfsigned`, `down`, `large1..3`. Any non-empty password except `wrong`. Each completed
collection drifts that system one "week" so Compare has changes. A first demo launch seeds saved
targets and six backdated snapshots (`demo_seed`).

**Packaging order:** freeze the sidecar first (from `core/`: `pyinstaller build/ntnxvision-sidecar.spec`,
needs `pip install -e ".[dev]"`), then `npm run package` from `app/`. See `build/README.md`.

**Versioning:** semver in `app/package.json`, `core/pyproject.toml` and
`ntnxvision_core.__version__`, set together by `node scripts/bump-version.mjs patch|minor|major`.
Every PR that changes shipped code (paths in `scripts/check-version-bump.mjs`) bumps it
(patch = fixes, minor = features) and adds a `## <version>` section to `CHANGELOG.md`. CI enforces both.

## Git, CI and releases

- Never commit to `main`; work on a branch. Commit after each significant, self-contained change
  (it builds, and the relevant local checks pass), with an imperative subject line, then push the
  branch. Pushing a branch runs no CI: `release.yml` runs only on PRs into `main`, `v*` tags and
  manual dispatch.
- Verify locally (pytest, ruff, mypy, `npm run lint`, `npm test`, `npm run e2e` when the app
  flow changed). Do not use CI as the test runner.
- Keep CI runs few: open a PR into `main` only when a meaningful batch is ready, stack related
  branches into one PR, and push to an open PR sparingly (every push re-runs the full pipeline).
  One version bump per batch, not per commit. Docs-only changes need no bump.
- No `v*` tag or release build for small changes: release only when the user asks for one.

## Architecture

- Electron shell + Python sidecar over local stdio JSON-RPC. Python owns Nutanix collection and
  the export engines (`openpyxl`, `python-docx`, `fpdf2`). The sidecar never opens an external port.
- The renderer never talks to Python; it only calls the typed channels in
  `app/src/shared/ipc.ts`, which the main process relays to the sidecar.
- Adding an IPC operation: update `app/src/shared/ipc.ts` first, then the sidecar (Python), the
  main-process relay, and `docs/IPC_CONTRACT.md`.

## Cross-cutting rules

- **Cluster column first** in every table (Inventory grid, Excel, PDF, Word), enforced by
  `TableSpec` and `content.make_table`. Only the VM tables opt out. In a combined run the
  "Prism Central" column follows Cluster. Test: `core/tests/test_column_order.py`.
- **Combined (multi-PC) environments have no `pc_config`** (no single management plane).
  Exporters branch on `env.is_combined` instead of rendering one PC's sizing as the estate's,
  and attribute rows via `exporters/sourcing.py`.
- **API routing per feature area:** v4 SDK when available and supported by the target PC
  version, else v3 REST, else v2.0 REST for cluster-local data not in PC. NDB always uses its own
  Era REST API. The floor is `pc.2024.1` and v4 is GA only from `pc.2024.3` / AOS 7.0, so v3 is
  the base (it works across the supported range until Nutanix's Q4-2026 legacy-API sunset).
  Never assume v4 when adding a collector.
- **Compat is warn-but-proceed:** out-of-range PC / AOS versions are non-blocking warnings;
  collection always continues.

## Safety invariants

- Collectors are **read-only**: GET/list only, never create/update/delete. `api/transport.py`'s
  guard (`check_request`) enforces it on every session. Allowed: GET/HEAD, the v3 `list`/`groups`
  POSTs, the vCenter session login/logout (path match on any host) and the two read-only LCM
  Genesis RPCs (`get_config`, `is_lcm_operation_in_progress`, body checked). Anything else is
  refused before it is sent.
- The **one exception** is the opt-in LCM inventory, in one of two forms:
  `POST /api/lifecycle/v4.x/operations/$actions/inventory` (v4), or on pc.2024.1 / pc.2024.2
  `POST /PrismGateway/services/rest/v1/genesis` whose body is checked to be exactly the LCM
  framework `perform_inventory` RPC naming the catalogue URL that LCM's own `get_config` reply
  gave (Genesis is a generic RPC endpoint, so its path alone admits nothing; raw path, strict
  UTF-8 JSON, no duplicate keys). Either is allowed only inside `transport.lcm_inventory_permit()`,
  entered only by `lcm_inventory.py`, only when the user switched "Run LCM inventory first" on
  for that run (never by default, never from schedules). It refreshes LCM's view and upgrades
  nothing, and is never started on a target already running an LCM operation. Do not add another
  non-read request (`core/tests/test_lcm_inventory.py` fails on one).
- Credentials go through the OS keychain, never plaintext to disk.
- IPC is local-only (stdio), no external listeners. Demo mode's mock Nutanix API is the only
  socket the sidecar opens: 127.0.0.1 only, opt-in, synthetic data only.
- Collect only what the area selection asks for; a cancelled run writes nothing.
- The repository is public: never commit real lab data (hostnames, IPs, usernames, emails,
  screenshots, snapshots, exported reports) or credentials. Use example values (`example.lab`,
  192.0.2.x / 198.51.100.x) in tests; private material belongs in `docs/`.
