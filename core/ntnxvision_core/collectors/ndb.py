"""NDB (Nutanix Database Service) collector.

Unlike the other collectors, NDB talks to the Era control plane via `NdbConnection`
(separate endpoint + credentials). The engine only runs this collector when NDB
credentials are supplied; otherwise it is skipped.

Because it uses a different connection object, this collector does not extend the base
`Collector` (which is bound to a `PrismConnection`). The engine calls `collect_ndb`
directly with an `NdbConnection`.

Field mapping checked against NDB 2.5.1.1 (2026-11-06):

- A DB server's ``type`` is always ``DBSERVER``; its engine is ``databaseType`` and its
  version the ``application_version`` property. vCPU and memory are not on the server: they
  come from its compute profile (``compute_profile_id`` property), as do the software and
  network profile names.
- Servers, time machines and snapshots name their Nutanix cluster by NDB id
  (``nxClusterId`` / ``sourceNxClusters``), resolved through ``clusters``.
- A database names its server in ``databaseNodes[].dbserver``; a time machine its database
  only by ``databaseId``.
- Clones are not in ``databases``: they come from ``clones`` and point at the source time
  machine with ``parentTimeMachineId``.
- Maintenance windows list their servers and tasks only with `load-task-associations`.
- NDB's own cluster name is replaced by Prism Central's when PC collected the same cluster
  (matched on ``nxClusterUUID``); the Clusters list keeps NDB's name.
- Profiles, SLAs, snapshots, maintenance windows and tags are read when the NDB version has
  them; a missing endpoint skips that list only.
- The appliance (one ``NdbAppliance`` row) comes from four endpoints, each optional:
  ``downloads/versions`` (its "ERA Server" component carries the NDB version and build; the
  ``ERA_HOST`` rows are the DB servers' agents), ``ha`` (``serverNodes`` are the NDB server
  VMs; ``clusterIP`` is set and the HA statuses stop being ``invalid`` only with HA on),
  ``config/era-server`` (VM name and id, the cluster it runs on, DNS, NTP, timezone, SMTP;
  the SMTP credentials are never read into the model) and ``config/about-era``.
  ``/era/v0.9/about`` does not exist on 2.5.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.api.ndb_client import NdbConnection
from ntnxvision_core.models import (
    Database,
    DatabaseServer,
    NdbAppliance,
    NdbCluster,
    NdbMaintenanceWindow,
    NdbProfile,
    NdbSla,
    NdbSnapshot,
    NdbTag,
    TimeMachine,
)


def collect_ndb(conn: NdbConnection, env: Any) -> None:
    """Populate env.database_servers / databases / time_machines and the NDB lists."""
    ctx = _Context(
        clusters=_optional(conn, "clusters"),
        profiles=_optional(conn, "profiles"),
    )
    _clusters(ctx, env)
    _appliance(conn, ctx, env)
    _profiles(ctx, env)
    servers = conn.get("dbservers", params={"detailed": "true"}) or []
    dbs = conn.get("databases", params={"detailed": "true"}) or []
    clones = _optional(conn, "clones", {"detailed": "true"})
    tms = conn.get("tms", params={"detailed": "true"}) or []
    snapshots = _optional(conn, "snapshots")
    ctx.tm_names = {tm.get("id"): tm.get("name") for tm in tms}
    _database_servers(ctx, servers, env)
    _databases(ctx, dbs, clones, env)
    _time_machines(ctx, tms, dbs, clones, snapshots, env)
    _slas(_optional(conn, "slas"), tms, env)
    _snapshots(ctx, snapshots, env)
    _maintenance(_optional(conn, "maintenance", {"load-task-associations": "true"}), env)
    _tags(_optional(conn, "tags"), env)
    _use_prism_cluster_names(ctx, env)
    env.collection_notes["ndb"] = "ok"


class _Context:
    """Lookups shared by the mappers (ids to names)."""

    def __init__(self, clusters: list[dict[str, Any]], profiles: list[dict[str, Any]]) -> None:
        self.cluster_names = {c.get("id"): c.get("name") for c in clusters}
        #: NDB cluster name -> the Nutanix cluster UUID it was registered with.
        self.cluster_uuids = {c.get("name"): c.get("nxClusterUUID") for c in clusters}
        self.clusters = clusters
        self.profiles = profiles
        self.profile_by_id = {p.get("id"): p for p in profiles}
        self.tm_names: dict[Any, Any] = {}

    def cluster_name(self, cluster_id: Any) -> str | None:
        """The registered cluster's name, else its NDB id (never blank when an id is known)."""
        return self.cluster_names.get(cluster_id) or cluster_id

    def profile_name(self, profile_id: Any) -> str | None:
        p = self.profile_by_id.get(profile_id)
        return p.get("name") if p else None


