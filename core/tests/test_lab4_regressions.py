"""Regressions from the multi-PC lab run (pc.7.5.1.1, lab-pc).

The multi-PC work was only ever exercised against synthetic data. These tests replay the
exact real payload shapes that left the "Prism Central" sheet half empty, so the fixes
stay locked in.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.pc_config import PrismCentralConfigCollector
from ntnxvision_core.collectors.vms import _classify_vm
from ntnxvision_core.models import Environment, Nic, VirtualMachine

# The PCVM name a real single-node PC deployment generates: NTNX-<pc-ip>-PCVM-<epoch>.
REAL_PCVM_NAME = "NTNX-198-51-100-4-PCVM-1700000000"


# ─── item 1: "PCVM" contains "CVM", so the PC VM was classified as a CVM ───


def test_real_pcvm_name_classifies_as_prism_central() -> None:
    assert _classify_vm(REAL_PCVM_NAME, {}, {}) == "PRISM_CENTRAL"


def test_pc_naming_variants_still_classify_as_prism_central() -> None:
    assert _classify_vm("PC-01", {}, {}) == "PRISM_CENTRAL"
    assert _classify_vm("NUTANIX-PC-A", {}, {}) == "PRISM_CENTRAL"
    assert (
        _classify_vm("anything", {}, {"categories": {"AppType": ["PRISM_CENTRAL"]}})
        == "PRISM_CENTRAL"
    )


def test_controller_vms_are_not_stolen_by_the_pc_rule() -> None:
    """The PC rule runs first now — real CVM names must still land on CVM."""
    assert _classify_vm("NTNX-host01-CVM", {}, {}) == "CVM"
    assert _classify_vm("NTNX-00XX00000001-A-CVM", {}, {}) == "CVM"
    assert _classify_vm("NTNX-00XX00000001-A", {}, {}) == "CVM"
    assert _classify_vm("some-vm", {"is_cvm": True}, {}) == "CVM"
    assert _classify_vm("app01", {}, {}) == "USER"


class _FakeConn:
    """Serves one canned /PrismGateway/services/rest/v1/cluster payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.creds = type("C", (), {"base": "https://pc.example:9440"})()

    def v4_ready(self, area: str) -> bool:
        """These regressions pin the v1 gateway path; the v4 one is in test_v4_pc_config."""
        return False

    def get_json(self, url: str) -> dict[str, Any]:
        return self.payload


def _env_with_pcvm() -> Environment:
    env = Environment()
    env.prism_central.version = "7.5.1.1"
    env.system_vms.append(
        VirtualMachine(
            uuid="pc-1",
            name=REAL_PCVM_NAME,
            vm_type="PRISM_CENTRAL",
            num_vcpus=18,
            memory_bytes=83751862272,
            nics=[Nic(ip_addresses=["198.51.100.4"])],
        )
    )
    return env


# ─── item 2: ncc_version was rendered but never assigned ───────────────


def test_ncc_version_is_read_from_the_gateway_payload() -> None:
    conn = _FakeConn({"numNodes": 1, "nccVersion": "ncc-5.3.1.1", "fullVersion": "el8.5-x"})
    env = _env_with_pcvm()
    PrismCentralConfigCollector(conn).collect(env)  # type: ignore[arg-type]
    assert env.pc_config.ncc_version == "ncc-5.3.1.1"


# ─── item 3: single-node PC reports no cluster VIP, so IPs came back empty ───


def test_ip_addresses_fall_back_to_pcvm_nics() -> None:
    """A single-node PC has no VIP: clusterExternalIPAddress is null and svmips absent."""
    conn = _FakeConn({"numNodes": 1, "clusterExternalIPAddress": None})
    env = _env_with_pcvm()
    PrismCentralConfigCollector(conn).collect(env)  # type: ignore[arg-type]
    assert env.pc_config.ip_addresses == ["198.51.100.4"]


def test_gateway_ips_win_over_the_pcvm_fallback() -> None:
    conn = _FakeConn({"numNodes": 3, "clusterExternalIPAddress": "198.51.100.9"})
    env = _env_with_pcvm()
    PrismCentralConfigCollector(conn).collect(env)  # type: ignore[arg-type]
    assert env.pc_config.ip_addresses == ["198.51.100.9"]


# ─── the sizing those blanks were hiding ───────────────────────────────


def test_pc_sizing_is_populated_once_the_pcvm_is_classified() -> None:
    conn = _FakeConn({"numNodes": 1})
    env = _env_with_pcvm()
    PrismCentralConfigCollector(conn).collect(env)  # type: ignore[arg-type]
    cfg = env.pc_config
    assert cfg.pc_vm_names == [REAL_PCVM_NAME]
    assert cfg.num_vcpus == 18
    assert cfg.memory_bytes == 83751862272
