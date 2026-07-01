"""Stdio JSON-RPC sidecar.

The Electron main process spawns `ntnxvision_core sidecar` and communicates over stdin/stdout
using newline-delimited JSON-RPC 2.0 messages. This keeps the Python core free of any
network listener — nothing binds a port.

Request  : {"jsonrpc":"2.0","id":1,"method":"inventory","params":{...}}
Response : {"jsonrpc":"2.0","id":1,"result":{...}}
Progress : {"jsonrpc":"2.0","method":"progress","params":{...}}   (notification, no id)
Error    : {"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"..."}}

Methods:
  ping                      -> {"pong": true, "version": "..."}
  inventory(host, username, password, port, insecure, name, export)  -> summary + paths
  list_snapshots            -> [{name, path, collected_at}]
  export(snapshot, format)  -> {path}
"""

from __future__ import annotations

import json
import sys
from typing import Any

from ntnxvision_core import __version__
from ntnxvision_core.api import PrismCredentials
from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.engine import collect_environment
from ntnxvision_core.exporters import ExcelExporter
from ntnxvision_core.paths import exports_dir, snapshots_dir
from ntnxvision_core.store import SnapshotStore


def serve_stdio() -> None:
    """Read JSON-RPC requests from stdin, one per line, until EOF."""
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
        _handle(request)


# ─── dispatch ──────────────────────────────────────────────────────────


def _handle(request: dict[str, Any]) -> None:
    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    try:
        handler = _METHODS.get(method)
        if handler is None:
            _error(req_id, -32601, f"Method not found: {method}")
            return
        result = handler(req_id, params)
        _send({"jsonrpc": "2.0", "id": req_id, "result": result})
    except NutanixApiError as exc:
        _error(req_id, -32000, str(exc))
    except Exception as exc:  # noqa: BLE001
        _error(req_id, -32001, f"{type(exc).__name__}: {exc}")


def _m_ping(_req_id: Any, _params: dict[str, Any]) -> dict[str, Any]:
    return {"pong": True, "version": __version__}


def _m_inventory(req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    creds = PrismCredentials(
        host=params["host"],
        username=params["username"],
        password=params["password"],
        port=int(params.get("port", 9440)),
        verify_ssl=not params.get("insecure", False),
    )
    name = params.get("name") or "environment"

    def progress(idx: int, total: int, label: str, status: str) -> None:
        _notify("progress", {"id": req_id, "index": idx, "total": total,
                             "label": label, "status": status})

    env = collect_environment(creds, progress=progress)

    snap_path = SnapshotStore(snapshots_dir()).save(env, name)
    result: dict[str, Any] = {
        "summary": env.summary(),
        "prism_central": env.prism_central.model_dump(),
        "collection_notes": env.collection_notes,
        "snapshot_path": str(snap_path),
    }
    if params.get("export", True):
        out = exports_dir() / f"{name}.xlsx"
        ExcelExporter().export(env, out)
        result["excel_path"] = str(out)
    return result


def _m_list_snapshots(_req_id: Any, _params: dict[str, Any]) -> list[dict[str, Any]]:
    store = SnapshotStore(snapshots_dir())
    out = []
    for path in store.list_snapshots():
        try:
            env = store.load(path)
            out.append({"name": path.stem.replace(".ntnxsnap", ""),
                        "path": str(path), "collected_at": env.collected_at,
                        "summary": env.summary()})
        except Exception:  # noqa: BLE001 — skip corrupt snapshots
            continue
    return out


def _m_export(_req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    env = SnapshotStore(snapshots_dir()).load(params["snapshot"])
    out = params.get("out") or str(exports_dir() / "environment.xlsx")
    ExcelExporter().export(env, out)
    return {"path": out}


_METHODS = {
    "ping": _m_ping,
    "inventory": _m_inventory,
    "list_snapshots": _m_list_snapshots,
    "export": _m_export,
}


# ─── wire helpers ──────────────────────────────────────────────────────


def _send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _notify(method: str, params: dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "method": method, "params": params})


def _error(req_id: Any, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})
