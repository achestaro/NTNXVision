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

from ntnxvision_core import __version__
from ntnxvision_core.versions import short_version


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
    #: The vSphere datastore holding the disk (ESXi). Set for disks outside a Nutanix
    #: storage container (a local VMFS volume, a foreign NFS export), which Nutanix
    #: reports with a size but no container; read from vCenter when it is connected.
    datastore: str | None = None


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
    #: Total provisioned disk when no per-disk detail was available (a VM recovered from
    #: the groups API reports only its `capacity_bytes`). Ignored when `disks` is set.
    disk_capacity_bytes: int | None = None
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
        if not self.disks and self.disk_capacity_bytes:
            return self.disk_capacity_bytes
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
    #: HYPER_CONVERGED / COMPUTE_ONLY / STORAGE_ONLY (v4 ``hostType``).
    host_type: str | None = None
    #: The block (rackable unit) the node sits in, and its slot in that block.
    rackable_unit_uuid: str | None = None
    block_position: int | None = None
    #: The rack id of the node's block, when rack awareness is configured (racks have
    #: no name in any Nutanix API: only an id).
    rack_id: int | None = None
    api_source: str = "v4"


class Cvm(SourcedObject):
    """The Controller VM of one node, with its sizing (v4 ``clusters/{c}/cvms``)."""

    uuid: str
    name: str | None = None
    cluster_uuid: str | None = None
    cluster_name: str | None = None
    host_uuid: str | None = None
    host_name: str | None = None
    num_vcpus: int | None = None
    memory_bytes: int | None = None
    ip_address: str | None = None
    backplane_ip: str | None = None
    hypervisor_type: str | None = None
    api_source: str = "v4"


class BlockNode(_Base):
    host_uuid: str | None = None
    position: int | None = None


class Block(SourcedObject):
    """A block (rackable unit): the chassis one or more nodes sit in.

    `serial` is collected for the inventory tables but is sensitive: diagrams never
    show it.
    """

    uuid: str
    cluster_uuid: str | None = None
    cluster_name: str | None = None
    model_name: str | None = None
    #: The raw model enum (``USELAYOUT`` on most current hardware).
    model: str | None = None
    serial: str | None = None
    rack_id: int | None = None
    rack_uuid: str | None = None
    nodes: list[BlockNode] = Field(default_factory=list)
    api_source: str = "v4"


# ─── Host networking ───────────────────────────────────────────────────


class HostNic(SourcedObject):
    """A physical NIC of a host, with the upstream switch when LLDP/CDP reports it.

    ESXi NICs come back from the Nutanix APIs with only their name and link speed: the
    switch fields stay None, and nothing is inferred.
    """

    uuid: str
    name: str | None = None
    host_uuid: str | None = None
    host_name: str | None = None
    cluster_uuid: str | None = None
    cluster_name: str | None = None
    #: The NIC model as the host describes it (v4 ``hostDescription``).
    model: str | None = None
    pci_model_id: str | None = None
    mac_address: str | None = None
    driver_version: str | None = None
    firmware_version: str | None = None
    #: Negotiated link speed (0 when the link is down) and the port's capacity.
    link_speed_kbps: int | None = None
    link_capacity_mbps: int | None = None
    link_up: bool | None = None
    mtu: int | None = None
    virtual_switch_uuid: str | None = None
    virtual_switch_name: str | None = None
    #: LLDP / CDP neighbour, only as reported.
    discovery_protocol: str | None = None
    switch_vendor: str | None = None
    switch_mac: str | None = None
    switch_device_id: str | None = None
    switch_port_id: str | None = None
    switch_management_ip: str | None = None
    switch_vlan_id: int | None = None
    api_source: str = "v4"

    @property
    def speed_mbps(self) -> int | None:
        """The port speed: the negotiated link speed, else the port's capacity."""
        if self.link_speed_kbps:
            return self.link_speed_kbps // 1000
        return self.link_capacity_mbps or None

    @property
    def has_neighbour(self) -> bool:
        return bool(self.switch_vendor or self.switch_mac or self.switch_device_id
                    or self.switch_port_id)


