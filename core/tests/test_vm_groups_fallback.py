"""VMs the v4 API cannot serve are recovered from the groups API, marked partial.

On the lab PC (pc.7.5.1.1) two ESXi VMs (edge-vm-01/02) answer HTTP 500 PLAT-10006 on
every v4 path, list page and single GET alike, and v3 ``vms/list`` has no ESXi VMs at
all. ``POST /api/nutanix/v3/groups`` for ``mh_vm`` still lists them, so they are read
from there (core sizing only) instead of being dropped.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from ntnxvision_core.api import PrismCredentials
from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.capacity import compute_environment
from ntnxvision_core.collectors.vms import GROUPS_PARTIAL_SOURCE, VmCollector, _groups_vm, _V3Detail
from ntnxvision_core.engine import collect_environment
from ntnxvision_core.models import Environment
from ntnxvision_core.synthetic.estate import Features, build_pc_estate
from ntnxvision_core.synthetic.mockapi import MockApi, Target
from ntnxvision_core.synthetic.profiles import make_resolver

HOST = "groups.demo.local"
PW = {"username": "admin", "password": "demo"}
# No Prism Element proxy: the groups API is the only fallback left (the last resort).
FEATURES = Features(esxi_bad_vms=2, groups_mh_vm=True, pe_proxy=False)
AUTH = "Basic YWRtaW46ZGVtbw=="  # admin:demo

# The lab row for edge-vm-02, as `v3_groups` flattens it.
NSX_EDGE_02 = {
    "entity_id": "0000d239-fb3d-40fc-acbc-4dc602480eaa", "vm_name": "edge-vm-02",
    "cluster_name": "lab-cluster-02", "cluster": "0000e44d-bc9d-4f50-a736-cc12243058af",
    "hypervisor_type": "kVMware", "num_vcpus": "4", "memory_size_bytes": "8589934592",
    "power_state": "on", "is_cvm": "0", "vm_type": "kGuestVM", "host_name": None,
    "node": "00004427-a27e-4ccc-a6d0-68da4d23b610", "capacity_bytes": "211527139328",
    "guest_os_name": "Ubuntu Linux (64-bit)",
}


def _build(epoch: int) -> Any:
    return build_pc_estate(HOST, "medium", "pc.2024.3", epoch=epoch, features=FEATURES)


@pytest.fixture
def groups_pc(demo: Any) -> Iterator[Any]:
    """A demo PC whose ESXi endpoint fails on two records and whose groups API serves
    ``mh_vm``: the lab case. Yields its estate."""
    demo.targets[HOST] = Target(host=HOST, kind="pc", build=_build)
    yield _build(0)
    demo.targets.pop(HOST, None)


def _bad(est: Any) -> list[dict[str, Any]]:
    return [v for v in est.vms if v["esxi_bad"]]


def _collect(**kw: Any) -> Environment:
    return collect_environment(PrismCredentials(host=HOST, **PW), **kw)


# ─── the mock reproduces the lab ───────────────────────────────────────


def test_mock_fails_bad_records_on_single_get_and_serves_mh_vm() -> None:
    api = MockApi(make_resolver({HOST: Target(host=HOST, kind="pc", build=_build)}))
    est = _build(0)
    bad = _bad(est)[0]
    status, body, _ = api.handle(HOST, "GET", f"/api/vmm/v4.0/esxi/config/vms/{bad['uuid']}",
                                 {}, None, AUTH)
    assert status == 500 and body["data"]["error"][0]["code"] == "PLAT-10006"
    good = next(v for v in est.vms if v["hypervisor"] == "ESXi" and not v["esxi_bad"])
    assert api.handle(HOST, "GET", f"/api/vmm/v4.0/esxi/config/vms/{good['uuid']}",
                      {}, None, AUTH)[0] == 200
    esxi_cluster = next(c for c in est.clusters if c["hypervisor"] == "ESXi")
    status, body, _ = api.handle(HOST, "POST", "/api/nutanix/v3/groups", {}, {
        "entity_type": "mh_vm", "group_member_count": 500,
        "group_member_attributes": [{"attribute": "vm_name"}, {"attribute": "is_cvm"}],
        "filter_criteria": f"is_cvm==0;cluster=in={esxi_cluster['uuid']}"}, AUTH)
    assert status == 200
    expected = sum(1 for v in est.vms if v["cluster_uuid"] == esxi_cluster["uuid"])
    assert body["total_entity_count"] == expected


# ─── recovery end to end (mock PC) ─────────────────────────────────────


def test_skipped_vms_are_recovered_with_their_groups_fields(groups_pc: Any) -> None:
    est = groups_pc
    env = _collect()
    by_uuid = {v.uuid: v for v in env.vms}
    bad = _bad(est)
    assert len(bad) == 2
    for raw in bad:
        vm = by_uuid[raw["uuid"]]
        assert vm.api_source == GROUPS_PARTIAL_SOURCE
        assert vm.name == raw["name"]
        assert vm.cluster_uuid == raw["cluster_uuid"]
        assert vm.cluster_name == raw["cluster_name"]
        assert vm.hypervisor == "ESXi"
        assert vm.num_vcpus == raw["sockets"] * raw["cores"]
        assert vm.vcpu_total == raw["sockets"] * raw["cores"]
        assert vm.memory_bytes == raw["memory"]
        assert vm.power_state == raw["power"]
        assert vm.guest_os == raw["guest_os"]
        # groups knows only the disks on Nutanix containers, like a real PC.
        on_containers = [d for d in raw["disks"] if d["container_uuid"]]
        assert vm.total_disk_bytes == sum(d["size"] for d in on_containers)
        assert len(vm.disks) == len(on_containers)
        assert {d.storage_container_uuid for d in vm.disks} == {
            d["container_uuid"] for d in on_containers}
        # NICs from `virtual_nic`: MAC, connection and guest IPs.
        assert [n.mac_address for n in vm.nics] == [n["mac"] for n in raw["nics"]]
        assert [ip for n in vm.nics for ip in n.ip_addresses] == [
            n["ip"] for n in raw["nics"] if n["ip"]]
        # The host name is resolved from the groups `node` uuid by the engine.
        assert vm.host_name == raw["host_name"]
    # Nothing else was read from groups, and nothing went missing.
    assert sum(v.api_source == GROUPS_PARTIAL_SOURCE for v in env.vms) == 2
    user_vms = [v for v in est.vms if "PCVM" not in v["name"]]
    assert len(env.vms) == len(user_vms)
    note = env.collection_notes["vms"]
    names = ", ".join(sorted((v["name"] for v in bad), key=str.casefold))
    esxi_name = next(c["name"] for c in est.clusters if c["hypervisor"] == "ESXi")
    assert note == (f"ok: Prism Element could not be read for {esxi_name} (HTTP 403), so "
                    "ESXi disks outside Nutanix containers, boot type and port group "
                    "VLANs may be missing; 2 VM(s) read from the groups API because no "
                    f"other API returned them: {names}")
    assert "—" not in note


def test_cvms_and_the_pc_vm_are_never_added_twice(groups_pc: Any) -> None:
    est = groups_pc
    env = _collect()
    assert not any(v.vm_type != "USER" for v in env.vms)
    assert not any(v.name.endswith("-CVM") for v in env.vms)
    cvms = [v for v in env.system_vms if v.vm_type == "CVM"]
    assert len(cvms) == len(est.cvms)
    pcs = [v for v in env.system_vms if v.vm_type == "PRISM_CENTRAL"]
    assert len(pcs) == 1 and pcs[0].api_source == "v4"
    uuids = [v.uuid for v in [*env.vms, *env.system_vms]]
    assert len(uuids) == len(set(uuids))


def test_scope_is_respected(groups_pc: Any) -> None:
    est = groups_pc
    esxi = next(c for c in est.clusters if c["hypervisor"] == "ESXi")
    other = next(c for c in est.clusters if c["hypervisor"] == "AHV")
    bad = {v["uuid"] for v in _bad(est)}

    only_esxi = _collect(clusters=[esxi["uuid"]])
    assert bad <= {v.uuid for v in only_esxi.vms}
    assert {v.cluster_uuid for v in only_esxi.vms} == {esxi["uuid"]}

    without_esxi = _collect(clusters=[other["uuid"]])
    assert not bad & {v.uuid for v in without_esxi.vms}
    assert not any(v.api_source == GROUPS_PARTIAL_SOURCE for v in without_esxi.vms)


def test_capacity_counts_recovered_vms(groups_pc: Any) -> None:
    est = groups_pc
    env = _collect()
    esxi_name = next(c["name"] for c in est.clusters if c["hypervisor"] == "ESXi")
    esxi = next(c for c in env.clusters if c.name == esxi_name)
    cap = compute_environment(env)[id(esxi)]
    raw = [v for v in est.vms if v["cluster_uuid"] == esxi.uuid]
    assert cap.provisioned_vcpus == sum(v["sockets"] * v["cores"] for v in raw)
    # Without Prism Element the disks outside Nutanix containers are not known.
    assert cap.provisioned_disk_bytes == sum(d["size"] for v in raw for d in v["disks"]
                                             if d["container_uuid"])


def test_demo_pc_recovers_from_prism_element_before_groups(demo: Any) -> None:
    """pc01 proxies Prism Element: the three VMs v4 fails on are read whole from it,
    and the groups API (which pc01 rejects for mh_vm) is never needed."""
    env = collect_environment(PrismCredentials(host="pc01.demo.local", **PW))
    note = env.collection_notes["vms"]
    assert note.startswith("ok: 3 ESXi VM(s) read from Prism Element")
    assert "skipped" not in note
    assert not any(v.api_source == GROUPS_PARTIAL_SOURCE for v in env.vms)


# ─── the collector against a fake connection ───────────────────────────


class _Conn:
    """v4 AHV lists one VM; ESXi skips two records (or fails outright)."""

    def __init__(self, groups: list[dict[str, Any]] | Exception, esxi_fails: bool = False
                 ) -> None:
        self.groups = groups
        self.esxi_fails = esxi_fails
        self.queries: list[tuple[str, str | None]] = []

    def v4_ready(self, area: str) -> bool:
        return True

    def v4_list_tolerant(self, path: str) -> tuple[list[dict[str, Any]], int]:
        if "ahv" in path:
            return [{"extId": "ahv-1", "name": "web-01", "numSockets": 2,
                     "cluster": {"extId": "c-ahv"}}], 0
        if self.esxi_fails:
            raise NutanixApiError("HTTP 500", status_code=500)
        return [], 2

    def v4_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        # The scope's `$filter` probe: this PC does not filter VMs server-side.
        raise NutanixApiError("HTTP 400", status_code=400)

    def v3_list(self, kind: str, **_kw: Any) -> list[dict[str, Any]]:
        return []

    def v3_groups(self, entity: str, attrs: list[str], **kw: Any) -> list[dict[str, Any]]:
        self.queries.append((entity, kw.get("filter_criteria")))
        if entity != "mh_vm":
            return []
        if isinstance(self.groups, Exception):
            raise self.groups
        return [dict(r) for r in self.groups]


NSX_EDGE_01 = {**NSX_EDGE_02, "entity_id": "0000a516-c6bd-4262-a0cf-2897d10e9750",
               "vm_name": "edge-vm-01"}
CVM_ROW = {**NSX_EDGE_02, "entity_id": "cvm-1", "vm_name": "NTNX-00XX00000001-A-CVM",
           "is_cvm": "1", "vm_type": "kCVM"}
AHV_ROW = {**NSX_EDGE_02, "entity_id": "ahv-1", "vm_name": "web-01"}


def _run(conn: _Conn) -> Environment:
    env = Environment()
    VmCollector(conn).collect(env)  # type: ignore[arg-type]
    return env


def test_lab_row_maps_into_the_vm_model() -> None:
    vm = _groups_vm(NSX_EDGE_02, _V3Detail({}, {}))
    assert vm is not None
    assert (vm.uuid, vm.name, vm.cluster_name, vm.cluster_uuid) == (
        "0000d239-fb3d-40fc-acbc-4dc602480eaa", "edge-vm-02", "lab-cluster-02",
        "0000e44d-bc9d-4f50-a736-cc12243058af")
    assert (vm.hypervisor, vm.num_vcpus, vm.memory_bytes, vm.power_state) == (
        "ESXi", 4, 8 * 1024**3, "ON")
    assert vm.total_disk_bytes == 211527139328
    assert vm.guest_os == "Ubuntu Linux (64-bit)"
    assert vm.host_uuid == "00004427-a27e-4ccc-a6d0-68da4d23b610"
    assert vm.vm_type == "USER" and vm.api_source == "v3-groups (partial)"


def test_only_missing_non_cvm_vms_are_added() -> None:
    conn = _Conn([NSX_EDGE_01, NSX_EDGE_02, CVM_ROW, AHV_ROW])
    env = _run(conn)
    assert sorted(v.name for v in env.vms) == ["edge-vm-01", "edge-vm-02", "web-01"]
    assert next(v for v in env.vms if v.name == "web-01").api_source == "v4"
    assert ("mh_vm", "is_cvm==0") in conn.queries
    assert env.collection_notes["vms"].startswith(
        "ok: 2 VM(s) read from the groups API because no other API returned them: "
        "edge-vm-01, edge-vm-02")


def test_groups_failure_keeps_the_skip_note() -> None:
    env = _run(_Conn(NutanixApiError("HTTP 500", status_code=500)))
    assert [v.name for v in env.vms] == ["web-01"]
    assert env.collection_notes["vms"] == (
        "ok: 2 VM(s) skipped: the v4 endpoint returned an internal error for those "
        "individual records")


def test_partly_recovered_reports_the_rest_as_skipped() -> None:
    env = _run(_Conn([NSX_EDGE_01]))
    note = env.collection_notes["vms"]
    assert "1 VM(s) read from the groups API" in note and "edge-vm-01" in note
    assert "1 VM(s) skipped" in note


def test_a_whole_v4_list_failing_is_recovered_too() -> None:
    env = _run(_Conn([NSX_EDGE_01, NSX_EDGE_02, AHV_ROW], esxi_fails=True))
    assert sorted(v.name for v in env.vms) == ["edge-vm-01", "edge-vm-02", "web-01"]
    assert "2 VM(s) read from the groups API" in env.collection_notes["vms"]


def test_a_whole_v4_list_failing_without_groups_is_noted() -> None:
    env = _run(_Conn(NutanixApiError("no", status_code=500), esxi_fails=True))
    assert env.collection_notes["vms"] == "ok: the v4 ESXi VM list could not be read"


def test_scoped_query_names_the_selected_clusters_and_prunes_the_rest() -> None:
    from ntnxvision_core.scope import RegisteredCluster, Scope

    conn = _Conn([NSX_EDGE_01, {**NSX_EDGE_02, "cluster": "c-other",
                                "cluster_name": "other"}])
    conn.scope = Scope(  # type: ignore[attr-defined]
        [RegisteredCluster(NSX_EDGE_01["cluster"], "lab-cluster-02"),
         RegisteredCluster("c-other", "other"), RegisteredCluster("c-ahv", "ahv")],
        [NSX_EDGE_01["cluster"], "c-ahv"])
    env = _run(conn)
    assert ("mh_vm", f"is_cvm==0;cluster=in={NSX_EDGE_01['cluster']}|c-ahv") in conn.queries
    assert "edge-vm-01" in {v.name for v in env.vms}
    assert "edge-vm-02" not in {v.name for v in env.vms}
