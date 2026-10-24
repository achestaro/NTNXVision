# Python core (`core/ntnxvision_core/`)

Root `CLAUDE.md` rules (read-only guard, API routing, Cluster-column-first, combined envs) apply here.
Single test: `pytest tests/test_excel_export.py -k foo`. CLI: `ntnxvision --help` (or `python -m ntnxvision_core`).

## Modules and their contracts

- `api/`: `connection.py` (Prism Central, v3 REST + version negotiation), `element_client.py`
  (direct Prism Element, v2.0 REST), `ndb_client.py` (Era API, separate base URL and auth),
  `transport.py` (read-only guard, demo routing), `v4_client.py` (per-target v4 capability
  probing behind `PrismConnection.v4_ready(area)`: collectors take the v4 path only when it
  answers yes), `vcenter_client.py` (optional read-only vCenter enrichment for ESXi clusters).
- `collectors/`: one module per product/feature area; each takes a connection and populates the
  shared `Environment`. Failures are isolated in `engine.py`: one broken collector never aborts
  the run, its error lands in `env.collection_notes`.
- `models/environment.py`: normalized, version-independent Pydantic model. v3 and v4 payloads map
  into the same schema.
- `areas.py`: collection-area catalogue; ids are shared with the UI, progress events and run
  metadata. `runinfo.py`: `RunTracker` (per-area status / API / objects / duration, typed
  progress events) and the run metadata stored on the snapshot (`Environment.run`).
- `cancel.py`: `CancelToken` / `RunCancelled`. `RunCancelled` is a `BaseException` so collector
  isolation cannot swallow it; the HTTP clients check the token before every request.
- `engine.py`: connect, collect (per collector, progress callback), resolve cross-references.
  Takes `areas=` (only those collectors run), `cancel=`, `tracker=`. Entry points:
  - `collect_environment`: Prism Central mode, all collectors + optional NDB step.
  - `collect_element_environment`: direct Prism Element mode, infrastructure only (DR, Flow,
    Files, NDB, IAM are PC-scoped and unavailable).
  - `combine_environments`: merges several PCs' runs (sidecar `inventory_multi`). List fields are
    concatenated, each object stamped with `source_pc`, per-PC identity / sizing / counts kept in
    `source_environments`. The result has **no** `pc_config` (see root rule on `env.is_combined`).
- `store/snapshot.py`: writes snapshots atomically so exports work fully offline, plus a small
  `.ntnxmeta.json` index per snapshot (summary, stats, run metadata, export history) so listing
  never parses big files; LRU `EnvironmentCache`.
- `inventory.py` (server-side filter/sort/page + facets, object detail with related objects),
  `compare.py` (UUID-based diff: field-level, grouped rows, summary figures, comparison export),
  `overview.py` (figures, coverage, API routing from the snapshot index), `connectivity.py`
  (read-only `test_connection` for PC / PE / NDB with outcome classification).
- `capacity.py`: per-cluster CPU / storage capacity and oversubscription (`X.X : 1`, full and N-1).
  The **only** place that math lives (`docs/ARCHITECTURE.md` §6); do not reimplement it elsewhere.
- `lcm_inventory.py`: optional "Run LCM inventory first" (off by default, per run), the tool's
  only non-read request (conditions in root Safety invariants). Reads each target's LCM status,
  starts or joins an inventory on PC and each selected cluster over the v4 lifecycle API (on
  pc.2024.1 / pc.2024.2 `LegacyLcmInventory` uses LCM's Genesis RPC), skips targets busy with an
  upgrade (reporting what it changes), polls tasks with backoff, timeout and cancel while the other
  areas run. LCM is collected last. Result on `Environment.lcm_inventory` and run metadata. Mock:
  `synthetic/lcm.py`.
- `synthetic/`: deterministic estates (`estate.py`), v4/v3/v2.0/Era renderers (`render.py`), mock
  API (`mockapi.py`, HTTPS front `server.py`, TLS minted at runtime by `certgen.py`), demo mode
  (`demo.py`, `profiles.py`, routed through `api/transport.py`).
- `exporters/`: `excel.py`, `pdf.py`, `word.py` share one pipeline: snapshot `Environment`,
  flattened rows/sections, format renderer. Word is the ADD template (the differentiator over a
  data dump). `tables.py` holds per-type column specs shared by the Excel sheets **and** the
  Inventory grid: change a column there, not in one view. `compare_pdf.py` reuses the inventory
  PDF renderer for the Compare export. `ExportOptions` carries PDF/Word section selection, the Word
  template (built-in or a customer `.dotx`) and the cover's prepared-for / author.
- `mailer.py`: stdlib `smtplib` for the sidecar's `test_smtp` / `send_email` (None / STARTTLS /
  SSL-TLS, error classification, attachment size guard, optional zip). Outbound SMTP is the only
  network use besides Nutanix APIs, only when the user configured a server; demo mode only sends to
  127.0.0.1. `synthetic/smtpsink.py` (`ntnxvision smtp-sink`) is a 127.0.0.1-only test sink.
- `compat.py`: supported range PC `pc.2024.1` to `7.5.1.6`, AOS `6.5` to `7.5`; warn-but-proceed.
- `sidecar.py`: stdio JSON-RPC server with the same operations as the CLI (`__main__.py`).
  Contract: `docs/IPC_CONTRACT.md`. Concurrency:
  - `ping` / `list_areas` / `cancel_run` run inline; everything else on a worker pool; stdout
    writes are locked.
  - Each collection run gets its own thread, one at a time: a second is refused with `-32002`.
    Multi-PC targets collect in parallel.
  - A cancelled run saves nothing (`-32800`); once it starts writing its snapshot it is committed.
