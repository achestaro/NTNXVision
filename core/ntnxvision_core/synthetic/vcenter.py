"""A mock VMware vCenter (vSphere Automation REST ``/api``) over a synthetic estate.

It serves the ESXi clusters of a Prism Central estate the way vCenter sees them, with
the payload shapes recorded from a lab vCenter 7.0.3 (values synthetic): session login
(``POST /api/session``, basic auth, the session id as a bare JSON string) and logout,
``vcenter/cluster``, ``vcenter/host``, ``vcenter/network``, ``vcenter/datastore``,
``vcenter/vm`` (filter ``hosts``), ``vcenter/vm/{id}`` and, for powered-on VMs, its
``guest/identity`` and ``guest/networking/interfaces`` (503 while VMware Tools is not
running, as on a real vCenter).

What it knows that the Nutanix side does not: the datastore of every disk (the local
VMFS scratch disks Prism Element reports with a size only) and each port group's switch
type. Read-only like every mock here; it holds no state but the session token.
"""

from __future__ import annotations

import base64
from typing import Any

from ntnxvision_core.synthetic.estate import MIB, PcEstate

Response = tuple[int, Any, dict[str, str]]

VERSION = "8.0.2.00100"
SESSION_HEADER = "vmware-api-session-id"


def session_token(host: str) -> str:
    return f"demo-session-{host}"