def _optional(conn: NdbConnection, path: str, params: dict[str, Any] | None = None
              ) -> list[dict[str, Any]]:
    """A list endpoint that older NDB versions may not have."""
    try:
        rows = conn.get(path, params=params)
    except NutanixApiError:
        return []
    return rows if isinstance(rows, list) else []


def _optional_obj(conn: NdbConnection, path: str) -> dict[str, Any]:
    """An object endpoint that older NDB versions may not have."""
    try:
        body = conn.get(path)
    except NutanixApiError:
        return {}
    return body if isinstance(body, dict) else {}


def ndb_version(conn: NdbConnection) -> tuple[str | None, dict[str, Any]]:
    """NDB's own version and its ``downloads/versions`` row (the "ERA Server" component)."""
    rows = _optional(conn, "downloads/versions")
    server = next((r for r in rows if str(r.get("component") or "").upper()
                   in ("ERA SERVER", "ERA_SERVER")), {})
    return _known(server.get("version")), server


def _appliance(conn: NdbConnection, ctx: _Context, env: Any) -> None:
    version, row = ndb_version(conn)
    ha = _optional_obj(conn, "ha")
    server = _optional_obj(conn, "config/era-server")
    about = _optional_obj(conn, "config/about-era")
    nodes = [n for n in ha.get("serverNodes") or [] if isinstance(n, dict)]
    host = getattr(getattr(conn, "creds", None), "host", None)
    statuses = {str(s).lower() for s in (ha.get("eraServiceHAStatus"), ha.get("repoHAStatus"))
                if s}
    ha_on = (bool(ha.get("clusterIP")) or len(nodes) > 1
             or bool(statuses - {"invalid", "disabled", "not_configured"})) if ha else None
    ips = [str(n["ip"]) for n in nodes if n.get("ip")] or (
        [str(row["machineIp"])] if row.get("machineIp") else [])
    cluster_id = server.get("clusterId") or next(
        (n.get("nxClusterId") for n in nodes if n.get("nxClusterId")), None)
    smtp_config = server.get("smtpConfig")
    smtp: dict[str, Any] = smtp_config if isinstance(smtp_config, dict) else {}
    env.ndb_appliances.append(NdbAppliance(
        id=str(server.get("vmId") or row.get("machineERAId") or host or "ndb"),
        name=about.get("eraName") or server.get("vmName")
        or next((n.get("hostname") for n in nodes if n.get("hostname")), None) or host,
        host=host,
        version=version,
        build=_known(row.get("buildId")),
        build_date=_known(row.get("date")),
        cluster_name=ctx.cluster_name(cluster_id) if cluster_id else None,
        ip_addresses=ips,
        ha_enabled=ha_on,
        ha_status=ha.get("eraServiceHAStatus"),
        cluster_ip=ha.get("clusterIP"),
        server_vms=[str(n["hostname"]) for n in nodes if n.get("hostname")],
        registered_clusters=len(ctx.clusters),
        multi_cluster=about.get("multiClusterEnabled"),
        timezone=server.get("timezone") or None,
        dns_servers=[str(s) for s in server.get("dnsServers") or [] if s],
        ntp_servers=[str(s) for s in server.get("ntpServers") or [] if s],
        smtp_server=smtp.get("smtpServerIPPort") or None,
    ))


def _known(value: Any) -> str | None:
    """NDB writes ``<Not Available>`` / ``NA`` where it has no value."""
    text = str(value).strip() if value is not None else ""
    return None if text.upper() in ("", "NA", "<NOT AVAILABLE>", "NONE") else text


def _clusters(ctx: _Context, env: Any) -> None:
    for c in ctx.clusters:
        hv = " ".join(str(x) for x in (c.get("hypervisorType"), c.get("hypervisorVersion")) if x)
        env.ndb_clusters.append(NdbCluster(
            id=c.get("id", ""),
            name=c.get("name"),
            ip_addresses=c.get("ipAddresses") or [],
            status=c.get("status"),
            hypervisor=hv or None,
            cloud_type=c.get("cloudType"),
            description=c.get("description") or None,
        ))


