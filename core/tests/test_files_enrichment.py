"""Nutanix Files: FSVMs, networks, DNS and usage from Files v4 (a2 detail + stats).

The payloads below follow the shapes Files 5.3.0.3 on pc.7.5.1.6 returned (2026-11-06),
with every identifier and address replaced by an example value.
"""

from __future__ import annotations

from typing import Any

from openpyxl import load_workbook

from ntnxvision_core.api.connection import PrismCredentials
from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.nus import FilesCollector
from ntnxvision_core.engine import collect_environment
from ntnxvision_core.exporters import ExcelExporter
from ntnxvision_core.exporters import tables as T
from ntnxvision_core.inventory import InventoryService, display, fmt_bytes, type_counts
from ntnxvision_core.models import Environment, VirtualMachine

FS = "fs000000-0000-4000-8000-000000000001"
CL = "cl000000-0000-4000-8000-000000000001"
VM1, VM2 = "vm000000-0000-4000-8000-000000000001", "vm000000-0000-4000-8000-000000000002"
MT_APPS, MT_HOME = "mt000000-0000-4000-8000-000000000001", "mt000000-0000-4000-8000-000000000002"


def _ip(v: str) -> dict[str, Any]:
    return {"$objectType": "common.v1.config.IPAddress", "ipv4": {"value": v, "prefixLength": 32}}


def _nic(v: str) -> dict[str, Any]:
    return {"$objectType": "files.v4.config.VMNic", "ipAddresses": [{"ipv4": {"value": v}}]}


def _series(value: int, ts: str = "2026-11-06T23:30:00Z") -> list[dict[str, Any]]:
    return [{"timestamp": ts, "value": value}]


#: `files/v4.0/config/file-servers`: extId and name only (also with $select=*).
V4_SERVER = {"$objectType": "files.v4.config.FileServer", "extId": FS, "name": "fs-lab"}

#: `files/v4.0.a2/config/file-servers`: the full record.
A2_SERVER = {
    "$objectType": "files.v4.config.FileServer", "extId": FS, "name": "fs-lab",
    "clusterExtId": CL, "version": "5.3.0.3", "nvmsCount": 2, "vcpus": 4,
    "memoryGiBytes": 12, "memoryGib": 12, "sizeGiBytes": 2048, "sizeInGib": 2048,
    "dnsDomainName": "files.example.lab", "dnsServers": [{"value": "192.0.2.53"}],
    "ntpServers": [{"$objectType": "common.v1.config.IPAddressOrFQDN",
                    "fqdn": {"value": "ntp.example.lab"}}],
    "cvmIpAddresses": [{"prefixLength": 32, "value": "192.0.2.10"}],
    "externalNetworks": [{"$objectType": "files.v4.config.Network",
                          "defaultGateway": _ip("198.51.100.1"),
                          "ipAddresses": [_ip("198.51.100.20"), _ip("198.51.100.21")],
                          "subnetMask": {"ipv4": {"value": "255.255.255.0"}},
                          "virtualNetworkName": "client-net", "isManaged": True,
                          "networkExtId": "net-client"}],
    "internalNetworks": [{"$objectType": "files.v4.config.Network",
                          "defaultGateway": _ip("192.0.2.1"),
                          "ipAddresses": [_ip("192.0.2.30"), _ip("192.0.2.31")],
                          "virtualIpAddress": _ip("192.0.2.29"),
                          "virtualNetworkName": "storage-net", "isManaged": True,
                          "networkExtId": "net-storage"}],
    "platformType": "ON_PREM", "deploymentType": "PRISM_CENTRAL",
    "vms": [{"$objectType": "files.v4.config.FileServerVM", "extId": VM1,
             "fsvmUuid": "nvm-1", "name": "NTNX-fs-lab-1", "vcpus": 4, "memoryGib": 12,
             "memoryGiBytes": 12, "externalNetworks": [_nic("198.51.100.20")],
             "internalNetworks": [_nic("192.0.2.30")]},
            {"$objectType": "files.v4.config.FileServerVM", "extId": VM2,
             "fsvmUuid": "nvm-2", "name": "NTNX-fs-lab-2", "vcpus": 4, "memoryGib": 12,
             "memoryGiBytes": 12, "externalNetworks": [_nic("198.51.100.21")],
             "internalNetworks": [_nic("192.0.2.31")]}],
}

SHARE = {"$objectType": "files.v4.config.MountTarget", "extId": MT_APPS, "name": "Apps",
         "path": "/Apps", "type": "GENERAL", "protocol": "SMB", "secondaryProtocol": ["NONE"],
         "maxSizeGB": 0, "isCompressionEnabled": True, "isReadOnly": False,
         "smbProperties": {"isAccessBasedEnumerationEnabled": False, "isCaEnabled": False}}

