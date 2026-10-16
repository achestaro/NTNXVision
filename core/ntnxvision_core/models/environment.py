"""The normalized inventory schema.

A single collection run produces one populated `Environment`. Every collector maps
its (v3 or v4) API payload into these models. The `Environment` is what gets persisted
to the snapshot store and fed to the exporters.

Phase 1 covers PrismCentral, Cluster, Host, StorageContainer, VirtualMachine, Subnet
and the Licensing summary. Later phases extend DataProtectionSummary, FlowSummary, and
add Files/Objects/NDB/X-Play/NCM/IAM sections.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


def coerce_bool(value: Any) -> bool | None:
    """Coerce Nutanix's varied boolean encodings (bool / 'ENABLED' / 'true' / 1) to bool.

    Real API payloads use strings like 'ENABLED'/'DISABLED' where a bool is expected;
    strict validation would drop the whole entity, so normalize instead.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "enabled", "on", "yes", "1"):
            return True
        if v in ("false", "disabled", "off", "no", "0", ""):
            return False
    return None


class _Base(BaseModel):
    """Shared config for all inventory models.

    A wildcard 'before' validator coerces any field annotated as bool from Nutanix's
    varied encodings ('ENABLED'/'DISABLED'/'true'/1) so one oddly-typed value never drops
    the whole entity during collection.
    """

    model_config = {"extra": "ignore", "populate_by_name": True}

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_bools(cls, value: Any, info: Any) -> Any:
        field = cls.model_fields.get(info.field_name)
        anno = getattr(field, "annotation", None) if field else None
        if anno is not None and _is_bool_annotation(anno):
            return coerce_bool(value)
        return value


def _is_bool_annotation(anno: Any) -> bool:
    """True if the annotation is bool or Optional[bool] (bool | None)."""
    import typing

    if anno is bool:
        return True
    args = typing.get_args(anno)
    return bool(args) and bool in args and all(a in (bool, type(None)) for a in args)


class SourcedObject(_Base):
    """A top-level inventory object that can be attributed to the PC it came from.

    Only populated in a combined multi-Prism-Central Environment: `combine_environments`
    stamps every merged object with its source PC host so exports can attribute each row.
    Stays None for an ordinary single-PC (or Prism Element) run, where the source is the
    environment itself.
    """

    source_pc: str | None = None


# ─── Compute: NICs & disks ─────────────────────────────────────────────


class Nic(_Base):
    uuid: str | None = None
    mac_address: str | None = None
    subnet_name: str | None = None
    subnet_uuid: str | None = None
    ip_addresses: list[str] = Field(default_factory=list)
    is_connected: bool | None = None
    vlan_id: int | None = None
    nic_type: str | None = None


class VirtualDisk(_Base):
    uuid: str | None = None
    label: str | None = None
    disk_size_bytes: int | None = None
    storage_container_uuid: str | None = None
    storage_container_name: str | None = None
    bus_type: str | None = None  # SCSI / SATA / IDE / PCI / NVME
    is_cdrom: bool = False
    source_image: str | None = None


# ─── VMs ───────────────────────────────────────────────────────────────


