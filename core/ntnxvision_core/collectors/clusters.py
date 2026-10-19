"""Cluster collector (v4 `clustermgmt` when served, else v3 `clusters/list`).

Maps registered clusters into `Cluster` models. Prism Central itself appears as a
"cluster" in both APIs — typed PRISM_CENTRAL in v3's service_list and in v4's
clusterFunction — and is filtered out of the physical cluster list either way.

**One field does not survive the move.** v4 exposes no cluster encryption status on
pc.7.5.1.1: it is absent from `clustermgmt/v4.0/config/clusters`, and the per-cluster
detail endpoint returns exactly the same keys as the list. v3's `encryption_status` is
still the only source, and dropping it would silently blank the Encryption column and
the security posture section of the ADD. So the v4 path fills that one field from v3
while v3 exists, and leaves it None once it does not. Everything else comes from v4,
which is also strictly richer here — v3 leaves `is_lts` null, v4 reports it.

**Design fields.** v4 also gives the cluster type (hyperconverged or compute-only), its
functions (``ONE_NODE`` / ``TWO_NODE``) and the fault tolerance state (fault domain level,
current and desired tolerance). Below the v4 line the fault domain and tolerance come from
each cluster's Prism Element (v2.0 ``cluster`` through the PC proxy), best-effort. Cluster
categories (a POD category, for instance) are read from v3, which names them; v4 lists
only category ext ids.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.collectors.normalize import hypervisor_name
from ntnxvision_core.models import Cluster


class ClusterCollector(Collector):
    name = "clusters"
    label = "Clusters"

    def collect(self, env: Any) -> None:
        if self.conn.v4_ready("clusters"):
            self._collect_v4(env)
        else:
            self._collect_v3(env)
        env.collection_notes[self.name] = "ok"

    # ─── v4 ────────────────────────────────────────────────────────

    def _collect_v4(self, env: Any) -> None:
        encryption, categories = self._v3_details_by_uuid()
        for entity in self.conn.v4_list("clustermgmt/v4.0/config/clusters"):
            config = entity.get("config") or {}
            functions = config.get("clusterFunction") or []
            uuid = entity.get("extId") or ""

            # The Prism Central pseudo-cluster, as v3's service_list does.
            if "PRISM_CENTRAL" in functions:
                if not env.prism_central.uuid:
                    env.prism_central.uuid = uuid
                    env.prism_central.version = _v4_software(config, "PRISM_CENTRAL")
                continue

            network = entity.get("network") or {}
            build = config.get("buildInfo") or {}
            ft = config.get("faultToleranceState") or {}
            env.clusters.append(
                Cluster(
                    uuid=uuid,
                    name=entity.get("name"),
                    # buildInfo.version is the clean "7.5"; the NOS entry in
                    # clusterSoftwareMap is the full el8.5-release-ganges-… build string.
                    aos_version=build.get("version") or _v4_software(config, "NOS"),
                    ncc_version=_v4_software(config, "NCC"),
                    # v4 spells it "ESX" where v3 says "ESXi" — normalize, or the same
                    # cluster reads differently depending on which path served it.
                    hypervisor_types=[
                        n
                        for h in (config.get("hypervisorTypes") or [])
                        if (n := hypervisor_name(h))
                    ],
                    cluster_arch=config.get("clusterArch"),
                    redundancy_factor=config.get("redundancyFactor"),
                    num_nodes=(entity.get("nodes") or {}).get("numberOfNodes"),
                    external_ip=_v4_ipv4(network.get("externalAddress")),
                    data_services_ip=_v4_ipv4(network.get("externalDataServiceIp")),
                    vcenter_address=v4_vcenter(network),
                    timezone=config.get("timezone"),
                    encryption_enabled=encryption.get(uuid),
                    is_lts=config.get("isLts"),
                    operation_mode=config.get("operationMode"),
                    cluster_type=_enum(config.get("clusterType")),
                    cluster_functions=[f for f in functions if isinstance(f, str)],
                    fault_domain=_enum(ft.get("domainAwarenessLevel")),
                    fault_tolerance=_int(ft.get("currentMaxFaultTolerance"))
                    if ft.get("currentMaxFaultTolerance") is not None
                    else _cft(ft.get("currentClusterFaultTolerance")),
                    desired_fault_tolerance=_int(ft.get("desiredMaxFaultTolerance"))
                    if ft.get("desiredMaxFaultTolerance") is not None
                    else _cft(ft.get("desiredClusterFaultTolerance")),
                    categories=categories.get(uuid, {}),
                    api_source="v4",
                )
            )

    def _v3_details_by_uuid(
        self,
    ) -> tuple[dict[str, bool | None], dict[str, dict[str, list[str]]]]:
        """uuid → encryption state and uuid → categories, read from v3: v4 does not
        expose the encryption state and lists categories only as ext ids.

        Best-effort by design: once the legacy APIs are retired this returns empty and
        the columns go blank rather than the run failing.
        """
        try:
            entities = self.conn.v3_list("clusters")
        except NutanixApiError:
            return {}, {}
        out: dict[str, bool | None] = {}
        cats: dict[str, dict[str, list[str]]] = {}
        for entity in entities:
            uuid = entity.get("metadata", {}).get("uuid")
            config = entity.get("status", {}).get("resources", {}).get("config", {})
            if uuid:
                out[uuid] = _encryption_enabled(config.get("encryption_status"))
                cats[uuid] = _v3_categories(entity.get("metadata", {}))
        return out, cats

    # ─── v3 ────────────────────────────────────────────────────────

    def _collect_v3(self, env: Any) -> None:
        for entity in self.conn.v3_list("clusters"):
            status = entity.get("status", {})
            resources = status.get("resources", {})
            config = resources.get("config", {})
            network = resources.get("network", {})

            service_list = config.get("service_list", []) or []
            # Skip the Prism Central "cluster" pseudo-entity.
            if "PRISM_CENTRAL" in service_list:
                if not env.prism_central.uuid:
                    env.prism_central.uuid = entity.get("metadata", {}).get("uuid")
                    env.prism_central.version = _software_version(config)
                continue

            hypervisors = list(
                {
                    h.get("hypervisor_type")
                    for h in config.get("node_list", [])
                    if h.get("hypervisor_type")
                }
            ) or config.get("supported_hypervisors", []) or []

            env.clusters.append(
                Cluster(
                    uuid=entity.get("metadata", {}).get("uuid", ""),
                    name=status.get("name"),
                    aos_version=_software_version(config),
                    ncc_version=_ncc_version(config),
                    hypervisor_types=[h for h in hypervisors if h],
                    cluster_arch=config.get("cluster_arch"),
                    redundancy_factor=config.get("redundancy_factor"),
                    num_nodes=len(config.get("node_list", []) or []) or None,
                    external_ip=network.get("external_ip"),
                    data_services_ip=network.get("external_data_services_ip") or None,
                    vcenter_address=v3_vcenter(config),
                    timezone=config.get("timezone"),
                    encryption_enabled=_encryption_enabled(config.get("encryption_status")),
                    operation_mode=config.get("operation_mode"),
                    categories=_v3_categories(entity.get("metadata", {})),
                    api_source="v3",
                )
            )
        self._pe_fault_tolerance(env)

    def _pe_fault_tolerance(self, env: Any) -> None:
        """Fault domain and tolerance from each cluster's Prism Element (v2.0), below v4.

        One proxied GET per cluster; a cluster whose PE cannot be read keeps None.
        """
        scope = getattr(self.conn, "scope", None)
        pe_get = getattr(self.conn, "pe_get", None)
        if pe_get is None:  # a connection without the Prism Element proxy
            return
        for cluster in env.clusters:
            if cluster.fault_domain is not None or not cluster.uuid:
                continue
            if scope is not None and scope.excludes(cluster.uuid):
                continue
            try:
                data = pe_get(cluster.uuid, "cluster")
            except NutanixApiError:
                continue
            if not isinstance(data, dict):
                continue
            cluster.fault_domain = _PE_DOMAINS.get(
                str(data.get("fault_tolerance_domain_type") or "").upper()) or _enum(
                data.get("fault_tolerance_domain_type"))
            state = data.get("cluster_redundancy_state") or {}
            desired_rf = _int(state.get("desired_redundancy_factor"))
            current_rf = _int(state.get("current_redundancy_factor"))
            if current_rf:
                cluster.fault_tolerance = current_rf - 1
            if desired_rf:
                cluster.desired_fault_tolerance = desired_rf - 1


#: Prism Element's fault_tolerance_domain_type → v4's domainAwarenessLevel.
_PE_DOMAINS = {"RACKABLE_UNIT": "BLOCK", "NODE": "NODE", "RACK": "RACK", "DISK": "DISK"}


def _enum(value: Any) -> str | None:
    """An enum string as given, None for absent / unknown (``$UNKNOWN``) values."""
    if not isinstance(value, str) or not value or value.startswith("$"):
        return None
    return value


def _int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def _cft(value: Any) -> int | None:
    """Failures tolerated, from a ``CFT_1N_OR_1D``-style enum (the node count)."""
    if not isinstance(value, str) or not value.startswith("CFT_"):
        return None
    digits = value[4:].split("N", 1)[0]
    return int(digits) if digits.isdigit() else None


def _v3_categories(metadata: dict[str, Any]) -> dict[str, list[str]]:
    """v3 ``metadata.categories_mapping`` (key → values), else ``categories`` (key →
    value), without the system ``PrismCentral``-style empty keys."""
    mapping = metadata.get("categories_mapping")
    if isinstance(mapping, dict) and mapping:
        return {str(k): [str(v) for v in (vals or [])] for k, vals in mapping.items()}
    flat = metadata.get("categories")
    if isinstance(flat, dict):
        return {str(k): [str(v)] for k, v in flat.items() if v not in (None, "")}
    return {}


def _v4_software(config: dict[str, Any], software_type: str) -> str | None:
    """Version for one entry of v4's clusterSoftwareMap (NOS / NCC / PRISM_CENTRAL)."""
    for entry in config.get("clusterSoftwareMap") or []:
        if entry.get("softwareType") == software_type:
            version = entry.get("version")
            return version if isinstance(version, str) else None
    return None


