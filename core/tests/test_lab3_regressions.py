"""Regressions from the second live lab run (pc.2024.3.1.8, cluster DEMENTOID).

Each test replays the exact payload shape (or its absence) that produced a wrong or
missing value in the lab reports, so the fixes stay locked in.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.catalog import _user_str
from ntnxvision_core.collectors.clusters import _encryption_enabled
from ntnxvision_core.collectors.dataprotection import _az_connectivity
from ntnxvision_core.collectors.licenses import _compliance
from ntnxvision_core.collectors.subnets import _vswitch
from ntnxvision_core.engine import _reclassify_pc_vms, _resolve_cross_references
from ntnxvision_core.models import (
    Cluster,
    Environment,
    Host,
    Image,
    Nic,
    Subnet,
    VirtualMachine,
    Vpc,
)

# ─── item 10: cluster reported encrypted while it's not ────────────────


def test_encryption_not_supported_is_not_encrypted() -> None:
    assert _encryption_enabled("NOT_SUPPORTED") is False
    assert _encryption_enabled("DISABLED") is False
    assert _encryption_enabled("ENABLED") is True
    assert _encryption_enabled("PARTIALLY_ENABLED") is True
    assert _encryption_enabled(None) is None
    assert _encryption_enabled("") is None


# ─── item 14: template created_by arrived as a v4 user object ──────────


def test_template_created_by_user_object() -> None:
    assert _user_str("jane.doe@example.lab") == "jane.doe@example.lab"
    assert (
        _user_str({"$reserved": {"$fv": "v4.r0", "username": "jane.doe@example.lab"}})
        == "jane.doe@example.lab"
    )
    assert _user_str({"$reserved": {"$fv": "v4.r0"}, "userName": "admin"}) == "admin"
    assert _user_str(None) is None


# ─── item 9: cluster hypervisor empty — derive from hosts ──────────────


def test_cluster_hypervisor_derived_from_hosts() -> None:
    env = Environment()
    env.clusters.append(Cluster(uuid="c1", name="DEMENTOID"))
    env.hosts.append(Host(uuid="h1", name="node1", cluster_uuid="c1", hypervisor_type="AHV"))
    _resolve_cross_references(env)
    assert env.clusters[0].hypervisor_types == ["AHV"]


# ─── item 11: hosts missing per-node VM count ──────────────────────────


def test_host_vm_count_backfilled_from_vms() -> None:
    env = Environment()
    env.clusters.append(Cluster(uuid="c1", name="DEMENTOID"))
    env.hosts.append(Host(uuid="h1", name="node1", cluster_uuid="c1"))
    env.vms.append(VirtualMachine(uuid="v1", name="a", host_uuid="h1"))
    env.vms.append(VirtualMachine(uuid="v2", name="b", host_uuid="h1"))
    env.system_vms.append(
        VirtualMachine(uuid="v3", name="NTNX-DEMENTOID-1-CVM", host_uuid="h1", vm_type="CVM")
    )
    _resolve_cross_references(env)
    assert env.hosts[0].num_vms == 3


# ─── item 12: PC VM identified by IP and moved to system VMs ───────────


def test_pc_vm_reclassified_by_ip() -> None:
    env = Environment()
    env.prism_central.host = "192.0.2.62"
    pc_vm = VirtualMachine(
        uuid="v1", name="LAB-PC", nics=[Nic(ip_addresses=["192.0.2.62"])]
    )
    user_vm = VirtualMachine(uuid="v2", name="app01", nics=[Nic(ip_addresses=["10.1.1.5"])])
    env.vms.extend([pc_vm, user_vm])
    _reclassify_pc_vms(env)
    assert [v.uuid for v in env.vms] == ["v2"]
    assert env.system_vms[0].vm_type == "PRISM_CENTRAL"
    assert env.pc_config.pc_vm_names == ["LAB-PC"]
    assert env.pc_config.node_count == 1
    assert env.pc_config.is_scale_out is False


# ─── item 15: image placement clusters resolved from uuids ─────────────


def test_image_cluster_names_resolved_from_uuids() -> None:
    env = Environment()
    env.clusters.append(Cluster(uuid="c1", name="DEMENTOID"))
    env.images.append(Image(uuid="i1", name="win10", cluster_uuids=["c1"]))
    _resolve_cross_references(env)
    assert env.images[0].cluster_names == ["DEMENTOID"]


# ─── item 18: VPC subnet membership backfilled ─────────────────────────


def test_vpc_subnets_backfilled_from_subnet_inventory() -> None:
    env = Environment()
    env.vpcs.append(Vpc(uuid="vpc1", name="prod-vpc"))
    env.subnets.append(Subnet(uuid="s1", name="overlay-1", vpc_name="prod-vpc"))
    env.subnets.append(Subnet(uuid="s2", name="vlan-10"))
    _resolve_cross_references(env)
    assert env.vpcs[0].subnet_names == ["overlay-1"]


# ─── item 13: subnet vswitch fallbacks ─────────────────────────────────


def test_subnet_vswitch_from_name_reference() -> None:
    res = {"virtual_switch_reference": {"kind": "virtual_switch", "name": "vs0"}}
    assert _vswitch(res, {}, {}) == "vs0"


def test_subnet_vswitch_from_vswitch_name() -> None:
    assert _vswitch({"vswitch_name": "br0"}, {}, {}) == "br0"
    assert _vswitch({}, {"vswitch_name": "br1"}, {}) == "br1"


def test_subnet_vswitch_uuid_resolved_via_map() -> None:
    res = {"virtual_switch_uuid": "abc-123"}
    assert _vswitch(res, {}, {"abc-123": "vs0"}) == "vs0"
    assert _vswitch(res, {}, {}) == "abc-123"  # uuid better than nothing


# ─── item 16: recovery plan locations under resources.parameters ───────


def test_recovery_plan_reads_parameters_nesting() -> None:
    from ntnxvision_core.collectors.dataprotection import DataProtectionCollector

    entity = {
        "metadata": {"uuid": "rp1"},
        "spec": {
            "name": "DR-to-DR2",
            "resources": {
                "stage_list": [{"stage_work": {"recover_entities": {
                    "entity_info_list": [{}, {}]}}}],
                "parameters": {
                    "availability_zone_list": [
                        {"availability_zone_url": "az-local-uuid"},
                        {"availability_zone_url": "https://dr2.example.lab:9440"},
                    ],
                    "network_mapping_list": [{"availability_zone_network_mapping_list": []}],
                },
            },
        },
    }

    class FakeConn:
        def v3_list(self, kind: str) -> list[dict[str, Any]]:
            return [entity] if kind == "recovery_plans" else []

    env = Environment()
    collector = DataProtectionCollector.__new__(DataProtectionCollector)
    collector.conn = FakeConn()  # type: ignore[assignment]
    collector._recovery_plans(env)
    plan = env.recovery_plans[0]
    assert plan.primary_location == "az-local-uuid"
    assert plan.recovery_location == "https://dr2.example.lab:9440"
    assert plan.network_mappings == 1
    assert plan.entities_count == 2


# ─── item 17: AZ connectivity across shape variants ────────────────────


def test_az_connectivity_shapes() -> None:
    assert _az_connectivity({"connection_status": "CONNECTED"}, {}) == "CONNECTED"
    assert (
        _az_connectivity({"connectivity_status": {"connection_status": "DISCONNECTED"}}, {})
        == "DISCONNECTED"
    )
    assert _az_connectivity({}, {"status": {"state": "COMPLETE"}}) == "COMPLETE"
    assert _az_connectivity({}, {}) is None


# ─── item 20: license compliance derived from expiry ───────────────────


def test_license_compliance_from_expired_date() -> None:
    assert _compliance({}, "2025-01-31") == "EXPIRED"
    assert _compliance({}, "2099-01-31T00:00:00Z") == "COMPLIANT"
    assert _compliance({"complianceStatus": "NON_COMPLIANT"}, "2099-01-01") == "NON_COMPLIANT"
    assert _compliance({}, None) is None


# ─── item 6: NCM report config endpoint ────────────────────────────────


def test_ncm_uses_report_configs_endpoint() -> None:
    from ntnxvision_core.collectors.ncm import NcmCollector

    calls: list[str] = []

    class FakeConn:
        def v3_list(self, kind: str) -> list[dict[str, Any]]:
            calls.append(kind)
            if kind == "report_configs":
                return [{"metadata": {"uuid": "r1"},
                         "spec": {"name": "BSC-Report", "resources": {}}}]
            if kind == "report_instances":
                return [{
                    "metadata": {"creation_time": "2026-06-09T17:15:41Z"},
                    "status": {"resources": {
                        "report_config_reference": {"uuid": "r1"},
                        "generation_end_time": "2026-06-09T17:15:41Z"}},
                }]
            raise RuntimeError(f"unexpected kind {kind}")

    env = Environment()
    collector = NcmCollector.__new__(NcmCollector)
    collector.conn = FakeConn()  # type: ignore[assignment]
    collector.collect(env)
    assert env.collection_notes["ncm_reports"] == "ok"
    assert env.ncm_reports[0].name == "BSC-Report"
    assert env.ncm_reports[0].last_run == "2026-06-09T17:15:41Z"
    assert "report_configs" in calls


# ─── item 7: OVAs fall back to the v3 endpoint ─────────────────────────


def test_ova_v3_fallback() -> None:
    from ntnxvision_core.api.errors import NutanixApiError
    from ntnxvision_core.collectors.catalog import OvaCollector

    class FakeConn:
        def v4_list(self, path: str) -> list[dict[str, Any]]:
            raise NutanixApiError(f"Not found: {path}")

        def v3_list(self, kind: str) -> list[dict[str, Any]]:
            assert kind == "ovas"
            return [{
                "metadata": {"uuid": "ova1"},
                "info": {"name": "Win10OVAtest",
                         "create_time": "2024-11-22T13:42:41Z",
                         "current_cluster_reference_list": [
                             {"kind": "cluster", "uuid": "c1", "name": "DEMENTOID"}]},
            }]

    env = Environment()
    collector = OvaCollector.__new__(OvaCollector)
    collector.conn = FakeConn()  # type: ignore[assignment]
    collector.collect(env)
    assert env.collection_notes["ovas"] == "ok"
    assert env.ovas[0].name == "Win10OVAtest"
    assert env.ovas[0].cluster_name == "DEMENTOID"


# ─── item 12: CVMs collected via the groups API ────────────────────────


def test_cvms_collected_via_groups() -> None:
    from ntnxvision_core.collectors.vms import VmCollector

    class FakeConn:
        def v3_groups(self, entity_type: str, attributes: list[str],
                      page_size: int = 250, filter_criteria: str | None = None,
                      ) -> list[dict[str, Any]]:
            assert entity_type == "vm"
            assert filter_criteria == "is_cvm==1"
            return [{
                "entity_id": "cvm-1",
                "vm_name": "NTNX-DEMENTOID-1-CVM",
                "power_state": "on",
                "cluster_name": "DEMENTOID",
                "host_name": "node1",
                "memory_size_bytes": "34359738368",
                "num_vcpus": "12",
                "ip_addresses": "192.0.2.11,192.168.5.2",
            }]

    env = Environment()
    collector = VmCollector.__new__(VmCollector)
    collector.conn = FakeConn()  # type: ignore[assignment]
    collector._collect_cvms(env)
    cvm = env.system_vms[0]
    assert cvm.vm_type == "CVM"
    assert cvm.name == "NTNX-DEMENTOID-1-CVM"
    assert cvm.memory_bytes == 34359738368
    assert cvm.nics[0].ip_addresses == ["192.0.2.11", "192.168.5.2"]
