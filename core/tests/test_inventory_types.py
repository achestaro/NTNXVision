"""The Inventory screen's object-type list: Buckets, the NKP labels, vCenter-only types."""

from __future__ import annotations

from typing import Any

import pytest

from ntnxvision_core.api import PrismCredentials
from ntnxvision_core.engine import collect_environment
from ntnxvision_core.exporters import tables as T
from ntnxvision_core.inventory import InventoryService, has_vcenter_data, type_counts
from ntnxvision_core.models import Environment
from ntnxvision_core.models.environment import Cluster, Host, PortGroup


@pytest.fixture
def env(demo: Any) -> Any:
    return collect_environment(PrismCredentials(host="pc01.demo.local", username="admin",
                                                password="demo"))


def test_buckets_are_an_inventory_type_after_object_stores() -> None:
    types = T.INVENTORY_TYPES
    assert types.index("buckets") == types.index("object_stores") + 1
    assert T.SPECS["buckets"].group == T.SPECS["object_stores"].group


def test_nkp_labels() -> None:
    assert T.SPECS["kubernetes_clusters"].label == "K8S Cluster (NKP)"
    assert T.SPECS["kubernetes_nodes"].label == "K8S Nodes (NKP)"
    assert T.SPECS["kubernetes_workloads"].label == "K8S Workloads (NKP)"


def test_demo_buckets_in_the_grid(env: Any) -> None:
    assert env.buckets, "the demo estate has buckets"
    counts = {t["type"]: t for t in type_counts(env)}
    assert counts["buckets"]["count"] == len(env.buckets)
    assert counts["buckets"]["label"] == "Buckets"
    res = InventoryService().query(env, "buckets", {}, None, {"offset": 0, "limit": 50})
    keys = [c["key"] for c in res["columns"]]
    assert keys[0] == "cluster"
    for key in ("name", "store", "owner", "versioning", "worm", "retention", "lifecycle",
                "cors", "website", "nfs", "notifications", "replication", "objects", "used"):
        assert key in keys, key
    legal = next(r for r in res["rows"] if r["cells"]["name"] == "legal-hold")
    assert legal["cells"]["worm"] == "Yes" and legal["cells"]["retention"] == 2555
    assert legal["cells"]["store"] == "objects-01"
    assert {f["key"] for f in res["facets"]} >= {"store", "versioning", "worm"}
    detail = InventoryService().detail(env, "buckets", legal["id"])
    labels = {f["label"] for f in detail["fields"]}
    assert {"Object store", "Owner", "WORM / Object Lock", "Retention (days)"} <= labels


def test_object_stores_grid_has_domain_and_vip(env: Any) -> None:
    res = InventoryService().query(env, "object_stores", {}, None, {"offset": 0, "limit": 5})
    keys = [c["key"] for c in res["columns"]]
    assert keys[0] == "cluster" and {"vip", "domain", "buckets", "objects"} <= set(keys)


def _ahv_env(**kw: Any) -> Environment:
    return Environment(clusters=[Cluster(uuid="c1", name="AHV-01", hypervisor_types=["AHV"])],
                       hosts=[Host(uuid="h1", name="n1", hypervisor_type="AHV")], **kw)


def test_port_groups_hidden_without_vcenter_data() -> None:
    env = _ahv_env()
    assert not has_vcenter_data(env)
    assert "port_groups" not in {t["type"] for t in type_counts(env)}


@pytest.mark.parametrize("change", ["esxi_cluster", "vcenter_address", "vcenter_note",
                                    "esxi_host", "port_group"])
def test_port_groups_listed_with_vcenter_data(change: str) -> None:
    env = _ahv_env()
    if change == "esxi_cluster":
        env.clusters[0].hypervisor_types = ["ESX"]
    elif change == "vcenter_address":
        env.clusters[0].vcenter_address = "192.0.2.10"
    elif change == "vcenter_note":
        env.collection_notes["vcenter"] = "skipped: not connected"
    elif change == "esxi_host":
        env.hosts[0].hypervisor_type = "VMware ESXi"
    else:
        env.port_groups.append(PortGroup(uuid="p1", name="VM Network", cluster_name="AHV-01"))
    assert "port_groups" in {t["type"] for t in type_counts(env)}


