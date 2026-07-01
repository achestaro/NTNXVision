"""Normalized, version-independent inventory data model.

Both v3 and v4 API payloads are mapped into these Pydantic models so that the rest
of the application (store, exporters, UI) never has to know which API version a given
field came from.
"""

from ntnxvision_core.models.environment import (
    AddressGroup,
    AvailabilityZone,
    Bucket,
    Category,
    Cluster,
    Database,
    DatabaseServer,
    DataProtectionSummary,
    Environment,
    FileServer,
    FileShare,
    FloatingIp,
    FlowSummary,
    Host,
    LicenseInfo,
    NcmReport,
    Nic,
    ObjectStore,
    Playbook,
    PrismCentral,
    Project,
    ProtectionPolicy,
    RecoveryPlan,
    RemoteSite,
    Role,
    SecurityPolicy,
    ServiceGroup,
    StorageContainer,
    Subnet,
    TimeMachine,
    User,
    VirtualDisk,
    VirtualMachine,
    VolumeGroup,
    Vpc,
)

__all__ = [
    "Environment",
    "PrismCentral",
    # Phase 1
    "Cluster",
    "Host",
    "StorageContainer",
    "VirtualMachine",
    "VirtualDisk",
    "Nic",
    "Subnet",
    "LicenseInfo",
    # Data Protection / DR
    "ProtectionPolicy",
    "RecoveryPlan",
    "AvailabilityZone",
    "RemoteSite",
    # Flow
    "SecurityPolicy",
    "Category",
    "ServiceGroup",
    "AddressGroup",
    "Vpc",
    "FloatingIp",
    # NUS
    "FileServer",
    "FileShare",
    "ObjectStore",
    "Bucket",
    "VolumeGroup",
    # NDB
    "DatabaseServer",
    "Database",
    "TimeMachine",
    # IAM / X-Play / NCM
    "User",
    "Role",
    "Project",
    "Playbook",
    "NcmReport",
    # Summaries
    "DataProtectionSummary",
    "FlowSummary",
]
