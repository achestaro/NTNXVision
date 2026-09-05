"""Shared value normalization across API versions.

The same fact is spelled differently depending on which API answered. Reports group and
compare on these values, so a collector that returns the raw spelling makes the output
depend on which path happened to run — and in a combined multi-PC report, two PCs on
different Prism Central versions can disagree about the same hypervisor.
"""

from __future__ import annotations

#: Canonical hypervisor names used throughout the models, exports and diagrams.
AHV = "AHV"
ESXI = "ESXi"
HYPERV = "Hyper-V"


def hypervisor_name(value: str | None) -> str | None:
    """Canonical hypervisor name, or the input unchanged if it is not recognised.

    Handles every spelling the supported APIs produce, which genuinely differ: v3
    cluster payloads say ``ESXi`` while v4 says ``ESX``; host payloads carry full
    product names (``VMware ESXi``, ``Nutanix AHV``) or an ``el8.5-release-…`` build
    string for AHV.
    """
    if not value:
        return None
    v = value.lower()
    if "ahv" in v or "nutanix" in v or "kvm" in v or "el" in v.split():
        return AHV
    if "esx" in v or "vmware" in v:
        return ESXI
    if "hyper-v" in v or "hyperv" in v:
        return HYPERV
    return value
