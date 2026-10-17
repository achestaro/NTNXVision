"""Render a synthetic estate as Nutanix API payloads.

One function per entity per API. Shapes follow the published definitions on
developers.nutanix.com and — where the collectors depend on a detail — the live
pc.7.5.1.1 payloads trimmed into the unit-test fixtures:

* **v4** entities carry ``extId`` plus the ``$objectType`` / ``$reserved`` markers, and
  list responses use ``{"data": [...], "metadata": {"totalAvailableResults", "flags",
  "links"}}``. Addresses use the nested ``common.v1.config.IPAddress`` shape
  (``{"ipv4": {"value", "prefixLength"}}``).
* **v3** entities are ``{"metadata", "spec", "status"}`` and list responses
  ``{"api_version": "3.1", "metadata": {"kind", "total_matches", "length", "offset"},
  "entities": [...]}``. The groups API answers ``group_results[].entity_results[].data``.
* **v2.0** (Prism Element) entities are flat; lists use
  ``{"metadata": {"grand_total_entities", "total_entities", "count", …}, "entities"}``.
* **Era** (NDB) returns bare JSON arrays of camelCase objects.

The collectors' known quirks are reproduced on purpose (v4 padding absent values with
``""``, the Prism Central pseudo host in v3 ``hosts/list``, ESXi VMs having different
uuids in v3 and v4) so the synthetic run exercises the same code paths a lab does.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.synthetic.estate import GIB, MIB, PcEstate, PeEstate, uid

_R = {"$fv": "v4.r0"}


def _v4(object_type: str, body: dict[str, Any]) -> dict[str, Any]:
    return {"$objectType": object_type, "$reserved": _R, **body}


def ip4(value: str | None, prefix: int = 32) -> dict[str, Any]:
    """v4's common.v1.config.IPAddress — absent addresses are `{}`, not null."""
    if not value:
        return {}
    return {"ipv4": {"value": value, "prefixLength": prefix}}


def v4_list(
    items: list[dict[str, Any]], total: int, path: str, page: int, limit: int,
    object_type: str = "common.v1.response.ApiResponse",
) -> dict[str, Any]:
    links = [{"href": f"/api/{path}?$page={page}&$limit={limit}", "rel": "self"}]
    if (page + 1) * limit < total:
        links.append({"href": f"/api/{path}?$page={page + 1}&$limit={limit}", "rel": "next"})
    return {
        "$objectType": object_type,
        "$reserved": _R,
        "data": items,
        "metadata": {
            "$objectType": "common.v1.response.ApiResponseMetadata",
            "totalAvailableResults": total,
            "flags": [{"name": "hasError", "value": False},
                      {"name": "isPaginated", "value": True}],
            "links": links,
        },
    }


def v4_single(item: dict[str, Any]) -> dict[str, Any]:
    return {"$objectType": "common.v1.response.ApiResponse", "$reserved": _R, "data": item,
            "metadata": {"flags": [{"name": "hasError", "value": False}]}}


def v3_list(kind: str, entities: list[dict[str, Any]], total: int, offset: int) -> dict[str, Any]:
    return {
        "api_version": "3.1",
        "metadata": {"kind": kind, "total_matches": total, "length": len(entities),
                     "offset": offset},
        "entities": entities,
    }


def v2_list(entities: list[dict[str, Any]], total: int, offset: int) -> dict[str, Any]:
    return {
        "metadata": {"grand_total_entities": total, "total_entities": total,
                     "count": len(entities), "start_index": offset,
                     "end_index": offset + len(entities)},
        "entities": entities,
    }


