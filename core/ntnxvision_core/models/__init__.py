"""Normalized, version-independent inventory data model.

Both v3 and v4 API payloads are mapped into these Pydantic models so that the rest
of the application (store, exporters, UI) never has to know which API version a given
field came from.
"""

from ntnxvision_core.models.environment import (
    Cluster,
    DataProtectionSummary,
    Environment,
    FlowSummary,
    Host,
    LicenseInfo,
    Nic,
    PrismCentral,
    StorageContainer,
    Subnet,
    VirtualDisk,
    VirtualMachine,
)

__all__ = [
    "Environment",
    "PrismCentral",
    "Cluster",
    "Host",
    "StorageContainer",
    "VirtualMachine",
    "VirtualDisk",
    "Nic",
    "Subnet",
    "LicenseInfo",
    "DataProtectionSummary",
    "FlowSummary",
]