def test_demo_esxi_estate_lists_port_groups(env: Any) -> None:
    assert "port_groups" in {t["type"] for t in type_counts(env)}


# ─── Files shares ──────────────────────────────────────────────────────


def test_file_server_vms_then_shares_listed_after_file_servers() -> None:
    types = T.INVENTORY_TYPES
    assert types.index("file_server_vms") == types.index("file_servers") + 1
    assert types.index("file_shares") == types.index("file_servers") + 2
    assert T.SPECS["file_shares"].group == T.SPECS["file_servers"].group == T.SERVICES
    assert T.SPECS["file_server_vms"].group == T.SERVICES


def test_demo_file_shares_in_the_grid(demo: Any) -> None:
    env = collect_environment(PrismCredentials(host="pc02.demo.local", username="admin",
                                               password="demo"))
    assert env.file_shares, "the pc02 demo estate runs Nutanix Files"
    counts = {t["type"]: t for t in type_counts(env)}
    assert counts["file_shares"]["count"] == len(env.file_shares)
    res = InventoryService().query(env, "file_shares", {}, None, {"offset": 0, "limit": 50})
    keys = [c["key"] for c in res["columns"]]
    assert keys[:4] == ["cluster", "name", "server", "protocol"]
    assert {"type", "path", "max", "used", "multiprotocol", "abe", "ca", "encryption",
            "compression", "previous", "read_only"} <= set(keys)
    servers = {f.name: f.cluster_name for f in env.file_servers}
    for row in res["rows"]:
        assert row["cells"]["cluster"] == servers[row["cells"]["server"]]
        # v3 puts the protocol in the share type: not shown as a type.
        assert row["cells"]["type"] != row["cells"]["protocol"]
    assert "file server" in res["facts"]


def _share(**kw: Any) -> Any:
    from ntnxvision_core.models.environment import FileServer, FileShare

    env = Environment(file_servers=[FileServer(uuid="fs1", name="fs-01", cluster_name="C1")],
                      file_shares=[FileShare(uuid="s1", name="home", file_server_name="fs-01",
                                             **kw)],
                      collection_notes={"files": "ok"})
    res = InventoryService().query(env, "file_shares", {}, None, {"offset": 0, "limit": 5})
    return res["rows"][0]["cells"]


def test_file_share_cells_from_the_v4_model() -> None:
    cells = _share(share_type="Distributed", protocol="SMB/NFS", path="/home",
                   max_size_bytes=2 * 1024**4, access_based_enumeration=True,
                   continuous_availability=False, compression=True)
    assert cells["cluster"] == "C1" and cells["type"] == "Distributed"
    assert cells["multiprotocol"] == "Yes" and cells["abe"] == "Yes" and cells["ca"] == "No"
    assert cells["max"] == 2 * 1024**4 and cells["path"] == "/home"
    assert _share(share_type="SMB", protocol="SMB")["type"] is None


# ─── Database Services (NDB) ───────────────────────────────────────────

NDB_TYPES = ["ndb_appliance", "databases", "database_servers", "time_machines", "ndb_snapshots",
             "ndb_clusters", "ndb_profiles", "ndb_slas", "ndb_maintenance_windows", "ndb_tags"]


def test_ndb_types_have_their_own_group() -> None:
    types = T.INVENTORY_TYPES
    start = types.index("ndb_appliance")
    assert types[start:start + len(NDB_TYPES)] == NDB_TYPES
    assert {T.SPECS[t].group for t in NDB_TYPES} == {T.NDB}
    assert T.NDB == "Database Services (NDB)"


@pytest.fixture
def ndb_env(demo: Any) -> Any:
    from ntnxvision_core.api.ndb_client import NdbCredentials

    return collect_environment(
        PrismCredentials(host="pc01.demo.local", username="admin", password="demo"),
        ndb_creds=NdbCredentials(host="ndb.demo.local", username="admin", password="demo"))


