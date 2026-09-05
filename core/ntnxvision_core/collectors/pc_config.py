"""Prism Central management-plane configuration & sizing collector.

Captures the PC's own version/build, VM sizing (vCPU/memory), single vs scale-out node
count, and the number of registered clusters — the management-tier details an architecture
document should record alongside the workload inventory.
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

        # PC nodes / sizing from the multicluster config (best-effort across versions).
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

        env.collection_notes[self.name] = "ok"

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