class VirtualSwitchHost(_Base):
    """One host's membership of a virtual switch: its bridge and uplinks."""

    host_uuid: str | None = None
    host_name: str | None = None
    bridge: str | None = None
    #: Uplink NIC names (``eth0``), as the switch lists them.
    uplinks: list[str] = Field(default_factory=list)
    active_uplink: str | None = None
    ip_address: str | None = None
    #: The uplink bond on this host (v4 ``uplink-bonds``), when listed.
    bond_name: str | None = None
    bond_type: str | None = None
    lacp_status: str | None = None


class VirtualSwitch(SourcedObject):
    """An AHV virtual switch on one cluster (one row per switch and cluster).

    v4 lists a virtual switch once with every cluster it spans; it is split per cluster
    here so it belongs to one cluster like every other inventory object. AHV only: ESXi
    vSwitches and distributed switches are not reported by the Nutanix APIs.
    """

    uuid: str
    name: str | None = None
    cluster_uuid: str | None = None
    cluster_name: str | None = None
    description: str | None = None
    is_default: bool | None = None
    #: ACTIVE_BACKUP / BALANCE_SLB / BALANCE_TCP / NONE.
    bond_mode: str | None = None
    mtu: int | None = None
    owner_type: str | None = None
    gateway_ip: str | None = None
    vlan_id: int | None = None
    hosts: list[VirtualSwitchHost] = Field(default_factory=list)
    api_source: str = "v4"

    @property
    def uplink_count(self) -> int | None:
        """Uplinks per host (the most common count), None when no host is listed."""
        counts = [len(h.uplinks) for h in self.hosts if h.uplinks]
        return max(set(counts), key=counts.count) if counts else None

    @property
    def lacp(self) -> bool:
        return (self.bond_mode or "").upper() == "BALANCE_TCP" or any(
            (h.lacp_status or "NIL").upper() not in ("NIL", "") for h in self.hosts)


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
    #: The external array this container lives on (v4.2+ ``externalStorageExtId``);
    #: None for a container on the cluster's own storage (DSF).
    external_storage_uuid: str | None = None
    external_storage_name: str | None = None
    #: A Nutanix internal container (NutanixManagementShare and the like).
    is_internal: bool | None = None
    storage_pool_uuid: str | None = None
    is_encrypted: bool | None = None
    api_source: str = "v4"


#: External storage provider enum → product name shown in reports and diagrams.
EXTERNAL_STORAGE_PRODUCTS: dict[str, str] = {
    "EVERPURE_FLASHARRAY": "Pure FlashArray",
    "PURE_STORAGE_FLASHARRAY": "Pure FlashArray",
    "PURE_FLASHARRAY": "Pure FlashArray",
    "DELL_POWERFLEX": "Dell PowerFlex",
    "DELL_POWERSTORE": "Dell PowerStore",
}

#: The protocol each provider implies (not an API field).
EXTERNAL_STORAGE_PROTOCOLS: dict[str, str] = {
    "Pure FlashArray": "NVMe/TCP",
}


def external_storage_product(provider: str | None) -> str | None:
    """Product name for an external storage provider enum; unknown values title-cased."""
    if not provider:
        return None
    key = str(provider).strip().upper()
    if key in EXTERNAL_STORAGE_PRODUCTS:
        return EXTERNAL_STORAGE_PRODUCTS[key]
    return " ".join(w.capitalize() for w in key.replace("-", "_").split("_") if w)


class ExternalStorage(SourcedObject):
    """An external storage array attached to a cluster (Pure FlashArray, Dell PowerFlex).

    Nutanix reports name, provider, endpoint, capacity and health only: the array model,
    software version and protocol are not API fields (the protocol is implied by the
    provider, see `protocol`). Credentials are never read.
    """

    uuid: str
    name: str | None = None
    #: The raw provider enum (EVERPURE_FLASHARRAY / DELL_POWERFLEX / DELL_POWERSTORE).
    provider_type: str | None = None
    cluster_uuid: str | None = None
    cluster_name: str | None = None
    endpoint: str | None = None
    total_capacity_bytes: int | None = None
    free_capacity_bytes: int | None = None
    health: str | None = None
    #: FlashArray realm / pod, PowerFlex protection domain / storage pool.
    realm_name: str | None = None
    pod_name: str | None = None
    protection_domain_name: str | None = None
    storage_pool_name: str | None = None
    #: Containers on this array (resolved from the containers' link).
    container_names: list[str] = Field(default_factory=list)
    api_source: str = "v4"

    @property
    def product(self) -> str:
        return external_storage_product(self.provider_type) or "External storage"

    @property
    def protocol(self) -> str | None:
        return EXTERNAL_STORAGE_PROTOCOLS.get(self.product)


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