def test_demo_ndb_types_in_the_grid(ndb_env: Any) -> None:
    counts = {t["type"]: t for t in type_counts(ndb_env)}
    svc = InventoryService()
    for type_id in NDB_TYPES:
        spec = T.SPECS[type_id]
        assert counts[type_id]["count"] == len(spec.items(ndb_env)) > 0, type_id
        assert counts[type_id]["group"] == T.NDB
        res = svc.query(ndb_env, type_id, {}, None, {"offset": 0, "limit": 5})
        if spec.cluster_col is not None:
            assert res["columns"][0]["key"] == "cluster", type_id
            assert res["rows"][0]["cells"]["cluster"], type_id
        detail = svc.detail(ndb_env, type_id, res["rows"][0]["id"])
        assert detail["fields"], type_id
    groups = [t["group"] for t in type_counts(ndb_env)]
    # One NDB heading, right after Services.
    first = groups.index(T.NDB)
    assert groups[first - 1] == T.SERVICES
    assert groups.count(T.NDB) == len(NDB_TYPES)
    assert groups[first:first + len(NDB_TYPES)] == [T.NDB] * len(NDB_TYPES)


def test_db_servers_snapshots_and_time_machines_name_their_cluster(ndb_env: Any) -> None:
    svc = InventoryService()
    for type_id in ("database_servers", "time_machines", "ndb_snapshots"):
        res = svc.query(ndb_env, type_id, {}, None, {"offset": 0, "limit": 50})
        assert all(r["cells"]["cluster"] for r in res["rows"]), type_id
    srv = svc.query(ndb_env, "database_servers", {}, None, {"offset": 0, "limit": 1})
    assert {"engine", "version", "ips", "status", "cores", "memory"} <= {
        c["key"] for c in srv["columns"]}


def test_ndb_detail_types_hidden_without_ndb(env: Any) -> None:
    listed = {t["type"] for t in type_counts(env)}
    assert "databases" in listed  # still discoverable, as "not collected"
    assert not listed & {"ndb_appliance", "database_servers", "ndb_profiles", "ndb_tags"}


def test_ndb_detail_types_listed_after_an_empty_ndb_run() -> None:
    env = _ahv_env(collection_notes={"ndb": "ok"})
    listed = {t["type"]: t["count"] for t in type_counts(env)}
    assert listed["ndb_profiles"] == 0 and listed["database_servers"] == 0


def test_demo_ndb_appliance_row(ndb_env: Any) -> None:
    svc = InventoryService()
    res = svc.query(ndb_env, "ndb_appliance", {}, None, {"offset": 0, "limit": 5})
    assert res["columns"][0]["key"] == "cluster"
    [row] = res["rows"]
    cells = row["cells"]
    assert cells["cluster"] and cells["name"] == "ndb-server-01"
    assert cells["host"] == "ndb.demo.local" and cells["version"] == "2.5.6"
    assert {"ha", "servers", "clusters", "timezone"} <= {c["key"] for c in res["columns"]}


def test_vcpu_ratios_are_toned_against_the_target() -> None:
    """Over the target is "error" (bold red on screen), at or under it "ok" (green)."""
    from ntnxvision_core.inventory import _ratio_parts

    over = _ratio_parts(3.0, 4.6, 6.1)
    assert [p["tone"] for p in over] == ["error", None, "error"]
    assert "".join(p["text"] for p in over) == "4.6 : 1 · N-1 6.1 : 1"
    assert [p["tone"] for p in _ratio_parts(3.0, 3.0)] == ["ok"]
    assert [p["tone"] for p in _ratio_parts(3.0, 2.4)] == ["ok"]
    assert [p["tone"] for p in _ratio_parts(None, 4.6)] == [None]
    assert [p["tone"] for p in _ratio_parts(3.0, 4.6, None)] == ["error", None, None]
