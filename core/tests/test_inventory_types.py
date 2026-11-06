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