class PortGroup(SourcedObject):
    """A vSphere port group on an ESXi cluster.

    Not a Nutanix subnet: Prism Central's subnet APIs (v3 and v4) list none for an ESXi
    cluster, whose VMs attach to vSwitch / distributed switch port groups instead. The
    cluster's Prism Element still knows them (v2.0 ``networks``, read through the Prism
    Central proxy) with their VLAN; vCenter, when connected, adds the switch type.
    """

    uuid: str
    name: str | None = None
    cluster_uuid: str | None = None
    cluster_name: str | None = None
    vlan_id: int | None = None
    #: STANDARD / DISTRIBUTED / OPAQUE (an NSX segment), from vCenter.
    switch_type: str | None = None
    #: Prism Element's network type (external / internal).
    network_type: str | None = None
    #: VMs with a NIC on the port group (filled from the collected VMs).
    vm_count: int | None = None
    api_source: str = "v2.0"


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
    #: The cluster's iSCSI Data Services IP (Volumes, Files, Objects, CSI); often unset.
    data_services_ip: str | None = None
    timezone: str | None = None
    encryption_enabled: bool | None = None
    is_lts: bool | None = None
    operation_mode: str | None = None
    #: The vCenter Server an ESXi cluster is registered to, as Prism reports it (v4
    #: ``network.managementServer``, v3 ``management_server_list``); None for AHV.
    vcenter_address: str | None = None
    api_source: str = "v4"
    #: The vCPU:pCPU target the capacity figures use for this cluster, recorded when it
    #: was collected (see `capacity.apply_targets`): a per-cluster override chosen in New
    #: collection, else the global setting at the time. None on snapshots written before
    #: per-cluster targets existed; those fall back to the configured setting.
    vcpu_ratio_target: float | None = None
    #: True when `vcpu_ratio_target` is a per-cluster override, False when it is the
    #: global default (None when no target was recorded).
    vcpu_ratio_custom: bool | None = None
    #: HYPER_CONVERGED or COMPUTE (a compute-only cluster), v4 ``config.clusterType``.
    cluster_type: str | None = None
    #: v4 ``clusterFunction`` (AOS, ONE_NODE, TWO_NODE, ...).
    cluster_functions: list[str] = Field(default_factory=list)
    #: Fault domain: NODE / BLOCK / RACK / DISK (v4 ``domainAwarenessLevel``).
    fault_domain: str | None = None
    #: Failures the cluster tolerates now, and the configured target.
    fault_tolerance: int | None = None
    desired_fault_tolerance: int | None = None
    #: The cluster supports NearSync replication (v4 dataprotection capabilities).
    nearsync_capable: bool | None = None
    #: Categories assigned to the cluster (``Key: [values]``), e.g. a POD category.
    categories: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("categories", mode="before")
    @classmethod
    def _normalize_categories(cls, value: Any) -> dict[str, list[str]]:
        return VirtualMachine._normalize_categories(value)

    @property
    def is_compute_only(self) -> bool:
        return (self.cluster_type or "").upper() in ("COMPUTE", "COMPUTE_ONLY")

    @field_validator("aos_version", "ncc_version", mode="before")
    @classmethod
    def _short_versions(cls, value: Any) -> Any:
        return short_version(value)


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
    #: Deployment size: STARTER / SMALL / LARGE / EXTRALARGE (v3 ``kXLarge`` normalized).
    size: str | None = None
    #: The cluster running the PC VMs (v4 ``hostingClusterExtId``).
    hosting_cluster_uuid: str | None = None
    hosting_cluster_name: str | None = None
    is_registered_with_hosting_cluster: bool | None = None
    fqdn: str | None = None
    #: The scale-out virtual IP (None for a single-VM PC).
    virtual_ip: str | None = None
    #: The configured size of one PC VM (the domain manager's resource config).
    vm_vcpus: int | None = None
    vm_memory_bytes: int | None = None
    vm_data_disk_bytes: int | None = None
    high_availability: bool | None = None
    #: The Microservices Platform (CMSP) is enabled.
    cmsp_enabled: bool | None = None
    #: PC products and their enablement (v4 ``domain-managers/{id}/products``).
    products: list[PcProduct] = Field(default_factory=list)
    #: Deployment status of the PC-managed data services (Files, Objects).
    services: list[PlatformService] = Field(default_factory=list)

    @field_validator("version", "build", "ncc_version", mode="before")
    @classmethod
    def _short_versions(cls, value: Any) -> Any:
        return short_version(value)

    @field_validator("size", mode="before")
    @classmethod
    def _pc_size(cls, value: Any) -> Any:
        return pc_size(value)

    @property
    def enabled_products(self) -> list[str]:
        return [p.name for p in self.products if p.enabled]