class VirtualMachine(SourcedObject):
    uuid: str
    name: str
    cluster_name: str | None = None
    cluster_uuid: str | None = None
    power_state: str | None = None  # ON / OFF
    num_vcpus: int | None = None
    num_cores_per_vcpu: int | None = None
    memory_bytes: int | None = None
    description: str | None = None
    hypervisor: str | None = None
    guest_os: str | None = None
    host_uuid: str | None = None
    host_name: str | None = None
    is_agent_vm: bool = False
    #: USER (normal workload) | CVM (Nutanix Controller VM) | PRISM_CENTRAL | SYSTEM
    vm_type: str = "USER"
    # v3 categories_mapping is dict[str, list[str]] (a key can have multiple values);
    # older/other payloads use dict[str, str]. Normalize both to dict[str, list[str]].
    categories: dict[str, list[str]] = Field(default_factory=dict)
    protection_type: str | None = None  # UNPROTECTED / RULE / …
    protection_policy: str | None = None
    gpus: list[str] = Field(default_factory=list)
    disks: list[VirtualDisk] = Field(default_factory=list)
    nics: list[Nic] = Field(default_factory=list)
    boot_type: str | None = None
    creation_time: str | None = None
    api_source: str = "v4"  # provenance: which API version populated this

    @field_validator("categories", mode="before")
    @classmethod
    def _normalize_categories(cls, value: Any) -> dict[str, list[str]]:
        """Accept dict[str,str] or dict[str,list[str]] and normalize to lists of strings."""
        if not isinstance(value, dict):
            return {}
        out: dict[str, list[str]] = {}
        for key, val in value.items():
            if isinstance(val, list):
                out[str(key)] = [str(v) for v in val]
            elif val is None:
                out[str(key)] = []
            else:
                out[str(key)] = [str(val)]
        return out

    @property
    def categories_display(self) -> str:
        """Flatten categories to 'Key:v1,v2; Key2:v3' for reports."""
        return "; ".join(f"{k}:{','.join(v)}" for k, v in self.categories.items() if v)

    @property
    def total_disk_bytes(self) -> int:
        return sum(d.disk_size_bytes or 0 for d in self.disks if not d.is_cdrom)

    @property
    def vcpu_total(self) -> int | None:
        if self.num_vcpus is None:
            return None
        return self.num_vcpus * (self.num_cores_per_vcpu or 1)


# ─── Hosts / nodes ─────────────────────────────────────────────────────


class Host(SourcedObject):
    uuid: str
    name: str | None = None
    cluster_uuid: str | None = None
    cluster_name: str | None = None
    hypervisor_type: str | None = None
    hypervisor_version: str | None = None
    cpu_model: str | None = None
    num_cpu_sockets: int | None = None
    num_cpu_cores: int | None = None
    num_cpu_threads: int | None = None
    cpu_capacity_hz: int | None = None
    memory_bytes: int | None = None
    block_serial: str | None = None
    block_model: str | None = None
    serial_number: str | None = None
    ipmi_ip: str | None = None
    cvm_ip: str | None = None
    hypervisor_ip: str | None = None
    boot_time: str | None = None
    maintenance_state: str | None = None
    num_vms: int | None = None
    gpu_list: list[str] = Field(default_factory=list)
    disk_count: int | None = None
    #: The node's raw storage: the sum of its physical disk sizes (v4 host `disk` list),
    #: or, where the API gives no per-disk sizes, the node's `storage.capacity_bytes`
    #: (v3 groups / v2.0 usage stats). Feeds the cluster capacity figures (`capacity.py`).
    storage_capacity_bytes: int | None = None
    api_source: str = "v4"


# ─── Storage ───────────────────────────────────────────────────────────


class StorageContainer(SourcedObject):
    uuid: str
    name: str | None = None
    cluster_uuid: str | None = None
    cluster_name: str | None = None
    replication_factor: int | None = None
    compression_enabled: bool | None = None
    compression_delay_secs: int | None = None
    deduplication: str | None = None  # NONE / POST_PROCESS / INLINE …
    erasure_coding: str | None = None
    max_capacity_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    advertised_capacity_bytes: int | None = None
    api_source: str = "v4"


# ─── Networking (Phase 1: subnets) ─────────────────────────────────────


class Subnet(SourcedObject):
    uuid: str
    name: str | None = None
    cluster_uuid: str | None = None
    cluster_name: str | None = None
    subnet_type: str | None = None  # VLAN / OVERLAY
    vlan_id: int | None = None
    virtual_switch: str | None = None
    vpc_name: str | None = None
    ip_prefix: str | None = None
    gateway_ip: str | None = None
    dhcp_enabled: bool | None = None
    api_source: str = "v4"


# ─── Clusters ──────────────────────────────────────────────────────────


