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

**Deployment design** (for the management plane diagram):

* the domain manager also gives the PC's size (SMALL / LARGE / EXTRALARGE), the cluster
  hosting its VMs, its FQDN and scale-out VIP, and the configured size of one PC VM;
* ``prism/v4.0/management/domain-managers/{id}/products`` lists the PC products (Flow,
  DR, Self-Service, ...) and whether each is enabled. Below v4 the v3
  ``services/{microseg,nucalm,disaster_recovery}/status`` calls give the same for the
  three services they cover (``oss`` is Objects, recorded by the Objects area);
* ``.../registrations`` lists the registered clusters and the paired remote Prism
  Centrals (``DOMAIN_MANAGER`` entries), with their connectivity: the v4 AZ pairing;
* v3 ``prism_central`` (a GET) lists each PC VM with its hosting cluster, container and
  sizing, on every supported version.

Each of these is best-effort: one that is not served is skipped, one that fails is named
in the area note, and the rest of the configuration is kept.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.api.v4_client import v4_is_ga
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import PcProduct, PcRegistration, PcVm, pc_size

#: v3 service status endpoints → the v4 product they stand for.
_V3_SERVICES = {
    "microseg": "FLOW_NETWORK_SECURITY",
    "nucalm": "SELF_SERVICE",
    "disaster_recovery": "NUTANIX_DISASTER_RECOVERY",
}


