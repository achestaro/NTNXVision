"""Regressions & features from the second lab run (run-2 fixes)."""

from __future__ import annotations

from ntnxvision_core.models import (
    AvailabilityZone,
    Cluster,
    Environment,
    ProtectionPolicy,
    RecoveryPlan,
    VirtualMachine,
)

# ─── AZ name resolution ────────────────────────────────────────────────


def test_az_uuids_resolved_to_names() -> None:
    from ntnxvision_core.engine import _resolve_availability_zone_names

    env = Environment()
    env.prism_central.uuid = "local-pc-uuid"
    env.prism_central.host = "pc.lab"
    env.availability_zones.append(AvailabilityZone(uuid="xi-uuid", name="Xi Cloud AZ"))
    env.protection_policies.append(
        ProtectionPolicy(uuid="pp1", name="dr", source_az="local-pc-uuid", target_az="xi-uuid")
    )
    env.recovery_plans.append(
        RecoveryPlan(uuid="rp1", name="rp", primary_location="local-pc-uuid",
                     recovery_location="xi-uuid")
    )
    _resolve_availability_zone_names(env)

    assert env.protection_policies[0].source_az == "Local (pc.lab)"
    assert env.protection_policies[0].target_az == "Xi Cloud AZ"
    assert env.recovery_plans[0].primary_location == "Local (pc.lab)"
    assert env.recovery_plans[0].recovery_location == "Xi Cloud AZ"


def test_az_management_url_shortened() -> None:
    from ntnxvision_core.engine import _resolve_availability_zone_names

    env = Environment()
    env.protection_policies.append(
        ProtectionPolicy(uuid="pp1", name="dr", source_az="https://remote.pc:9440/x")
    )
    _resolve_availability_zone_names(env)
    assert env.protection_policies[0].source_az == "remote.pc"


# ─── CVM / PC VM classification ────────────────────────────────────────


def test_classify_vm_types() -> None:
    from ntnxvision_core.collectors.vms import _classify_vm

    assert _classify_vm("NTNX-DEM1-A-CVM", {}, {}) == "CVM"
    assert _classify_vm("NTNX-14SM-A", {"is_cvm": True}, {}) == "CVM"
    assert _classify_vm("PC-DEMENTOID-1", {}, {}) == "PRISM_CENTRAL"
    assert _classify_vm("Win-STD-2022", {}, {}) == "USER"


def test_system_vms_counted_separately() -> None:
    env = Environment()
    env.vms.append(VirtualMachine(uuid="v1", name="app", vm_type="USER"))
    env.system_vms.append(VirtualMachine(uuid="c1", name="NTNX-A-CVM", vm_type="CVM"))
    env.system_vms.append(VirtualMachine(uuid="p1", name="PC-1", vm_type="PRISM_CENTRAL"))
    s = env.summary()
    assert s["vms"] == 1
    assert s["system_vms"] == 2


# ─── Host empty-row / hypervisor ───────────────────────────────────────


def test_hypervisor_short_forms() -> None:
    from ntnxvision_core.collectors.hosts import _short

    assert _short("AHV") == "AHV"
    assert _short("Nutanix 20230302") == "AHV"
    assert _short("VMware ESXi 8.0") == "ESXi"
    assert _short("") is None


# ─── PC config sizing ──────────────────────────────────────────────────


def test_pc_config_in_summary_and_export(tmp_path) -> None:
    from ntnxvision_core.exporters import export_environment

    env = Environment()
    env.clusters.append(Cluster(uuid="c1", name="C1"))
    env.system_vms.append(VirtualMachine(uuid="p1", name="PC-1", vm_type="PRISM_CENTRAL",
                                         num_vcpus=8, memory_bytes=32 * 1024**3))
    env.pc_config.version = "2024.3.1.8"
    env.pc_config.registered_cluster_count = 1
    s = env.summary()
    assert "templates" in s and "images" in s and "system_vms" in s

    # Exports must render with the new sections without error.
    for fmt in ("excel", "pdf", "word"):
        out = export_environment(env, fmt, tmp_path, "pc")
        assert out.exists() and out.stat().st_size > 1000