class Cluster(SourcedObject):
    uuid: str
    name: str | None = None
    aos_version: str | None = None
    ncc_version: str | None = None
    hypervisor_types: list[str] = Field(default_factory=list)
    cluster_arch: str | None = None
    redundancy_factor: int | None = None
    num_nodes: int | None = None
    external_ip: str | None = None
    timezone: str | None = None
    encryption_enabled: bool | None = None
    is_lts: bool | None = None
    operation_mode: str | None = None
    api_source: str = "v4"


# ─── Licensing summary ─────────────────────────────────────────────────


class LicenseInfo(SourcedObject):
    cluster_uuid: str | None = None
    cluster_name: str | None = None
    product: str | None = None  # NCI / NCM / NUS / NDB …
    edition: str | None = None  # Starter / Pro / Ultimate
    category: str | None = None
    expiry: str | None = None
    quantity: int | None = None
    compliance: str | None = None


# ─── Prism Central inventory objects: Templates / Images / OVAs ────────


class Template(SourcedObject):
    uuid: str
    name: str | None = None
    description: str | None = None
    guest_os: str | None = None
    num_vcpus: int | None = None
    memory_bytes: int | None = None
    active_version: str | None = None
    version_count: int | None = None
    created_by: str | None = None
    api_source: str = "v4"


class Image(SourcedObject):
    uuid: str
    name: str | None = None
    image_type: str | None = None  # DISK_IMAGE / ISO_IMAGE
    size_bytes: int | None = None
    architecture: str | None = None
    source: str | None = None
    cluster_names: list[str] = Field(default_factory=list)
    #: Placement cluster uuids; the engine resolves them to cluster_names post-collection.
    cluster_uuids: list[str] = Field(default_factory=list)
    state: str | None = None
    api_source: str = "v3"


class Ova(SourcedObject):
    uuid: str
    name: str | None = None
    size_bytes: int | None = None
    disk_count: int | None = None
    cluster_name: str | None = None
    created: str | None = None
    api_source: str = "v4"


class PrismCentralConfig(_Base):
    """Prism Central management-plane configuration & sizing."""

    version: str | None = None
    build: str | None = None
    num_vcpus: int | None = None
    memory_bytes: int | None = None
    node_count: int | None = None  # 1 = single, 3 = scale-out
    is_scale_out: bool | None = None
    registered_cluster_count: int | None = None
    ncc_version: str | None = None
    ip_addresses: list[str] = Field(default_factory=list)
    pc_vm_names: list[str] = Field(default_factory=list)


# ─── Data Protection / DR ──────────────────────────────────────────────


class ProtectionPolicy(SourcedObject):
    uuid: str
    name: str | None = None
    rpo_seconds: int | None = None
    replication_type: str | None = None  # ASYNC / NEARSYNC / SYNC / METRO
    local_retention: int | None = None
    remote_retention: int | None = None
    source_az: str | None = None
    target_az: str | None = None
    categories: list[str] = Field(default_factory=list)
    api_source: str = "v3"


class RecoveryPlan(SourcedObject):
    uuid: str
    name: str | None = None
    stage_count: int | None = None
    entities_count: int | None = None
    primary_location: str | None = None
    recovery_location: str | None = None
    network_mappings: int | None = None
    has_test_failover: bool | None = None
    api_source: str = "v3"


class AvailabilityZone(SourcedObject):
    uuid: str | None = None
    name: str | None = None
    az_type: str | None = None  # PHYSICAL / XI
    region: str | None = None
    url: str | None = None
    connection_status: str | None = None
    api_source: str = "v3"


# ─── Health / Alerts ────────────────────────────────────────────────────


class Alert(SourcedObject):
    uuid: str | None = None
    title: str | None = None
    message: str | None = None
    severity: str | None = None  # CRITICAL / WARNING / INFO / OTHER (normalized)
    resolved: bool | None = None
    acknowledged: bool | None = None
    auto_resolved: bool | None = None
    entity_type: str | None = None
    entity_name: str | None = None
    cluster_uuid: str | None = None
    cluster_name: str | None = None
    created_at: str | None = None
    classifications: list[str] = Field(default_factory=list)
    api_source: str = "v3"