def _profiles(ctx: _Context, env: Any) -> None:
    for p in ctx.profiles:
        version = _latest_version(p)
        env.ndb_profiles.append(NdbProfile(
            id=p.get("id", ""),
            name=p.get("name"),
            profile_type=p.get("type"),
            db_engine=_engine(p.get("engineType")),
            topology=p.get("topology"),
            version=version.get("version") or p.get("latestVersion"),
            details=_profile_details(p.get("type"), _props(version)),
            system=p.get("systemProfile"),
            status=p.get("status"),
        ))


def _database_servers(ctx: _Context, servers: list[dict[str, Any]], env: Any) -> None:
    for srv in servers:
        props = _props(srv)
        compute = _props(_latest_version(ctx.profile_by_id.get(props.get("compute_profile_id"))
                                         or {}))
        cores = _compute_cores(compute)
        engine = srv.get("databaseType") or props.get("application_type")
        if srv.get("type") not in (None, "DBSERVER"):
            engine = engine or srv.get("type")  # older NDB put the engine in `type`
        env.database_servers.append(
            DatabaseServer(
                id=srv.get("id", ""),
                name=srv.get("name"),
                db_engine=_engine(engine),
                engine_version=props.get("application_version"),
                ip_addresses=srv.get("ipAddresses", []) or [],
                status=srv.get("status"),
                cluster_name=ctx.cluster_name(srv.get("nxClusterId")),
                vm_cores=_as_int(props.get("vm_cpu_count")) or cores,
                memory_gb=(_as_float(props.get("vm_memory_gb"))
                           or _as_float(compute.get("MEMORY_SIZE"))),
                os_type=props.get("os_type"),
                software_profile=ctx.profile_name(props.get("software_profile_id")),
                compute_profile=ctx.profile_name(props.get("compute_profile_id")),
                network_profile=ctx.profile_name(props.get("network_profile_id")),
                ndb_created=_as_bool(srv.get("eraCreated", props.get("isEraCreated"))),
                tags=_tags_of(srv),
                description=srv.get("description") or None,
            )
        )


def _databases(ctx: _Context, dbs: list[dict[str, Any]], clones: list[dict[str, Any]],
               env: Any) -> None:
    seen = set()
    for db in [*dbs, *clones]:
        if db.get("id") in seen:
            continue
        seen.add(db.get("id"))
        props = _props(db)
        tm = db.get("timeMachine", {}) or {}
        is_clone = bool(db.get("clone")) or db in clones
        size = _as_float(props.get("SIZE"))
        if size is not None and str(props.get("SIZE_UNIT", "GB")).upper() == "MB":
            size /= 1024
        env.databases.append(
            Database(
                id=db.get("id", ""),
                name=db.get("name"),
                db_engine=_engine(db.get("type")),
                engine_version=props.get("version"),
                db_type=(db.get("databaseClusterType")
                         or ("HA" if db.get("clustered") else "single")),
                status=db.get("status"),
                cluster_name=_node_cluster(ctx, db),
                db_server_name=_server_name(db),
                time_machine_name=tm.get("name") or ctx.tm_names.get(db.get("timeMachineId")),
                sla_name=tm.get("sla", {}).get("name") if isinstance(tm.get("sla"), dict) else None,
                size_gb=size,
                listener_port=_as_int(props.get("listener_port")),
                parameter_profile=ctx.profile_name(props.get("db_parameter_profile_id")),
                clone=is_clone,
                parent_time_machine=(ctx.tm_names.get(db.get("parentTimeMachineId"))
                                     if is_clone else None),
                tags=_tags_of(db),
                provisioned=db.get("dateCreated"),
            )
        )


def _time_machines(ctx: _Context, tms: list[dict[str, Any]], dbs: list[dict[str, Any]],
                   clones: list[dict[str, Any]], snapshots: list[dict[str, Any]],
                   env: Any) -> None:
    db_names = {d.get("id"): d.get("name") for d in dbs}
    clone_counts = Counter(c.get("parentTimeMachineId") for c in clones)
    snap_counts = Counter(s.get("timeMachineId") for s in snapshots)
    for tm in tms:
        sla = tm.get("sla", {}) or {}
        db: dict[str, Any] = tm["database"] if isinstance(tm.get("database"), dict) else {}
        clusters = [ctx.cluster_names.get(c, c) for c in tm.get("sourceNxClusters") or []]
        legacy_clones = tm.get("clones") if isinstance(tm.get("clones"), int) else None
        env.time_machines.append(
            TimeMachine(
                id=tm.get("id", ""),
                name=tm.get("name"),
                database_name=db_names.get(tm.get("databaseId")) or db.get("name")
                or tm.get("databaseName"),
                db_engine=_engine(tm.get("type")),
                cluster_name=", ".join(str(c) for c in clusters if c) or None,
                sla_name=sla.get("name"),
                status=tm.get("status"),
                schedule=_schedule(tm.get("schedule", {})),
                scope=tm.get("scope"),
                snapshot_count=snap_counts.get(tm.get("id"), 0) if snapshots else None,
                clone_count=clone_counts.get(tm.get("id"), 0) if clones or legacy_clones is None
                else legacy_clones,
                tags=_tags_of(tm),
            )
        )