#: `files/v4.0/stats/file-servers/{id}?$select=*` (without $select: files + latency only).
SERVER_STATS = {"$objectType": "files.v4.stats.FileServerStats", "extId": FS,
                "totalSpaceUsedBytes": _series(2_105_344),
                "datasetSpaceUsedBytes": _series(2_105_344),
                "totalAvailableSpaceBytes": _series(868_614_185_943_040),
                "numberOfFiles": _series(112), "numberOfConnections": _series(1)}


def _share_stats(mt: str, used: int, files: int) -> dict[str, Any]:
    return {"$objectType": "files.v4.stats.MountTargetStats", "extId": mt,
            "usedBytes": _series(used), "physicalUsedBytes": _series(used),
            "datasetSpaceUsedBytes": _series(used), "numberOfFiles": _series(files)}


#: `GET v3 vms/{uuid}` of an FSVM (the fields read).
def _v3_vm(uuid: str, name: str, host: str) -> dict[str, Any]:
    return {"metadata": {"uuid": uuid, "kind": "vm"},
            "status": {"name": name, "cluster_reference": {"kind": "cluster", "uuid": CL,
                                                            "name": "ahv-01"},
                       "resources": {"num_sockets": 4, "num_vcpus_per_socket": 1,
                                     "memory_size_mib": 12288, "power_state": "ON",
                                     "host_reference": {"kind": "host", "name": host}}}}


class FilesConn:
    """v4 lists / GETs by path, v3 lists, groups and plain GETs; a missing key is a 404."""

    def __init__(self, v4: dict[str, Any], gets: dict[str, Any] | None = None,
                 groups: dict[str, Any] | None = None, v3: dict[str, Any] | None = None,
                 ) -> None:
        self.v4, self.gets, self.groups, self.v3 = v4, gets or {}, groups or {}, v3 or {}
        self.creds = type("C", (), {"base": "https://pc.example.lab:9440"})()
        self.negotiated: dict[str, str] = {}
        self.calls: list[str] = []
        self.params: dict[str, Any] = {}

    @staticmethod
    def _get(table: dict[str, Any], key: str) -> Any:
        if key not in table:
            raise NutanixApiError(f"Not found: {key}", status_code=404)
        return table[key]

    def v4_ready(self, area: str) -> bool:
        return True

    def v4_list(self, path: str, **_kw: Any) -> list[dict[str, Any]]:
        self.calls.append(path)
        return list(self._get(self.v4, path))

    def v4_get(self, path: str, params: Any = None) -> dict[str, Any]:
        self.calls.append(path)
        self.params[path] = params
        return {"data": self._get(self.v4, path)}

    def v3_list(self, kind: str, **_kw: Any) -> list[dict[str, Any]]:
        return list(self._get(self.v3, kind))

    def v3_groups(self, entity_type: str, attrs: list[str], **_kw: Any) -> list[dict[str, Any]]:
        return list(self._get(self.groups, entity_type))

    def get_json(self, url: str, params: Any = None) -> Any:
        path = url.split(":9440", 1)[-1]
        self.calls.append(path)
        return self._get(self.gets, path)


STATS = "files/v4.0/stats/file-servers"


def _lab_conn(*, a2: bool = True) -> FilesConn:
    v4: dict[str, Any] = {
        "files/v4.0/config/file-servers": [V4_SERVER],
        f"files/v4.0/config/file-servers/{FS}/mount-targets": [
            SHARE, {**SHARE, "extId": MT_HOME, "name": "Home", "path": "/Home",
                    "type": "HOMES"}],
        f"{STATS}/{FS}": SERVER_STATS,
        f"{STATS}/{FS}/mount-targets/{MT_APPS}": _share_stats(MT_APPS, 139_264, 7),
        f"{STATS}/{FS}/mount-targets/{MT_HOME}": _share_stats(MT_HOME, 1_966_080, 105),
    }
    if a2:
        v4["files/v4.0.a2/config/file-servers"] = [A2_SERVER]
    return FilesConn(
        v4,
        gets={f"/api/nutanix/v3/vms/{VM1}": _v3_vm(VM1, "NTNX-fs-lab-1", "192.0.2.41"),
              f"/api/nutanix/v3/vms/{VM2}": _v3_vm(VM2, "NTNX-fs-lab-2", "192.0.2.42")},
        groups={"file_server_service": [{
            "entity_id": FS, "name": "fs-lab", "cluster_name": "ahv-01",
            "afs_version": "5.3.0.3", "nvm_uuid_list": [VM1, VM2],
            # Not the shares' sum (the stats total is): kept only without stats.
            "last_used_size_bytes": "21561344"}]})


