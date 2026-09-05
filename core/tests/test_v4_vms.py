"""The v4 VM path: two hypervisor schemas, a tolerant pager, and v3 enrichment."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.vms import (
    VmCollector,
    _V3Detail,
    _v4_boot_type,
    _v4_nics,
)
from ntnxvision_core.models import Environment

AHV_VM = {
    "extId": "630049cb-66bc-4fda-8c29-edd7d2779ed3",
    "name": "move",
    "powerState": "ON",
    "numSockets": 2,
    "numCoresPerSocket": 2,
    "memorySizeBytes": 8589934592,
    "cluster": {"extId": "cluster-ahv"},
    "host": {"extId": "host-1"},
    "bootConfig": {"$objectType": "vmm.v4.ahv.config.LegacyBoot", "bootOrder": ["DISK"]},
    "nics": [
        {
            "extId": "nic-1",
            "backingInfo": {"macAddress": "50:6b:8d:8f:d3:4c", "isConnected": True},
            "networkInfo": {
                "nicType": "NORMAL_NIC",
                "subnet": {"extId": "subnet-1"},
                "ipv4Config": {"ipAddress": {"value": "198.51.100.174"}},
            },
        }
    ],
}

# ESXi is a different schema: numCpus not numSockets, a port group instead of a subnet,
# guestOsName present, and no bootConfig at all.
ESXI_VM = {
    "extId": "5500177d-c59f-40d2-ba96-5ffd8f40a8dc",
    "name": "nmm-linux-068",
    "powerState": "OFF",
    "numCpus": 1,
    "numCoresPerSocket": 1,
    "memorySizeBytes": 1073741824,
    "guestOsName": "Other Linux (64-bit)",
    "cluster": {"extId": "cluster-esxi"},
    "nics": [
        {
            "extId": "nic-2",
            "backingInfo": {
                "macAddress": "00:50:56:a0:a3:da",
                "isConnected": False,
                "adapterType": "E1000",
            },
            "networkInfo": {
                "portGroupInfo": {"name": "servers_vlan"},
                "ipAddressInfo": {},
            },
        }
    ],
}


class _Conn:
    def __init__(self, v3_vms: list[dict[str, Any]] | None = None) -> None:
        self.v3_vms = v3_vms or []

    def v4_ready(self, area: str) -> bool:
        return True

    def v4_list_tolerant(self, path: str) -> tuple[list[dict[str, Any]], int]:
        if "ahv" in path:
            return [dict(AHV_VM)], 0
        return [dict(ESXI_VM)], 2

    def v3_list(self, kind: str) -> list[dict[str, Any]]:
        return list(self.v3_vms)

    def v3_groups(self, *a: Any, **k: Any) -> list[dict[str, Any]]:
        return []


def _collect(v3_vms: list[dict[str, Any]] | None = None) -> Environment:
    env = Environment()
    VmCollector(_Conn(v3_vms)).collect(env)  # type: ignore[arg-type]
    return env


# ─── both hypervisor schemas ───────────────────────────────────────────


def test_both_endpoints_are_unioned() -> None:
    """v3 sees only PC VM intents; the ESXi endpoint is the only source for the rest."""
    names = {v.name for v in _collect().vms}
    assert names == {"move", "nmm-linux-068"}


def test_esxi_cpu_count_uses_numCpus() -> None:
    esxi = next(v for v in _collect().vms if v.name == "nmm-linux-068")
    assert esxi.num_vcpus == 1
    assert esxi.hypervisor == "ESXi"


def test_ahv_cpu_count_uses_numSockets() -> None:
    ahv = next(v for v in _collect().vms if v.name == "move")
    assert ahv.num_vcpus == 2
    assert ahv.hypervisor == "AHV"


def test_esxi_guest_os_comes_from_v4() -> None:
    esxi = next(v for v in _collect().vms if v.name == "nmm-linux-068")
    assert esxi.guest_os == "Other Linux (64-bit)"


# ─── boot type lives in the payload discriminator ──────────────────────


def test_boot_type_from_object_type() -> None:
    assert _v4_boot_type({"$objectType": "vmm.v4.ahv.config.LegacyBoot"}) == "LEGACY"
    assert _v4_boot_type({"$objectType": "vmm.v4.ahv.config.UefiBoot"}) == "UEFI"
    assert (
        _v4_boot_type(
            {"$objectType": "vmm.v4.ahv.config.UefiBoot", "isSecureBootEnabled": True}
        )
        == "SECURE_BOOT"
    )
    assert _v4_boot_type(None) is None  # ESXi VMs have no bootConfig


# ─── NIC shapes ────────────────────────────────────────────────────────


def test_ahv_nic_keeps_subnet_reference() -> None:
    nic = _v4_nics(AHV_VM)[0]
    assert nic.subnet_uuid == "subnet-1"
    assert nic.ip_addresses == ["198.51.100.174"]
    assert nic.nic_type == "NORMAL_NIC"


def test_esxi_nic_uses_the_port_group_as_its_network_name() -> None:
    nic = _v4_nics(ESXI_VM)[0]
    assert nic.subnet_name == "servers_vlan"
    assert nic.subnet_uuid is None
    assert nic.ip_addresses == []  # ipAddressInfo is empty while powered off
    assert nic.nic_type == "E1000"


# ─── v3 enrichment ─────────────────────────────────────────────────────


def _v3_entity(uuid: str, name: str) -> dict[str, Any]:
    return {
        "metadata": {"uuid": uuid, "categories_mapping": {"AppType": ["Web"]}},
        "status": {
            "name": name,
            "description": "from v3",
            "resources": {"boot_config": {"boot_type": "LEGACY"},
                          "protection_type": "UNPROTECTED"},
        },
    }


def test_uuid_match_enriches_fields_v4_does_not_carry() -> None:
    env = _collect([_v3_entity(str(AHV_VM["extId"]), "move")])
    ahv = next(v for v in env.vms if v.name == "move")
    assert ahv.description == "from v3"
    assert ahv.categories == {"AppType": ["Web"]}
    assert ahv.boot_type == "LEGACY"


def test_esxi_falls_back_to_a_unique_name_because_uuids_differ() -> None:
    """v3 reports the PC intent uuid for an ESXi VM; v4 reports the VM's own."""
    env = _collect([_v3_entity("a-totally-different-uuid", "nmm-linux-068")])
    esxi = next(v for v in env.vms if v.name == "nmm-linux-068")
    assert esxi.protection_type == "UNPROTECTED"
    assert esxi.description == "from v3"


