"""Prism Central management-plane configuration & sizing collector.

Captures the PC's own version/build, VM sizing (vCPU/memory), single vs scale-out node
count, and the number of registered clusters — the management-tier details an architecture
document should record alongside the workload inventory.

**No single v4 endpoint replaces the v1 gateway here, so the v4 path reads two.**
`prism/v4.0/config/domain-managers` is Prism Central's record of itself and the only API
that states its *declared* sizing — vCPU, memory, and the ext ids of its PCVMs — which
the v1 gateway never carried at all. But its `buildInfo` stops at the short
``pc.7.5.1.1`` and it reports no NCC version anywhere; both of those live on the PC's own
``PRISM_CENTRAL`` entry in `clustermgmt/v4.0/config/clusters`, whose
`buildInfo.fullVersion` is byte-for-byte what v1 returned as ``fullVersion``. The two are
merged, and the v1 gateway stays as the fallback for PCs below the v4 GA line.

This collector runs last: the vCPU/memory totals are summed from the classified
`PRISM_CENTRAL` system VMs, which is the only source that is unambiguously the *aggregate*
across every PCVM.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.base import Collector


class PrismCentralConfigCollector(Collector):
    name = "pc_config"
    label = "Prism Central Configuration"

    def collect(self, env: Any) -> None:
        cfg = env.pc_config
        cfg.version = env.prism_central.version
        cfg.registered_cluster_count = len(env.clusters)

        # PC nodes / sizing from the PC's own record (best-effort across versions).
        details = self._pc_details_v4() if self.conn.v4_ready("clusters") else None
        if details is None:
            details = self._pc_details()
        if details:
            cfg.node_count = details.get("node_count")
            cfg.is_scale_out = (details.get("node_count") or 1) > 1
            cfg.ip_addresses = details.get("ips", [])
            cfg.build = details.get("build")
            cfg.ncc_version = details.get("ncc_version")

        # PC VM sizing: sum resources of the classified Prism Central VMs.
        pc_vms = [v for v in env.system_vms if v.vm_type == "PRISM_CENTRAL"]
        if pc_vms:
            cfg.pc_vm_names = [v.name for v in pc_vms if v.name]
            cfg.num_vcpus = sum(v.vcpu_total or 0 for v in pc_vms) or None
            cfg.memory_bytes = sum(v.memory_bytes or 0 for v in pc_vms) or None
            if cfg.node_count is None:
                cfg.node_count = len(pc_vms)
                cfg.is_scale_out = len(pc_vms) > 1
            # A single-node PC has no cluster VIP, so the v1 gateway reports a null
            # clusterExternalIPAddress and no svmips. Fall back to the PCVMs' own NIC
            # addresses rather than reporting the management plane as having no IP.
            if not cfg.ip_addresses:
                cfg.ip_addresses = list(
                    dict.fromkeys(
                        ip
                        for v in pc_vms
                        for nic in v.nics
                        for ip in nic.ip_addresses
                        if ip
                    )
                )

        # The domain manager declares its own size, which covers the case the sum above
        # cannot: a PC whose PCVMs are not in its own VM inventory (hosted on a cluster
        # it does not manage), where these two fields were previously left blank. The
        # measured sum still wins, because `resourceConfig` describes one domain
        # manager's configured size while the report totals every PCVM.
        if details:
            if cfg.num_vcpus is None:
                cfg.num_vcpus = details.get("num_vcpus")
            if cfg.memory_bytes is None:
                cfg.memory_bytes = details.get("memory_bytes")

        env.collection_notes[self.name] = "ok"

    # ─── v4 ────────────────────────────────────────────────────────

    def _pc_details_v4(self) -> dict[str, Any] | None:
        """Merge the PC's domain-manager record with its own cluster entry.

        Returns None when neither answers, so the caller falls back to the v1 gateway
        rather than reporting an unsized, unaddressed management plane.
        """
        manager = self._domain_manager()
        entry = self._pc_cluster_entry()
        if manager is None and entry is None:
            return None

        ips: list[str] = []
        node_count: int | None = None
        build: str | None = None
        ncc_version: str | None = None

        if entry is not None:
            config = entry.get("config") or {}
            nodes = entry.get("nodes") or {}
            network = entry.get("network") or {}
            node_count = nodes.get("numberOfNodes")
            # v1 listed the cluster VIP first and the per-node service IPs after it.
            # A single-node PC has no VIP and v4 renders that absence as `{}`.
            ips = [
                ip
                for ip in (
                    _v4_ipv4(network.get("externalAddress")),
                    *(
                        _v4_ipv4(node.get("controllerVmIp"))
                        for node in nodes.get("nodeList") or []
                    ),
                )
                if ip
            ]
            # `buildInfo.fullVersion` is the el8.5-release-… string v1 called
            # fullVersion. `buildInfo.version` is only "7.5" here and "pc.7.5.1.1" on
            # the domain manager — neither is the build.
            build = (config.get("buildInfo") or {}).get("fullVersion") or _v4_software(
                config, "PRISM_CENTRAL"
            )
            ncc_version = _v4_software(config, "NCC")

        num_vcpus: int | None = None
        memory_bytes: int | None = None
        if manager is not None:
            resources = (manager.get("config") or {}).get("resourceConfig") or {}
            num_vcpus = resources.get("numVcpus")
            memory_bytes = resources.get("memorySizeBytes")
            if node_count is None:
                node_count = len(manager.get("nodeExtIds") or []) or None
            for external in (manager.get("network") or {}).get("externalNetworks") or []:
                ips += [
                    ip
                    for address in external.get("ipAddresses") or []
                    if (ip := _v4_ipv4(address))
                ]

        return {
            "node_count": node_count,
            "ips": list(dict.fromkeys(ips)),
            "build": build,
            "ncc_version": ncc_version,
            "num_vcpus": num_vcpus,
            "memory_bytes": memory_bytes,
        }

    def _domain_manager(self) -> dict[str, Any] | None:
        """Prism Central's own `prism` domain-manager record, or None if not served.

        `prism` is not one of the negotiated namespaces, so this is guarded separately:
        a PC can route `clustermgmt` without routing `prism`, and the collector should
        then degrade to what the cluster entry carries instead of failing the run.
        """
        try:
            managers = self.conn.v4_list("prism/v4.0/config/domain-managers")
        except NutanixApiError:
            return None
        # The collection holds exactly one entry — the Prism Central being queried.
        return managers[0] if managers else None

    def _pc_cluster_entry(self) -> dict[str, Any] | None:
        """The PRISM_CENTRAL pseudo-cluster the PC keeps for itself in the cluster list.

        The clusters collector reads the same list but does not retain this entry, and
        re-listing is cheaper than coupling the two collectors' ordering.
        """
        try:
            clusters = self.conn.v4_list("clustermgmt/v4.0/config/clusters")
        except NutanixApiError:
            return None
        for entity in clusters:
            functions = (entity.get("config") or {}).get("clusterFunction") or []
            if "PRISM_CENTRAL" in functions:
                return entity
        return None

    # ─── v1 gateway ────────────────────────────────────────────────

    def _pc_details(self) -> dict[str, Any] | None:
        """Read the PC's own cluster entity (v1 gateway on the PC itself).

        Prism Central is itself a cluster; its gateway reports the true PCVM node
        count and service IPs. (The previous source — multicluster
        cluster_external_state — lists *registered PE clusters*, whose IP count made
        a single-node PC look scaled-out.)
        """
        try:
            data = self.conn.get_json(
                f"{self.conn.creds.base}/PrismGateway/services/rest/v1/cluster"
            )
        except NutanixApiError:
            return None
        if not isinstance(data, dict) or not data:
            return None
        ips = [ip for ip in (data.get("clusterExternalIPAddress"),) if ip]
        svm_ips = data.get("svmips")
        if isinstance(svm_ips, str):
            ips += [p.strip() for p in svm_ips.split(",") if p.strip()]
        elif isinstance(svm_ips, list):
            ips += [str(p) for p in svm_ips]
        return {
            "node_count": data.get("numNodes"),
            "ips": list(dict.fromkeys(ips)),
            "build": data.get("fullVersion"),
            "ncc_version": data.get("nccVersion"),
        }


def _v4_software(config: dict[str, Any], software_type: str) -> str | None:
    """Version for one entry of v4's clusterSoftwareMap (NCC / PRISM_CENTRAL)."""
    for entry in config.get("clusterSoftwareMap") or []:
        if entry.get("softwareType") == software_type:
            version = entry.get("version")
            return version if isinstance(version, str) else None
    return None


def _v4_ipv4(address: Any) -> str | None:
    """Pull the plain address out of v4's nested common.v1.config.IPAddress shape.

    Absent addresses arrive as `{}` rather than null, so an empty result must read as
    "no address" and not as a missing key.
    """
    if not isinstance(address, dict):
        return None
    ipv4 = address.get("ipv4")
    if not isinstance(ipv4, dict):
        return None
    value = ipv4.get("value")
    return value if isinstance(value, str) and value else None