class PrismCentralConfigCollector(Collector):
    name = "pc_config"
    label = "Prism Central Configuration"

    def collect(self, env: Any) -> None:
        self._manager: dict[str, Any] | None = None
        self._manager_read = False
        self._notes: list[str] = []
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

        self._deployment(env)
        env.collection_notes[self.name] = (
            "ok: " + "; ".join(self._notes) if self._notes else "ok")

    # ─── deployment design ─────────────────────────────────────────

    def _deployment(self, env: Any) -> None:
        cfg = env.pc_config
        manager = (self._domain_manager()
                   if v4_is_ga(getattr(self.conn, "pc_version", None)) else None)
        if manager is not None:
            config = manager.get("config") or {}
            resources = config.get("resourceConfig") or {}
            network = manager.get("network") or {}
            cfg.size = pc_size(config.get("size")) or cfg.size
            cfg.hosting_cluster_uuid = manager.get("hostingClusterExtId") or None
            cfg.is_registered_with_hosting_cluster = manager.get(
                "isRegisteredWithHostingCluster")
            cfg.fqdn = network.get("fqdn") or None
            cfg.virtual_ip = _v4_ipv4(network.get("externalAddress"))
            cfg.vm_vcpus = resources.get("numVcpus")
            cfg.vm_memory_bytes = resources.get("memorySizeBytes")
            cfg.vm_data_disk_bytes = resources.get("dataDiskSizeBytes")
            cfg.high_availability = manager.get("shouldEnableHighAvailability")
            manager_id = manager.get("extId")
            if manager_id:
                self._products(env, manager_id)
                self._registrations(env, manager_id)
        if not cfg.products:
            self._products_v3(env)
        self._pc_vms_v3(env)

    def _products(self, env: Any, manager_id: str) -> None:
        path = f"prism/v4.0/management/domain-managers/{manager_id}/products"
        rows = self._optional_list(path, "PC products")
        env.pc_config.products = sorted(
            (PcProduct(
                name=str(r.get("name")),
                enablement_state=r.get("enablementState") if _known(r.get("enablementState"))
                else None,
                version=r.get("version") or None,
                enabled_at=r.get("serviceEnablementTime") or None,
                cpu_count=(r.get("resourceSpec") or {}).get("cpuCount"),
                memory_bytes=(r.get("resourceSpec") or {}).get("memorySizeBytes"),
                api_source="v4",
            ) for r in rows if _known(r.get("name"))),
            key=lambda p: p.name)

    def _registrations(self, env: Any, manager_id: str) -> None:
        path = f"prism/v4.0/management/domain-managers/{manager_id}/registrations"
        for r in self._optional_list(path, "registrations"):
            remote = r.get("remoteClusterDetails") or {}
            uuid = r.get("remoteClusterExtId") or r.get("extId")
            if not uuid:
                continue
            status = r.get("connectivityStatus")
            env.pc_registrations.append(PcRegistration(
                remote_uuid=str(uuid),
                name=remote.get("name"),
                remote_type=remote.get("clusterType") if _known(remote.get("clusterType"))
                else None,
                version=remote.get("clusterVersion") or None,
                address=_v4_address(remote.get("externalAddress")),
                node_ips=[ip for a in remote.get("nodeIpAddresses") or []
                          if (ip := _v4_address(a))],
                connectivity_status=status if _known(status) else None,
                api_source="v4",
            ))
        env.pc_registrations.sort(key=lambda r: (r.remote_type or "", (r.name or "").casefold(),
                                                 r.remote_uuid))

    def _optional_list(self, path: str, what: str) -> list[dict[str, Any]]:
        """A v4 list that may not be served: 404 is silent, other errors are noted."""
        try:
            return self.conn.v4_list(path)
        except NutanixApiError as exc:
            if exc.status_code != 404:
                self._notes.append(f"{what} unavailable ({_reason(exc)})")
            return []

    def _products_v3(self, env: Any) -> None:
        products: list[PcProduct] = []
        for service, product in _V3_SERVICES.items():
            try:
                data = self.conn.get_json(
                    f"{self.conn.creds.base}/api/nutanix/v3/services/{service}/status")
            except NutanixApiError:
                continue
            state = (data or {}).get("service_enablement_status")
            if isinstance(state, str) and state:
                products.append(PcProduct(
                    name=product, enablement_state=state.upper(),
                    enabled_at=data.get("service_enablement_timestamp") or None,
                    api_source="v3"))
        env.pc_config.products = sorted(products, key=lambda p: p.name)

    def _pc_vms_v3(self, env: Any) -> None:
        """PC VMs with their hosting cluster and sizing (v3 ``prism_central``, a GET)."""
        try:
            data = self.conn.get_json(f"{self.conn.creds.base}/api/nutanix/v3/prism_central")
        except NutanixApiError as exc:
            if exc.status_code != 404:
                self._notes.append(f"PC VMs unavailable ({_reason(exc)})")
            return
        resources = (data or {}).get("resources") or {}
        cfg = env.pc_config
        if cfg.size is None:
            cfg.size = pc_size(resources.get("pc_size"))
        if cfg.is_registered_with_hosting_cluster is None:
            cfg.is_registered_with_hosting_cluster = resources.get("is_registered_to_hosting_pe")
        cmsp = resources.get("cmsp_config")
        cfg.cmsp_enabled = bool(cmsp) if cmsp is not None else None
        vms: list[PcVm] = []
        for vm in resources.get("pc_vm_list") or []:
            if not isinstance(vm, dict) or not vm.get("vm_uuid"):
                continue
            ips = [ip for nic in vm.get("nic_list") or [] if isinstance(nic, dict)
                   for ip in nic.get("ip_list") or [] if ip]
            vms.append(PcVm(
                uuid=str(vm["vm_uuid"]),
                name=vm.get("vm_name"),
                hosting_cluster_uuid=(vm.get("cluster_reference") or {}).get("uuid"),
                container_uuid=vm.get("container_uuid") or None,
                # v3 reports PC VM vCPUs as sockets (one core each).
                num_vcpus=vm.get("num_sockets"),
                memory_bytes=vm.get("memory_size_bytes"),
                data_disk_bytes=vm.get("data_disk_size_bytes"),
                ip_addresses=list(dict.fromkeys(ips)),
                power_state=vm.get("power_state"),
                status=vm.get("status"),
                api_source="v3",
            ))
        env.pc_vms.extend(sorted(vms, key=lambda v: ((v.name or "").casefold(), v.uuid)))
        if cfg.hosting_cluster_uuid is None:
            hosting = {v.hosting_cluster_uuid for v in vms if v.hosting_cluster_uuid}
            if len(hosting) == 1:
                cfg.hosting_cluster_uuid = hosting.pop()

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
        if self._manager_read:
            return self._manager
        self._manager_read = True
        try:
            managers = self.conn.v4_list("prism/v4.0/config/domain-managers")
        except NutanixApiError:
            return None
        # The collection holds exactly one entry — the Prism Central being queried.
        self._manager = managers[0] if managers else None
        return self._manager

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


def _v4_address(address: Any) -> str | None:
    """IPAddressOrFQDN → its address."""
    if not isinstance(address, dict):
        return None
    for key in ("ipv4", "fqdn", "ipv6"):
        inner = address.get(key)
        if isinstance(inner, dict) and isinstance(inner.get("value"), str) and inner["value"]:
            return str(inner["value"])
    return None


def _known(value: Any) -> bool:
    """A real enum value (not None, empty or ``$UNKNOWN`` / ``$REDACTED``)."""
    return isinstance(value, str) and bool(value) and not value.startswith("$")


def _reason(exc: NutanixApiError) -> str:
    if exc.status_code:
        return f"HTTP {exc.status_code}"
    return str(exc).split(":", 1)[0] or "request failed"