class RemoteSite(SourcedObject):
    uuid: str | None = None
    name: str | None = None
    cluster_name: str | None = None
    address: str | None = None
    status: str | None = None
    api_source: str = "v3"


# ─── Flow Network Security ─────────────────────────────────────────────


class SecurityPolicy(SourcedObject):
    uuid: str
    name: str | None = None
    policy_type: str | None = None  # APPLICATION / ISOLATION / QUARANTINE
    state: str | None = None  # MONITOR / ENFORCE / SAVE
    app_rule_action: str | None = None
    inbound_rule_count: int | None = None
    outbound_rule_count: int | None = None
    secured_group: str | None = None
    api_source: str = "v3"


class Category(SourcedObject):
    name: str
    value: str | None = None
    description: str | None = None
    api_source: str = "v3"


class ServiceGroup(SourcedObject):
    uuid: str
    name: str | None = None
    is_system: bool | None = None
    tcp_ports: str | None = None
    udp_ports: str | None = None
    icmp: bool = False
    api_source: str = "v3"


class AddressGroup(SourcedObject):
    uuid: str
    name: str | None = None
    is_system: bool | None = None
    ip_ranges: list[str] = Field(default_factory=list)
    api_source: str = "v3"


# ─── Flow Virtual Networking ───────────────────────────────────────────


class Vpc(SourcedObject):
    uuid: str
    name: str | None = None
    external_subnets: list[str] = Field(default_factory=list)
    externally_routable_prefixes: list[str] = Field(default_factory=list)
    dns_servers: list[str] = Field(default_factory=list)
    #: Overlay subnets inside this VPC (backfilled from the subnet inventory).
    subnet_names: list[str] = Field(default_factory=list)
    route_count: int | None = None
    routing_policy_count: int | None = None
    api_source: str = "v4"


class FloatingIp(SourcedObject):
    uuid: str | None = None
    floating_ip: str | None = None
    vpc_name: str | None = None
    external_subnet: str | None = None
    associated_private_ip: str | None = None
    api_source: str = "v4"


# ─── Nutanix Unified Storage: Files / Objects / Volumes ────────────────


class FileServer(SourcedObject):
    uuid: str
    name: str | None = None
    cluster_name: str | None = None
    version: str | None = None
    num_fsvm: int | None = None
    internal_network: str | None = None
    external_network: str | None = None
    size_bytes: int | None = None
    used_bytes: int | None = None
    share_count: int | None = None
    api_source: str = "v3"


class FileShare(SourcedObject):
    uuid: str
    name: str | None = None
    file_server_name: str | None = None
    share_type: str | None = None  # SMB / NFS
    max_size_bytes: int | None = None
    used_bytes: int | None = None
    protocol: str | None = None
    api_source: str = "v3"


class ObjectStore(SourcedObject):
    uuid: str
    name: str | None = None
    cluster_name: str | None = None
    deployment_version: str | None = None
    num_worker_nodes: int | None = None
    total_capacity_bytes: int | None = None
    used_bytes: int | None = None
    bucket_count: int | None = None
    state: str | None = None
    api_source: str = "v3"


class Bucket(SourcedObject):
    name: str
    object_store_name: str | None = None
    versioning: bool | None = None
    worm_enabled: bool | None = None
    object_count: int | None = None
    used_bytes: int | None = None
    api_source: str = "v3"


class VolumeGroup(SourcedObject):
    uuid: str
    name: str | None = None
    cluster_name: str | None = None
    disk_count: int | None = None
    total_capacity_bytes: int | None = None
    is_shared: bool | None = None
    iscsi_target: str | None = None
    attachment_count: int | None = None
    flash_mode: bool | None = None
    api_source: str = "v4"


# ─── NDB — Nutanix Database Service ────────────────────────────────────