_PC_SIZES = {"KSTARTER": "STARTER", "KSMALL": "SMALL", "KLARGE": "LARGE",
             "KXLARGE": "EXTRALARGE", "KEXTRALARGE": "EXTRALARGE", "XLARGE": "EXTRALARGE"}


def pc_size(value: Any) -> str | None:
    """PC size in the v4 vocabulary: v3's ``kXLarge`` is v4's ``EXTRALARGE``."""
    if not isinstance(value, str) or not value.strip() or value.startswith("$"):
        return None
    key = value.strip().upper()
    return _PC_SIZES.get(key, key)


#: PC product enum → display name.
PC_PRODUCT_LABELS: dict[str, str] = {
    "FLOW_VIRTUAL_NETWORKING": "Flow Virtual Networking",
    "FLOW_NETWORK_SECURITY": "Flow Network Security",
    "NUTANIX_DISASTER_RECOVERY": "Disaster Recovery",
    "SELF_SERVICE": "Self-Service",
    "NUTANIX_MARKETPLACE": "Marketplace",
    "INTELLIGENT_OPERATIONS": "Intelligent Operations",
    "NUTANIX_CLOUD_MANAGER": "Nutanix Cloud Manager",
    "FLOW_CONTROLLER": "Flow Controller",
}


class PcProduct(_Base):
    """One Prism Central product (service) and whether it is enabled."""

    name: str
    #: ENABLED / DISABLED.
    enablement_state: str | None = None
    version: str | None = None
    enabled_at: str | None = None
    cpu_count: int | None = None
    memory_bytes: int | None = None
    api_source: str = "v4"

    @property
    def enabled(self) -> bool:
        return (self.enablement_state or "").upper() == "ENABLED"

    @property
    def label(self) -> str:
        return PC_PRODUCT_LABELS.get(self.name) or self.name.replace("_", " ").title()


class PlatformService(_Base):
    """Deployment status of a PC-managed data service (Files, Objects).

    `status` is DEPLOYED (instances found), NOT_DEPLOYED (the service answers with none),
    NOT_ENABLED (the service is not running: Objects answers 503) or UNKNOWN (the v4
    service answered another error; nothing is claimed).
    """

    name: str  # FILES / OBJECTS
    status: str
    count: int = 0
    api_source: str | None = None


class PcVm(SourcedObject):
    """One Prism Central VM and where it runs (v3 ``prism_central`` ``pc_vm_list``)."""

    uuid: str
    name: str | None = None
    #: The hosting cluster. Not named ``cluster_uuid`` on purpose: a PC VM belongs to
    #: the management plane, so a cluster-scoped run must not prune it.
    hosting_cluster_uuid: str | None = None
    hosting_cluster_name: str | None = None
    container_uuid: str | None = None
    num_vcpus: int | None = None
    memory_bytes: int | None = None
    data_disk_bytes: int | None = None
    ip_addresses: list[str] = Field(default_factory=list)
    power_state: str | None = None
    status: str | None = None
    api_source: str = "v3"