def test_ambiguous_name_enriches_nothing() -> None:
    """7 names are duplicated across clusters on the lab PC — a guess would misattribute."""
    detail = _V3Detail({}, {"dupe": [{"guest_os": "A"}, {"guest_os": "B"}]})
    assert detail.for_vm("no-such-uuid", "dupe") == {}


def test_missing_v3_leaves_the_v4_inventory_intact() -> None:
    class _NoV3(_Conn):
        def v3_list(self, kind: str) -> list[dict[str, Any]]:
            raise NutanixApiError("v3 removed", status_code=404)

    env = Environment()
    VmCollector(_NoV3()).collect(env)  # type: ignore[arg-type]
    assert len(env.vms) == 2
    assert env.vms[0].description is None


# ─── skipped records are reported, not hidden ──────────────────────────


def test_unreadable_records_are_surfaced_in_the_collection_note() -> None:
    note = _collect().collection_notes["vms"]
    assert note.startswith("ok")
    assert "2 VM(s) skipped" in note


# ─── the tolerant pager ────────────────────────────────────────────────


class _PagedConn:
    """A v4 collection with `bad` record offsets the server cannot serialize.

    Only `v4_get` is faked; the paging and salvage logic under test is the real one.
    """

    from ntnxvision_core.api.connection import PrismConnection as _P

    _salvage_page = _P._salvage_page
    v4_list_tolerant = _P.v4_list_tolerant
    del _P

    def __init__(self, total: int, bad: set[int]) -> None:
        self.total, self.bad = total, bad
        self.calls = 0

    def v4_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls += 1
        params = params or {}
        page, limit = params.get("$page", 0), params.get("$limit", 100)
        start, stop = page * limit, min(page * limit + limit, self.total)
        window = range(start, stop)
        if any(i in self.bad for i in window):
            raise NutanixApiError("HTTP 500", status_code=500)
        return {
            "data": [{"extId": f"vm-{i}"} for i in window],
            "metadata": {"totalAvailableResults": self.total},
        }


def _pager(total: int, bad: set[int], page_size: int = 10):
    return _PagedConn(total, bad).v4_list_tolerant("p", page_size=page_size)


def test_healthy_collection_is_read_whole() -> None:
    items, skipped = _pager(25, set())
    assert [i["extId"] for i in items] == [f"vm-{i}" for i in range(25)]
    assert skipped == 0


def test_a_bad_record_costs_only_itself() -> None:
    """Without salvage, one poisoned record loses its whole page."""
    items, skipped = _pager(25, {13})
    assert skipped == 1
    assert len(items) == 24
    assert "vm-13" not in {i["extId"] for i in items}
    assert "vm-19" in {i["extId"] for i in items}  # same page, still recovered


def test_several_bad_records_across_pages() -> None:
    items, skipped = _pager(60, {39, 47})
    assert skipped == 2
    assert len(items) == 58


def test_a_failing_final_page_still_terminates() -> None:
    items, skipped = _pager(12, {11})
    assert skipped == 1
    assert len(items) == 11


def test_healthy_pages_cost_no_extra_requests() -> None:
    conn = _PagedConn(20, set())
    conn.v4_list_tolerant("p", page_size=10)
    assert conn.calls == 2  # two pages, no per-record fallback