class DatabaseServer(SourcedObject):
    id: str
    name: str | None = None
    db_engine: str | None = None
    ip_addresses: list[str] = Field(default_factory=list)
    status: str | None = None
    cluster_name: str | None = None
    vm_cores: int | None = None
    memory_gb: float | None = None
    api_source: str = "ndb"


class Database(SourcedObject):
    id: str
    name: str | None = None
    db_engine: str | None = None  # oracle / postgres / sqlserver / mysql / mongodb
    db_type: str | None = None  # single / HA cluster
    status: str | None = None
    db_server_name: str | None = None
    time_machine_name: str | None = None
    sla_name: str | None = None
    clone: bool = False
    provisioned: str | None = None
    api_source: str = "ndb"


class TimeMachine(SourcedObject):
    id: str
    name: str | None = None
    database_name: str | None = None
    sla_name: str | None = None
    status: str | None = None
    schedule: str | None = None
    clone_count: int | None = None
    api_source: str = "ndb"


# ─── IAM ───────────────────────────────────────────────────────────────


class User(SourcedObject):
    uuid: str | None = None
    username: str | None = None
    display_name: str | None = None
    user_type: str | None = None  # LOCAL / SAML / LDAP
    directory: str | None = None
    state: str | None = None
    api_source: str = "v3"


class Role(SourcedObject):
    uuid: str | None = None
    name: str | None = None
    description: str | None = None
    is_system: bool | None = None
    permission_count: int | None = None
    api_source: str = "v3"


class Project(SourcedObject):
    uuid: str | None = None
    name: str | None = None
    description: str | None = None
    resource_domain: str | None = None
    user_count: int | None = None
    api_source: str = "v3"


# ─── X-Play (Playbooks) ────────────────────────────────────────────────


class Playbook(SourcedObject):
    uuid: str
    name: str | None = None
    description: str | None = None
    trigger_type: str | None = None  # ALERT / MANUAL / WEBHOOK / SCHEDULED
    action_count: int | None = None
    is_enabled: bool | None = None
    execution_count: int | None = None
    api_source: str = "v3"


# ─── NCM Reports ───────────────────────────────────────────────────────


class NcmReport(SourcedObject):
    uuid: str | None = None
    name: str | None = None
    report_type: str | None = None
    schedule: str | None = None
    last_run: str | None = None
    api_source: str = "v3"


# ─── LCM — Life Cycle Manager ──────────────────────────────────────────


class LcmEntity(SourcedObject):
    """One upgradable component in the LCM inventory.

    LCM is the answer to "what firmware and software is actually running, and what is
    out of date" — AOS, AHV, NCC, foundation, disk and NIC firmware, and the Prism
    Central services, each with the versions LCM would let you move to.
    """

    uuid: str
    name: str | None = None  # LCM's entityModel, e.g. "AHV" or a disk model
    entity_class: str | None = None  # "Core Cluster" / "Hypervisor" / "PC Service" …
    entity_type: str | None = None
    current_version: str | None = None
    target_version: str | None = None
    #: Versions LCM offers as upgrade targets, newest-first as LCM orders them.
    available_versions: list[str] = Field(default_factory=list)
    #: True when LCM offers a version other than the one running.
    update_available: bool | None = None
    cluster_uuid: str | None = None
    cluster_name: str | None = None
    last_updated: str | None = None
    api_source: str = "v4"


# ─── Feature-area summaries ────────────────────────────────────────────


class DataProtectionSummary(_Base):
    protection_policy_count: int = 0
    recovery_plan_count: int = 0
    remote_site_count: int = 0
    availability_zone_count: int = 0
    protected_vm_count: int = 0


class FlowSummary(_Base):
    security_policy_count: int = 0
    category_count: int = 0
    service_group_count: int = 0
    address_group_count: int = 0
    vpc_count: int = 0


class AlertSummary(_Base):
    total_count: int = 0
    critical_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    other_count: int = 0
    unresolved_count: int = 0