class PcRegistration(SourcedObject):
    """A cluster or a remote Prism Central registered with this Prism Central.

    v4 ``domain-managers/{id}/registrations``: AOS entries are registered clusters,
    DOMAIN_MANAGER entries the paired remote Prism Centrals (AZ pairing).
    """

    remote_uuid: str
    name: str | None = None
    #: AOS / DOMAIN_MANAGER / WITNESS_SERVICE.
    remote_type: str | None = None
    version: str | None = None
    address: str | None = None
    node_ips: list[str] = Field(default_factory=list)
    #: CONNECTED / DISCONNECTED / PENDING (``$UNKNOWN`` reads as None).
    connectivity_status: str | None = None
    api_source: str = "v4"

    @property
    def is_remote_pc(self) -> bool:
        return (self.remote_type or "").upper() == "DOMAIN_MANAGER"


PrismCentralConfig.model_rebuild()


# ─── Data Protection / DR ──────────────────────────────────────────────


#: Highest RPO (seconds) that is still NearSync; anything longer is Async.
NEARSYNC_MAX_RPO_SECONDS = 15 * 60

REPLICATION_LABELS: dict[str, str] = {"SYNC": "Sync", "NEARSYNC": "NearSync",
                                      "ASYNC": "Async"}


def replication_type_for_rpo(rpo_seconds: Any) -> str | None:
    """The replication type an RPO implies: the one place this rule lives.

    v4 protection policies carry no sync/async enum, only the RPO: 0 is synchronous
    (SYNC), 1 to 15 minutes NearSync (NEARSYNC), anything longer asynchronous (ASYNC).
    None (no schedule) stays None.
    """
    if rpo_seconds is None or isinstance(rpo_seconds, bool):
        return None
    try:
        rpo = int(rpo_seconds)
    except (TypeError, ValueError):
        return None
    if rpo <= 0:
        return "SYNC"
    if rpo <= NEARSYNC_MAX_RPO_SECONDS:
        return "NEARSYNC"
    return "ASYNC"


def replication_label(replication_type: str | None) -> str | None:
    """"Sync" / "NearSync" / "Async" for a stored replication type."""
    if not replication_type:
        return None
    return REPLICATION_LABELS.get(replication_type.upper(), replication_type.title())


class ReplicationLocation(_Base):
    """One site of a protection policy: an AZ (Prism Central) and its clusters."""

    label: str | None = None
    #: The AZ identifier as the API gives it (a Prism Central ext id or URL).
    az_ref: str | None = None
    #: Its display name, resolved after collection.
    az_name: str | None = None
    is_primary: bool | None = None
    cluster_uuids: list[str] = Field(default_factory=list)
    cluster_names: list[str] = Field(default_factory=list)


class ReplicationRule(_Base):
    """One replication direction of a protection policy (source site to target site)."""

    source_label: str | None = None
    target_label: str | None = None
    rpo_seconds: int | None = None
    replication_type: str | None = None
    recovery_point_type: str | None = None
    local_retention: int | None = None
    remote_retention: int | None = None
    sync_auto_suspend_timeout_secs: int | None = None


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
    #: The policy's sites and replication directions (v4); empty on v3 and on older
    #: snapshots.
    locations: list[ReplicationLocation] = Field(default_factory=list)
    replications: list[ReplicationRule] = Field(default_factory=list)
    #: Clusters of the source and target sites (names resolved where collected).
    source_clusters: list[str] = Field(default_factory=list)
    target_clusters: list[str] = Field(default_factory=list)
    api_source: str = "v3"

    @model_validator(mode="after")
    def _sync_from_rpo(self) -> ProtectionPolicy:
        """RPO 0 is synchronous. Snapshots written before the one RPO rule existed
        stored NEARSYNC for a v4 policy with RPO 0: read them back as SYNC."""
        if self.rpo_seconds == 0 and self.replication_type in (None, "NEARSYNC"):
            self.replication_type = "SYNC"
        return self

    @property
    def is_bidirectional(self) -> bool:
        pairs = {(r.source_label, r.target_label) for r in self.replications}
        return any((t, s) in pairs for s, t in pairs if s != t)

    @property
    def replication_label(self) -> str | None:
        return replication_label(self.replication_type)


