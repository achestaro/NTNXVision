"""NDB collector: field mapping in the shapes NDB 2.5 returns (see `collectors/ndb.py`)."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.ndb import collect_ndb
from ntnxvision_core.models import Cluster, Environment
from ntnxvision_core.synthetic.estate import build_ndb_estate


class FakeNdb:
    """Answers `get(path)` from a synthetic NDB estate; `missing` paths are 404s."""

    def __init__(self, missing: tuple[str, ...] = ()) -> None:
        est = build_ndb_estate("ndb.example.lab")
        self.tables = {"clusters": est.clusters, "dbservers": est.dbservers,
                       "databases": est.databases, "tms": est.time_machines,
                       "clones": est.clones, "profiles": est.profiles, "slas": est.slas,
                       "snapshots": est.snapshots, "maintenance": est.maintenance,
                       "tags": est.tags}
        self.missing = missing

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if path in self.missing:
            raise NutanixApiError(f"Not found: {path}", status_code=404)
        return self.tables[path]


def _collect(**kw: Any) -> Environment:
    env = Environment()
    collect_ndb(FakeNdb(**kw), env)  # type: ignore[arg-type]
    return env


def test_servers_take_engine_cluster_and_sizing_from_the_right_places() -> None:
    env = _collect()
    srv = next(s for s in env.database_servers if s.name == "pg-srv-01")
    # `type` is DBSERVER: the engine is `databaseType`; sizing is the compute profile's.
    assert (srv.db_engine, srv.engine_version, srv.cluster_name) == (
        "PostgreSQL", "15.4", "PHX-PROD-01")
    assert (srv.vm_cores, srv.memory_gb, srv.compute_profile) == (8, 64.0, "PROD_8C_64G")
    assert srv.software_profile == "POSTGRES_15_OOB" and srv.network_profile == "PG_PROD_NETWORK"
    assert {s.db_engine for s in env.database_servers} == {
        "PostgreSQL", "Oracle", "SQL Server", "MySQL", "MariaDB", "MongoDB"}


def test_databases_name_their_server_and_clones_their_source() -> None:
    env = _collect()
    db = next(d for d in env.databases if d.name == "pg-db-01")
    assert (db.db_server_name, db.cluster_name, db.time_machine_name, db.sla_name) == (
        "pg-srv-01", "PHX-PROD-01", "pg-db-01_TM", "DEFAULT_OOB_GOLD_SLA")
    assert (db.size_gb, db.listener_port, db.parameter_profile) == (
        50.0, 5432, "DEFAULT_PG_PARAMS")
    assert "env=prod" in db.tags
    clones = [d for d in env.databases if d.clone]
    assert [(c.name, c.parent_time_machine) for c in clones] == [
        ("mongo-db-06-clone", "mongo-db-06_TM"), ("mongo-db-12-clone", "mongo-db-12_TM")]


def test_time_machines_count_their_snapshots_and_clones() -> None:
    env = _collect()
    tm = next(t for t in env.time_machines if t.name == "mongo-db-06_TM")
    assert (tm.database_name, tm.db_engine, tm.cluster_name) == (
        "mongo-db-06", "MongoDB", "PHX-PROD-02")
    assert (tm.snapshot_count, tm.clone_count) == (3, 1)
    assert tm.schedule == "Daily @ 02:00; logs every 30 min; weekly Sun; monthly day 1"


def test_profiles_slas_maintenance_and_tags() -> None:
    env = _collect()
    compute = next(p for p in env.ndb_profiles if p.name == "PROD_8C_64G")
    assert (compute.profile_type, compute.details, compute.system) == (
        "Compute", "4 vCPU x 2 core, 64 GB", False)
    sla = {s.name: s for s in env.ndb_slas}
    assert sla["DEFAULT_OOB_GOLD_SLA"].time_machine_count == 3
    assert (sla["FINANCE_7Y_SLA"].weekly, sla["FINANCE_7Y_SLA"].system) == (52, False)
    mw = env.ndb_maintenance_windows[0]
    assert (mw.schedule, mw.db_server_count, mw.tasks) == (
        "Weekly, Sunday 02:00 UTC, 3 h", 2, "DB patching, OS patching")
    assert {(t.name, t.entity_type) for t in env.ndb_tags} >= {("cost-center", "CLONE")}
    assert len(env.ndb_snapshots) == sum(1 + i % 3 for i in range(12))


def test_an_older_ndb_without_the_extra_lists_still_collects_the_core() -> None:
    env = _collect(missing=("clones", "profiles", "slas", "snapshots", "maintenance", "tags"))
    assert len(env.databases) == 12 and len(env.time_machines) == 12
    srv = env.database_servers[0]
    assert srv.vm_cores is None and srv.compute_profile is None  # no profiles to resolve
    assert env.time_machines[0].snapshot_count is None
    assert not env.ndb_profiles and not env.ndb_slas
    assert env.collection_notes["ndb"] == "ok"


def test_ndb_cluster_names_follow_prism_central_when_the_uuid_matches() -> None:
    fake = FakeNdb()
    fake.tables["clusters"] = [{**fake.tables["clusters"][0], "name": "NDB-Cluster-A",
                                "nxClusterUUID": "0006-aaaa"}, fake.tables["clusters"][1]]
    env = Environment(clusters=[Cluster(uuid="0006-aaaa", name="phx-prod-01")])
    collect_ndb(fake, env)  # type: ignore[arg-type]
    assert next(s for s in env.database_servers if s.name == "pg-srv-01").cluster_name == (
        "phx-prod-01")
    assert next(t for t in env.time_machines if t.name == "pg-db-01_TM").cluster_name == (
        "phx-prod-01")
    # The unmatched cluster keeps NDB's name; NDB's own cluster list keeps its names.
    assert next(s for s in env.database_servers if s.name == "ora-srv-02").cluster_name == (
        "PHX-PROD-02")
    assert [c.name for c in env.ndb_clusters] == ["NDB-Cluster-A", "PHX-PROD-02"]
