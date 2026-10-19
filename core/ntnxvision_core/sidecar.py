"""Stdio JSON-RPC sidecar.

The Electron main process spawns `ntnxvision_core sidecar` and communicates over stdin/stdout
using newline-delimited JSON-RPC 2.0 messages. This keeps the Python core free of any
network listener — nothing binds a port (demo mode's mock Nutanix server, which binds
127.0.0.1 only, is the single opt-in exception; see `synthetic.demo`).

Request  : {"jsonrpc":"2.0","id":1,"method":"inventory","params":{...}}
Response : {"jsonrpc":"2.0","id":1,"result":{...}}
Progress : {"jsonrpc":"2.0","method":"progress","params":{...}}   (notification, no id)
Error    : {"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"..."}}

Every method, with its params and result, is specified in docs/IPC_CONTRACT.md.

Concurrency
-----------
The reader thread never does slow work. ``ping``, ``list_areas`` and ``cancel_run`` are
answered inline; collection runs (``inventory``, ``inventory_cluster``,
``inventory_multi``) get a thread of their own; everything else (snapshot browsing,
exports, connection tests) runs on a small worker pool. Writes to stdout are serialised
by one lock, so responses and progress notifications from any thread never interleave.

One collection request runs at a time (a second is refused with -32002, as the UI shows
a single run); a multi-PC request collects its targets in parallel, each with its own
progress tagging.

Cancellation
------------
`cancel_run` sets the run's `CancelToken`. The connection layers check it before every
request and the engine between areas, so a run stops within one HTTP round-trip. A
cancelled run **saves nothing**: no snapshot, no reports, and the request fails with
-32800. Once a run has started writing its snapshot it is committed and `cancel_run`
answers ``{"cancelled": false}`` — a half-written snapshot is never left behind.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ntnxvision_core import __version__
from ntnxvision_core import areas as area_catalogue
from ntnxvision_core.api import PrismCredentials
from ntnxvision_core.api.element_client import ElementCredentials
from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.api.ndb_client import NdbCredentials
from ntnxvision_core.api.transport import require_pin
from ntnxvision_core.api.vcenter_client import VcenterCredentials
from ntnxvision_core.cancel import CancelToken, RunCancelled
from ntnxvision_core.capacity import MAX_VCPU_RATIO, MIN_VCPU_RATIO, apply_targets, valid_ratio
from ntnxvision_core.engine import (
    collect_element_environment,
    collect_environment,
    combine_environments,
)
from ntnxvision_core.exporters import export_environment, extension_for
from ntnxvision_core.exporters.naming import report_path
from ntnxvision_core.paths import demo_enabled, exports_dir, snapshots_dir
from ntnxvision_core.runinfo import CombinedTracker, RunTracker, build_run_metadata
from ntnxvision_core.store import SnapshotStore, transfer
from ntnxvision_core.store import snapshot as snapstore
from ntnxvision_core.store.names import max_stem_for, safe_name, unique_name
from ntnxvision_core.versions import pc_version

CANCELLED = -32800
BUSY = -32002
API_ERROR = -32000
OTHER_ERROR = -32001
#: Snapshot files (`export_snapshot_file`, `inspect_snapshot_file`, `import_snapshot_file`).
INVALID_FILE = -32010
NEWER_VERSION = -32011
PASSWORD_ERROR = -32012
TRANSFER_ERROR = -32013
#: The snapshot name is missing, unusable or already taken (checked before collecting).
INVALID_NAME = -32003

_FORMAT_LABELS = {"excel": "Excel workbook", "pdf": "PDF report", "word": "Word ADD"}


class Busy(Exception):
    """A collection is already running."""


class InvalidName(Exception):
    """A collection's snapshot name is missing, unusable or already used."""


# ─── wire ──────────────────────────────────────────────────────────────

_write_lock = threading.Lock()


def _send(message: dict[str, Any]) -> None:
    data = json.dumps(message, default=str) + "\n"
    with _write_lock:
        sys.stdout.write(data)
        sys.stdout.flush()


def _notify(method: str, params: dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "method": method, "params": params})