def _slas(slas: list[dict[str, Any]], tms: list[dict[str, Any]], env: Any) -> None:
    used = Counter((tm.get("sla") or {}).get("id") or tm.get("slaId") for tm in tms)
    for s in slas:
        env.ndb_slas.append(NdbSla(
            id=s.get("id", ""),
            name=s.get("name"),
            continuous_days=_as_int(s.get("continuousRetention")),
            daily=_as_int(s.get("dailyRetention")),
            weekly=_as_int(s.get("weeklyRetention")),
            monthly=_as_int(s.get("monthlyRetention")),
            quarterly=_as_int(s.get("quarterlyRetention")),
            yearly=_as_int(s.get("yearlyRetention")),
            pitr=s.get("pitrEnabled"),
            system=s.get("systemSla"),
            time_machine_count=used.get(s.get("id"), 0),
        ))


def _snapshots(ctx: _Context, snapshots: list[dict[str, Any]], env: Any) -> None:
    for s in snapshots:
        env.ndb_snapshots.append(NdbSnapshot(
            id=s.get("id", ""),
            name=s.get("name"),
            time_machine_name=ctx.tm_names.get(s.get("timeMachineId")),
            snapshot_type=s.get("type"),
            status=s.get("status"),
            taken=s.get("snapshotTimeStamp") or s.get("dateCreated"),
            db_server_name=s.get("dbserverName"),
        ))


def _maintenance(windows: list[dict[str, Any]], env: Any) -> None:
    for w in windows:
        assoc = w.get("entityTaskAssoc") or []
        servers = {a.get("entityId") for a in assoc
                   if a.get("entityType") in (None, "ERA_DBSERVER")}
        tasks = sorted({_TASKS.get(a["taskType"], _title(a["taskType"]))
                        for a in assoc if a.get("taskType")})
        env.ndb_maintenance_windows.append(NdbMaintenanceWindow(
            id=w.get("id", ""),
            name=w.get("name"),
            schedule=_window(w.get("schedule") or {}, w.get("timezone")),
            status=w.get("status"),
            next_run=w.get("nextRunTime"),
            db_server_count=len(servers - {None}) if assoc else 0,
            tasks=", ".join(tasks) or None,
        ))


def _tags(tags: list[dict[str, Any]], env: Any) -> None:
    for t in tags:
        env.ndb_tags.append(NdbTag(
            id=t.get("id", ""),
            name=t.get("name"),
            entity_type=t.get("entityType"),
            required=t.get("required"),
            status=t.get("status"),
            description=t.get("description") or None,
        ))


def _use_prism_cluster_names(ctx: _Context, env: Any) -> None:
    """Name NDB's clusters as Prism Central does when PC collected the same cluster.

    NDB registers a cluster under a name of its own ("NDB-Cluster-…"); matched on the
    Nutanix cluster UUID, every NDB row shows the cluster name the rest of the report uses.
    """
    pc_names = {c.uuid: c.name for c in getattr(env, "clusters", []) if c.uuid and c.name}
    rename = {name: pc_names[uuid] for name, uuid in ctx.cluster_uuids.items()
              if name and uuid in pc_names}
    if not rename:
        return
    for row in [*env.ndb_appliances, *env.database_servers, *env.databases,
                *env.time_machines]:
        if row.cluster_name:
            row.cluster_name = ", ".join(rename.get(n, n) for n in row.cluster_name.split(", "))


# ─── helpers ───────────────────────────────────────────────────────────


_ENGINES = {
    "postgres_database": "PostgreSQL", "mysql_database": "MySQL", "mariadb_database": "MariaDB",
    "mongodb_database": "MongoDB", "oracle_database": "Oracle", "sqlserver_database": "SQL Server",
    "saphana_database": "SAP HANA", "Generic": "Any",
}


def _engine(value: Any) -> str | None:
    """`postgres_database` -> `PostgreSQL` (unknown engines pass through)."""
    if not value:
        return None
    return _ENGINES.get(str(value), str(value))