def handle_vcenter(est: PcEstate, host: str, method: str, path: str, query: dict[str, str],
                   authorization: str | None, headers: dict[str, str]) -> Response:
    if path == "/api/session":
        if method == "POST":
            if not _basic_ok(authorization):
                return 401, _error("UNAUTHENTICATED", "Authentication required."), {}
            return 201, session_token(host), {}
        if method == "DELETE":
            return 200, None, {}
    if not path.startswith("/api/"):
        # Only the /api generation is mocked; an old client's /rest probe sees a 404.
        return 404, _error("NOT_FOUND", f"Not found: {path}"), {}
    if headers.get(SESSION_HEADER) != session_token(host):
        return 401, _error("UNAUTHENTICATED", "This method requires authentication."), {}
    if method != "GET":
        return 405, _error("METHOD_NOT_ALLOWED", "Read-only mock."), {}
    view = _View(est)
    what = path[len("/api/"):].strip("/")
    if what == "appliance/system/version":
        return 200, {"version": VERSION, "product": "VMware vCenter Server",
                     "type": "vCenter Server with an embedded Platform Services Controller"}, {}
    if what == "vcenter/cluster":
        return 200, [{"cluster": c["vc_id"], "name": c["vc_name"], "drs_enabled": True,
                      "ha_enabled": True} for c in view.clusters], {}
    if what == "vcenter/host":
        return 200, [{"host": h["vc_id"], "name": h["name"], "connection_state": "CONNECTED",
                      "power_state": "POWERED_ON"} for h in view.hosts], {}
    if what == "vcenter/network":
        return 200, [{"network": p["vc_id"], "name": p["name"],
                      "type": f"{p['switch']}_PORTGROUP"} for p in view.port_groups], {}
    if what == "vcenter/datastore":
        return 200, view.datastores(), {}
    if what == "vcenter/vm":
        wanted = set(filter(None, (query.get("hosts") or "").split(",")))
        rows = [v for v in view.vms if not wanted or v["vc_host"] in wanted]
        return 200, [{"vm": v["vc_id"], "name": v["name"], "power_state": _power(v),
                      "cpu_count": v["sockets"] * v["cores"],
                      "memory_size_MiB": v["memory"] // MIB} for v in rows], {}
    parts = what.split("/")
    if len(parts) >= 3 and parts[:2] == ["vcenter", "vm"]:
        vm = view.vm_by_id.get(parts[2])
        if vm is None:
            return 404, _error("NOT_FOUND", f"VM {parts[2]} not found"), {}
        rest = "/".join(parts[3:])
        if not rest:
            return 200, view.vm_detail(vm), {}
        if rest in ("guest/identity", "guest/networking/interfaces"):
            if vm["power"] != "ON":
                return 503, _error("SERVICE_UNAVAILABLE",
                                   "VMware Tools is not running in the guest."), {}
            return 200, (view.identity(vm) if rest == "guest/identity"
                         else view.interfaces(vm)), {}
    return 404, _error("NOT_FOUND", f"Not found: {path}"), {}


class _View:
    """The estate's ESXi part with deterministic vCenter ids."""

    def __init__(self, est: PcEstate) -> None:
        self.est = est
        esxi = {c["uuid"] for c in est.clusters if c["hypervisor"] == "ESXi"}
        self.clusters = [{**c, "vc_id": f"domain-c{100 + c['index']}",
                          "vc_name": f"{c['name'].lower()}-vsphere"}
                         for c in est.clusters if c["uuid"] in esxi]
        self.hosts = [{**h, "vc_id": f"host-{1000 + i}"}
                      for i, h in enumerate(h for h in est.hosts if h["cluster_uuid"] in esxi)]
        host_id = {h["uuid"]: h["vc_id"] for h in self.hosts}
        host_by_name = {h["name"]: h for h in self.hosts}
        self.port_groups = [p for p in est.port_groups if p["cluster_uuid"] in esxi]
        self.pg_by_uuid = {p["uuid"]: p for p in self.port_groups}
        vms = [v for v in est.vms if v["cluster_uuid"] in esxi]
        self.vms = [{**v, "vc_id": f"vm-{2000 + i}", "vc_host": host_id[v["host_uuid"]]}
                    for i, v in enumerate(vms)]
        # The Controller VMs run on the hosts too (vCenter lists them like any VM).
        for i, c in enumerate(c for c in est.cvms if c["host_name"] in host_by_name):
            h = host_by_name[c["host_name"]]
            self.vms.append({
                "uuid": c["uuid"], "name": c["name"], "power": "ON", "sockets": c["vcpus"],
                "cores": 1, "memory": c["memory"], "guest_os": "CentOS 7 (64-bit)",
                "boot": "LEGACY", "disks": [], "nics": [], "vc_id": f"vm-{900 + i}",
                "vc_host": h["vc_id"], "cvm_ip": c["ip"]})
        self.vm_by_id = {v["vc_id"]: v for v in self.vms}

    def datastores(self) -> list[dict[str, Any]]:
        out = [{"datastore": f"datastore-{100 + i}", "name": ct["name"], "type": "NFS",
                "capacity": ct["max"], "free_space": ct["free"]}
               for i, ct in enumerate(ct for ct in self.est.containers
                                      if ct["cluster_uuid"] in {c["uuid"] for c in self.clusters})]
        for i, h in enumerate(self.hosts):
            out.append({"datastore": f"datastore-{500 + i}", "name": f"{h['name']}-local-ds",
                        "type": "VMFS", "capacity": 400 * 1024**3,
                        "free_space": 300 * 1024**3})
        return out

    def vm_detail(self, vm: dict[str, Any]) -> dict[str, Any]:
        nics: dict[str, Any] = {}
        for k, n in enumerate(vm["nics"]):
            pg = self.pg_by_uuid.get(n.get("port_group_uuid") or "")
            backing: dict[str, Any] = {"type": "STANDARD_PORTGROUP", "network_name": "VM Network"}
            if pg is not None:
                backing = {"type": f"{pg['switch']}_PORTGROUP", "network": pg["vc_id"]}
                if pg["switch"] == "STANDARD":
                    backing["network_name"] = pg["name"]
            nics[str(4000 + k)] = {
                "mac_address": n["mac"], "backing": backing, "type": "VMXNET3",
                "state": "CONNECTED" if n["connected"] else "NOT_CONNECTED",
                "label": f"Network adapter {k + 1}", "start_connected": True}
        disks: dict[str, Any] = {}
        for d in vm["disks"]:
            store = d.get("datastore") or d.get("container_name") or "unknown"
            disks[str(2000 + d["index"])] = {
                "type": "SCSI", "scsi": {"bus": 0, "unit": d["index"]},
                "label": f"Hard disk {d['index'] + 1}", "capacity": d["size"],
                "backing": {"type": "VMDK_FILE",
                            "vmdk_file": f"[{store}] {vm['name']}/{vm['name']}_{d['index']}.vmdk"}}
        return {
            "name": vm["name"], "power_state": _power(vm),
            "guest_OS": "OTHER_LINUX_64" if "Windows" not in vm["guest_os"] else "WINDOWS_9_64",
            "cpu": {"count": vm["sockets"] * vm["cores"], "cores_per_socket": vm["cores"],
                    "hot_add_enabled": False, "hot_remove_enabled": False},
            "memory": {"size_MiB": vm["memory"] // MIB, "hot_add_enabled": False},
            "boot": {"type": "EFI" if vm["boot"] == "UEFI" else "BIOS", "delay": 0},
            "hardware": {"version": "VMX_19", "upgrade_policy": "NEVER"},
            "identity": {"name": vm["name"], "instance_uuid": vm["uuid"]},
            "nics": nics, "disks": disks, "cdroms": {},
        }

    def identity(self, vm: dict[str, Any]) -> dict[str, Any]:
        ip = next((n["ip"] for n in vm["nics"] if n["ip"]), vm.get("cvm_ip"))
        return {"full_name": {"default_message": vm["guest_os"], "args": [],
                              "id": "vmsg.guestos.label"},
                "name": "OTHER_LINUX_64", "family": "WINDOWS" if "Windows" in vm["guest_os"]
                else "LINUX", "host_name": vm["name"], **({"ip_address": ip} if ip else {})}

    def interfaces(self, vm: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"mac_address": n["mac"], "nic": str(4000 + k),
                 "ip": {"ip_addresses": [{"ip_address": n["ip"], "prefix_length": 24,
                                          "state": "PREFERRED"}] if n["ip"] else []}}
                for k, n in enumerate(vm["nics"])]


def _power(vm: dict[str, Any]) -> str:
    return "POWERED_ON" if vm["power"] == "ON" else "POWERED_OFF"


def _basic_ok(header: str | None) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        user, _, password = base64.b64decode(header[6:]).decode().partition(":")
    except Exception:  # noqa: BLE001 — a malformed header is just unauthorized
        return False
    return bool(user) and bool(password) and password != "wrong"


def _error(kind: str, message: str) -> dict[str, Any]:
    return {"error_type": kind, "messages": [{"default_message": message, "args": [],
                                              "id": f"vapi.{kind.lower()}"}]}