def _error(req_id: Any, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def _emit_progress(payload: dict[str, Any]) -> None:
    _notify("progress", payload)


# ─── runs ──────────────────────────────────────────────────────────────


class RunHandle:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.token = CancelToken()
        self.committed = False
        self._lock = threading.Lock()

    def cancel(self) -> bool:
        with self._lock:
            if self.committed:
                return False
            self.token.cancel()
            return True

    def commit(self) -> None:
        """Point of no return: from here the run writes its results and cannot be
        cancelled. Raises `RunCancelled` if a cancel arrived first."""
        with self._lock:
            self.token.check()
            self.committed = True


class RunRegistry:
    def __init__(self, max_concurrent: int = 1) -> None:
        self.max_concurrent = max_concurrent
        self._runs: dict[str, RunHandle] = {}
        self._lock = threading.Lock()

    def begin(self, run_id: str | None) -> RunHandle:
        with self._lock:
            run_id = run_id or uuid.uuid4().hex
            existing = self._runs.get(run_id)
            if existing is not None:
                return existing
            if len(self._runs) >= self.max_concurrent:
                raise Busy("A collection is already running")
            handle = RunHandle(run_id)
            self._runs[run_id] = handle
            return handle

    def get(self, run_id: str | None) -> RunHandle | None:
        with self._lock:
            return self._runs.get(run_id or "")

    def end(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)

    def cancel(self, run_id: str | None) -> bool:
        handle = self.get(run_id)
        return handle.cancel() if handle else False

    def cancel_all(self) -> None:
        with self._lock:
            handles = list(self._runs.values())
        for h in handles:
            h.cancel()

    def active(self) -> bool:
        with self._lock:
            return bool(self._runs)


_runs = RunRegistry()
_cache = snapstore.EnvironmentCache()
_inventory: Any = None
_inventory_lock = threading.Lock()


def _inventory_service() -> Any:
    global _inventory
    with _inventory_lock:
        if _inventory is None:
            from ntnxvision_core.inventory import InventoryService

            _inventory = InventoryService()
        return _inventory


# ─── dispatch ──────────────────────────────────────────────────────────

_RUN_METHODS = {"inventory", "inventory_cluster", "inventory_multi"}
_INLINE_METHODS = {"ping", "list_areas", "cancel_run"}


def serve_stdio() -> None:
    """Read JSON-RPC requests from stdin, one per line, until EOF."""
    if demo_enabled():
        from ntnxvision_core.synthetic import demo

        demo.enable()
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rpc")
    threads: list[threading.Thread] = []
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                _send({"jsonrpc": "2.0", "id": None,
                       "error": {"code": -32700, "message": "Parse error"}})
                continue
            if not isinstance(request, dict):
                _error(None, -32600, "Invalid request")
                continue
            method = request.get("method")
            if method in _INLINE_METHODS:
                _handle(request)
            elif method in _RUN_METHODS:
                params = request.get("params") or {}
                try:
                    handle = _runs.begin(params.get("run_id"))
                except Busy as exc:
                    _error(request.get("id"), BUSY, str(exc))
                    continue
                params["run_id"] = handle.run_id
                request["params"] = params
                t = threading.Thread(target=_handle, args=(request,), daemon=True,
                                     name=f"run-{handle.run_id[:8]}")
                t.start()
                threads.append(t)
                threads = [x for x in threads if x.is_alive()]
            else:
                pool.submit(_handle, request)
    finally:
        # stdin closed: the app is quitting. Stop runs at their next request.
        _runs.cancel_all()
        pool.shutdown(wait=False, cancel_futures=True)


def _handle(request: dict[str, Any]) -> None:
    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    try:
        handler = _METHODS.get(method) if isinstance(method, str) else None
        if handler is None:
            _error(req_id, -32601, f"Method not found: {method}")
            return
        result = handler(req_id, params)
        _send({"jsonrpc": "2.0", "id": req_id, "result": result})
    except RunCancelled:
        _error(req_id, CANCELLED, "Run cancelled")
    except Busy as exc:
        _error(req_id, BUSY, str(exc))
    except InvalidName as exc:
        _error(req_id, INVALID_NAME, str(exc))
    except NutanixApiError as exc:
        _error(req_id, API_ERROR, str(exc))
    except transfer.TransferError as exc:
        _error(req_id, _transfer_code(exc), str(exc))
    except Exception as exc:  # noqa: BLE001
        _error(req_id, OTHER_ERROR, f"{type(exc).__name__}: {exc}")


# ─── simple methods ────────────────────────────────────────────────────


def _m_ping(_req_id: Any, _params: dict[str, Any]) -> dict[str, Any]:
    return {"pong": True, "version": __version__, "demo": demo_enabled()}


def _m_list_areas(_req_id: Any, _params: dict[str, Any]) -> dict[str, Any]:
    return area_catalogue.catalogue()


def _m_cancel_run(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"cancelled": _runs.cancel(params.get("run_id"))}


def _m_test_connection(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    from ntnxvision_core.connectivity import test_connection

    return test_connection(params)


def _m_list_clusters(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    from ntnxvision_core.clusterlist import list_clusters

    return list_clusters(params)


def _m_demo_info(_req_id: Any, _params: dict[str, Any]) -> dict[str, Any]:
    if not demo_enabled():
        return {"enabled": False, "hosts": []}
    from ntnxvision_core.synthetic import demo

    return {"enabled": True, "hosts": demo.hosts()}


# ─── collection ────────────────────────────────────────────────────────


def snapshot_name_for(raw: Any, store: SnapshotStore | None = None,
                      taken: set[str] | None = None, fallback: str | None = None,
                      *, unique: bool = False) -> str:
    """The snapshot name a collection will be saved under, or `InvalidName`.

    The user types the name (no date is added: report files carry the export date).
    It is sanitized like every snapshot file name (reserved characters, trimmed, capped),
    and a name another snapshot already uses is refused, ignoring case, before anything
    is collected. Snapshot ids stay unique regardless (each save gets a fresh UUID).
    `taken` holds names claimed earlier in the same request (multi-PC targets).

    `fallback` (multi-PC API callers that send no name: the host) is used as given and
    never refused; the save then appends -2, -3 rather than replace a snapshot.

    `unique` (scheduled runs, which reuse one name) takes the next free name instead of
    refusing a taken one: ``name``, ``name-2``, ``name-3``, ... as the store's save does.
    """
    text = str(raw or "").strip()
    store = store or _store()
    if not text and fallback:
        return safe_name(fallback, max_len=max_stem_for(store.directory))
    if not text:
        raise InvalidName("Enter a snapshot name.")
    safe = safe_name(text, max_len=max_stem_for(store.directory), fallback="")
    if not safe:
        raise InvalidName("The snapshot name has no usable characters.")
    if unique:
        return unique_name(safe, [*store.taken_names(), *(taken or set())],
                           max_len=max_stem_for(store.directory))
    if store.name_taken(safe) or safe.casefold() in (taken or set()):
        raise InvalidName(f"A snapshot named '{safe}' already exists. Choose another name.")
    return safe


def _export_dir_from(params: dict[str, Any]) -> Path:
    """The export target directory — the user's configured folder or the default.

    Falls back to the default exports dir if the configured one cannot be created.
    """
    raw = params.get("export_dir")
    if isinstance(raw, str) and raw.strip():
        candidate = Path(raw).expanduser()
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            pass
    return exports_dir()


def _export_options(params: dict[str, Any]) -> dict[str, Any]:
    word = params.get("word") or {}
    return {
        "sections": params.get("sections"),
        "chapters": params.get("chapters"),
        "section_order": params.get("section_order"),
        "template": word.get("template") or "standard",
        "prepared_for": word.get("prepared_for") or "",
        "author": word.get("author") or "",
        "vcpu_ratio": params.get("vcpu_ratio"),
    }


def _file_prefix(params: dict[str, Any]) -> str:
    """The user's report file name prefix (sanitized when the name is built)."""
    raw = params.get("file_prefix")
    return raw if isinstance(raw, str) else ""


def _write_reports(env: Any, name: str, formats: list[str], out_dir: Path,
                   options: dict[str, Any], tracker: RunTracker | CombinedTracker | None = None,
                   prefix: str = "", snapshot: Path | None = None
                   ) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Write each format as ``<prefix>-<name>-<YYYY-MM-DD>.<ext>`` (see exporters.naming),
    never overwriting an existing file. The figures of a saved `snapshot` draw the
    diagram edits saved on the Diagrams page."""
    if snapshot is not None:  # every format: the diagrams bundle draws them too
        from ntnxvision_core.diagrams import store as diagram_store

        options = {**options, "diagram_models": diagram_store.edited_models(snapshot, env),
                   "diagram_origins": _carried_names(snapshot)}
    files: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for fmt in formats:
        if tracker is not None:
            tracker.phase("export", f"Writing {_FORMAT_LABELS.get(fmt, fmt)}…")
        try:
            ext = extension_for(fmt)
            stem = report_path(out_dir, name, ext, prefix).stem if ext else name
            path, detail = export_environment(env, fmt, out_dir, stem, options=options,
                                              with_detail=True)
            files.append(_report_file(fmt, Path(path), detail))
        except Exception as exc:  # noqa: BLE001 — one failed format shouldn't lose the run
            errors[fmt] = str(exc)
    return files, errors


def _report_file(fmt: str, path: Path, detail: str | None) -> dict[str, Any]:
    try:
        size: int | None = path.stat().st_size
    except OSError:
        size = None
    return {"format": fmt, "path": str(path), "name": path.name, "size_bytes": size,
            "detail": detail}


def _finalize(env: Any, name: str, params: dict[str, Any], *,
              tracker: RunTracker | CombinedTracker | None = None, mode: str | None = None,
              handle: RunHandle | None = None) -> dict[str, Any]:
    """Snapshot, export requested formats, and assemble the JSON-RPC result.

    A snapshot-save failure or a per-format export failure is captured as a warning /
    structured error rather than aborting the whole request — a successful collection is
    never discarded because a downstream write failed.
    """
    if handle is not None:
        handle.commit()
    if tracker is not None and tracker.finished_at is None:
        tracker.finish()
    # Every fresh collection replays the source's saved diagram edits when they all
    # apply, whatever it exports now: a schedule collects first and writes its reports
    # later (send_email), and those reports must draw the edits too.
    carry = _auto_carry_plan(env)
    env.run = build_run_metadata(env, tracker, mode=mode)
    result: dict[str, Any] = {
        "summary": env.summary(),
        "source_type": env.source_type,
        "prism_central": {**env.prism_central.model_dump(),
                          "version": env.prism_central.version
                          if env.source_type == "prism_element"
                          else pc_version(env.prism_central.version)},
        "warnings": list(env.warnings),
        "collection_notes": env.collection_notes,
        "snapshot_path": None,
        "snapshot_name": name,
        "exports": {},
        "export_errors": {},
    }

    if tracker is not None:
        tracker.phase("snapshot", "Saving snapshot…")
    snap_path: Path | None = None
    try:
        snap_path = SnapshotStore(snapshots_dir()).save(env, name, unique=True)
        result["snapshot_path"] = str(snap_path)
        result["snapshot_name"] = snapstore.snapshot_name(snap_path)
        _cache.invalidate(snap_path)
        if carry is not None:
            failed = _auto_carry(carry.offer, snap_path, env) if carry.apply else None
            if carry.apply and not failed:
                result["diagram_carry"] = {
                    "from": carry.offer["snapshot"].get("name"),
                    "diagrams": [d["title"] for d in carry.offer["diagrams"]]}
            else:  # where the edits came from: a later collection can still carry them
                with contextlib.suppress(Exception):
                    from ntnxvision_core.diagrams import store as diagram_store

                    diagram_store.mark_carry_failed(snap_path, carry.offer["snapshot"])
            if failed:
                env.warnings.append(failed)
                result["warnings"].append(failed)
                with contextlib.suppress(Exception):  # keep the warning with the snapshot
                    SnapshotStore(snapshots_dir()).save(env, result["snapshot_name"],
                                                        path=snap_path)
                    _cache.invalidate(snap_path)
    except Exception as exc:  # noqa: BLE001 — don't lose a successful run on a save error
        result["warnings"].append(f"Snapshot could not be saved: {exc}")

    formats = params.get("formats")
    if formats is None:
        formats = ["excel"] if params.get("export", True) else []
    files, errors = _write_reports(env, result["snapshot_name"] or name, list(formats),
                                   _export_dir_from(params), _export_options(params), tracker,
                                   prefix=_file_prefix(params), snapshot=snap_path)
    result["exports"] = {f["format"]: f["path"] for f in files}
    result["export_errors"] = errors
    if "excel" in result["exports"]:
        result["excel_path"] = result["exports"]["excel"]
    if snap_path is not None and files:
        snapstore.record_exports(snap_path, [{**f, "run": True} for f in files])

    run = dict(env.run or {})
    run["reports"] = files
    result.update({
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "duration_ms": run.get("duration_ms"),
        "areas": run.get("areas") or [],
        "reports": files,
        "figures": run.get("figures") or [],
        "api_routing": run.get("api_routing") or [],
        "compat": run.get("compat"),
        "scope": run.get("scope"),
    })
    return result


@dataclass
class _CarryPlan:
    offer: dict[str, Any]
    #: Every edit applies: replay them on the new snapshot.
    apply: bool


def _auto_carry_plan(env: Any) -> _CarryPlan | None:
    """Saved diagram edits for a fresh collection (`_carry_offer`). When every edit
    applies to the new data they are replayed on the new snapshot (its reports draw
    them); otherwise the reports draw the generated diagrams, the new snapshot records
    where the edits came from (so a later collection can still carry them), and the run
    says why in a warning, also in the email, unless the previous run already said so."""
    try:
        key = _source_key(snapstore.describe(env, Path("new.ntnxsnap.json")))
        offer = _carry_offer(key, env.collected_at, None, env)
    except Exception:  # noqa: BLE001 — never fail a collection over diagram edits
        return None
    if offer is None:
        return None
    edits = sum(d["edits"] for d in offer["diagrams"])
    bad = sum(d["edits"] - d["applies"] for d in offer["diagrams"])
    if bad:
        if not offer.get("repeat"):
            verb = "matches" if bad == 1 else "match"
            count = (f"{bad:,} {'edit' if bad == 1 else 'edits'} no longer {verb}"
                     if bad == edits else f"{bad:,} of {edits:,} edits no longer {verb}")
            env.warnings.append(
                f"Saved diagram edits from {offer['snapshot'].get('name')} were not "
                f"applied: {count} the collected data. The reports draw the generated "
                "diagrams; open the Diagrams page to carry or redo them.")
        return _CarryPlan(offer, apply=False)
    return _CarryPlan(offer, apply=True)


def _auto_carry(offer: dict[str, Any], snap_path: Path, env: Any) -> str | None:
    """Replay the offer's edits on the new snapshot; the warning text if that failed."""
    from ntnxvision_core.diagrams import store as diagram_store

    src = offer["snapshot"]
    try:
        diagram_store.carry_forward(
            Path(str(src["path"])),
            {"name": src.get("name"), "collected_at": src.get("collected_at"),
             "snapshot_id": src.get("snapshot_id"), "auto": True},
            snap_path, env, [d["type"] for d in offer["diagrams"]])
    except Exception as exc:  # noqa: BLE001 — the reports then draw the generated diagrams
        return (f"Saved diagram edits from {src.get('name')} were not applied: they could "
                f"not be saved with this snapshot ({exc}). The reports draw the generated "
                "diagrams; open the Diagrams page to carry or redo them.")
    return None


def _diagram_notes(snap_path: Path, env: Any) -> list[str]:
    """Lines for the email body: edits carried forward automatically, or why not."""

    out = [w for w in env.warnings if str(w).startswith("Saved diagram edits")]
    for name in dict.fromkeys(_carried_names(snap_path).values()):
        out.append(f"Diagrams: the saved edits from {name} were carried forward; the "
                   "reports draw them.")
    return out


def _snapshot_names() -> dict[str, str]:
    """snapshot_id -> the snapshot's name now (a snapshot can be renamed)."""
    from ntnxvision_core.overview import all_metas

    return {str(m["snapshot_id"]): str(m.get("name"))
            for m in all_metas(_store().list_snapshots(), _cache) if m.get("snapshot_id")}


def _current_name(source: dict[str, Any], names: dict[str, str] | None = None) -> str:
    """The name of the snapshot `source` ({snapshot_id, name}) points at, as it is called
    now when it still exists, else the name stored with it."""
    names = _snapshot_names() if names is None else names
    return names.get(str(source.get("snapshot_id") or ""), "") or str(source.get("name") or "")


def _carried_names(snapshot: Path) -> dict[str, str]:
    """type id -> the current name of the snapshot its edits were carried from
    automatically."""
    from ntnxvision_core.diagrams import store as diagram_store

    out: dict[str, str] = {}
    names: dict[str, str] | None = None
    for type_id, entry in diagram_store.load(snapshot).items():
        src = entry.get("carried_from") or {}
        if src.get("auto"):
            names = _snapshot_names() if names is None else names
            out[type_id] = _current_name(src, names)
    return out


def _ndb_from(block: dict[str, Any] | None, insecure: bool) -> NdbCredentials | None:
    """The optional NDB of a target. Its own `insecure` flag wins (NDB has its own
    certificate); only a caller that sends none inherits the Prism Central's. With
    verification off the NDB certificate must be pinned (`pinned_sha256`)."""
    ndb = block or {}
    if ndb.get("host") and ndb.get("username"):
        skip = bool(ndb.get("insecure", insecure))
        return NdbCredentials(host=ndb["host"], username=ndb["username"],
                              password=ndb.get("password", ""), verify_ssl=not skip,
                              pinned_sha256=require_pin(ndb["host"], skip,
                                                        ndb.get("pinned_sha256")))
    return None


def _vcenter_from(block: dict[str, Any] | None, insecure: bool) -> VcenterCredentials | None:
    """The optional vCenter of a target (ESXi enrichment); its own `insecure` flag wins,
    since a vCenter's certificate is often self-signed where the PC's is not."""
    vc = block or {}
    if vc.get("host") and vc.get("username"):
        skip = bool(vc.get("insecure", insecure))
        return VcenterCredentials(host=vc["host"], username=vc["username"],
                                  password=vc.get("password", ""),
                                  port=int(vc.get("port") or 443),
                                  verify_ssl=not skip,
                                  pinned_sha256=require_pin(vc["host"], skip,
                                                            vc.get("pinned_sha256")))
    return None


def _advance_demo(host: str) -> None:
    if demo_enabled():
        from ntnxvision_core.synthetic import demo

        demo.advance(host)


def _clusters_param(params: dict[str, Any]) -> list[str] | None:
    """The selected cluster uuids; None (absent) collects every registered cluster."""
    raw = params.get("clusters")
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(u, str) and u for u in raw):
        raise NutanixApiError("clusters must be a list of cluster uuids.")
    if not raw:
        raise NutanixApiError("Choose at least one cluster to collect.")
    return list(raw)


def _ratio_overrides_param(raw: Any) -> dict[str, float]:
    """Per-cluster vCPU:pCPU targets ({cluster uuid: ratio}); absent = none.

    Each ratio must be a number from MIN_VCPU_RATIO to MAX_VCPU_RATIO: a bad one is a
    caller error, refused before anything is collected rather than silently dropped."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise NutanixApiError("vcpu_ratio_overrides must map cluster uuids to ratios.")
    out: dict[str, float] = {}
    for cluster_uuid, value in raw.items():
        ratio = valid_ratio(value)
        if not isinstance(cluster_uuid, str) or not cluster_uuid or ratio is None:
            raise NutanixApiError(
                f"Invalid vCPU:pCPU target for cluster {cluster_uuid!r}: enter a number "
                f"from {MIN_VCPU_RATIO:g} to {MAX_VCPU_RATIO:g}.")
        out[cluster_uuid] = ratio
    return out


def _m_inventory(req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    handle = _runs.begin(params.get("run_id"))
    try:
        insecure = bool(params.get("insecure", False))
        creds = PrismCredentials(
            host=params["host"],
            username=params["username"],
            password=params["password"],
            port=int(params.get("port", 9440)),
            verify_ssl=not insecure,
            pinned_sha256=require_pin(params["host"], insecure, params.get("pinned_sha256")),
        )
        name = snapshot_name_for(params.get("name"), unique=bool(params.get("unique_name")))
        overrides = _ratio_overrides_param(params.get("vcpu_ratio_overrides"))
        tracker = RunTracker(_emit_progress, run_id=handle.run_id, mode="prism_central",
                             host=creds.host, name=name, formats=params.get("formats") or [])
        env = collect_environment(creds, ndb_creds=_ndb_from(params.get("ndb"), insecure),
                                  vcenter_creds=_vcenter_from(params.get("vcenter"), insecure),
                                  areas=params.get("areas"), cancel=handle.token,
                                  tracker=tracker, clusters=_clusters_param(params))
        apply_targets(env, params.get("vcpu_ratio"), overrides)
        result = _finalize(env, name, params, tracker=tracker, mode="prism_central",
                           handle=handle)
        _advance_demo(creds.host)
        return result
    finally:
        _runs.end(handle.run_id)


def _m_inventory_cluster(req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Direct Prism Element (v2.0) inventory of a single cluster."""
    handle = _runs.begin(params.get("run_id"))
    try:
        creds = ElementCredentials(
            host=params["host"],
            username=params["username"],
            password=params["password"],
            port=int(params.get("port", 9440)),
            verify_ssl=not params.get("insecure", False),
            pinned_sha256=require_pin(params["host"], bool(params.get("insecure", False)),
                                      params.get("pinned_sha256")),
        )
        name = snapshot_name_for(params.get("name"), unique=bool(params.get("unique_name")))
        overrides = _ratio_overrides_param(params.get("vcpu_ratio_overrides"))
        tracker = RunTracker(_emit_progress, run_id=handle.run_id, mode="prism_element",
                             host=creds.host, name=name, formats=params.get("formats") or [])
        env = collect_element_environment(creds, areas=params.get("areas"),
                                          cancel=handle.token, tracker=tracker)
        apply_targets(env, params.get("vcpu_ratio"), overrides)
        result = _finalize(env, name, params, tracker=tracker, mode="prism_element",
                           handle=handle)
        _advance_demo(creds.host)
        return result
    finally:
        _runs.end(handle.run_id)


def _m_inventory_multi(req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Collect from several Prism Centrals in one request.

    Targets are collected **in parallel** (each with its own progress tagging) and each
    is isolated — one unreachable or misconfigured PC is recorded as a per-target failure
    and never aborts the others. Nothing is written until every target has finished
    collecting, so cancelling a multi-PC run leaves no partial set of snapshots behind.
    Each successful target then gets its own snapshot + exports, exactly as a solo
    `inventory` call would; when `combine` is set and at least two targets succeeded, an
    additional merged snapshot/report is produced via `combine_environments`.
    """
    targets = params.get("targets") or []
    if not targets:
        raise NutanixApiError("At least one target is required.")
    handle = _runs.begin(params.get("run_id"))
    try:
        store = _store()
        claimed: set[str] = set()
        unique = bool(params.get("unique_name"))
        for t in targets:
            _clusters_param(t)  # a bad per-target cluster list fails before collecting
            t["vcpu_ratio_overrides"] = _ratio_overrides_param(t.get("vcpu_ratio_overrides"))
            t["name"] = snapshot_name_for(t.get("name"), store, claimed,
                                          fallback=str(t.get("host") or "target"),
                                          unique=unique)
            claimed.add(t["name"].casefold())
        if params.get("combine") and len(targets) >= 2:
            params["combined_name"] = snapshot_name_for(params.get("combined_name"), store,
                                                        claimed, fallback="combined",
                                                        unique=unique)
        return _multi(handle, targets, params)
    finally:
        _runs.end(handle.run_id)


def _multi(handle: RunHandle, targets: list[dict[str, Any]], params: dict[str, Any]
           ) -> dict[str, Any]:
    finalize_params = {"formats": params.get("formats"), "export_dir": params.get("export_dir"),
                       "sections": params.get("sections"), "word": params.get("word"),
                       "chapters": params.get("chapters"),
                       "section_order": params.get("section_order"),
                       "vcpu_ratio": params.get("vcpu_ratio"),
                       "file_prefix": params.get("file_prefix")}
    formats = params.get("formats") or []
    outcomes: list[tuple[Any, RunTracker | None, Exception | None]] = [
        (None, None, None)] * len(targets)

    def collect(i: int) -> None:
        t = targets[i]
        host = t.get("host", "")
        tracker = RunTracker(_emit_progress, run_id=handle.run_id, mode="prism_central",
                             host=host, name=t.get("name") or host, formats=formats,
                             target={"target_index": i, "target_total": len(targets),
                                     "target_host": host})
        try:
            insecure = bool(t.get("insecure", False))
            creds = PrismCredentials(
                host=host,
                username=t["username"],
                password=t["password"],
                port=int(t.get("port", 9440)),
                verify_ssl=not insecure,
                pinned_sha256=require_pin(host, insecure, t.get("pinned_sha256")),
            )
            env = collect_environment(creds, ndb_creds=_ndb_from(t.get("ndb"), insecure),
                                      vcenter_creds=_vcenter_from(t.get("vcenter"), insecure),
                                      areas=params.get("areas"), cancel=handle.token,
                                      tracker=tracker, clusters=_clusters_param(t))
            apply_targets(env, params.get("vcpu_ratio"), t.get("vcpu_ratio_overrides"))
            tracker.finish()
            outcomes[i] = (env, tracker, None)
        except RunCancelled:
            outcomes[i] = (None, tracker, None)
        except Exception as exc:  # noqa: BLE001 — one target's failure never aborts the rest
            outcomes[i] = (None, tracker, exc)

    parallel = bool(params.get("parallel", True)) and len(targets) > 1
    if parallel:
        with ThreadPoolExecutor(max_workers=min(4, len(targets)),
                                thread_name_prefix="target") as pool:
            list(pool.map(collect, range(len(targets))))
    else:
        for i in range(len(targets)):
            collect(i)

    handle.commit()  # raises RunCancelled if cancelled while collecting

    target_results: list[dict[str, Any]] = []
    successful_envs = []
    successful_trackers: list[tuple[str, RunTracker]] = []
    for i, (env, tracker, exc) in enumerate(outcomes):
        t = targets[i]
        host = t.get("host", "")
        if env is None:
            target_results.append({"host": host, "success": False,
                                   "error": str(exc) if exc else "Not collected"})
            continue
        name = t.get("name") or host or f"target-{i + 1}"
        if tracker is not None:
            tracker.phase("snapshot", f"Saving {name}…")
        result = _finalize(env, name, finalize_params, tracker=tracker, mode="prism_central")
        target_results.append({"host": host, "success": True, **result})
        successful_envs.append(env)
        if tracker is not None:
            successful_trackers.append((host, tracker))
        _advance_demo(host)

    combined_result = None
    if params.get("combine") and len(successful_envs) >= 2:
        _emit_progress({"type": "phase", "run_id": handle.run_id, "phase": "combine",
                        "label": f"Combining {len(successful_envs):,} Prism Centrals…"})
        combined_env = combine_environments(successful_envs)
        combined_name = params.get("combined_name") or "combined"
        combined_result = _finalize(
            combined_env, combined_name, finalize_params, mode="prism_central",
            tracker=CombinedTracker(successful_trackers, _emit_progress, handle.run_id))

    return {"targets": target_results, "combined": combined_result}


# ─── snapshots ─────────────────────────────────────────────────────────


def _store() -> SnapshotStore:
    return SnapshotStore(snapshots_dir())


def _m_list_snapshots(_req_id: Any, _params: dict[str, Any]) -> dict[str, Any]:
    from ntnxvision_core.overview import all_metas

    store = _store()
    metas = all_metas(store.list_snapshots(), _cache)
    return {
        "snapshots": [snapstore.info_from_meta(m) for m in metas],
        "dir": str(store.directory),
        "total_bytes": sum(int(m.get("size_bytes") or 0) for m in metas),
    }


def _m_get_overview(_req_id: Any, _params: dict[str, Any]) -> dict[str, Any]:
    from ntnxvision_core.overview import all_metas, build

    return build(all_metas(_store().list_snapshots(), _cache))


def _m_get_snapshot(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_snapshot_path(params["snapshot"])
    meta = snapstore.read_meta(path, _cache)
    stats = meta.get("stats") or {}
    run = meta.get("run")
    if isinstance(run, dict):
        run = dict(run)
        reports = [f for f in meta.get("exports") or [] if f.get("run")]
        run["reports"] = [{k: v for k, v in f.items() if k != "run"} for f in reports
                          if Path(str(f.get("path"))).exists()]
    summary = meta.get("summary") or {}
    return {
        **snapstore.info_from_meta(meta),
        "source_type": meta.get("source_type") or "prism_central",
        "prism_central": {"host": meta.get("source"), "version": meta.get("version")},
        "is_combined": meta.get("kind") == "combined",
        "sources": meta.get("sources") or [],
        "collection_notes": stats.get("notes") or {},
        "export_estimates": _estimates(summary, meta.get("kind")),
        "run": run,
        "exports": [{k: v for k, v in f.items() if k != "run"}
                    for f in meta.get("exports") or []],
    }


def _estimates(summary: dict[str, Any], kind: Any) -> dict[str, Any]:
    """Rough sizes for the export dialog's format cards (calibrated on synthetic runs)."""
    rows = sum(int(v or 0) for k, v in summary.items() if k != "source_environments")
    sheets = 4 + sum(1 for k, v in summary.items()
                     if v and k not in ("source_environments", "system_vms"))
    vms = int(summary.get("vms") or 0)
    # The PDF and the ADD carry no per-VM table, so VMs barely move their length.
    doc_rows = max(0, rows - vms)
    return {
        "excel": {"bytes": 12_000 + rows * 190, "detail": f"{sheets:,} sheets"},
        "pdf": {"bytes": 650_000 + doc_rows * 1_100, "detail": f"≈ {10 + doc_rows // 30:,} pages"},
        "word": {"bytes": 800_000 + doc_rows * 900, "detail": f"≈ {16 + doc_rows // 25:,} pages"},
    }


def _m_report_sections(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Which report sections have content in a snapshot (the export dialog greys out the
    others), in the default document order."""
    from ntnxvision_core.exporters.sections import availability

    env = _cache.get(_resolve_snapshot_path(params["snapshot"]))
    return {"sections": availability(env)}


def _m_export(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Re-export a saved snapshot to one or more formats (excel / pdf / word).

    The snapshot path is confined to the snapshots directory (no arbitrary file reads)
    and must exist; per-format failures are reported structurally, not as pseudo-success
    strings, so callers can tell success from failure.
    """
    snap_path = _resolve_snapshot_path(params["snapshot"])
    env = _cache.get(snap_path)
    name = params.get("name") or snapstore.snapshot_name(snap_path)
    formats = params.get("formats") or [params.get("format", "excel")]
    files, errors = _write_reports(env, name, list(formats), _export_dir_from(params),
                                   _export_options(params), prefix=_file_prefix(params),
                                   snapshot=snap_path)
    if files:
        snapstore.record_exports(snap_path, files)
    paths = {f["format"]: f["path"] for f in files}
    return {"paths": paths, "errors": errors, "path": next(iter(paths.values()), None),
            "files": files}


def _m_rename_snapshot(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Rename a saved snapshot (its file stem, confined to the snapshots directory)."""
    snap_path = _resolve_snapshot_path(params["snapshot"])
    new_name = str(params.get("new_name") or "").strip()
    if not new_name:
        raise NutanixApiError("A new name is required.")
    safe = safe_name(new_name, fallback="")
    if not safe:
        raise NutanixApiError("The new name has no usable characters.")
    try:
        target = snapstore.rename(snap_path, safe)
    except FileExistsError as exc:
        raise NutanixApiError(f"A snapshot named '{safe}' already exists.") from exc
    _cache.invalidate(snap_path)
    return {"path": str(target), "name": safe}


def _m_delete_snapshot(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Delete a saved snapshot (confined to the snapshots directory)."""
    snap_path = _resolve_snapshot_path(params["snapshot"])
    snapstore.delete(snap_path)
    _cache.invalidate(snap_path)
    return {"deleted": True}


def _m_clear_snapshots(_req_id: Any, _params: dict[str, Any]) -> dict[str, Any]:
    """Delete every snapshot (never exports, never the directory itself)."""
    if _runs.active():
        raise Busy("A collection is running. Wait for it to finish before clearing")
    store = _store()
    deleted = 0
    for path in store.list_snapshots():
        try:
            snapstore.delete(path)
            deleted += 1
        except OSError:
            continue
    for suffix in (snapstore.META_SUFFIX, snapstore.DIAGRAMS_SUFFIX):
        for orphan in store.directory.glob(f"*{suffix}"):
            orphan.unlink(missing_ok=True)
    _cache.invalidate()
    return {"deleted": deleted}


def _resolve_snapshot_path(raw: str) -> Path:
    """Resolve a snapshot path, requiring it to live inside the snapshots directory."""
    # R3-03: judge the text before any filesystem call: resolve() on a UNC path would touch
    # the network share (Windows sends SMB credentials).
    if raw.startswith(("\\\\", "//")) or "\0" in raw:
        raise NutanixApiError("Snapshot path cannot be a network (UNC) path.")
    base = snapshots_dir()
    root = base.resolve()
    candidate = Path(raw)
    if candidate.is_absolute():
        lexical = os.path.normcase(os.path.normpath(raw))
        allowed = {os.path.normcase(os.path.normpath(str(r))) for r in (base, root)}
        if not any(lexical == r or lexical.startswith(r.rstrip(os.sep) + os.sep) for r in allowed):
            raise NutanixApiError("Snapshot path is outside the snapshots directory.")
    # Bare names are resolved against the snapshots dir; full paths are validated.
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if root not in resolved.parents and resolved != root:
        raise NutanixApiError("Snapshot path is outside the snapshots directory.")
    if not resolved.is_file():
        raise NutanixApiError(f"Snapshot not found: {resolved.name}")
    return resolved


# ─── snapshot files (.ntnxsnap) ────────────────────────────────────────


def _transfer_code(exc: transfer.TransferError) -> int:
    if isinstance(exc, transfer.UnsupportedVersion):
        return NEWER_VERSION
    if isinstance(exc, (transfer.PasswordRequired, transfer.WrongPassword)):
        return PASSWORD_ERROR
    if isinstance(exc, transfer.InvalidSnapshotFile):
        return INVALID_FILE
    return TRANSFER_ERROR


def _export_item(raw: str) -> transfer.ExportItem:
    path = _resolve_snapshot_path(raw)
    store = _store()
    env = _cache.get(path)
    if not env.snapshot_id:
        transfer.ensure_snapshot_id(store, path, env)
        _cache.invalidate(path)
    return transfer.ExportItem(env=env, meta=snapstore.read_meta(path, _cache))


def _local_file(raw: Any, what: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise transfer.TransferError(f"A {what} is required.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise transfer.TransferError(f"The {what} must be an absolute path.")
    return path


def _m_export_snapshot_file(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Export one snapshot (`snapshot`) or a bundle (`snapshots`) to one .ntnxsnap file."""
    raws = params.get("snapshots") or [params.get("snapshot")]
    items = [_export_item(str(r)) for r in raws if r]
    dest = _local_file(params.get("dest_path"), "destination file")
    return transfer.export_file(items, dest, password=params.get("password") or None,
                                note=params.get("note"), app_version=params.get("app_version"))


def _m_export_snapshot_files(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Export several snapshots to a folder, one .ntnxsnap file each."""
    dest_dir = _local_file(params.get("dest_dir"), "destination folder")
    if not dest_dir.is_dir():
        raise transfer.TransferError(f"The folder {dest_dir} does not exist.")
    files = []
    errors = []
    for raw in params.get("snapshots") or []:
        try:
            item = _export_item(str(raw))
            dest = transfer.unique_file(dest_dir, str(item.meta.get("name") or "snapshot"))
            files.append(transfer.export_file(
                [item], dest, password=params.get("password") or None, note=params.get("note"),
                app_version=params.get("app_version")))
        except Exception as exc:  # noqa: BLE001 — one failed snapshot never stops the batch
            errors.append({"snapshot": str(raw), "error": str(exc)})
    return {"dir": str(dest_dir), "files": files, "errors": errors}


def _m_inspect_snapshot_file(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    path = _local_file(params.get("path"), "snapshot file")
    return transfer.inspect_file(path, _store(), password=params.get("password") or None)


def _m_import_snapshot_file(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    path = _local_file(params.get("path"), "snapshot file")
    with _import_lock:
        return transfer.import_file(path, _store(), password=params.get("password") or None,
                                    on_conflict=str(params.get("on_conflict") or "keep_both"),
                                    on_saved=_cache.invalidate)


_import_lock = threading.Lock()


# ─── inventory & compare ───────────────────────────────────────────────


def _m_query_inventory(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_snapshot_path(params["snapshot"])
    env = _cache.get(path)
    return _inventory_service().query(
        env, str(params.get("object_type") or "vms"), params.get("filter") or {},
        params.get("sort"), params.get("page") or {}, params.get("vcpu_ratio"))


def _m_get_object_detail(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_snapshot_path(params["snapshot"])
    env = _cache.get(path)
    try:
        return _inventory_service().detail(
            env, str(params.get("object_type") or "vms"), str(params.get("id") or ""),
            params.get("vcpu_ratio"))
    except KeyError as exc:
        raise NutanixApiError(str(exc).strip("'\"")) from exc


def _m_compare_snapshots(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    from ntnxvision_core.compare import compare

    base_path = _resolve_snapshot_path(params["baseline"])
    cur_path = _resolve_snapshot_path(params["current"])
    base_env, cur_env = _cache.get(base_path), _cache.get(cur_path)
    result = compare(base_env, cur_env)
    return {
        "baseline": snapstore.info_from_meta(snapstore.read_meta(base_path, _cache)),
        "current": snapstore.info_from_meta(snapstore.read_meta(cur_path, _cache)),
        **result,
    }


def _m_export_comparison(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    from ntnxvision_core.compare import export_comparison

    base_path = _resolve_snapshot_path(params["baseline"])
    cur_path = _resolve_snapshot_path(params["current"])
    fmt = str(params.get("format")) if params.get("format") in ("csv", "pdf") else "excel"
    out = export_comparison(_cache.get(base_path), _cache.get(cur_path),
                            snapstore.snapshot_name(base_path), snapstore.snapshot_name(cur_path),
                            fmt, _export_dir_from(params), prefix=_file_prefix(params))
    return {"path": str(out)}


# ─── diagrams (Diagrams page) ──────────────────────────────────────────


def _diagram_type(params: dict[str, Any]) -> str:
    raw = params.get("type")
    if not isinstance(raw, str) or not raw:
        raise NutanixApiError("A diagram type is required.")
    return raw


def _diagram_call(fn: Any) -> Any:
    """Edit errors (unknown diagram, malformed edits) as API errors with their text."""
    from ntnxvision_core.diagrams.edits import EditError

    try:
        return fn()
    except EditError as exc:
        raise NutanixApiError(str(exc)) from exc


def _carry_candidate(snap_path: Path, env: Any) -> dict[str, Any] | None:
    """The newest older snapshot of the same source with saved diagram edits, when this
    one has none, with what carrying them forward would do."""
    from ntnxvision_core.diagrams import store as diagram_store

    if diagram_store.load(snap_path):
        return None
    here = snapstore.read_meta(snap_path, _cache)
    offer = _carry_offer(_source_key(here), here.get("collected_at"), snap_path, env)
    if offer is not None:
        offer.pop("repeat", None)
    return offer


def _source_key(meta: dict[str, Any]) -> str | None:
    """Which estate a snapshot is of: its Prism Central (or Prism Element) host, or for a
    combined run the sorted set of its Prism Central hosts ("3 Prism Centrals" alone
    would match any other combination of three)."""
    kind = meta.get("kind") or "pc"
    if kind == "combined":
        hosts = sorted({str(h) for h in meta.get("sources") or [] if h})
        return "combined:" + ",".join(hosts) if hosts else None
    source = meta.get("source")
    return f"{kind}:{source}" if source else None


def _carry_offer(key: str | None, collected_at: Any, exclude: Path | None, env: Any
                 ) -> dict[str, Any] | None:
    """Saved diagram edits to carry to `env` (a snapshot of `key` collected at
    `collected_at`, other than `exclude`), and what replaying them would do.

    Walks back over the earlier snapshots of the same estate, newest first, past those
    with neither edits nor a reset marker (never edited, or a carry that failed and
    recorded where it came from), and stops at the first with edits (offered) or a
    reset marker (nothing: a reset is final). ``repeat`` says the newest earlier
    snapshot already recorded the same failed carry, so its warning is not repeated."""
    from ntnxvision_core.diagrams import store as diagram_store
    from ntnxvision_core.overview import all_metas, collected_key

    if not key:
        return None
    when = collected_key(collected_at)
    # all_metas is newest first.
    earlier = [m for m in all_metas(_store().list_snapshots(), _cache)
               if _source_key(m) == key
               and (exclude is None or Path(str(m.get("path"))).resolve() != exclude)
               and collected_key(m.get("collected_at")) <= when]
    if not earlier:
        return None
    previous_failed = (diagram_store.markers(Path(str(earlier[0]["path"])))
                       .get("carry_failed_from") or {})
    # Per diagram type: the first earlier snapshot with edits of that type, unless one
    # in between reset that type (resetting one diagram never blocks another's edits).
    found: dict[str, dict[str, Any]] = {}
    open_types = set(_diagram_types())
    for meta in earlier:
        if not open_types:
            break
        src = Path(str(meta["path"]))
        entries = diagram_store.load(src)
        resets = set(diagram_store.markers(src).get("reset", []))
        for type_id in sorted(open_types):
            if type_id in entries:
                found[type_id] = meta
            elif type_id in resets:
                found[type_id] = {}
        open_types -= set(found)
    sources = [m for m in found.values() if m]
    if not sources:
        return None
    # The newest source wins (one carry replays one snapshot's edits).
    meta = max(sources, key=lambda m: collected_key(m.get("collected_at")))
    types = sorted(t for t, m in found.items() if m is meta)
    src = Path(str(meta["path"]))
    report = [r for r in diagram_store.carry_report(src, env, types) if r["edits"]]
    if not report:
        return None
    sid = meta.get("snapshot_id")
    return {"snapshot": snapstore.info_from_meta(meta), "diagrams": report,
            "repeat": bool(sid) and previous_failed.get("snapshot_id") == sid}


def _diagram_types() -> list[str]:
    from ntnxvision_core.diagrams import TYPES

    return list(TYPES)


def _m_list_diagrams(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    from ntnxvision_core.diagrams import store as diagram_store

    snap_path = _resolve_snapshot_path(params["snapshot"])
    env = _cache.get(snap_path)
    return {"snapshot": str(snap_path),
            "diagrams": diagram_store.list_result(snap_path, env),
            "carry": _carry_candidate(snap_path, env)}


def _m_get_diagram(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    from ntnxvision_core.diagrams import store as diagram_store

    snap_path = _resolve_snapshot_path(params["snapshot"])
    env = _cache.get(snap_path)
    result = _diagram_call(lambda: diagram_store.get_result(snap_path, env,
                                                            _diagram_type(params)))
    if isinstance(result.get("carried_from"), dict):  # renamed since: today's name
        result["carried_from"] = {**result["carried_from"],
                                  "name": _current_name(result["carried_from"])}
    return result


def _m_layout_diagram(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    from ntnxvision_core.diagrams import edits
    from ntnxvision_core.diagrams import store as diagram_store

    snap_path = _resolve_snapshot_path(params["snapshot"])
    env = _cache.get(snap_path)
    return _diagram_call(lambda: diagram_store.layout_result(
        env, _diagram_type(params), edits.clean_ops(params.get("ops") or [])))


def _m_save_diagram(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    from ntnxvision_core.diagrams import store as diagram_store

    snap_path = _resolve_snapshot_path(params["snapshot"])
    env = _cache.get(snap_path)
    return _diagram_call(lambda: diagram_store.save_result(
        snap_path, env, _diagram_type(params), params.get("ops") or []))


def _m_reset_diagram(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    from ntnxvision_core.diagrams import store as diagram_store

    snap_path = _resolve_snapshot_path(params["snapshot"])
    env = _cache.get(snap_path)
    return _diagram_call(lambda: diagram_store.reset_result(snap_path, env,
                                                            _diagram_type(params)))


def _m_carry_diagrams(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Replay the edits of `from` on `to` (types: all edited ones, or `types`)."""
    from ntnxvision_core.diagrams import store as diagram_store

    src = _resolve_snapshot_path(params["from"])
    dst = _resolve_snapshot_path(params["to"])
    if src == dst:
        raise NutanixApiError("Choose a different snapshot to carry the edits to.")
    types = params.get("types")
    if types is not None and not (isinstance(types, list)
                                  and all(isinstance(t, str) for t in types)):
        raise NutanixApiError("types must be a list of diagram types.")
    meta = snapstore.read_meta(src, _cache)
    # Who the edits came from: Compare uses it as the baseline of "what changed".
    info = {"name": meta.get("name"), "collected_at": meta.get("collected_at"),
            "snapshot_id": meta.get("snapshot_id")}
    report = _diagram_call(lambda: diagram_store.carry_forward(
        src, info, dst, _cache.get(dst), types))
    return {"diagrams": report}


# ─── demo seeding ──────────────────────────────────────────────────────

DEMO_PASSWORD = "demo"
_seed_lock = threading.Lock()


def _shift_iso(value: str | None, delta: Any) -> str | None:
    from datetime import datetime

    if not value:
        return value
    try:
        return (datetime.fromisoformat(value.replace("Z", "+00:00")) - delta).isoformat(
            timespec="seconds")
    except ValueError:
        return value


def _backdate(env: Any, tracker: RunTracker | None, delta: Any) -> None:
    """Make a seeded collection look as if it ran `delta` ago."""
    env.collected_at = _shift_iso(env.collected_at, delta)
    if tracker is not None:
        if tracker.finished_at is None:
            tracker.finish()
        tracker.started_at = _shift_iso(tracker.started_at, delta) or tracker.started_at
        tracker.finished_at = _shift_iso(tracker.finished_at, delta)


def _seed_pc(host: str, name: str, delta: Any, formats: list[str], params: dict[str, Any],
             *, finalize: bool = True) -> tuple[Any, RunTracker, dict[str, Any] | None]:
    creds = PrismCredentials(host=host, username="admin", password=DEMO_PASSWORD,
                             port=9440, verify_ssl=True)
    tracker = RunTracker(None, run_id=uuid.uuid4().hex, mode="prism_central", host=host,
                         name=name, formats=formats)
    env = collect_environment(creds, tracker=tracker)
    _backdate(env, tracker, delta)
    result = None
    if finalize:
        result = _finalize(env, name, {"formats": formats, "export_dir": params.get("export_dir")},
                           tracker=tracker, mode="prism_central")
    _advance_demo(host)
    return env, tracker, result


def _seed_demo(params: dict[str, Any]) -> list[str]:
    """Collect a believable history from the demo estate.

    * pc01 two weeks ago and again today (so Compare has a real pair, and the user's next
      pc01 collection drifts one more week);
    * pe01 (Prism Element) three days ago;
    * pc02 + pc03 yesterday, plus their combined multi-PC report;
    * today's pc01 run also writes Excel + Word reports, so Results shows files.

    Names follow the app's convention: typed by the user, no date (the collection time is
    stored with the snapshot and report files carry the export date).
    """
    from datetime import timedelta

    made: list[str] = []

    _, _, r = _seed_pc("pc01.demo.local", "pc01 baseline", timedelta(days=14), [], params)
    made.append(r["snapshot_name"] if r else "")

    creds = ElementCredentials(host="pe01.demo.local", username="admin",
                               password=DEMO_PASSWORD, port=9440, verify_ssl=True)
    tracker = RunTracker(None, run_id=uuid.uuid4().hex, mode="prism_element",
                         host=creds.host, name="pe01 baseline")
    env = collect_element_environment(creds, tracker=tracker)
    _backdate(env, tracker, timedelta(days=3))
    r = _finalize(env, "pe01 baseline", {"formats": []}, tracker=tracker,
                  mode="prism_element")
    _advance_demo(creds.host)
    made.append(r["snapshot_name"])

    parts = []
    for host in ("pc02.demo.local", "pc03.demo.local"):
        short = host.split(".")[0]
        env_i, tracker_i, r = _seed_pc(host, f"{short} baseline", timedelta(days=1), [],
                                       params)
        made.append(r["snapshot_name"] if r else "")
        parts.append((host, env_i, tracker_i))
    combined = combine_environments([p[1] for p in parts])
    combined.collected_at = parts[-1][1].collected_at
    combined_tracker = CombinedTracker([(h, t) for h, _, t in parts], None, None)
    combined_tracker.finished_at = max(t.finished_at or "" for _, _, t in parts) or None
    combined_tracker.duration_ms = max(t.duration_ms or 0 for _, _, t in parts)
    r = _finalize(combined, "pc02 and pc03 combined", {"formats": []}, mode="prism_central",
                  tracker=combined_tracker)
    made.append(r["snapshot_name"])

    _, _, r = _seed_pc("pc01.demo.local", "pc01 week 2", timedelta(hours=2),
                       ["excel", "word"], params)
    made.append(r["snapshot_name"] if r else "")
    return [m for m in made if m]


def _m_demo_seed(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Demo mode only: fill an empty demo profile with a few finished collections.

    Idempotent — does nothing when demo snapshots already exist (unless `force`).
    """
    if not demo_enabled():
        raise NutanixApiError("demo_seed is only available in demo mode.")
    from ntnxvision_core.synthetic import demo

    with _seed_lock:
        if _store().list_snapshots() and not params.get("force"):
            return {"seeded": False, "snapshots": []}
        state = demo.state() or demo.enable()
        saved_latency = state.api.latency_ms
        state.api.latency_ms = 0.0  # seeding should take seconds, not minutes
        try:
            return {"seeded": True, "snapshots": _seed_demo(params)}
        finally:
            state.api.latency_ms = saved_latency


def _m_disk_usage(_req_id: Any, _params: dict[str, Any]) -> dict[str, Any]:
    usage = shutil.disk_usage(snapshots_dir())
    return {"free_bytes": usage.free, "total_bytes": usage.total}


# ─── email ─────────────────────────────────────────────────────────────


def _m_test_smtp(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Send a test message with the given (possibly unsaved) mail server settings."""
    from ntnxvision_core import mailer

    try:
        cfg = mailer.SmtpConfig.from_params(params.get("smtp"))
    except mailer.MailError as err:
        return mailer.result(False, err.status, err.message, detail=err.detail)
    return mailer.send_test(cfg, str(params.get("to") or ""))


def _local_day(value: Any) -> str | None:
    from datetime import datetime

    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.strftime("%Y-%m-%d")


def _summary_line(summary: dict[str, Any]) -> str:
    parts = []
    for key, one, many in (("clusters", "cluster", "clusters"), ("hosts", "host", "hosts"),
                           ("vms", "VM", "VMs")):
        n = int(summary.get(key) or 0)
        parts.append(f"{n:,} {one if n == 1 else many}")
    return ", ".join(parts)


def _m_send_email(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Write a snapshot's (or several snapshots') reports and email them.

    The reports land in the export folder like any export (and stay there when sending
    fails); SMTP failures come back as a classified result, not a JSON-RPC error.
    """
    from datetime import date

    from ntnxvision_core import mailer

    try:
        cfg = mailer.SmtpConfig.from_params(params.get("smtp"))
    except mailer.MailError as err:
        return mailer.result(False, err.status, err.message, detail=err.detail)
    to = mailer.clean_addresses(params.get("to"))
    cc = mailer.clean_addresses(params.get("cc"))
    if not to:
        return mailer.result(False, "invalid", "Add at least one recipient.")
    bad = [a for a in to + cc if not mailer.is_valid_address(a)]
    if bad:
        return mailer.result(False, "invalid", f"Not an email address: {', '.join(bad)}.",
                             rejected=bad)
    raws = params.get("snapshots") or ([params["snapshot"]] if params.get("snapshot") else [])
    if not raws:
        return mailer.result(False, "invalid", "Choose a snapshot to email.")
    formats = list(params.get("formats") or [])
    if not formats:
        return mailer.result(False, "invalid", "Choose at least one report format.")

    out_dir = _export_dir_from(params)
    options = _export_options(params)
    prefix = _file_prefix(params)
    files: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    names: list[str] = []
    metas: list[dict[str, Any]] = []
    diagram_lines: list[str] = []
    for raw in raws:
        snap_path = _resolve_snapshot_path(str(raw))
        env = _cache.get(snap_path)
        name = snapstore.snapshot_name(snap_path)
        written, errs = _write_reports(env, name, formats, out_dir, options, prefix=prefix,
                                       snapshot=snap_path)
        if written:
            snapstore.record_exports(snap_path, written)
        files.extend(written)
        errors.update({(f"{name} {k}" if len(raws) > 1 else k): v for k, v in errs.items()})
        names.append(name)
        metas.append(snapstore.read_meta(snap_path, _cache))
        diagram_lines.extend(_diagram_notes(snap_path, env))
    if not files:
        detail = "; ".join(f"{k}: {v}" for k, v in errors.items()) or None
        return mailer.result(False, "error", "The reports could not be written.", detail=detail)

    context = params.get("context") or {}
    target = str(context.get("target") or "").strip() or ", ".join(
        str(m.get("source") or m.get("name") or "") for m in metas)
    days = sorted(d for d in (_local_day(m.get("collected_at")) for m in metas) if d)
    if len(metas) == 1:
        summary = f"Summary: {_summary_line(metas[0].get('summary') or {})}"
    else:
        summary = "Summary:\n" + "\n".join(
            f"{n}: {_summary_line(m.get('summary') or {})}"
            for n, m in zip(names, metas, strict=True))
    ctx = {"target": target, "date": days[-1] if days else date.today().isoformat(),
           "snapshot": ", ".join(names), "summary": summary}
    subject = mailer.render_subject(str(params.get("subject") or ""), ctx) or \
        f"NTNXVision report: {target}"
    body = mailer.render_template(str(params.get("body") or ""), ctx)
    if diagram_lines:
        body += "\n\n" + "\n".join(dict.fromkeys(diagram_lines)) + "\n"
    if errors:
        body += "\n\nNot attached (could not be written): " + ", ".join(errors) + "\n"
    result = mailer.send_files(cfg, to, cc, subject, body, [Path(f["path"]) for f in files],
                               zip_name=f"{names[0]}-reports")
    result["files"] = files
    if errors:
        result["detail"] = "; ".join(filter(None, [
            result.get("detail"), *(f"{k}: {v}" for k, v in errors.items())]))
    return result


_METHODS = {
    "ping": _m_ping,
    "list_areas": _m_list_areas,
    "test_connection": _m_test_connection,
    "list_clusters": _m_list_clusters,
    "cancel_run": _m_cancel_run,
    "demo_info": _m_demo_info,
    "inventory": _m_inventory,
    "inventory_cluster": _m_inventory_cluster,
    "inventory_multi": _m_inventory_multi,
    "get_overview": _m_get_overview,
    "list_snapshots": _m_list_snapshots,
    "get_snapshot": _m_get_snapshot,
    "query_inventory": _m_query_inventory,
    "get_object_detail": _m_get_object_detail,
    "compare_snapshots": _m_compare_snapshots,
    "export_comparison": _m_export_comparison,
    "export": _m_export,
    "report_sections": _m_report_sections,
    "rename_snapshot": _m_rename_snapshot,
    "delete_snapshot": _m_delete_snapshot,
    "clear_snapshots": _m_clear_snapshots,
    "export_snapshot_file": _m_export_snapshot_file,
    "export_snapshot_files": _m_export_snapshot_files,
    "inspect_snapshot_file": _m_inspect_snapshot_file,
    "import_snapshot_file": _m_import_snapshot_file,
    "disk_usage": _m_disk_usage,
    "test_smtp": _m_test_smtp,
    "send_email": _m_send_email,
    "demo_seed": _m_demo_seed,
    "list_diagrams": _m_list_diagrams,
    "get_diagram": _m_get_diagram,
    "layout_diagram": _m_layout_diagram,
    "save_diagram": _m_save_diagram,
    "reset_diagram": _m_reset_diagram,
    "carry_diagrams": _m_carry_diagrams,
}
