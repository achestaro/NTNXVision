"""Normalized, version-independent inventory data model.

Both v3 and v4 API payloads are mapped into these Pydantic models so that the rest
of the application (store, exporters, UI) never has to know which API version a given
field came from.
"""

from ntnxvision_core.models.environment import (
    AddressGroup,
    Alert,
    AlertSummary,
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
    Image,
    LicenseInfo,
    NcmReport,
    Nic,
    ObjectStore,
    Ova,
    Playbook,
    PrismCentral,
    PrismCentralConfig,
    Project,
    ProtectionPolicy,
    RecoveryPlan,
    RemoteSite,
    Role,
    SecurityPolicy,
    ServiceGroup,
    StorageContainer,
    Subnet,
    Template,
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
    # PC inventory objects
    "Template",
    "Image",
    "Ova",
    "PrismCentralConfig",
    # Data Protection / DR
    "ProtectionPolicy",
    "RecoveryPlan",
    "AvailabilityZone",
    "RemoteSite",
    # Health / Alerts
    "Alert",
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
    "AlertSummary",
]