def _v4_ipv4(address: Any) -> str | None:
    """Pull the plain address out of v4's nested common.v1.config.IPAddress shape."""
    if not isinstance(address, dict):
        return None
    ipv4 = address.get("ipv4")
    if not isinstance(ipv4, dict):
        return None
    value = ipv4.get("value")
    return value if isinstance(value, str) else None


def v4_vcenter(network: Any) -> str | None:
    """The vCenter an ESXi cluster is registered to, from v4 ``network.managementServer``.

    Verified on a live pc.7.5.1.1: ``{"ip": {"ipv4": {"value": ...}}, "type": "VCENTER",
    "isRegistered": true, ...}``. AHV clusters have no management server.
    """
    if not isinstance(network, dict):
        return None
    server = network.get("managementServer")
    if not isinstance(server, dict) or str(server.get("type") or "VCENTER") != "VCENTER":
        return None
    ip = server.get("ip") or {}
    fqdn = (server.get("fqdn") or {}).get("value") if isinstance(server.get("fqdn"), dict) \
        else None
    return _v4_ipv4(ip) or ((ip.get("ipv6") or {}).get("value") if isinstance(ip, dict)
                            else None) or fqdn


def v3_vcenter(config: Any) -> str | None:
    """The same from v3's ``config.management_server_list`` (type ``VCENTER``)."""
    for server in (config or {}).get("management_server_list") or []:
        if isinstance(server, dict) and str(server.get("type") or "").upper() == "VCENTER":
            ip = server.get("ip")
            if isinstance(ip, str) and ip:
                return ip
    return None


def _encryption_enabled(status: Any) -> bool | None:
    """True only for explicitly enabled states.

    v3 reports values like ENABLED / PARTIALLY_ENABLED / DISABLED / NOT_SUPPORTED —
    treating anything != DISABLED as encrypted produced false positives.
    """
    if not isinstance(status, str) or not status:
        return None
    return status.upper() in ("ENABLED", "PARTIALLY_ENABLED", "PARTIAL")


def _software_version(config: dict[str, Any]) -> str | None:
    versions = config.get("software_map", {}) or {}
    nos = versions.get("NOS", {})
    return nos.get("version") if isinstance(nos, dict) else None


def _ncc_version(config: dict[str, Any]) -> str | None:
    versions = config.get("software_map", {}) or {}
    ncc = versions.get("NCC", {})
    return ncc.get("version") if isinstance(ncc, dict) else None
