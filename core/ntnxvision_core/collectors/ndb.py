"""NDB (Nutanix Database Service) collector.

Unlike the other collectors, NDB talks to the Era control plane via `NdbConnection`
(separate endpoint + credentials). The engine only runs this collector when NDB
credentials are supplied; otherwise it is skipped.

Because it uses a different connection object, this collector does not extend the base
`Collector` (which is bound to a `PrismConnection`). The engine calls `collect_ndb`
directly with an `NdbConnection`.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.ndb_client import NdbConnection
from ntnxvision_core.models import Database, DatabaseServer, TimeMachine


def collect_ndb(conn: NdbConnection, env: Any) -> None:
    """Populate env.database_servers / databases / time_machines from NDB."""
    _database_servers(conn, env)
    _databases(conn, env)
    _time_machines(conn, env)
    env.collection_notes["ndb"] = "ok"


def _database_servers(conn: NdbConnection, env: Any) -> None:
    for srv in conn.get("dbservers", params={"detailed": "true"}) or []:
        props = _props(srv)
        env.database_servers.append(
            DatabaseServer(
                id=srv.get("id", ""),
                name=srv.get("name"),
                db_engine=srv.get("type") or props.get("application_type"),
                ip_addresses=srv.get("ipAddresses", []) or [],
                status=srv.get("status"),
                cluster_name=srv.get("nxClusterId"),
                vm_cores=_as_int(props.get("vm_cpu_count")),
                memory_gb=_as_float(props.get("vm_memory_gb")),
                api_source="ndb",
            )
        )


def _databases(conn: NdbConnection, env: Any) -> None:
    for db in conn.get("databases", params={"detailed": "true"}) or []:
        tm = db.get("timeMachine", {}) or {}
        env.databases.append(
            Database(
                id=db.get("id", ""),
                name=db.get("name"),
                db_engine=db.get("type"),
                db_type=db.get("databaseClusterType") or "single",
                status=db.get("status"),
                db_server_name=db.get("dbserverLogicalCluster", {}).get("name")
                if isinstance(db.get("dbserverLogicalCluster"), dict)
                else None,
                time_machine_name=tm.get("name"),
                sla_name=tm.get("sla", {}).get("name") if isinstance(tm.get("sla"), dict) else None,
                clone=db.get("clone", False),
                provisioned=db.get("dateCreated"),
                api_source="ndb",
            )
        )


def _time_machines(conn: NdbConnection, env: Any) -> None:
    for tm in conn.get("tms", params={"detailed": "true"}) or []:
        sla = tm.get("sla", {}) or {}
        env.time_machines.append(
            TimeMachine(
                id=tm.get("id", ""),
                name=tm.get("name"),
                database_name=tm.get("clustered") and tm.get("name") or tm.get("databaseName"),
                sla_name=sla.get("name"),
                status=tm.get("status"),
                schedule=_schedule(tm.get("schedule", {})),
                clone_count=tm.get("clones"),
                api_source="ndb",
            )
        )


# ─── helpers ───────────────────────────────────────────────────────────


def _props(entity: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in entity.get("properties", []) or []:
        if "name" in p:
            out[p["name"]] = p.get("value")
    return out


def _schedule(schedule: dict[str, Any]) -> str | None:
    if not schedule:
        return None
    snap = schedule.get("snapshotTimeOfDay", {})
    if snap:
        return f"Daily @ {snap.get('hours', 0):02d}:{snap.get('minutes', 0):02d}"
    return schedule.get("name")


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
