"""The normalized inventory schema.

A single collection run produces one populated `Environment`. Every collector maps
its (v3 or v4) API payload into these models. The `Environment` is what gets persisted
to the snapshot store and fed to the exporters.

Phase 1 covers PrismCentral, Cluster, Host, StorageContainer, VirtualMachine, Subnet
and the Licensing summary. Later phases extend DataProtectionSummary, FlowSummary, and
add Files/Objects/NDB/X-Play/NCM/IAM sections.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class _Base(BaseModel):
    """Shared config for all inventory models."""

    model_config = {"extra": "ignore", "populate_by_name": True}


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


class VirtualMachine(_Base):
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
    categories: dict[str, str] = Field(default_factory=dict)
    protection_type: str | None = None  # UNPROTECTED / RULE / …
    protection_policy: str | None = None
    gpus: list[str] = Field(default_factory=list)
    disks: list[VirtualDisk] = Field(default_factory=list)
    nics: list[Nic] = Field(default_factory=list)
    boot_type: str | None = None
    creation_time: str | None = None
    api_source: str = "v4"  # provenance: which API version populated this

    @property
    def total_disk_bytes(self) -> int:
        return sum(d.disk_size_bytes or 0 for d in self.disks if not d.is_cdrom)

    @property
    def vcpu_total(self) -> int | None:
        if self.num_vcpus is None:
            return None
        return self.num_vcpus * (self.num_cores_per_vcpu or 1)


# ─── Hosts / nodes ─────────────────────────────────────────────────────


class Host(_Base):
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
    api_source: str = "v4"


# ─── Storage ───────────────────────────────────────────────────────────


class StorageContainer(_Base):
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


class Subnet(_Base):
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


class Cluster(_Base):
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


class LicenseInfo(_Base):
    cluster_uuid: str | None = None
    cluster_name: str | None = None
    product: str | None = None  # NCI / NCM / NUS / NDB …
    edition: str | None = None  # Starter / Pro / Ultimate
    category: str | None = None
    expiry: str | None = None
    quantity: int | None = None
    compliance: str | None = None


# ─── Feature-area summaries (populated in later phases) ────────────────


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


# ─── Root ──────────────────────────────────────────────────────────────


class PrismCentral(_Base):
    uuid: str | None = None
    host: str | None = None
    version: str | None = None
    negotiated_api_versions: dict[str, str] = Field(default_factory=dict)


class Environment(_Base):
    """A full collection run — the unit persisted and exported."""

    collected_at: str | None = None
    tool_version: str = "0.1.0"
    prism_central: PrismCentral = Field(default_factory=PrismCentral)

    clusters: list[Cluster] = Field(default_factory=list)
    hosts: list[Host] = Field(default_factory=list)
    vms: list[VirtualMachine] = Field(default_factory=list)
    storage_containers: list[StorageContainer] = Field(default_factory=list)
    subnets: list[Subnet] = Field(default_factory=list)
    licenses: list[LicenseInfo] = Field(default_factory=list)

    data_protection: DataProtectionSummary = Field(default_factory=DataProtectionSummary)
    flow: FlowSummary = Field(default_factory=FlowSummary)

    # Collection diagnostics: {collector_name: "ok" | "skipped: reason" | "error: ..."}
    collection_notes: dict[str, str] = Field(default_factory=dict)

    def summary(self) -> dict[str, int]:
        return {
            "clusters": len(self.clusters),
            "hosts": len(self.hosts),
            "vms": len(self.vms),
            "storage_containers": len(self.storage_containers),
            "subnets": len(self.subnets),
            "licenses": len(self.licenses),
        }