def test_the_file_server_is_enriched_from_the_a2_detail_and_stats() -> None:
    env = Environment()
    conn = _lab_conn()
    FilesCollector(conn).collect(env)  # type: ignore[arg-type]
    fs = env.file_servers[0]
    assert (fs.cluster_name, fs.cluster_uuid, fs.version, fs.num_fsvm) == (
        "ahv-01", CL, "5.3.0.3", 2)
    assert (fs.external_network, fs.internal_network, fs.storage_vip) == (
        "client-net", "storage-net", "192.0.2.29")
    assert (fs.dns_domain, fs.dns_servers, fs.ntp_servers) == (
        "files.example.lab", ["192.0.2.53"], ["ntp.example.lab"])
    assert (fs.fsvm_vcpus, fs.fsvm_memory_bytes, fs.size_bytes) == (4, 12 * 1024**3,
                                                                     2048 * 1024**3)
    # The stats total (the shares' sum) wins over the groups figure.
    assert (fs.used_bytes, fs.file_count, fs.share_count) == (2_105_344, 112, 2)
    # Stats answer the usage only when every field is selected.
    assert conn.params[f"{STATS}/{FS}"] == {"$select": "*"}


def test_fsvms_come_from_the_a2_detail_with_host_and_power_from_their_vms() -> None:
    env = Environment()
    FilesCollector(_lab_conn()).collect(env)  # type: ignore[arg-type]
    one, two = env.file_server_vms
    assert (one.uuid, one.name, one.file_server_name, one.cluster_name) == (
        VM1, "NTNX-fs-lab-1", "fs-lab", "ahv-01")
    assert (one.client_ips, one.storage_ips) == (["198.51.100.20"], ["192.0.2.30"])
    assert (one.num_vcpus, one.memory_bytes, one.version) == (4, 12 * 1024**3, "5.3.0.3")
    assert (one.host_name, one.power_state, one.api_source) == ("192.0.2.41", "ON", "v4")
    assert two.host_name == "192.0.2.42"


def test_collected_vms_spare_the_per_fsvm_reads() -> None:
    env = Environment(vms=[VirtualMachine(uuid=VM1, name="NTNX-fs-lab-1", host_name="node-a",
                                          power_state="OFF")])
    conn = _lab_conn()
    FilesCollector(conn).collect(env)  # type: ignore[arg-type]
    assert env.file_server_vms[0].host_name == "node-a"
    assert env.file_server_vms[0].power_state == "OFF"
    assert f"/api/nutanix/v3/vms/{VM1}" not in conn.calls
    assert f"/api/nutanix/v3/vms/{VM2}" in conn.calls


def test_without_the_a2_namespace_fsvms_come_from_groups_and_their_vms() -> None:
    env = Environment()
    FilesCollector(_lab_conn(a2=False)).collect(env)  # type: ignore[arg-type]
    fs = env.file_servers[0]
    assert fs.num_fsvm == 2 and fs.dns_domain is None and fs.used_bytes == 2_105_344
    vms = env.file_server_vms
    assert [(v.name, v.host_name, v.num_vcpus, v.api_source) for v in vms] == [
        ("NTNX-fs-lab-1", "192.0.2.41", 4, "v3"), ("NTNX-fs-lab-2", "192.0.2.42", 4, "v3")]
    assert vms[0].client_ips == []  # only Files tells client from storage addresses


def test_share_usage_and_file_counts_come_from_the_share_stats() -> None:
    env = Environment()
    FilesCollector(_lab_conn()).collect(env)  # type: ignore[arg-type]
    shares = {s.name: s for s in env.file_shares}
    assert (shares["Apps"].used_bytes, shares["Apps"].file_count) == (139_264, 7)
    assert (shares["Home"].used_bytes, shares["Home"].file_count) == (1_966_080, 105)


def test_missing_stats_leave_usage_unknown() -> None:
    conn = _lab_conn()
    del conn.v4[f"{STATS}/{FS}"], conn.v4[f"{STATS}/{FS}/mount-targets/{MT_HOME}"]
    env = Environment()
    FilesCollector(conn).collect(env)  # type: ignore[arg-type]
    # Without stats the groups figure stands.
    assert env.file_servers[0].used_bytes == 21_561_344
    assert env.file_servers[0].file_count is None
    assert {s.name: s.used_bytes for s in env.file_shares} == {"Apps": 139_264, "Home": None}