# ─── Root ──────────────────────────────────────────────────────────────


class PrismCentral(_Base):
    uuid: str | None = None
    host: str | None = None
    version: str | None = None
    negotiated_api_versions: dict[str, str] = Field(default_factory=dict)
    version_supported: bool = True  # within the tested compatibility window


class SourceEnvironment(_Base):
    """One Prism Central that contributed to a combined Environment.

    A combined run has no single management plane, so the per-PC version, sizing and
    object counts are kept here rather than being flattened into the combined
    `Environment.pc_config` — presenting one PC's sizing as the whole estate's would be
    wrong. Exporters render these as the "Source Prism Centrals" table.
    """

    prism_central: PrismCentral = Field(default_factory=PrismCentral)
    pc_config: PrismCentralConfig = Field(default_factory=lambda: PrismCentralConfig())
    #: This source's own Environment.summary() counts, taken before the merge.
    summary: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_shape(cls, data: Any) -> Any:
        """Load snapshots written when `source_environments` was a bare list of
        PrismCentral. Without this the flat keys are simply ignored and every source
        reads back as "unknown" — a silent downgrade rather than a visible error.
        """
        if isinstance(data, dict) and "prism_central" not in data and "host" in data:
            return {"prism_central": data}
        return data


#: Version of the serialized `Environment` schema. Bump it when a change needs a
#: migration for snapshot files written by older versions (`store.transfer` migrates
#: older payloads forward and refuses newer ones). Payloads written before the field
#: existed count as version 0.
ENVIRONMENT_SCHEMA_VERSION = 1


