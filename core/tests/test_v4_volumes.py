"""The v4 volume-group path and its deliberate cost trade."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.nus import VolumesCollector
from ntnxvision_core.models import Cluster, Environment

CLUSTER_UUID = "0000d596-d86d-45b7-adae-2662c463d891"

# Trimmed from a live pc.7.5.1.1 response.
V4_VG = {
    "extId": "0000462d-5a56-4787-a215-b5014e40b1c2",
    "name": "pvc-0000c0de",
    "sharingStatus": "NOT_SHARED",
    # The *full* target name. v3 only had iscsi_target_prefix, which is shared across
    # every group from the same provisioner.
    "targetName": "ntnx-k8s-0000462d-5a56-4787-a215-b5014e40b1c2",
    "clusterReference": CLUSTER_UUID,
    "storageFeatures": {"flashMode": {"isEnabled": False}},
}

V3_VG = {
    "metadata": {"uuid": V4_VG["extId"]},
    "spec": {
        "name": "pvc-0000c0de",
        "resources": {
            "disk_list": [{"disk_size_mib": 1024}, {"disk_size_mib": 2048}],
            "attachment_list": [{"vm_reference": {"uuid": "vm-1"}}],
            "iscsi_target_prefix": "ntnx-k8s",
        },
    },
}


class _Conn:
    """v3 aggregates present by default; `no_v3` simulates life after the sunset."""

    def __init__(self, v4: bool = True, no_v3: bool = False) -> None:
        self._v4, self._no_v3 = v4, no_v3
        self.sub_calls: list[str] = []

    def v4_ready(self, area: str) -> bool:
        return self._v4

    def v4_list(self, path: str) -> list[dict[str, Any]]:
        if path.endswith("/disks"):
            self.sub_calls.append(path)
            return [{"diskSizeBytes": 1073741824}, {"diskSizeBytes": 2147483648}]
        if path.endswith("/vm-attachments"):
            self.sub_calls.append(path)
            return [{"extId": "vm-1"}]
        return [dict(V4_VG)]

    def v3_list(self, kind: str) -> list[dict[str, Any]]:
        if self._no_v3:
            raise RuntimeError("v3 removed")
        return [dict(V3_VG)]


def _collect(conn: _Conn) -> Environment:
    env = Environment()
    env.clusters.append(Cluster(uuid=CLUSTER_UUID, name="lab-cluster-01"))
    VolumesCollector(conn).collect(env)  # type: ignore[arg-type]
    return env


def test_v4_volume_group_maps_config_fields() -> None:
    vg = _collect(_Conn()).volume_groups[0]
    assert vg.name == "pvc-0000c0de"
    assert vg.is_shared is False
    assert vg.flash_mode is False
    assert vg.api_source == "v4"


def test_cluster_name_is_resolved_from_the_uuid_reference() -> None:
    """v4 reports the cluster by uuid; v3 left the name null on every group here."""
    assert _collect(_Conn()).volume_groups[0].cluster_name == "lab-cluster-01"


def test_iscsi_target_is_the_full_name_not_the_shared_prefix() -> None:
    """Deliberate change: v3's iscsi_target_prefix repeats across unrelated groups."""
    vg = _collect(_Conn()).volume_groups[0]
    assert vg.iscsi_target == "ntnx-k8s-0000462d-5a56-4787-a215-b5014e40b1c2"
    assert vg.iscsi_target != "ntnx-k8s"


def test_aggregates_prefer_the_single_v3_call() -> None:
    """Two sub-resource requests per group is 70 on the lab PC; v3 gets them in one."""
    conn = _Conn()
    vg = _collect(conn).volume_groups[0]
    assert vg.disk_count == 2
    assert vg.total_capacity_bytes == 3 * 1024 * 1024 * 1024
    assert vg.attachment_count == 1
    assert conn.sub_calls == []  # no per-group walk while v3 answers


def test_aggregates_fall_back_to_v4_sub_resources_after_the_sunset() -> None:
    conn = _Conn(no_v3=True)
    vg = _collect(conn).volume_groups[0]
    assert vg.disk_count == 2
    assert vg.total_capacity_bytes == 3 * 1024 * 1024 * 1024
    assert vg.attachment_count == 1
    assert len(conn.sub_calls) == 2  # exactly the two sub-resources, per group


def test_v3_path_is_used_when_v4_is_not_served() -> None:
    env = _collect(_Conn(v4=False))
    assert env.volume_groups[0].api_source == "v3"
    assert env.collection_notes["volume_groups"] == "ok"