def ref(kind: str, uuid: str | None, name: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": kind, "uuid": uuid}
    if name is not None:
        out["name"] = name
    return out


def _v3(kind: str, uuid: str, name: str | None, resources: dict[str, Any],
        spec_resources: dict[str, Any] | None = None, extra_status: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None, description: str | None = None,
        ) -> dict[str, Any]:
    spec: dict[str, Any] = {"name": name, "resources": spec_resources
                            if spec_resources is not None else resources}
    status: dict[str, Any] = {"name": name, "state": "COMPLETE", "resources": resources,
                              **(extra_status or {})}
    if description is not None:
        spec["description"] = description
        status["description"] = description
    return {
        "metadata": {"kind": kind, "uuid": uuid, "spec_version": 0, **(metadata or {})},
        "spec": spec,
        "status": status,
    }


# ─── clusters ──────────────────────────────────────────────────────────


def _nos_build(aos: str) -> str:
    return f"el8.5-release-fraser-{aos}-stable-0"


def cluster_v4(est: PcEstate, c: dict[str, Any]) -> dict[str, Any]:
    hosts = [h for h in est.hosts if h["cluster_uuid"] == c["uuid"]]
    return _v4("clustermgmt.v4.config.Cluster", {
        "extId": c["uuid"],
        "name": c["name"],
        "nodes": {
            "numberOfNodes": c["nodes"],
            "nodeList": [{"nodeUuid": h["uuid"], "controllerVmIp": ip4(h["cvm_ip"]),
                          "hostIp": ip4(h["hv_ip"])} for h in hosts],
        },
        "network": {
            "externalAddress": ip4(c["external_ip"]),
            **({"externalDataServiceIp": ip4(c["data_services_ip"])}
               if c.get("data_services_ip") else {}),
            "nameServerIpList": [ip4("10.20.1.5"), ip4("10.20.1.6")],
            "ntpServerIpList": [{"fqdn": {"value": "pool.ntp.org"}}],
            **({"managementServer": {
                "$objectType": "clustermgmt.v4.config.ManagementServerRef",
                "ip": ip4(est.features.vcenter_address), "type": "VCENTER",
                "isDrsEnabled": True, "isRegistered": True, "isInUse": True}}
               if c["hypervisor"] == "ESXi" and est.features.vcenter_address else {}),
        },
        "config": {
            "clusterFunction": ["AOS"],
            "hypervisorTypes": ["ESX" if c["hypervisor"] == "ESXi" else "AHV"],
            "clusterArch": c["arch"],
            "redundancyFactor": c["rf"],
            "timezone": c["timezone"],
            "operationMode": "NORMAL",
            "isLts": c["is_lts"],
            "encryptionInTransitStatus": "DISABLED",
            "buildInfo": {"version": c["aos"], "fullVersion": _nos_build(c["aos"])},
            "clusterSoftwareMap": [
                {"softwareType": "NCC", "version": c["ncc"]},
                {"softwareType": "NOS", "version": _nos_build(c["aos"])},
            ],
        },
    })


def pc_cluster_v4(est: PcEstate) -> dict[str, Any]:
    return _v4("clustermgmt.v4.config.Cluster", {
        "extId": est.pc_uuid,
        "name": est.host.split(".")[0],
        "nodes": {"numberOfNodes": 1,
                  "nodeList": [{"controllerVmIp": ip4(est.pc_ip), "hostIp": {}}]},
        # A single-node PC has no cluster VIP — v4 renders that absence as `{}`.
        "network": {"externalAddress": {}},
        "config": {
            "clusterFunction": ["PRISM_CENTRAL"],
            "buildInfo": {"version": est.version, "fullVersion": est.pc_build},
            "clusterSoftwareMap": [
                {"softwareType": "PRISM_CENTRAL", "version": est.version},
                {"softwareType": "NCC", "version": est.ncc},
            ],
        },
    })


def cluster_v3(est: PcEstate, c: dict[str, Any]) -> dict[str, Any]:
    hosts = [h for h in est.hosts if h["cluster_uuid"] == c["uuid"]]
    resources = {
        "config": {
            "service_list": ["AOS"],
            "software_map": {
                "NOS": {"software_type": "NOS", "status": "INSTALLED", "version": c["aos"]},
                "NCC": {"software_type": "NCC", "status": "INSTALLED", "version": c["ncc"]},
            },
            "redundancy_factor": c["rf"],
            "cluster_arch": c["arch"],
            "timezone": c["timezone"],
            "operation_mode": "NORMAL",
            "encryption_status": c["encryption"],
            "is_available": True,
            **({"management_server_list": [{
                "ip": est.features.vcenter_address, "type": "VCENTER",
                "status_list": ["REGISTERED", "IN_USE"], "drs_enabled": True}]}
               if c["hypervisor"] == "ESXi" and est.features.vcenter_address else {}),
        },
        "network": {"external_ip": c["external_ip"],
                    "external_data_services_ip": c.get("data_services_ip"),
                    "name_server_ip_list": ["10.20.1.5", "10.20.1.6"]},
        "nodes": {"hypervisor_server_list": [
            {"ip": h["hv_ip"], "type": "ESX" if h["hypervisor"] == "ESXi" else "AHV",
             "version": h["hv_version"]} for h in hosts]},
    }
    return _v3("cluster", c["uuid"], c["name"], resources)


def pc_cluster_v3(est: PcEstate) -> dict[str, Any]:
    resources = {
        "config": {
            "service_list": ["PRISM_CENTRAL"],
            "software_map": {
                "NOS": {"software_type": "NOS", "version": est.version},
                "NCC": {"software_type": "NCC", "version": est.ncc},
            },
        },
        "network": {"external_ip": None},
    }
    return _v3("cluster", est.pc_uuid, est.host.split(".")[0], resources)


def pc_gateway_v1(est: PcEstate) -> dict[str, Any]:
    """`PrismGateway/services/rest/v1/cluster` on a Prism Central (version detection)."""
    return {
        "id": f"{est.pc_uuid}::1",
        "uuid": est.pc_uuid,
        "name": est.host.split(".")[0],
        "version": est.version,
        "fullVersion": est.pc_build,
        "clusterExternalIPAddress": None,
        "numNodes": 1,
        "svmips": est.pc_ip,
        "nccVersion": est.ncc,
        "isNSEnabled": False,
    }


def domain_manager_v4(est: PcEstate) -> dict[str, Any]:
    return _v4("prism.v4.management.DomainManager", {
        "extId": est.pc_uuid,
        "name": est.host.split(".")[0],
        "config": {"resourceConfig": {"numVcpus": 6, "memorySizeBytes": 28 * GIB,
                                      "dataDiskSizeBytes": 500 * GIB},
                   "buildInfo": {"version": est.version}, "size": "SMALL"},
        "nodeExtIds": [next(v["uuid"] for v in est.vms if "PCVM" in v["name"])],
        "network": {"externalNetworks": [{"ipAddresses": [ip4(est.pc_ip)]}]},
        "isRegisteredWithHostingCluster": True,
    })


# ─── hosts ─────────────────────────────────────────────────────────────


def host_v4(h: dict[str, Any], num_vms: int) -> dict[str, Any]:
    return _v4("clustermgmt.v4.config.Host", {
        "extId": h["uuid"],
        "hostName": h["name"],
        "hostType": "HYPER_CONVERGED",
        "cluster": {"uuid": h["cluster_uuid"], "name": h["cluster_name"]},
        "hypervisor": {
            "type": "ESX" if h["hypervisor"] == "ESXi" else "AHV",
            "fullName": h["hv_version"],
            "externalAddress": ip4(h["hv_ip"]),
            "numberOfVms": num_vms,
            "state": "ACROPOLIS_NORMAL",
        },
        "cpuModel": h["cpu_model"],
        "numberOfCpuSockets": h["sockets"],
        "numberOfCpuCores": h["cores"],
        "numberOfCpuThreads": h["threads"],
        "cpuFrequencyHz": h["freq_hz"],
        # Aggregate across cores — NOT v3's per-core cpu_capacity_hz (see hosts.py).
        "cpuCapacityHz": h["freq_hz"] * h["cores"],
        "memorySizeBytes": h["memory"],
        "blockSerial": h["block_serial"],
        "blockModel": h["block_model"],
        "nodeSerial": h["serial"],
        "ipmi": {"ip": ip4(h["ipmi_ip"]), "username": "ADMIN"},
        "controllerVm": {"externalAddress": ip4(h["cvm_ip"]), "isInMaintenanceMode": False},
        "maintenanceState": h["maintenance"],
        # v4 pads a GPU-less host's list with an empty string.
        "gpuList": h["gpus"] or [""],
        # Physical disks; their sizes sum to the node's raw storage.
        "disk": [{"extId": uid(h["uuid"], "disk", i), "serialId": f"{h['serial']}-D{i:02d}",
                  "sizeInBytes": d["size"], "storageTier": d["tier"],
                  "mountPath": f"/home/nutanix/data/stargate-storage/disks/{i}"}
                 for i, d in enumerate(h["disks"])],
    })


def host_v3(h: dict[str, Any], num_vms: int) -> dict[str, Any]:
    resources = {
        "host_type": "HYPER_CONVERGED",
        "cpu_model": h["cpu_model"],
        "num_cpu_sockets": h["sockets"],
        "num_cpu_cores": h["cores"],
        "cpu_capacity_hz": h["freq_hz"],
        "memory_capacity_mib": h["memory"] // MIB,
        "serial_number": h["serial"],
        "block": {"block_serial_number": h["block_serial"], "block_model": h["block_model"]},
        "ipmi": {"ip": h["ipmi_ip"]},
        "controller_vm": {"ip": h["cvm_ip"], "oplog_usage": {"oplog_disk_pct": 0.4}},
        "hypervisor": {"ip": h["hv_ip"], "hypervisor_full_name": h["hv_version"],
                       "num_vms": num_vms},
        "gpu_list": [{"name": g, "mode": "PASSTHROUGH_GRAPHICS"} for g in h["gpus"]],
        "node_state": "NORMAL",
    }
    return _v3("host", h["uuid"], h["name"], resources,
               extra_status={"cluster_reference": ref("cluster", h["cluster_uuid"],
                                                      h["cluster_name"])})


def pc_pseudo_host_v3(est: PcEstate) -> dict[str, Any]:
    """v3 returns Prism Central itself as a host: no CPU, a serial that is really its IP."""
    return _v3("host", est.pc_uuid, None, {
        "serial_number": est.pc_ip.replace(".", "-"),
        "block": {"block_serial_number": "null", "block_model": "null"},
        "controller_vm": {"ip": est.pc_ip},
    })


# ─── storage ───────────────────────────────────────────────────────────


def container_v4(ct: dict[str, Any]) -> dict[str, Any]:
    return _v4("clustermgmt.v4.config.StorageContainer", {
        "containerExtId": ct["uuid"],
        "extId": ct["uuid"],
        "name": ct["name"],
        "clusterName": ct["cluster_name"],
        "clusterExtId": ct["cluster_uuid"],
        "replicationFactor": ct["rf"],
        "isCompressionEnabled": ct["compression"],
        "compressionDelaySecs": ct["compression_delay"],
        "onDiskDedup": ct["dedup"],
        "erasureCode": ct["ec"].upper(),
        "maxCapacityBytes": ct["raw"],
        "isEncrypted": False,
    })


_GROUP_ATTRS = {
    "container_name": lambda ct: ct["name"],
    "cluster_name": lambda ct: ct["cluster_name"],
    "cluster": lambda ct: ct["cluster_uuid"],
    "replication_factor": lambda ct: str(ct["rf"]),
    "compression_enabled": lambda ct: "true" if ct["compression"] else "false",
    "compression_delay_in_secs": lambda ct: str(ct["compression_delay"]),
    "on_disk_dedup": lambda ct: ct["dedup"],
    "erasure_code": lambda ct: ct["ec"],
    "max_capacity": lambda ct: str(ct["raw"]),
    "storage.user_container_max_capacity_bytes": lambda ct: str(ct["max"]),
    "storage.capacity_bytes": lambda ct: str(ct["max"]),
    "storage.user_usage_bytes": lambda ct: str(ct["used"]),
    "storage.user_free_bytes": lambda ct: str(ct["free"]),
}

_HOST_ATTRS = {
    "host_name": lambda h: h["name"],
    "storage.capacity_bytes": lambda h: str(h["storage_raw"]),
}

_CVM_ATTRS = {
    "vm_name": lambda v: v["name"],
    "power_state": lambda v: "on",
    "cluster_name": lambda v: v["cluster_name"],
    "host_name": lambda v: v["host_name"],
    "memory_size_bytes": lambda v: str(v["memory"]),
    "num_vcpus": lambda v: str(v["vcpus"]),
    "ip_addresses": lambda v: v["ip"],
    "hypervisor_type": lambda v: "kKvm" if v["hypervisor"] == "AHV" else "kVMware",
}


def groups_response(
    entity_type: str, rows: list[dict[str, Any]], attrs: list[str], total: int,
) -> dict[str, Any]:
    getters = (_GROUP_ATTRS if entity_type == "storage_container"
               else _HOST_ATTRS if entity_type == "host" else _CVM_ATTRS)
    results = []
    for row in rows:
        data = []
        for attr in attrs:
            # mh_vm / virtual_nic / virtual_disk rows arrive pre-flattened (mockapi),
            # keyed by attribute.
            if entity_type in ("mh_vm", "virtual_nic", "virtual_disk"):
                value = row.get(attr)
            else:
                getter = getters.get(attr)
                value = getter(row) if getter else None
            # A multi-valued attribute (a VM's IPs) carries every value in one list.
            values = (list(value) if isinstance(value, list)
                      else [value] if value is not None else [])
            data.append({"name": attr, "values": [{"values": values,
                                                   "time": 1759309200000000}]})
        results.append({"entity_id": row["uuid"], "data": data})
    return {
        "entity_type": entity_type,
        "filtered_entity_count": total,
        "total_entity_count": total,
        "filtered_group_count": 1,
        "total_group_count": 1,
        "group_results": [{"entity_results": results, "total_entity_count": total}],
    }


def container_v3(ct: dict[str, Any]) -> dict[str, Any]:
    return _v3("storage_container", ct["uuid"], ct["name"], {
        "replication_factor": ct["rf"],
        "compression": {"enabled": ct["compression"], "delay_in_secs": ct["compression_delay"]},
        "on_disk_dedup": ct["dedup"],
        "erasure_code": ct["ec"],
        "advertised_capacity": {"max_capacity_bytes": ct["max"]},
        "usage_stats": {"storage.capacity_bytes": str(ct["max"]),
                        "storage.user_usage_bytes": str(ct["used"]),
                        "storage.user_free_bytes": str(ct["free"])},
    }, extra_status={"cluster_reference": ref("cluster", ct["cluster_uuid"], ct["cluster_name"])})


# ─── networking ────────────────────────────────────────────────────────


def subnet_v3(est: PcEstate, s: dict[str, Any]) -> dict[str, Any]:
    ip_config: dict[str, Any] = {
        "subnet_ip": s["prefix"], "prefix_length": s["prefix_len"],
        "default_gateway_ip": s["gateway"],
    }
    if s["dhcp"]:
        ip_config["dhcp_options"] = {"domain_name_server_list": ["10.20.1.5"]}
        base = s["prefix"].rsplit(".", 1)[0]
        ip_config["pool_list"] = [{"range": f"{base}.100 {base}.200"}]
    resources: dict[str, Any] = {
        "subnet_type": s["type"],
        "ip_config": ip_config,
    }
    if s["vlan"] is not None:
        resources["vlan_id"] = s["vlan"]
    if s["vswitch_uuid"]:
        resources["virtual_switch_uuid"] = s["vswitch_uuid"]
    if s["vpc"]:
        vpc = next(v for v in est.vpcs if v["name"] == s["vpc"])
        resources["vpc_reference"] = ref("vpc", vpc["uuid"], vpc["name"])
    extra = {}
    if s["cluster_uuid"]:
        extra["cluster_reference"] = ref("cluster", s["cluster_uuid"], s["cluster_name"])
    return _v3("subnet", s["uuid"], s["name"], resources, extra_status=extra)


def subnet_v4(s: dict[str, Any]) -> dict[str, Any]:
    return _v4("networking.v4.config.Subnet", {
        "extId": s["uuid"], "name": s["name"], "subnetType": s["type"],
        "networkId": s["vlan"], "clusterReference": s["cluster_uuid"],
        "virtualSwitchReference": s["vswitch_uuid"],
    })


def vswitch_v4(vs: dict[str, Any]) -> dict[str, Any]:
    return _v4("networking.v4.config.VirtualSwitch",
               {"extId": vs["uuid"], "name": vs["name"], "isDefault": vs["name"] == "vs0"})


def vpc_v3(vpc: dict[str, Any]) -> dict[str, Any]:
    return _v3("vpc", vpc["uuid"], vpc["name"], {
        "external_subnet_list": [{"external_subnet_reference": ref(
            "subnet", None, vpc["external_subnet"])}],
        "externally_routable_prefix_list": [{"ip": ip, "prefix_length": n}
                                            for ip, n in vpc["prefixes"]],
        "common_domain_name_server_ip_list": [{"ip": d} for d in vpc["dns"]],
    })


def floating_ip_v3(est: PcEstate, f: dict[str, Any]) -> dict[str, Any]:
    vpc = next(v for v in est.vpcs if v["name"] == f["vpc"])
    entity = _v3("floating_ip", f["uuid"], None, {
        "vpc_reference": ref("vpc", vpc["uuid"], vpc["name"]),
        "external_subnet_reference": ref("subnet", None, f["external_subnet"]),
        "private_ip": f["private_ip"],
    })
    entity["status"]["resources"] = {**entity["status"]["resources"], "floating_ip": f["ip"]}
    return entity


def route_table_v4(vpc: dict[str, Any]) -> dict[str, Any]:
    return _v4("networking.v4.config.RouteTable", {
        "extId": f"rt-{vpc['uuid']}", "vpcReference": vpc["uuid"],
        "staticRoutes": [{"destination": {"ipv4": {"ip": {"value": f"192.168.{i}.0"},
                                                   "prefixLength": 24}}}
                         for i in range(vpc["routes"])],
    })


def routing_policy_v4(vpc: dict[str, Any], i: int) -> dict[str, Any]:
    return _v4("networking.v4.config.RoutingPolicy", {
        "extId": f"rp-{vpc['uuid']}-{i}", "name": f"{vpc['name']}-policy-{i + 1}",
        "vpcExtId": vpc["uuid"], "priority": 100 + i,
    })


# ─── VMs ───────────────────────────────────────────────────────────────


def vm_v4_ahv(vm: dict[str, Any]) -> dict[str, Any]:
    boot: dict[str, Any] = {"$objectType": "vmm.v4.ahv.config.LegacyBoot",
                            "bootOrder": ["CDROM", "DISK", "NETWORK"]}
    if vm["boot"] == "UEFI":
        boot = {"$objectType": "vmm.v4.ahv.config.UefiBoot",
                "isSecureBootEnabled": vm["secure_boot"]}
    disks = []
    for d in vm["disks"]:
        backing: dict[str, Any] = {
            "$objectType": "vmm.v4.ahv.config.VmDisk",
            "diskExtId": d["uuid"],
            "diskSizeBytes": d["size"],
            "storageContainer": {"extId": d["container_uuid"]},
        }
        if d["image"]:
            backing["dataSource"] = {"reference": {
                "$objectType": "vmm.v4.ahv.config.ImageReference", "imageExtId": d["image"]}}
        disks.append({"extId": d["uuid"], "diskAddress": {"busType": d["bus"],
                                                          "index": d["index"]},
                      "backingInfo": backing})
    nics = []
    for n in vm["nics"]:
        network: dict[str, Any] = {"nicType": "NORMAL_NIC", "vlanMode": "ACCESS",
                                   "subnet": {"extId": n["subnet_uuid"]}}
        if n["ip"]:
            network["ipv4Config"] = {"shouldAssignIp": True,
                                     "ipAddress": {"value": n["ip"], "prefixLength": 32}}
        nics.append({"extId": n["uuid"],
                     "backingInfo": {"macAddress": n["mac"], "isConnected": n["connected"],
                                     "model": "VIRTIO"},
                     "networkInfo": network})
    return _v4("vmm.v4.ahv.config.Vm", {
        "extId": vm["uuid"],
        "name": vm["name"],
        "powerState": vm["power"],
        "numSockets": vm["sockets"],
        "numCoresPerSocket": vm["cores"],
        "numThreadsPerCore": 1,
        "memorySizeBytes": vm["memory"],
        "cluster": {"extId": vm["cluster_uuid"]},
        "host": {"extId": vm["host_uuid"]} if vm["power"] == "ON" else None,
        "machineType": "PC",
        "isAgentVm": False,
        "protectionType": vm["protection_type"],
        "createTime": vm["created"],
        "bootConfig": boot,
        "disks": disks,
        "cdRoms": [{"extId": f"{vm['uuid'][:8]}-cd{i}",
                    "diskAddress": {"busType": "IDE", "index": i}}
                   for i in range(vm["cdroms"])],
        "nics": nics,
    })


def vm_v4_esxi(vm: dict[str, Any]) -> dict[str, Any]:
    nics = [{
        "extId": n["uuid"],
        "backingInfo": {"macAddress": n["mac"], "isConnected": n["connected"],
                        "adapterType": "VMXNET3"},
        # Empty while the VM is powered off, as on the lab PC (pc.7.5.1.1 shape).
        "networkInfo": {"portGroupInfo": {"name": n["port_group"] or "servers_vlan"},
                        "ipAddressInfo": {"ipv4Addresses": [{"value": n["ip"]}]}
                        if n["ip"] else {}},
    } for n in vm["nics"]]
    return _v4("vmm.v4.esxi.config.Vm", {
        "extId": vm["uuid"],
        "name": vm["name"],
        "powerState": vm["power"],
        # VMware semantics: numCpus is the total vCPU count, not sockets.
        "numCpus": vm["sockets"] * vm["cores"],
        "numCoresPerSocket": vm["cores"],
        "memorySizeBytes": vm["memory"],
        "guestOsName": vm["guest_os"],
        "cluster": {"extId": vm["cluster_uuid"]},
        "host": {"extId": vm["host_uuid"]},
        # Only disks on a Nutanix storage container: v4 leaves the others out.
        "disks": [{"extId": d["uuid"], "diskAddress": {"busType": d["bus"], "index": d["index"]},
                   "backingInfo": {"diskSizeBytes": d["size"],
                                   "storageContainer": {"extId": d["container_uuid"]}}}
                  for d in vm["disks"] if d["container_uuid"]],
        "nics": nics,
    })


def vm_v3(est: PcEstate | PeEstate, vm: dict[str, Any], intent_uuid: str | None = None
          ) -> dict[str, Any]:
    subnets = {s["uuid"]: s for s in est.subnets}
    disk_list = []
    for d in vm["disks"]:
        if not d["container_uuid"]:
            continue
        disk: dict[str, Any] = {
            "uuid": d["uuid"],
            "disk_size_bytes": d["size"],
            "disk_size_mib": d["size"] // MIB,
            "device_properties": {"device_type": "DISK",
                                  "disk_address": {"adapter_type": d["bus"],
                                                   "device_index": d["index"]}},
            "storage_config": {"storage_container_reference": ref(
                "storage_container", d["container_uuid"], d["container_name"])},
        }
        if d["image"]:
            image_name = next((i["name"] for i in getattr(est, "images", [])
                               if i["uuid"] == d["image"]), None)
            disk["data_source_reference"] = ref("image", d["image"], image_name)
        disk_list.append(disk)
    for i in range(vm["cdroms"]):
        disk_list.append({"uuid": f"{vm['uuid'][:8]}-cd{i}",
                          "device_properties": {"device_type": "CDROM",
                                                "disk_address": {"adapter_type": "IDE",
                                                                 "device_index": i}}})
    nic_list = []
    for n in vm["nics"]:
        subnet = subnets.get(n["subnet_uuid"] or "")
        nic: dict[str, Any] = {"uuid": n["uuid"], "mac_address": n["mac"],
                               "nic_type": "NORMAL_NIC", "is_connected": n["connected"],
                               "ip_endpoint_list": [{"ip": n["ip"], "type": "ASSIGNED"}]
                               if n["ip"] else []}
        if subnet:
            nic["subnet_reference"] = ref("subnet", subnet["uuid"], subnet["name"])
        nic_list.append(nic)
    boot_type = "SECURE_BOOT" if vm["secure_boot"] else vm["boot"]
    resources = {
        "power_state": vm["power"],
        "num_sockets": vm["sockets"],
        "num_vcpus_per_socket": vm["cores"],
        "num_threads_per_core": 1,
        "memory_size_mib": vm["memory"] // MIB,
        "hypervisor_type": "ESX" if vm["hypervisor"] == "ESXi" else "AHV",
        "is_agent_vm": False,
        "host_reference": ref("host", vm["host_uuid"], vm["host_name"]),
        "guest_tools": {"nutanix_guest_tools": {"guest_os_version": vm["guest_os_id"],
                                                "state": "ENABLED"}},
        "protection_type": vm["protection_type"],
        "boot_config": {"boot_type": boot_type},
        "gpu_list": [{"vendor": "NVIDIA", "mode": "VIRTUAL", "name": g} for g in vm["gpus"]],
        "disk_list": disk_list,
        "nic_list": nic_list,
    }
    cats = vm["categories"]
    return _v3(
        "vm", intent_uuid or vm["uuid"], vm["name"], resources,
        extra_status={"cluster_reference": ref("cluster", vm["cluster_uuid"],
                                               vm["cluster_name"])},
        metadata={"categories_mapping": cats,
                  "categories": {k: v[0] for k, v in cats.items() if v},
                  "creation_time": vm["created"]},
        description=vm["description"],
    )


# ─── catalog ───────────────────────────────────────────────────────────


def image_v4(img: dict[str, Any]) -> dict[str, Any]:
    return _v4("vmm.v4.content.Image", {
        "extId": img["uuid"], "name": img["name"], "type": img["type"],
        "sizeBytes": img["size"], "clusterLocationExtIds": img["clusters"],
        "description": "", "createTime": "2025-03-01T10:00:00Z",
    })


def image_v3(est: PcEstate, img: dict[str, Any]) -> dict[str, Any]:
    names = {c["uuid"]: c["name"] for c in est.clusters}
    return _v3("image", img["uuid"], img["name"], {
        "image_type": img["type"], "size_bytes": img["size"], "architecture": img["arch"],
        "source_uri": img["source"],
        "current_cluster_reference_list": [ref("cluster", u, names.get(u))
                                           for u in img["clusters"]],
    })


def template_v4(t: dict[str, Any], detail: bool = False) -> dict[str, Any]:
    vm_spec: dict[str, Any] = {"numSockets": t["sockets"], "numCoresPerSocket": 1,
                               "memorySizeBytes": t["memory"]}
    if detail:
        vm_spec["guestTools"] = {"guestOsVersion": t["guest_os"], "isEnabled": True}
    return _v4("vmm.v4.content.Template", {
        "extId": t["uuid"],
        "templateName": t["name"],
        "templateDescription": t["description"],
        "templateVersionSpec": {"versionName": f"v{t['versions']}", "isActiveVersion": True,
                                "vmSpec": vm_spec},
        "createdBy": {"$reserved": _R, "username": t["created_by"]},
        "createTime": "2025-06-01T08:00:00Z",
    })


def ova_v4(est: PcEstate, o: dict[str, Any], detail: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {"extId": o["uuid"], "name": o["name"], "sizeBytes": o["size"],
                            "createTime": o["created"]}
    if detail:
        body["vmConfig"] = {"disks": [{"diskAddress": {"busType": "SCSI", "index": i}}
                                      for i in range(o["disks"])], "cdRoms": []}
        body["clusterLocationExtIds"] = [o["cluster_uuid"]]
    return _v4("vmm.v4.content.Ova", body)


def ova_v3(est: PcEstate, o: dict[str, Any]) -> dict[str, Any]:
    names = {c["uuid"]: c["name"] for c in est.clusters}
    return {
        "metadata": {"kind": "ova", "uuid": o["uuid"], "creation_time": o["created"]},
        "info": {
            "name": o["name"], "size_bytes": o["size"], "create_time": o["created"],
            "current_cluster_reference_list": [ref("cluster", o["cluster_uuid"],
                                                   names.get(o["cluster_uuid"]))],
            "vm_spec": {"spec": {"resources": {"disk_list": [{} for _ in range(o["disks"])]}}},
        },
    }


# ─── data protection ───────────────────────────────────────────────────


def protection_policy_v4(est: PcEstate, p: dict[str, Any]) -> dict[str, Any]:
    cat_ids = [c["uuid"] for c in est.categories
               if c["value"] in p["categories"].get(c["key"], [])]
    return _v4("datapolicies.v4.config.ProtectionPolicy", {
        "extId": p["uuid"],
        "name": p["name"],
        "replicationLocations": [
            {"label": "a-source", "domainManagerExtId": p["source"]["url"], "isPrimary": True},
            {"label": "b-target", "domainManagerExtId": p["target"]["url"]},
        ],
        "replicationConfigurations": [{
            "sourceLocationLabel": "a-source",
            "remoteLocationLabel": "b-target",
            "schedule": {
                "recoveryPointType": "CRASH_CONSISTENT",
                "recoveryPointObjectiveTimeSeconds": p["rpo"],
                "retention": {"$objectType": "datapolicies.v4.config.LinearRetention",
                              "local": p["local_retention"], "remote": p["remote_retention"]},
            },
        }],
        "categoryIds": cat_ids,
    })


def protection_rule_v3(p: dict[str, Any]) -> dict[str, Any]:
    resources = {
        "category_filter": {"type": "CATEGORIES_MATCH_ANY", "kind_list": ["vm"],
                            "params": p["categories"]},
        "ordered_availability_zone_list": [
            {"availability_zone_url": p["source"]["url"]},
            {"availability_zone_url": p["target"]["url"]},
        ],
        "availability_zone_connectivity_list": [{
            "source_availability_zone_index": 0,
            "destination_availability_zone_index": 1,
            "snapshot_schedule_list": [{
                "recovery_point_objective_secs": p["rpo"],
                "snapshot_type": "CRASH_CONSISTENT",
                "local_snapshot_retention_policy": {"num_snapshots": p["local_retention"]},
                "remote_snapshot_retention_policy": {"num_snapshots": p["remote_retention"]},
            }],
        }],
    }
    return _v3("protection_rule", p["uuid"], p["name"], resources)


def recovery_plan_v3(rp: dict[str, Any]) -> dict[str, Any]:
    stages = []
    per_stage = max(1, rp["entities"] // rp["stages"])
    for s in range(rp["stages"]):
        count = per_stage if s < rp["stages"] - 1 else rp["entities"] - per_stage * s
        stages.append({"stage_work": {"recover_entities": {"entity_info_list": [
            {"any_entity_reference": {"kind": "vm", "uuid": f"e-{s}-{i}"}}
            for i in range(max(0, count))]}}, "delay_time_secs": 0})
    azs = [{"availability_zone_url": rp["primary"]}, {"availability_zone_url": rp["recovery"]}]
    resources = {
        "stage_list": stages,
        "parameters": {
            "availability_zone_list": azs,
            "network_mapping_list": [{"availability_zone_network_mapping_list": [
                {"availability_zone_url": rp["primary"],
                 "recovery_network": {"name": f"prod-{i}"}},
                {"availability_zone_url": rp["recovery"],
                 "recovery_network": {"name": f"dr-{i}"}},
            ]} for i in range(rp["network_maps"])],
        },
    }
    return _v3("recovery_plan", rp["uuid"], rp["name"], resources)


def availability_zone_v3(az: dict[str, Any]) -> dict[str, Any]:
    entity = _v3("availability_zone", az["uuid"], az["name"], {
        "availability_zone_type": az["type"], "management_url": az["url"],
        "region": az["region"], "display_name": az["name"],
    })
    entity["status"]["resources"] = {**entity["status"]["resources"],
                                     "connection_status": az["status"]}
    return entity


# ─── alerts ────────────────────────────────────────────────────────────


def alert_v4(a: dict[str, Any]) -> dict[str, Any]:
    created = a["created"].replace("Z", ".118249Z")
    return _v4("monitoring.v4.serviceability.Alert", {
        "extId": a["uuid"], "title": a["title"], "message": a["message"],
        "severity": a["severity"], "isResolved": a["resolved"],
        "isAcknowledged": a["acknowledged"], "isAutoResolved": a["auto_resolved"],
        "affectedEntities": [{"extId": None, "name": a["entity"], "type": a["type"]}],
        "sourceEntity": {"name": a["entity"], "type": a["type"]},
        "originatingClusterUUID": a["cluster_uuid"], "clusterUUID": a["cluster_uuid"],
        "creationTime": created, "classifications": a["classifications"],
    })


def alert_v3(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "metadata": {"kind": "alert", "uuid": a["uuid"]},
        "status": {"state": "COMPLETE", "resources": {
            "title": a["title"],
            "default_message": a["message"],
            "severity": a["severity"].lower(),
            "resolution_status": {"is_true": a["resolved"],
                                  "is_auto_resolved": a["auto_resolved"]},
            "acknowledged_status": {"is_true": a["acknowledged"]},
            "affected_entity_list": [{"entity_type": a["type"], "entity_name": a["entity"]}],
            "originating_cluster_uuid": a["cluster_uuid"],
            "creation_time": a["created"],
            "classification_list": a["classifications"],
        }},
    }


# ─── Flow ──────────────────────────────────────────────────────────────


def category_v4(c: dict[str, Any]) -> dict[str, Any]:
    return _v4("prism.v4.config.Category", {"extId": c["uuid"], "key": c["key"],
                                             "value": c["value"], "type": "USER",
                                             "description": c["description"]})


def category_keys_v3(est: PcEstate) -> list[dict[str, Any]]:
    keys = sorted({c["key"] for c in est.categories} | set(est.valueless_category_keys))
    return [{"name": k, "description": f"{k} category", "system_defined": False} for k in keys]


def security_policy_v4(p: dict[str, Any], detail: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {"extId": p["uuid"], "name": p["name"], "type": p["type"],
                            "state": p["state"], "securedGroups": p["group_ids"],
                            "description": f"{p['type'].title()} policy"}
    if detail:
        rules: list[dict[str, Any]] = []
        secured = p["group_ids"][:1]
        for i in range(p["inbound"]):
            rules.append({"type": "APPLICATION", "spec": {
                "$objectType": "microseg.v4.config.ApplicationRuleSpec",
                "securedGroupCategoryReferences": secured,
                "srcAllowSpec" if i == 0 else "srcCategoryReferences":
                    "ALL" if i == 0 else secured}})
        for _ in range(p["outbound"]):
            rules.append({"type": "APPLICATION", "spec": {
                "$objectType": "microseg.v4.config.ApplicationRuleSpec",
                "securedGroupCategoryReferences": secured,
                "destCategoryReferences": secured}})
        if p["type"] == "ISOLATION":
            rules.append({"type": "TWO_ENV_ISOLATION", "spec": {
                "$objectType": "microseg.v4.config.TwoEnvIsolationRuleSpec",
                "firstIsolationGroup": p["group_ids"][:1],
                "secondIsolationGroup": p["group_ids"][1:2]}})
        rules.append({"type": "INTRA_GROUP", "spec": {
            "$objectType": "microseg.v4.config.IntraEntityGroupRuleSpec",
            "securedGroupAction": "ALLOW"}})
        body["rules"] = rules
    return _v4("microseg.v4.config.NetworkSecurityPolicy", body)


def security_policy_v3(p: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, list[str]] = {}
    for key, value in p["groups"]:
        params.setdefault(key, []).append(value)
    action = {"ENFORCE": "APPLY", "MONITOR": "MONITOR", "SAVE": "MONITOR"}[p["state"]]
    resources: dict[str, Any] = {}
    peer = {"peer_specification_type": "FILTER",
            "filter": {"type": "CATEGORIES_MATCH_ALL", "kind_list": ["vm"], "params": params}}
    if p["type"] == "ISOLATION":
        k1, v1 = p["groups"][0]
        k2, v2 = p["groups"][1] if len(p["groups"]) > 1 else p["groups"][0]
        resources["isolation_rule"] = {
            "action": action,
            "first_entity_filter": {"type": "CATEGORIES_MATCH_ALL", "kind_list": ["vm"],
                                    "params": {k1: [v1]}},
            "second_entity_filter": {"type": "CATEGORIES_MATCH_ALL", "kind_list": ["vm"],
                                     "params": {k2: [v2]}},
        }
    elif p["type"] == "QUARANTINE":
        resources["quarantine_rule"] = {"action": action, "target_group": peer}
    else:
        resources["app_rule"] = {
            "action": action,
            "target_group": peer,
            "inbound_allow_list": [{"peer_specification_type": "ALL"}] * p["inbound"],
            "outbound_allow_list": [{"peer_specification_type": "ALL"}] * p["outbound"],
        }
    return _v3("network_security_rule", p["uuid"], p["name"], resources)


def service_group_v4(sg: dict[str, Any]) -> dict[str, Any]:
    return _v4("microseg.v4.config.ServiceGroup", {
        "extId": sg["uuid"], "name": sg["name"], "isSystemDefined": sg["system"],
        "tcpServices": [{"startPort": a, "endPort": b} for a, b in sg["tcp"]],
        "udpServices": [{"startPort": a, "endPort": b} for a, b in sg["udp"]],
        "icmpServices": [{"isAllAllowed": True}] if sg["icmp"] else [],
    })


def service_group_v3(sg: dict[str, Any]) -> dict[str, Any]:
    services = []
    if sg["tcp"]:
        services.append({"protocol": "TCP", "tcp_port_range_list": [
            {"start_port": a, "end_port": b} for a, b in sg["tcp"]]})
    if sg["udp"]:
        services.append({"protocol": "UDP", "udp_port_range_list": [
            {"start_port": a, "end_port": b} for a, b in sg["udp"]]})
    if sg["icmp"]:
        services.append({"protocol": "ICMP", "icmp_type_code_list": []})
    return {"uuid": sg["uuid"], "service_group": {"name": sg["name"],
                                                  "is_system_defined": sg["system"],
                                                  "service_list": services}}


def address_group_v4(ag: dict[str, Any]) -> dict[str, Any]:
    return _v4("microseg.v4.config.AddressGroup", {
        "extId": ag["uuid"], "name": ag["name"],
        "ipv4Addresses": [{"value": ip, "prefixLength": n} for ip, n in ag["v4"]],
        "ipRanges": [{"startIp": {"value": a}, "endIp": {"value": b}} for a, b in ag["ranges"]],
    })


def address_group_v3(ag: dict[str, Any]) -> dict[str, Any]:
    return {"uuid": ag["uuid"], "address_group": {
        "name": ag["name"], "is_system_defined": ag["system"],
        "ip_address_block_list": [{"ip": ip, "prefix_length": n} for ip, n in ag["v4"]]}}


# ─── NUS ───────────────────────────────────────────────────────────────


def file_server_v3(fs: dict[str, Any]) -> dict[str, Any]:
    entity = _v3("file_server", fs["uuid"], fs["name"], {
        "nvms_count": fs["fsvms"], "size_gib": fs["size_gib"], "version": fs["version"],
        "internal_networks": [{"subnet_reference": ref("subnet", None, fs["internal"])}],
        "external_networks": [{"subnet_reference": ref("subnet", None, fs["external"])}],
    }, extra_status={"cluster_reference": ref("cluster", None, fs["cluster_name"])})
    entity["status"]["resources"] = {"version": fs["version"], "usage_bytes": fs["used"]}
    return entity


def file_share_v3(sh: dict[str, Any]) -> dict[str, Any]:
    return _v3("mount_target", sh["uuid"], sh["name"], {
        "file_server_reference": ref("file_server", None, sh["file_server"]),
        "protocol_type": sh["protocol"], "max_size_gib": sh["max_gib"],
    })


def file_server_v4(fs: dict[str, Any]) -> dict[str, Any]:
    return _v4("files.v4.config.FileServer", {"extId": fs["uuid"], "name": fs["name"],
                                               "version": fs["version"]})


def object_store_v3(o: dict[str, Any]) -> dict[str, Any]:
    entity = _v3("objectstore", o["uuid"], o["name"], {
        "cluster_reference": ref("cluster", None, o["cluster_name"]),
        "num_worker_nodes": o["workers"], "total_capacity_gib": o["capacity_gib"],
    })
    entity["status"]["resources"] = {"deployment_version": o["version"],
                                     "usage_bytes": o["used"]}
    entity["status"]["state"] = o["state"]
    return entity


def volume_group_v4(vg: dict[str, Any]) -> dict[str, Any]:
    return _v4("volumes.v4.config.VolumeGroup", {
        "extId": vg["uuid"], "name": vg["name"], "clusterReference": vg["cluster_uuid"],
        "sharingStatus": "SHARED" if vg["shared"] else "NOT_SHARED",
        "targetName": vg["target"],
        "storageFeatures": {"flashMode": {"isEnabled": vg["flash"]}},
    })


def volume_group_v3(vg: dict[str, Any]) -> dict[str, Any]:
    return _v3("volume_group", vg["uuid"], vg["name"], {
        "disk_list": [{"index": i, "disk_size_mib": mib} for i, mib in enumerate(vg["disks_mib"])],
        "attachment_list": [{"vm_reference": ref("vm", f"att-{i}")}
                            for i in range(vg["attachments"])],
        "sharing_status": "SHARED" if vg["shared"] else "NOT_SHARED",
        "iscsi_target_prefix": vg["target"],
        "flash_mode": "ENABLED" if vg["flash"] else "DISABLED",
    }, extra_status={"cluster_reference": ref("cluster", vg["cluster_uuid"],
                                              vg["cluster_name"])})


# ─── IAM ───────────────────────────────────────────────────────────────


def user_v4(est: PcEstate, u: dict[str, Any]) -> dict[str, Any]:
    idp = next((d["uuid"] for d in est.directories if d["name"] == u["directory"]),
               "local-idp")
    return _v4("iam.v4.authn.User", {
        "extId": u["uuid"], "username": u["username"],
        "displayName": u["display"] if u["type"] != "SERVICE_ACCOUNT" else "",
        "firstName": u["display"], "lastName": "", "userType": u["type"],
        "idpId": idp, "status": u["state"],
    })


def user_v3(u: dict[str, Any]) -> dict[str, Any]:
    resources: dict[str, Any] = {}
    if u["type"] == "LDAP":
        resources["directory_service_user"] = {
            "user_principal_name": u["username"],
            "directory_service_reference": ref("directory_service", None, u["directory"])}
    elif u["type"] == "SAML":
        resources["identity_provider_user"] = {
            "username": u["username"],
            "identity_provider_reference": ref("identity_provider", None, u["directory"])}
    entity = _v3("user", u["uuid"], None, {**resources, "display_name": u["display"]},
                 spec_resources=resources)
    return entity


def directory_v4(d: dict[str, Any]) -> dict[str, Any]:
    kind = "iam.v4.authn.DirectoryService" if d["kind"] == "ldap" \
        else "iam.v4.authn.SamlIdentityProvider"
    return _v4(kind, {"extId": d["uuid"], "name": d["name"]})


def role_v4(r: dict[str, Any]) -> dict[str, Any]:
    return _v4("iam.v4.authz.Role", {
        "extId": r["uuid"], "displayName": r["name"], "description": r["description"],
        "isSystemDefined": r["system"],
        "operations": [f"op-{i}" for i in range(r["perms"])],
    })


def role_v3(r: dict[str, Any]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if not r["system"]:
        meta["owner_reference"] = ref("user", "owner", "admin")
    entity = _v3("role", r["uuid"], r["name"], {
        "permission_reference_list": [ref("permission", f"p-{i}") for i in range(r["perms"])],
    }, metadata=meta, description=r["description"])
    return entity


def project_v3(p: dict[str, Any]) -> dict[str, Any]:
    return _v3("project", p["uuid"], p["name"], {
        "user_reference_list": [ref("user", f"u-{i}") for i in range(p["users"])],
    }, description=p["description"])


# ─── automation / reports / LCM / licensing ────────────────────────────


def playbook_v3(pb: dict[str, Any]) -> dict[str, Any]:
    # `action_rules/list` returns spec: null and everything under status (see xplay.py).
    return {
        "metadata": {"kind": "action_rule", "uuid": pb["uuid"]},
        "spec": None,
        "status": {"name": pb["name"], "state": "COMPLETE", "resources": {
            "name": pb["name"], "description": pb["description"],
            "is_enabled": pb["enabled"],
            "trigger_list": [{"action_trigger_type_reference": {"name": pb["trigger"]}}],
            "action_list": [{"action_type_reference": {"name": "email_action"}}
                            for _ in range(pb["actions"])],
        }},
    }


def report_config_v4(r: dict[str, Any]) -> dict[str, Any]:
    return _v4("opsmgmt.v4.config.ReportConfig", {
        "extId": r["uuid"], "name": r["name"], "description": "",
        "supportedFormats": ["PDF", "CSV"], "isSystemDefined": False,
        "creationTime": "2025-01-10T00:00:00Z",
    })


def report_instance_v4(r: dict[str, Any]) -> dict[str, Any]:
    return _v4("opsmgmt.v4.config.Report", {"extId": f"inst-{r['uuid']}",
                                             "configExtId": r["uuid"],
                                             "creationTime": r["last_run"]})


def report_config_v3(r: dict[str, Any]) -> dict[str, Any]:
    return _v3("report_config", r["uuid"], r["name"], {"report_type": "REPORT"})


def report_instance_v3(r: dict[str, Any]) -> dict[str, Any]:
    return {"metadata": {"kind": "report_instance", "uuid": f"inst-{r['uuid']}"},
            "status": {"resources": {"report_config_reference": ref("report_config",
                                                                    r["uuid"]),
                                     "generation_end_time": r["last_run"]}}}


def lcm_entity_v4(e: dict[str, Any]) -> dict[str, Any]:
    return _v4("lifecycle.v4.resources.Entity", {
        "extId": e["uuid"], "entityModel": e["model"], "entityClass": e["class"],
        "entityType": e["type"], "entityVersion": e["version"],
        # Equals entityVersion while no upgrade is staged — not an upgrade target.
        "targetVersion": e["version"],
        "availableVersions": [{"version": v, "isEnabled": True, "order": o,
                               "status": "RECOMMENDED" if o == max(x[1] for x in e["available"])
                               else "AVAILABLE"} for v, o in e["available"]],
        "clusterExtId": e["cluster_uuid"], "lastUpdatedTime": e["updated"],
        "hardwareFamily": "",
    })


def lcm_status_v4(stale: bool = False) -> dict[str, Any]:
    return _v4("lifecycle.v4.resources.StatusInfo", {
        "frameworkVersion": {"currentVersion": "3.1.2" if stale else "3.2",
                             "availableVersion": "3.2", "isUpdateNeeded": stale},
        "inProgressOperation": {},
    })


def license_v4(lic: dict[str, Any]) -> dict[str, Any]:
    return _v4("licensing.v4.config.License", {
        "extId": lic["uuid"], "name": f"{lic['category']} {lic['type']}",
        "category": lic["category"], "type": lic["type"], "quantity": lic["quantity"],
        "expiryDate": lic["expiry"], "meter": "CORES",
    })


# ─── Prism Element v2.0 ────────────────────────────────────────────────


def pe_cluster_v2(est: PeEstate) -> dict[str, Any]:
    c = est.cluster
    return {
        "id": f"{c['uuid']}::1", "uuid": c["uuid"], "cluster_uuid": c["uuid"],
        "name": c["name"], "version": est.version, "full_version": _nos_build(est.version),
        "ncc_version": c["ncc"], "hypervisor_types": ["kKvm"],
        "cluster_redundancy_state": {"current_redundancy_factor": c["rf"],
                                     "desired_redundancy_factor": c["rf"]},
        "num_nodes": c["nodes"], "cluster_external_ipaddress": c["external_ip"],
        "cluster_external_data_services_ipaddress": c.get("data_services_ip"),
        "timezone": c["timezone"], "encrypted": c["encryption"] == "ENABLED",
        "operation_mode": "Normal",
    }


def pe_host_v2(h: dict[str, Any], num_vms: int) -> dict[str, Any]:
    return {
        "uuid": h["uuid"], "name": h["name"], "cluster_uuid": h["cluster_uuid"],
        "hypervisor_type": "kKvm", "hypervisor_full_name": h["hv_version"],
        "cpu_model": h["cpu_model"], "num_cpu_sockets": h["sockets"],
        "num_cpu_cores": h["cores"], "num_cpu_threads": h["threads"],
        "cpu_capacity_in_hz": h["freq_hz"], "memory_capacity_in_bytes": h["memory"],
        "block_serial": h["block_serial"], "block_model_name": h["block_model"],
        "serial": h["serial"], "ipmi_address": h["ipmi_ip"],
        "service_vmexternal_ip": h["cvm_ip"], "hypervisor_address": h["hv_ip"],
        "boot_time_in_usecs": 1755000000000000, "num_vms": num_vms,
        "usage_stats": {"storage.capacity_bytes": str(h["storage_raw"])},
    }


def pe_cvm_v1(cvm: dict[str, Any], host_uuid: str | None) -> dict[str, Any]:
    """A Controller VM as Prism Element's v1 VM list reports it (v2.0 omits CVMs)."""
    return {
        "vmId": f"{cvm['uuid']}::1", "uuid": cvm["uuid"], "vmName": cvm["name"],
        "controllerVm": True, "powerState": "on", "numVCpus": cvm["vcpus"],
        "memoryCapacityInBytes": cvm["memory"], "hostName": cvm["host_name"],
        "hostUuid": host_uuid, "ipAddresses": [cvm["ip"]],
    }


def v1_list(entities: list[dict[str, Any]], total: int, page: int, count: int
            ) -> dict[str, Any]:
    return {"metadata": {"grandTotalEntities": total, "totalEntities": total,
                         "page": page, "count": count,
                         "startIndex": (page - 1) * count + 1,
                         "endIndex": (page - 1) * count + len(entities)},
            "entities": entities}


def pe_container_v2(ct: dict[str, Any]) -> dict[str, Any]:
    return {
        "storage_container_uuid": ct["uuid"], "name": ct["name"],
        "cluster_uuid": ct["cluster_uuid"], "replication_factor": ct["rf"],
        "compression_enabled": ct["compression"],
        "compression_delay_in_secs": ct["compression_delay"],
        "on_disk_dedup": "OFF" if ct["dedup"] == "NONE" else ct["dedup"],
        "finger_print_on_write": "OFF", "erasure_code": ct["ec"],
        "max_capacity": ct["max"], "advertised_capacity": ct["max"],
        "usage_stats": {"storage.user_usage_bytes": str(ct["used"]),
                        "storage.user_free_bytes": str(ct["free"])},
    }


def pe_network_v2(s: dict[str, Any]) -> dict[str, Any]:
    ip_config: dict[str, Any] = {"network_address": s["prefix"],
                                 "prefix_length": s["prefix_len"],
                                 "default_gateway": s["gateway"]}
    if s["dhcp"]:
        ip_config["dhcp_options"] = {"domain_name_servers": "10.20.1.5"}
    return {"uuid": s["uuid"], "name": s["name"], "vlan_id": s["vlan"],
            "ip_config": ip_config}


def pe_vm_v2(vm: dict[str, Any], include_disks: bool, include_nics: bool) -> dict[str, Any]:
    """A VM as Prism Element v2.0 reports it (directly, or through the PC proxy).

    ESXi VMs carry the VMware guest id, every disk (one on a local datastore has no
    container uuid) and NICs naming their port group's network uuid, as on the lab PE.
    """
    esxi = vm["hypervisor"] == "ESXi"
    boot: dict[str, Any] = {"hardware_virtualization": False}
    if vm["boot"] == "UEFI":
        boot["uefi_boot"] = True
        if vm["secure_boot"]:
            boot["secure_boot"] = True
    out: dict[str, Any] = {
        "uuid": vm["uuid"], "name": vm["name"], "power_state": vm["power"].lower(),
        "num_vcpus": vm["sockets"], "num_cores_per_vcpu": vm["cores"],
        "memory_mb": vm["memory"] // MIB, "description": vm["description"],
        "host_uuid": vm["host_uuid"], "vm_gpus": [{"device_name": g} for g in vm["gpus"]],
        "boot": boot,
    }
    if esxi:
        out["guest_os"] = vm["guest_os_id"]
        out["tools_running_status"] = ("GuestToolsRunning" if vm["power"] == "ON"
                                       else "GuestToolsNotRunning")
    if include_disks:
        disks: list[dict[str, Any]] = []
        for d in vm["disks"]:
            disk: dict[str, Any] = {
                "disk_address": {"device_uuid": d["uuid"], "vmdisk_uuid": uid(d["uuid"], "vdisk"),
                             "device_bus": d["bus"].lower(),
                                 "device_index": d["index"]},
                "size": d["size"], "is_cdrom": False,
            }
            if d["container_uuid"]:
                disk["storage_container_uuid"] = d["container_uuid"]
            disks.append(disk)
        out["vm_disk_info"] = disks
    if include_nics:
        nics = []
        for n in vm["nics"]:
            nic: dict[str, Any] = {
                "mac_address": n["mac"], "nic_uuid": n["uuid"],
                "network_uuid": n.get("port_group_uuid") or n["subnet_uuid"],
                "is_connected": n["connected"],
                "adapter_type": "Vmxnet3" if esxi else None,
            }
            if n["ip"]:
                nic["ip_address"] = n["ip"]
                nic["ip_addresses"] = [n["ip"]]
            nics.append(nic)
        out["vm_nics"] = nics
    return out


def pe_port_group_v2(pg: dict[str, Any]) -> dict[str, Any]:
    """An ESXi port group in Prism Element's v2.0 ``networks`` (VLAN 0 left out)."""
    out: dict[str, Any] = {"uuid": pg["uuid"], "name": pg["name"],
                           "network_type": "external",
                           "ip_config": {"prefix_length": 0, "ipam_enabled": False}}
    if pg["vlan"]:
        out["vlan_id"] = pg["vlan"]
    return out