def test_the_newest_stats_point_is_kept() -> None:
    conn = _lab_conn()
    conn.v4[f"{STATS}/{FS}/mount-targets/{MT_APPS}"] = {
        "usedBytes": [{"timestamp": "2026-11-06T23:35:00Z", "value": 4096},
                      {"timestamp": "2026-11-06T23:30:00Z", "value": 1024},
                      {"timestamp": "2026-11-06T23:40:00Z", "value": None}]}
    env = Environment()
    FilesCollector(conn).collect(env)  # type: ignore[arg-type]
    assert next(s for s in env.file_shares if s.name == "Apps").used_bytes == 4096


def test_small_sizes_read_in_kb_not_as_nothing() -> None:
    assert fmt_bytes(139_264) == "136 KB"
    assert fmt_bytes(12 * 1024) == "12 KB"
    assert fmt_bytes(1_966_080) == "1.9 MB"
    assert fmt_bytes(512) == "512 B"
    used = next(c for c in T.FILE_SHARES.cols if c.key == "used")
    assert display(used, 139_264) == "136 KB"
    assert used.excel is not None and used.excel(139_264) == "136 KB"


def test_file_server_vms_grid_and_excel_sheet(tmp_path: Any) -> None:
    env = Environment(collection_notes={"files": "ok"})
    FilesCollector(_lab_conn()).collect(env)  # type: ignore[arg-type]
    counts = {t["type"]: t for t in type_counts(env)}
    assert counts["file_server_vms"]["count"] == 2
    assert counts["file_server_vms"]["label"] == "File Server VMs"
    res = InventoryService().query(env, "file_server_vms", {}, None,
                                   {"offset": 0, "limit": 10})
    keys = [c["key"] for c in res["columns"]]
    assert keys[:3] == ["cluster", "server", "name"]
    assert {"client_ips", "storage_ips", "vcpu", "memory", "host", "power", "version"} <= set(
        keys)
    cells = res["rows"][0]["cells"]
    assert (cells["client_ips"], cells["storage_ips"]) == ("198.51.100.20", "192.0.2.30")
    servers = InventoryService().query(env, "file_servers", {}, None,
                                       {"offset": 0, "limit": 10})
    fs = servers["rows"][0]["cells"]
    assert (fs["client_network"], fs["storage_network"], fs["dns_domain"], fs["fsvm_vcpu"]) == (
        "client-net", "storage-net", "files.example.lab", 4)

    out = tmp_path / "files.xlsx"
    ExcelExporter().export(env, out)
    wb = load_workbook(out)
    names = wb.sheetnames
    assert names.index("File Server VMs") == names.index("File Servers") + 1
    ws = wb["File Server VMs"]
    header = [c.value for c in ws[1]]
    assert header[:3] == ["Cluster", "File Server", "Name"]
    shares = wb["File Shares"]
    sh_header = [c.value for c in shares[1]]
    used = [r[sh_header.index("Used")].value for r in shares.iter_rows(min_row=2)]
    assert "136 KB" in used and "1.9 MB" in used


def test_demo_files_shapes_match_the_lab(demo: Any) -> None:
    env = collect_environment(PrismCredentials(host="pc02.demo.local", username="admin",
                                               password="demo"), areas=["files"])
    assert env.file_servers and env.file_server_vms
    for fs in env.file_servers:
        mine = [v for v in env.file_server_vms if v.file_server_name == fs.name]
        assert len(mine) == fs.num_fsvm == 3
        assert fs.dns_domain and fs.storage_vip and fs.fsvm_vcpus == 4
        assert fs.used_bytes == sum(s.used_bytes or 0 for s in env.file_shares
                                    if s.file_server_name == fs.name)
    assert all(v.host_name and v.power_state == "ON" and v.client_ips and v.storage_ips
               for v in env.file_server_vms)
    scratch = [s for s in env.file_shares if s.name == "scratch"]
    assert scratch and all(s.used_bytes == 12 * 1024 and s.file_count == 3 for s in scratch)


def test_a_collected_vm_without_a_host_name_is_named_from_the_hosts() -> None:
    from ntnxvision_core.models import Host

    env = Environment(hosts=[Host(uuid="h-1", name="node-b")],
                      vms=[VirtualMachine(uuid=VM1, name="NTNX-fs-lab-1", host_uuid="h-1",
                                          power_state="ON"),
                           VirtualMachine(uuid=VM2, name="NTNX-fs-lab-2", host_uuid="h-9")])
    conn = _lab_conn()
    FilesCollector(conn).collect(env)  # type: ignore[arg-type]
    assert [v.host_name for v in env.file_server_vms] == ["node-b", "192.0.2.42"]
    assert f"/api/nutanix/v3/vms/{VM1}" not in conn.calls
    assert env.vms[0].host_name is None  # the collected VM itself is left to the resolver
