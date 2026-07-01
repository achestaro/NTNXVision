"""Regressions from the first live lab run (pc.2024.3.1.8 / AOS 7.0).

Each test replays a real API payload shape that broke collection on the first run, so the
fixes stay locked in.
"""

from __future__ import annotations

from ntnxvision_core.models import VirtualMachine, VolumeGroup


def test_vm_categories_list_shape_does_not_drop_vm() -> None:
    """v3 categories_mapping is dict[str, list[str]] — must not raise (dropped all 34 VMs)."""
    vm = VirtualMachine(
        uuid="v1",
        name="LABPGINST-2",
        categories={
            "CalmApplication": ["dl_travp_ad"],
            "CalmProject": ["DevOps"],
            "OSType": ["Windows"],
        },
    )
    assert vm.categories["OSType"] == ["Windows"]
    assert "OSType:Windows" in vm.categories_display


def test_vm_categories_scalar_shape_still_works() -> None:
    vm = VirtualMachine(uuid="v2", name="x", categories={"Env": "prod"})
    assert vm.categories["Env"] == ["prod"]


def test_volume_group_flash_mode_string_coerced() -> None:
    """Real payload sends flash_mode='DISABLED' (str) where a bool is expected."""
    vg = VolumeGroup(uuid="vg1", name="registry", flash_mode="DISABLED")
    assert vg.flash_mode is False
    vg2 = VolumeGroup(uuid="vg2", name="x", flash_mode="ENABLED")
    assert vg2.flash_mode is True


def test_vpc_dns_servers_dict_entries_extracted() -> None:
    """VPC dns_servers arrive as [{'ip': '8.8.8.8'}] — the collector must extract the ip."""
    from ntnxvision_core.collectors.flow import _ip_of, _prefix_str

    assert _ip_of({"ip": "8.8.8.8"}) == "8.8.8.8"
    assert _ip_of("1.1.1.1") == "1.1.1.1"
    assert _ip_of(None) is None
    assert _prefix_str({"ip": "192.168.0.0", "prefix_length": 24}) == "192.168.0.0/24"


def test_xplay_handles_null_spec_and_trigger() -> None:
    """X-Play crashed with 'NoneType.get' — null spec/trigger must be tolerated."""
    from ntnxvision_core.collectors.xplay import _first, _trigger_type

    assert _first([]) == {}
    assert _first([None]) == {}
    assert _trigger_type({}) is None


def test_clean_error_condenses_pydantic_trace() -> None:
    from ntnxvision_core.notes import clean_error, pretty_label

    raw = (
        "error: 5 validation errors for VirtualMachine\ncategories.OSType\n  "
        "Input should be a valid string [type=string_type, ...]"
    )
    cleaned = clean_error(raw)
    assert "\n" not in cleaned
    assert "could not parse" in cleaned
    assert pretty_label("vms") == "Virtual Machines"
    assert pretty_label("storage_containers") == "Storage Containers"


def test_hypervisor_type_inferred_from_full_name() -> None:
    from ntnxvision_core.collectors.hosts import _hypervisor_type

    ahv = {"hypervisor": {"hypervisor_full_name": "Nutanix 20230302.103003"}}
    esxi = {"hypervisor": {"hypervisor_full_name": "VMware ESXi 8.0.2"}}
    assert _hypervisor_type(ahv) == "AHV"
    assert _hypervisor_type(esxi) == "ESXi"
