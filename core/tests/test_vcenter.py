"""Optional vCenter enrichment of ESXi clusters, against the demo's mock vCenter.

pc01's ESXi cluster (AMS-EDGE-02) reports a vCenter at 198.51.100.20; the mock serves it
there and at vcenter.demo.local. Payload shapes follow a lab vCenter 7.0.3 (values are
synthetic). vCenter fills what Nutanix cannot say (disk datastores, port group switch
types), never adds VMs, reads only the hosts of the scoped ESXi clusters, and a vCenter
failure is isolated in its own collection note.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api import PrismCredentials
from ntnxvision_core.api.vcenter_client import VcenterConnection, VcenterCredentials
from ntnxvision_core.collectors.element import disk_key
from ntnxvision_core.connectivity import test_connection as probe
from ntnxvision_core.engine import collect_environment
from ntnxvision_core.models import Environment
from ntnxvision_core.runinfo import RunTracker
from ntnxvision_core.sidecar import _vcenter_from
from ntnxvision_core.synthetic.estate import Features, build_pc_estate
from ntnxvision_core.synthetic.profiles import VCENTER_ADDRESS, VCENTER_HOST

HOST = "pc01.demo.local"
PW = {"username": "admin", "password": "demo"}


def _vc(host: str = VCENTER_HOST, password: str = "demo") -> VcenterCredentials:
    return VcenterCredentials(host=host, username="administrator@vsphere.example",
                              password=password)


def _collect(**kw: Any) -> Environment:
    return collect_environment(PrismCredentials(host=HOST, **PW),
                               areas=["clusters", "hosts", "storage_containers", "subnets",
                                      "vms"], **kw)


def _estate() -> Any:
    return build_pc_estate(HOST, "medium", "pc.2024.3", features=Features(
        esxi_bad_vms=3, vcenter_address=VCENTER_ADDRESS))


def test_disk_addresses_read_the_same_from_every_api() -> None:
    assert disk_key("SCSI.0") == "scsi0:0"
    assert disk_key("scsi.17") == "scsi1:1"  # 16 slots per SCSI controller
    assert disk_key("scsi1:1") == "scsi1:1"
    assert disk_key("ide0:1") == "ide0:1"
    assert disk_key(None) is None and disk_key("disk") is None


def test_the_sidecar_reads_an_optional_vcenter_block() -> None:
    assert _vcenter_from(None, False) is None
    assert _vcenter_from({"host": "vc.example.lab"}, False) is None  # no user
    creds = _vcenter_from({"host": "vc.example.lab", "username": "u", "password": "p",
                           "pinned_sha256": "ab" * 32}, True)
    assert creds is not None and creds.verify_ssl is False and creds.port == 443
    assert creds.pinned_sha256 == "ab" * 32
    creds = _vcenter_from({"host": "vc.example.lab", "username": "u", "password": "p",
                           "insecure": False}, True)
    assert creds is not None and creds.verify_ssl is True


def test_connection_test_speaks_vcenter(demo: Any) -> None:
    ok = probe({"mode": "vcenter", "host": VCENTER_HOST, "username": "u",
                          "password": "demo"})
    assert ok["ok"] and ok["message"].startswith("Connected to vCenter 8.0.2")
    assert ok["hosts"] == 2  # AMS-EDGE-02's two ESXi hosts
    bad = probe({"mode": "vcenter", "host": VCENTER_HOST, "username": "u",
                           "password": "wrong"})
    assert bad["status"] == "auth_failed"


def test_session_is_opened_and_closed(demo: Any) -> None:
    with VcenterConnection(_vc()) as vc:
        assert vc.flavor == "api" and vc.version
        assert isinstance(vc.get("vcenter/host"), list)
    hits = demo.api.hits
    assert hits.get((VCENTER_HOST, "POST", "/api/session"), 0) >= 1
    assert hits.get((VCENTER_HOST, "DELETE", "/api/session"), 0) >= 1


def test_vcenter_fills_datastores_and_switch_types(demo: Any) -> None:
    est = _estate()
    esxi = next(c for c in est.clusters if c["hypervisor"] == "ESXi")
    env = _collect(vcenter_creds=_vc(VCENTER_ADDRESS))
    note = env.collection_notes["vcenter"]
    assert note.startswith(f"ok: vCenter {VCENTER_ADDRESS} (8.0.2")
    assert "VM(s) matched" in note and "—" not in note
    vms = [v for v in env.vms if v.cluster_uuid == esxi["uuid"]]
    raw = {v["uuid"]: v for v in est.vms if v["cluster_uuid"] == esxi["uuid"]}
    # Same VMs as without vCenter: it only fills gaps.
    assert {v.uuid for v in vms} == set(raw)
    for vm in vms:
        disks = [d for d in vm.disks if not d.is_cdrom]
        assert len(disks) == len(raw[vm.uuid]["disks"])  # nothing duplicated
        for d in disks:
            assert d.datastore, (vm.name, d.label)
    local = [d for v in vms for d in v.disks if d.storage_container_uuid is None]
    assert local and all(d.datastore and d.datastore.endswith("-local-ds") for d in local)
    types = {p.name: p.switch_type for p in env.port_groups}
    assert types == {"servers_vlan": "DISTRIBUTED", "VM Network": "STANDARD",
                     "vMotion": "DISTRIBUTED"}


def test_vcenter_failure_is_isolated(demo: Any) -> None:
    events: list[dict[str, Any]] = []
    tracker = RunTracker(events.append, run_id="r", host=HOST)
    env = _collect(vcenter_creds=_vc(password="wrong"), tracker=tracker)
    tracker.finish()
    assert env.collection_notes["vcenter"].startswith("error:")
    assert env.vms and env.port_groups  # the Nutanix side is unaffected
    by_id = {a["id"]: a for a in tracker.final_areas()}
    assert by_id["vcenter"]["status"] == "failed"
    assert by_id["vcenter"]["api"] == "vcenter"
    assert by_id["vms"]["status"] in ("collected", "notes")


def test_vcenter_reads_nothing_outside_the_scope(demo: Any) -> None:
    est = _estate()
    ahv = next(c for c in est.clusters if c["hypervisor"] == "AHV")

    def vm_reads() -> int:
        return sum(n for (host, _m, path), n in demo.api.hits.items()
                   if host == VCENTER_HOST and path.startswith("/api/vcenter/vm"))

    before = vm_reads()
    env = _collect(vcenter_creds=_vc(), clusters=[ahv["uuid"]])
    assert env.collection_notes["vcenter"] == "skipped: no ESXi cluster in this collection"
    assert vm_reads() == before