def _props(entity: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in entity.get("properties", []) or []:
        if "name" in p:
            out[p["name"]] = p.get("value")
    return out


def _latest_version(profile: dict[str, Any] | None) -> dict[str, Any]:
    if not profile:
        return {}
    versions = profile.get("versions") or []
    latest = profile.get("latestVersionId")
    for v in versions:
        if v.get("id") == latest:
            return dict(v)
    return dict(versions[-1]) if versions else {}


def _compute_cores(props: dict[str, Any]) -> int | None:
    cpus, per = _as_int(props.get("CPUS")), _as_int(props.get("CORE_PER_CPU")) or 1
    return cpus * per if cpus else None


def _profile_details(kind: Any, props: dict[str, Any]) -> str | None:
    if kind == "Compute":
        cpus, per, mem = props.get("CPUS"), props.get("CORE_PER_CPU"), props.get("MEMORY_SIZE")
        if cpus:
            shape = f"{cpus} vCPU x {per or 1} core"
            return f"{shape}, {mem} GB" if mem else shape
    if kind == "Network":
        vlan = props.get("VLAN_NAME")
        return f"{vlan} ({props.get('VLAN_TYPE')})" if vlan and props.get("VLAN_TYPE") else vlan
    return None


def _server_name(db: dict[str, Any]) -> str | None:
    found = [((n.get("dbserver") or {}).get("name")) for n in db.get("databaseNodes") or []]
    names = [str(n) for n in found if n]
    if names:
        return ", ".join(dict.fromkeys(names))
    cluster = db.get("dbserverLogicalCluster") or db.get("dbserverlogicalCluster")
    return cluster.get("name") if isinstance(cluster, dict) else None


def _node_cluster(ctx: _Context, db: dict[str, Any]) -> str | None:
    for n in db.get("databaseNodes") or []:
        cid = (n.get("dbserver") or {}).get("nxClusterId")
        if cid:
            return ctx.cluster_name(cid)
    return None


def _tags_of(entity: dict[str, Any]) -> list[str]:
    out = []
    for t in entity.get("tags") or []:
        name = t.get("tagName") or t.get("name")
        if name:
            out.append(f"{name}={t.get('value')}" if t.get("value") not in (None, "") else name)
    return out


_TASKS = {"OS_PATCHING": "OS patching", "DB_PATCHING": "DB patching"}

_DAYS = {"SUNDAY": "Sunday", "MONDAY": "Monday", "TUESDAY": "Tuesday", "WEDNESDAY": "Wednesday",
         "THURSDAY": "Thursday", "FRIDAY": "Friday", "SATURDAY": "Saturday"}


def _schedule(schedule: dict[str, Any]) -> str | None:
    """`Daily @ 01:00; logs every 30 min; weekly Sun; monthly day 1`."""
    if not schedule:
        return None
    snap = schedule.get("snapshotTimeOfDay", {})
    if not snap:
        return schedule.get("name")
    parts = [f"Daily @ {snap.get('hours', 0):02d}:{snap.get('minutes', 0):02d}"]
    cont = schedule.get("continuousSchedule") or {}
    if cont.get("enabled") and cont.get("logBackupInterval"):
        parts.append(f"logs every {cont['logBackupInterval']} min")
    weekly = schedule.get("weeklySchedule") or {}
    if weekly.get("enabled") and weekly.get("dayOfWeek"):
        parts.append(f"weekly {_DAYS.get(weekly['dayOfWeek'], weekly['dayOfWeek'])[:3]}")
    monthly = schedule.get("monthlySchedule") or {}
    if monthly.get("enabled") and monthly.get("dayOfMonth"):
        parts.append(f"monthly day {monthly['dayOfMonth']}")
    return "; ".join(parts)


def _window(schedule: dict[str, Any], tz: Any) -> str | None:
    if not schedule:
        return None
    rec = _title(schedule.get("recurrence"))
    day = _DAYS.get(str(schedule.get("dayOfWeek")), schedule.get("dayOfWeek"))
    start = str(schedule.get("startTime") or "")[:5]
    zone = schedule.get("timeZone") or tz or ""
    dur = schedule.get("duration")
    when = " ".join(str(x) for x in (day, start, zone) if x)
    text = ", ".join(x for x in (rec, when) if x)
    return f"{text}, {dur} h" if dur else text or None


def _title(value: Any) -> str:
    return str(value or "").replace("_", " ").capitalize()


def _as_int(value: Any) -> int | None:
    try:
        return int(float(value)) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool) or value is None:
        return value
    return str(value).lower() == "true"