class Environment(_Base):
    """A full collection run — the unit persisted and exported."""

    schema_version: int = ENVIRONMENT_SCHEMA_VERSION
    #: Stable identity of the snapshot (a UUID), kept across export and import so a
    #: snapshot file imported twice is recognised. Assigned when the snapshot is first
    #: saved; snapshots written before it existed get one on their first export.
    snapshot_id: str | None = None
    collected_at: str | None = None
    tool_version: str = "0.3.0"
    #: "prism_central" (collected via PC) or "prism_element" (direct cluster, v2.0).
    source_type: str = "prism_central"
    prism_central: PrismCentral = Field(default_factory=PrismCentral)
    #: Populated only on a combined Environment (see engine.combine_environments) — one
    #: entry per Prism Central that contributed to it. Empty for a normal single-PC run;
    #: a non-empty list is what marks an Environment as combined (`is_combined`).
    source_environments: list[SourceEnvironment] = Field(default_factory=list)

    #: Non-fatal compatibility / collection warnings surfaced to the user.
    warnings: list[str] = Field(default_factory=list)

    # ─ Phase 1: core infrastructure ─
    clusters: list[Cluster] = Field(default_factory=list)
    hosts: list[Host] = Field(default_factory=list)
    vms: list[VirtualMachine] = Field(default_factory=list)  # USER workloads only
    #: Nutanix system VMs (CVMs, Prism Central VMs) — kept separate from user VMs.
    system_vms: list[VirtualMachine] = Field(default_factory=list)
    storage_containers: list[StorageContainer] = Field(default_factory=list)
    subnets: list[Subnet] = Field(default_factory=list)
    licenses: list[LicenseInfo] = Field(default_factory=list)
    # ─ Prism Central inventory objects ─
    templates: list[Template] = Field(default_factory=list)
    images: list[Image] = Field(default_factory=list)
    ovas: list[Ova] = Field(default_factory=list)
    pc_config: PrismCentralConfig = Field(default_factory=lambda: PrismCentralConfig())

    # ─ Phase 2: Data Protection / DR ─
    protection_policies: list[ProtectionPolicy] = Field(default_factory=list)
    recovery_plans: list[RecoveryPlan] = Field(default_factory=list)
    availability_zones: list[AvailabilityZone] = Field(default_factory=list)
    remote_sites: list[RemoteSite] = Field(default_factory=list)

    # ─ Phase 2: Health / Alerts ─
    alerts: list[Alert] = Field(default_factory=list)

    # ─ Phase 2: Flow ─
    security_policies: list[SecurityPolicy] = Field(default_factory=list)
    categories: list[Category] = Field(default_factory=list)
    service_groups: list[ServiceGroup] = Field(default_factory=list)
    address_groups: list[AddressGroup] = Field(default_factory=list)
    vpcs: list[Vpc] = Field(default_factory=list)
    floating_ips: list[FloatingIp] = Field(default_factory=list)

    # ─ Phase 2: NUS (Files / Objects / Volumes) ─
    file_servers: list[FileServer] = Field(default_factory=list)
    file_shares: list[FileShare] = Field(default_factory=list)
    object_stores: list[ObjectStore] = Field(default_factory=list)
    buckets: list[Bucket] = Field(default_factory=list)
    volume_groups: list[VolumeGroup] = Field(default_factory=list)

    # ─ Phase 2: NDB ─
    database_servers: list[DatabaseServer] = Field(default_factory=list)
    databases: list[Database] = Field(default_factory=list)
    time_machines: list[TimeMachine] = Field(default_factory=list)

    # ─ Phase 2: IAM / X-Play / NCM ─
    users: list[User] = Field(default_factory=list)
    roles: list[Role] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    playbooks: list[Playbook] = Field(default_factory=list)
    ncm_reports: list[NcmReport] = Field(default_factory=list)
    lcm_entities: list[LcmEntity] = Field(default_factory=list)

    # Roll-up summaries
    data_protection: DataProtectionSummary = Field(default_factory=DataProtectionSummary)
    flow: FlowSummary = Field(default_factory=FlowSummary)
    alerts_summary: AlertSummary = Field(default_factory=AlertSummary)

    # Collection diagnostics: {collector_name: "ok" | "skipped: reason" | "error: ..."}
    collection_notes: dict[str, str] = Field(default_factory=dict)

    #: Metadata of the collection run that produced this snapshot — per-area outcome,
    #: API routing, compatibility, duration, reports (see `runinfo.build_run_metadata`).
    #: None on snapshots written before it existed; the UI then shows the summary only.
    run: dict[str, Any] | None = None

    @property
    def is_combined(self) -> bool:
        """True for an Environment merged from several Prism Centrals.

        Exporters branch on this: a combined run has no single management plane, so the
        per-PC sizing lives in `source_environments` and each object carries `source_pc`.
        """
        return bool(self.source_environments)

    def summary(self) -> dict[str, int]:
        return {
            "source_environments": len(self.source_environments),
            "clusters": len(self.clusters),
            "hosts": len(self.hosts),
            "vms": len(self.vms),
            "system_vms": len(self.system_vms),
            "templates": len(self.templates),
            "images": len(self.images),
            "ovas": len(self.ovas),
            "storage_containers": len(self.storage_containers),
            "subnets": len(self.subnets),
            "licenses": len(self.licenses),
            "protection_policies": len(self.protection_policies),
            "recovery_plans": len(self.recovery_plans),
            "availability_zones": len(self.availability_zones),
            "alerts": len(self.alerts),
            "security_policies": len(self.security_policies),
            "categories": len(self.categories),
            "service_groups": len(self.service_groups),
            "address_groups": len(self.address_groups),
            "vpcs": len(self.vpcs),
            "file_servers": len(self.file_servers),
            "file_shares": len(self.file_shares),
            "object_stores": len(self.object_stores),
            "buckets": len(self.buckets),
            "volume_groups": len(self.volume_groups),
            "database_servers": len(self.database_servers),
            "databases": len(self.databases),
            "time_machines": len(self.time_machines),
            "users": len(self.users),
            "roles": len(self.roles),
            "projects": len(self.projects),
            "playbooks": len(self.playbooks),
            "ncm_reports": len(self.ncm_reports),
            "lcm_entities": len(self.lcm_entities),
        }
