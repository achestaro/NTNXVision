"""Collectors — one module per Nutanix object type / feature area.

Each collector takes a `PrismConnection`, calls the appropriate API (v4 preferred,
v3 fallback), and returns normalized model objects. The `engine` runs them and
assembles an `Environment`.

Phase 1 (core infra): clusters, hosts, storage, subnets, vms, licenses.
Phase 2 (platform):    data protection, flow security + networking, files, objects,
                       volumes, iam, x-play, ncm reports.  NDB is separate (own conn).
"""

from ntnxvision_core.collectors.alerts import AlertsCollector
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.collectors.catalog import (
    ImageCollector,
    OvaCollector,
    TemplateCollector,
)
from ntnxvision_core.collectors.clusters import ClusterCollector
from ntnxvision_core.collectors.dataprotection import DataProtectionCollector
from ntnxvision_core.collectors.flow import (
    FlowNetworkingCollector,
    FlowSecurityCollector,
)
from ntnxvision_core.collectors.hosts import HostCollector
from ntnxvision_core.collectors.iam import IamCollector
from ntnxvision_core.collectors.lcm import LcmCollector
from ntnxvision_core.collectors.licenses import LicenseCollector
from ntnxvision_core.collectors.ncm import NcmCollector
from ntnxvision_core.collectors.nus import (
    FilesCollector,
    ObjectsCollector,
    VolumesCollector,
)
from ntnxvision_core.collectors.pc_config import PrismCentralConfigCollector
from ntnxvision_core.collectors.storage import StorageCollector
from ntnxvision_core.collectors.subnets import SubnetCollector
from ntnxvision_core.collectors.vms import VmCollector
from ntnxvision_core.collectors.xplay import XPlayCollector

#: Phase 1 collectors — core infrastructure inventory.
PHASE1_COLLECTORS: list[type[Collector]] = [
    ClusterCollector,
    HostCollector,
    StorageCollector,
    SubnetCollector,
    VmCollector,
    LicenseCollector,
]

#: Phase 2 collectors — full platform coverage (all PrismConnection-based).
PHASE2_COLLECTORS: list[type[Collector]] = [
    DataProtectionCollector,
    AlertsCollector,
    FlowSecurityCollector,
    FlowNetworkingCollector,
    FilesCollector,
    ObjectsCollector,
    VolumesCollector,
    IamCollector,
    XPlayCollector,
    NcmCollector,
    LcmCollector,
    # PC inventory objects + management config (pc_config runs last: needs system_vms).
    ImageCollector,
    TemplateCollector,
    OvaCollector,
    PrismCentralConfigCollector,
]

#: The default full inventory — everything reachable through Prism Central.
ALL_COLLECTORS: list[type[Collector]] = PHASE1_COLLECTORS + PHASE2_COLLECTORS

__all__ = [
    "Collector",
    "ClusterCollector",
    "HostCollector",
    "StorageCollector",
    "SubnetCollector",
    "VmCollector",
    "LicenseCollector",
    "DataProtectionCollector",
    "AlertsCollector",
    "FlowSecurityCollector",
    "FlowNetworkingCollector",
    "FilesCollector",
    "ObjectsCollector",
    "VolumesCollector",
    "IamCollector",
    "XPlayCollector",
    "NcmCollector",
    "LcmCollector",
    "PHASE1_COLLECTORS",
    "PHASE2_COLLECTORS",
    "ALL_COLLECTORS",
]