class RecoveryPlan(SourcedObject):
    uuid: str
    name: str | None = None
    description: str | None = None
    stage_count: int | None = None
    entities_count: int | None = None
    primary_location: str | None = None
    recovery_location: str | None = None
    #: Clusters named on each side (v4 ``primaryLocation.clusters``): uuids as listed,
    #: names resolved after collection (the uuid when the cluster is not collected).
    primary_cluster_uuids: list[str] = Field(default_factory=list)
    recovery_cluster_uuids: list[str] = Field(default_factory=list)
    primary_clusters: list[str] = Field(default_factory=list)
    recovery_clusters: list[str] = Field(default_factory=list)
    #: The Metro witness, when the plan names one.
    witness_uuid: str | None = None
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
    #: Local / PC / Xi (v3 ``management_plane_type``).
    management_plane_type: str | None = None
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
    #: v4 reports the owning cluster by uuid; kept so a cluster-scoped run can place the
    #: group even when its cluster is outside the scope (and so has no name to resolve).
    cluster_uuid: str | None = None
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

    @field_validator("version", mode="before")
    @classmethod
    def _short_versions(cls, value: Any) -> Any:
        return short_version(value)


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


class ScopeCluster(_Base):
    """One registered cluster as the cluster scope records it."""

    uuid: str
    name: str | None = None


class ClusterScope(_Base):
    """Which of a Prism Central's registered clusters a run collected.

    A collection can be limited to some of the registered Prism Element clusters. Objects
    that belong to the others are never kept (see `ntnxvision_core.scope`), so a snapshot
    records what it covers: `selected` are the clusters collected, `excluded` the
    registered ones left out, `registered_count` how many were registered at the time.
    Prism-Central-wide objects (IAM, Flow policies, categories, protection policies,
    X-Play, NCM) are collected whatever the scope.
    """

    selected: list[ScopeCluster] = Field(default_factory=list)
    excluded: list[ScopeCluster] = Field(default_factory=list)
    registered_count: int = 0

    @property
    def is_partial(self) -> bool:
        """True when at least one registered cluster was left out."""
        return bool(self.excluded)


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
    tool_version: str = __version__
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
    #: vSphere port groups of ESXi clusters (their networks are not Nutanix subnets).
    port_groups: list[PortGroup] = Field(default_factory=list)

    # ─ Design data (architecture diagrams); all empty on older snapshots ─
    #: Blocks (rackable units) and Controller VMs per cluster.
    blocks: list[Block] = Field(default_factory=list)
    cvms: list[Cvm] = Field(default_factory=list)
    #: AHV virtual switches (one per switch and cluster) and host physical NICs.
    virtual_switches: list[VirtualSwitch] = Field(default_factory=list)
    host_nics: list[HostNic] = Field(default_factory=list)
    #: External storage arrays attached to clusters.
    external_storages: list[ExternalStorage] = Field(default_factory=list)
    #: Prism Central VMs and the clusters / Prism Centrals registered with this PC.
    pc_vms: list[PcVm] = Field(default_factory=list)
    pc_registrations: list[PcRegistration] = Field(default_factory=list)
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

    #: The registered clusters this run collected (None: snapshots written before cluster
    #: selection existed, and combined runs, whose per-PC runs collected every cluster).
    cluster_scope: ClusterScope | None = None

    #: The global vCPU:pCPU target (the Settings value) when this run was collected:
    #: clusters without an override use it. None on older snapshots and on combined runs
    #: whose Prism Centrals were collected with different values.
    vcpu_ratio_default: float | None = None

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
            "port_groups": len(self.port_groups),
            "blocks": len(self.blocks),
            "cvms": len(self.cvms),
            "virtual_switches": len(self.virtual_switches),
            "host_nics": len(self.host_nics),
            "external_storages": len(self.external_storages),
            "pc_vms": len(self.pc_vms),
            "pc_registrations": len(self.pc_registrations),
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
