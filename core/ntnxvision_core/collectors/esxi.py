"""What Nutanix knows about ESXi clusters beyond the v4 VM endpoint.

An ESXi cluster registered to Prism Central is thinner in the v4 and v3 APIs than an AHV
one, and measured on the lab PC (pc.7.5.1.1, one single-node ESXi 7.0.3 cluster with 57
VMs) the gaps are specific:

* ``vmm/v4.0/esxi/config/vms`` answers HTTP 500 (PLAT-10006) for some individual VMs on
  every path, and v3 ``vms/list`` has no ESXi VMs at all.
* v4 lists only the disks that live on a Nutanix storage container: a VM whose disks are
  on a local VMFS volume or a foreign NFS export reads as diskless (21 of 57 there).
* v4 has no boot firmware for ESXi VMs, and the subnet APIs list no networks for an ESXi
  cluster, whose VMs sit on vSphere port groups.

The cluster's own Prism Element fills all of it, and Prism Central proxies its API to a PC
account (``proxyClusterUuid``), which is how this module reads it: v2.0 ``vms`` (with disk
and NIC config) and v2.0 ``networks``, one paged GET each per in-scope ESXi cluster. That
is the routing rule's third tier ("v2.0 for cluster-local data not in PC"), used only
where v4 falls short. Read-only throughout.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.normalize import ESXI, hypervisor_name

#: The include flags without which v2.0 ``vms`` returns no disks and no NICs.
VM_INCLUDES = {"include_vm_disk_config": "true", "include_vm_nic_config": "true"}


def esxi_clusters(conn: Any, env: Any) -> list[tuple[str, str | None]]:
    """``(uuid, name)`` of every ESXi cluster this run collects.

    Taken from the run's resolved scope (the registered clusters, with their
    hypervisors) so it works whether or not the Clusters area was selected; falls back to
    the collected clusters when no scope could be resolved.
    """
    scope = getattr(conn, "scope", None)
    out: list[tuple[str, str | None]] = []
    registered = getattr(scope, "registered", None) or {}
    if scope is not None and registered:
        for uuid, cluster in registered.items():
            if uuid in scope.selected and ESXI in (cluster.hypervisors or []):
                out.append((uuid, cluster.name))
        return out
    for cluster in getattr(env, "clusters", []) or []:
        types = {hypervisor_name(t) for t in cluster.hypervisor_types or []}
        if ESXI in types and cluster.uuid:
            out.append((cluster.uuid, cluster.name))
    return out


class PeReadError(Exception):
    """A Prism Element read through the proxy failed (the message says why)."""


def pe_vms(conn: Any, cluster_uuid: str) -> list[dict[str, Any]]:
    """The cluster's VMs as its Prism Element reports them (v2.0, disks and NICs)."""
    try:
        return list(conn.pe_v2_list(cluster_uuid, "vms", extra_params=VM_INCLUDES))
    except NutanixApiError as exc:
        raise PeReadError(_reason(exc)) from exc


def pe_networks(conn: Any, cluster_uuid: str) -> list[dict[str, Any]]:
    """The cluster's networks (port groups on ESXi) from Prism Element v2.0.

    Cached on the connection: the subnets area lists them as port groups and the VM
    area names each NIC's port group from the same answer.
    """
    cache: dict[str, Any] = conn.__dict__.setdefault("_pe_networks", {})
    if cluster_uuid in cache:
        found = cache[cluster_uuid]
        if isinstance(found, PeReadError):
            raise found
        return list(found)
    try:
        cache[cluster_uuid] = list(conn.pe_v2_list(cluster_uuid, "networks"))
    except NutanixApiError as exc:
        cache[cluster_uuid] = PeReadError(_reason(exc))
        raise cache[cluster_uuid] from exc
    return list(cache[cluster_uuid])


def _reason(exc: NutanixApiError) -> str:
    if exc.status_code:
        return f"HTTP {exc.status_code}"
    return str(exc).split(":", 1)[0] or "request failed"
