"""Collectors — one module per Nutanix object type / feature area.

Each collector takes a `PrismConnection`, calls the appropriate API (v4 preferred,
v3 fallback), and returns normalized model objects. The `engine` runs them and
assembles an `Environment`.

Phase 1 collectors: clusters, hosts, vms, storage, subnets, licenses.
Later phases add: dataprotection, flow_microseg, flow_vpc, files, objects, ndb,
iam, lifecycle, monitoring, xplay, ncm_reports.
"""

from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.collectors.clusters import ClusterCollector
from ntnxvision_core.collectors.hosts import HostCollector
from ntnxvision_core.collectors.licenses import LicenseCollector
from ntnxvision_core.collectors.storage import StorageCollector
from ntnxvision_core.collectors.subnets import SubnetCollector
from ntnxvision_core.collectors.vms import VmCollector

#: Ordered list of Phase 1 collectors the engine runs by default.
PHASE1_COLLECTORS: list[type[Collector]] = [
    ClusterCollector,
    HostCollector,
    StorageCollector,
    SubnetCollector,
    VmCollector,
    LicenseCollector,
]

__all__ = [
    "Collector",
    "ClusterCollector",
    "HostCollector",
    "StorageCollector",
    "SubnetCollector",
    "VmCollector",
    "LicenseCollector",
    "PHASE1_COLLECTORS",
]
