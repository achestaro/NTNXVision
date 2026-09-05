"""The v4 catalog paths (images, templates, OVAs) and the traps they set.

Fixtures are trimmed from live pc.7.5.1.1 responses. The three traps worth guarding are
that the v4 image list silently drops three fields v3 carries, that both the template and
OVA *list* payloads are thinner than their detail payloads, and that the disks/cdRoms
split means the obvious "count the hardware" reading of an OVA is wrong.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.catalog import ImageCollector, OvaCollector, TemplateCollector
from ntnxvision_core.models import Cluster, Environment

CLUSTER_UUID = "0006442f-1fc6-3ee1-0000-000000016d34"

V4_IMAGE = {
    "$objectType": "vmm.v4.content.Image",
    "extId": "5a61e7ff-181d-44cb-85c5-78257ea8c03f",
    "sizeBytes": 6014152704,
    "createTime": "2025-12-08T11:13:47.997818Z",
    "ownerName": "david.quinney@quinney.dev",
    "name": "Windows Server 2025 Eval",
    "type": "ISO_IMAGE",
    "clusterLocationExtIds": [CLUSTER_UUID],
}

#: The same image over v3 — the only source of architecture, source URI and state.
V3_IMAGE = {
    "status": {
        "name": "Windows Server 2025 Eval",
        "state": "COMPLETE",
        "resources": {
            "image_type": "ISO_IMAGE",
            "architecture": "X86_64",
            "source_uri": "https://go.microsoft.com/fwlink/?linkid=2293312",
            "size_bytes": 6014152704,
            "current_cluster_reference_list": [{"kind": "cluster", "uuid": CLUSTER_UUID}],
        },
    },
    "metadata": {"uuid": V4_IMAGE["extId"], "kind": "image"},
}

V4_TEMPLATE = {
    "$objectType": "vmm.v4.content.Template",
    "extId": "00c7f451-b4ec-4c0f-89da-9ba77701de64",
    "createTime": "2025-12-11T11:41:23.128537Z",
    "templateName": "Windows Server 2025 Template",
    "createdBy": {
        "$objectType": "vmm.v4.content.TemplateUser",
        "extId": "868b7755-bb2d-55ef-b757-06a34e92bcf2",
        "username": "david.quinney@quinney.dev",
    },
    # The list row's vmSpec is trimmed to sizing — no guestTools, so no guest OS.
    "templateVersionSpec": {
        "extId": "27cceb5c-23b5-482c-882b-492dcad15984",
        "isActiveVersion": True,
        "versionName": "Initial Version",
        "clusterLocationExtIds": [CLUSTER_UUID],
        "vmSpec": {
            "numSockets": 1,
            "numCoresPerSocket": 2,
            "numThreadsPerCore": 1,
            "memorySizeBytes": 8589934592,
        },
    },
}

#: The detail response for the same template — same shape plus the guest tools.
V4_TEMPLATE_DETAIL = {
    **V4_TEMPLATE,
    "templateVersionSpec": {
        **V4_TEMPLATE["templateVersionSpec"],  # type: ignore[dict-item]
        "vmSpec": {
            **V4_TEMPLATE["templateVersionSpec"]["vmSpec"],  # type: ignore[index]
            "name": "Windows Server 2025 Template",
            "guestTools": {
                "version": "4.5",
                "isInstalled": True,
                "guestOsVersion": "windows:64:WindowsServer2025Standard",
                "guestInfo": {
                    "guestOsFullName": "windows:64:WindowsServer2025Standard",
                    "guestOsBuildNumber": "26100",
                },
            },
        },
    },
}

V4_OVA = {
    "$objectType": "vmm.v4.content.Ova",
    "extId": "4.3.0-uuid",
    "sizeBytes": 10912195072,
    "createTime": "2025-12-19T07:43:11.677192Z",
    "name": "4.3.0-CalmPolicyVM.ova",
    "diskFormat": "VMDK",
    "createdBy": {
        "$objectType": "iam.v4.authn.User",
        "username": "david.quinney@quinney.dev",
    },
}

#: The OVA detail — the list row carries neither of these two keys.
V4_OVA_DETAIL = {
    **V4_OVA,
    "clusterLocationExtIds": [CLUSTER_UUID],
    "vmConfig": {
        "$objectType": "vmm.v4.ahv.config.Vm",
        "numSockets": 4,
        "disks": [
            {"backingInfo": {"diskSizeBytes": 75161927680}},
        ],
        "cdRoms": [
            {"diskAddress": {"busType": "SATA", "index": 0}, "isoType": "OTHER"},
        ],
    },
}


class _Conn:
    def __init__(
        self,
        v4: bool = True,
        v4_lists: dict[str, Any] | None = None,
        v4_gets: dict[str, Any] | None = None,
        v3_lists: dict[str, Any] | None = None,
    ) -> None:
        self._v4 = v4
        self.v4_lists = v4_lists or {}
        self.v4_gets = v4_gets or {}
        self.v3_lists = v3_lists or {}
        self.calls: list[str] = []

    def v4_ready(self, area: str) -> bool:
        return self._v4

    def _serve(self, store: dict[str, Any], path: str) -> Any:
        self.calls.append(path)
        value = store.get(path)
        if value is None:
            raise NutanixApiError(f"404 {path}", status_code=404)
        return value

    def v4_list(self, path: str, page_size: int = 100) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._serve(self.v4_lists, path)
        return result

    def v4_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        result: dict[str, Any] = self._serve(self.v4_gets, path)
        return result

    def v3_list(self, kind: str, page_size: int = 250) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = self._serve(self.v3_lists, kind)
        return result


def _image_conn(**kw: Any) -> _Conn:
    return _Conn(
        v4_lists={"vmm/v4.0/content/images": [dict(V4_IMAGE)]},
        v3_lists={"images": [dict(V3_IMAGE)]},
        **kw,
    )


def _collect(collector: type, conn: _Conn, env: Environment | None = None) -> Environment:
    env = env if env is not None else Environment()
    collector(conn).collect(env)
    return env


# ─── images ────────────────────────────────────────────────────────────


def test_v4_image_takes_identity_from_v4_and_the_rest_from_v3() -> None:
    """v4 serves everything but architecture, source and state; those come from v3."""
    image = _collect(ImageCollector, _image_conn()).images[0]
    assert image.uuid == V4_IMAGE["extId"]
    assert image.name == "Windows Server 2025 Eval"
    assert image.image_type == "ISO_IMAGE"
    assert image.size_bytes == 6014152704
    assert image.cluster_uuids == [CLUSTER_UUID]
    assert image.api_source == "v4"
    # The three the v4 list payload does not carry at all.
    assert image.architecture == "X86_64"
    assert image.source == "https://go.microsoft.com/fwlink/?linkid=2293312"
    assert image.state == "COMPLETE"


def test_v4_image_degrades_rather_than_fails_after_the_legacy_sunset() -> None:
    """No v3 endpoint means three blank columns, not a failed collection."""
    conn = _Conn(v4_lists={"vmm/v4.0/content/images": [dict(V4_IMAGE)]}, v3_lists={})
    env = _collect(ImageCollector, conn)
    image = env.images[0]
    assert image.name == "Windows Server 2025 Eval"
    assert (image.architecture, image.source, image.state) == (None, None, None)
    assert env.collection_notes["images"] == "ok"


def test_v4_image_placement_drops_the_empty_strings_v4_pads_with() -> None:
    """v4 pads absent values with "" rather than omitting them; an empty cluster uuid
    would be resolved to an empty name and printed as a phantom placement."""
    padded = {**V4_IMAGE, "clusterLocationExtIds": [CLUSTER_UUID, ""]}
    conn = _Conn(v4_lists={"vmm/v4.0/content/images": [padded]}, v3_lists={"images": []})
    assert _collect(ImageCollector, conn).images[0].cluster_uuids == [CLUSTER_UUID]


def test_v3_backfill_falls_back_to_a_name_only_when_it_is_unique() -> None:
    """Both APIs agree on image uuids today, but ESXi VMs show the two can drift."""
    renumbered = {**V3_IMAGE, "metadata": {"uuid": "a-different-uuid"}}
    conn = _Conn(
        v4_lists={"vmm/v4.0/content/images": [dict(V4_IMAGE)]},
        v3_lists={"images": [renumbered]},
    )
    assert _collect(ImageCollector, conn).images[0].architecture == "X86_64"


def test_v3_backfill_refuses_an_ambiguous_name_match() -> None:
    """This lab really does hold two images named __curie_image_disk_ubuntu2204-x86_64_os;
    attributing one image's source URI to the other is worse than an empty cell."""
    twin_a = {**V3_IMAGE, "metadata": {"uuid": "uuid-a"}}
    twin_b = {
        "metadata": {"uuid": "uuid-b"},
        "status": {
            "name": "Windows Server 2025 Eval",
            "state": "ERROR",
            "resources": {"architecture": "AARCH64", "source_uri": "nfs:///other"},
        },
    }
    conn = _Conn(
        v4_lists={"vmm/v4.0/content/images": [dict(V4_IMAGE)]},
        v3_lists={"images": [twin_a, twin_b]},
    )
    image = _collect(ImageCollector, conn).images[0]
    assert (image.architecture, image.source, image.state) == (None, None, None)


def test_images_stay_on_v3_when_v4_is_not_served() -> None:
    env = _collect(ImageCollector, _image_conn(v4=False))
    image = env.images[0]
    assert image.api_source == "v3"
    assert image.architecture == "X86_64"
    assert image.cluster_uuids == [CLUSTER_UUID]
    assert env.collection_notes["images"] == "ok"


def test_images_note_the_skip_when_neither_api_answers() -> None:
    env = _collect(ImageCollector, _Conn(v4=False))
    assert env.images == []
    assert env.collection_notes["images"].startswith("skipped")


# ─── templates ─────────────────────────────────────────────────────────


def _template_conn(**kw: Any) -> _Conn:
    tid = V4_TEMPLATE["extId"]
    return _Conn(
        v4_lists={"vmm/v4.0/content/templates": [dict(V4_TEMPLATE)]},
        v4_gets={
            f"vmm/v4.0/content/templates/{tid}": {"data": dict(V4_TEMPLATE_DETAIL)},
            f"vmm/v4.0/content/templates/{tid}/versions": {
                "data": [{"extId": "27cceb5c", "versionName": "Initial Version"}],
                "metadata": {"totalAvailableResults": 1},
            },
        },
        **kw,
    )


def test_v4_template_maps_sizing_and_owner_from_the_list_row() -> None:
    t = _collect(TemplateCollector, _template_conn()).templates[0]
    assert t.name == "Windows Server 2025 Template"
    # 1 socket x 2 cores; the model's vcpu count is the total, not the socket count.
    assert t.num_vcpus == 2
    assert t.memory_bytes == 8589934592
    assert t.created_by == "david.quinney@quinney.dev"
    # templateVersionSpec on a template *is* its active version.
    assert t.active_version == "Initial Version"
    assert t.api_source == "v4"


def test_v4_template_guest_os_needs_the_detail_payload() -> None:
    """The trap: the list row's vmSpec is trimmed to sizing and has no guestTools.

    The string must also match the one the VM collector reports for the VM the template
    was captured from, or the same guest OS reads two ways in one report.
    """
    t = _collect(TemplateCollector, _template_conn()).templates[0]
    assert t.guest_os == "windows:64:WindowsServer2025Standard"
    assert "guestTools" not in V4_TEMPLATE["templateVersionSpec"]["vmSpec"]  # type: ignore[index,operator]


def test_v4_template_version_count_comes_from_the_paging_total() -> None:
    """Nothing on the template reports it; the versions sub-collection totals it."""
    t = _collect(TemplateCollector, _template_conn()).templates[0]
    assert t.version_count == 1


def test_v4_template_survives_a_detail_endpoint_that_will_not_answer() -> None:
    """A per-entity request that fails must cost one column, not the whole template."""
    conn = _Conn(v4_lists={"vmm/v4.0/content/templates": [dict(V4_TEMPLATE)]})
    t = _collect(TemplateCollector, conn).templates[0]
    assert t.name == "Windows Server 2025 Template"
    assert t.num_vcpus == 2
    assert t.guest_os is None
    assert t.version_count is None


def test_templates_are_skipped_without_v4_because_v3_has_no_endpoint() -> None:
    """v3 `vm_templates`, `templates` and `vm_template` all 404 on a live PC."""
    env = _collect(TemplateCollector, _Conn(v4=False))
    assert env.templates == []
    assert env.collection_notes["templates"].startswith("skipped")


def test_templates_are_skipped_when_the_content_endpoint_is_absent() -> None:
    """The vmm namespace can answer its probe on a build without the content APIs."""
    env = _collect(TemplateCollector, _Conn(v4=True))
    assert env.templates == []
    assert env.collection_notes["templates"].startswith("skipped")


# ─── OVAs ──────────────────────────────────────────────────────────────


def _ova_conn(**kw: Any) -> _Conn:
    oid = V4_OVA["extId"]
    return _Conn(
        v4_lists={"vmm/v4.0/content/ovas": [dict(V4_OVA)]},
        v4_gets={f"vmm/v4.0/content/ovas/{oid}": {"data": dict(V4_OVA_DETAIL)}},
        **kw,
    )


def _env_with_cluster() -> Environment:
    env = Environment()
    env.clusters.append(Cluster(uuid=CLUSTER_UUID, name="lab-cluster-01"))
    return env


def test_v4_ova_counts_disks_not_cdroms() -> None:
    """v4 splits virtual hardware into `disks` and `cdRoms`; only the disks are disks.

    The fixture OVA has one of each, so counting the whole hardware list would say 2.
    """
    ova = _collect(OvaCollector, _ova_conn(), _env_with_cluster()).ovas[0]
    assert ova.disk_count == 1


def test_v4_ova_resolves_its_cluster_from_the_already_collected_clusters() -> None:
    """Placement is a uuid in the detail payload and absent from the list row; clusters
    are collected in phase 1, so naming it costs no extra request."""
    ova = _collect(OvaCollector, _ova_conn(), _env_with_cluster()).ovas[0]
    assert ova.cluster_name == "lab-cluster-01"
    assert ova.size_bytes == 10912195072
    assert ova.created == "2025-12-19T07:43:11.677192Z"
    assert ova.api_source == "v4"


def test_v4_ova_keeps_the_raw_uuid_for_an_unregistered_cluster() -> None:
    ova = _collect(OvaCollector, _ova_conn()).ovas[0]
    assert ova.cluster_name == CLUSTER_UUID


def test_v4_ova_survives_a_detail_endpoint_that_will_not_answer() -> None:
    conn = _Conn(v4_lists={"vmm/v4.0/content/ovas": [dict(V4_OVA)]})
    ova = _collect(OvaCollector, conn, _env_with_cluster()).ovas[0]
    assert ova.name == "4.3.0-CalmPolicyVM.ova"
    assert ova.size_bytes == 10912195072
    assert (ova.disk_count, ova.cluster_name) == (None, None)


def test_ovas_fall_back_to_v3_when_the_v4_content_endpoint_is_absent() -> None:
    conn = _Conn(
        v4=True,
        v3_lists={
            "ovas": [
                {
                    "info": {
                        "name": "legacy.ova",
                        "current_cluster_reference_list": [
                            {"kind": "cluster", "uuid": CLUSTER_UUID, "name": "lab-cluster-01"}
                        ],
                    },
                    "metadata": {"uuid": "legacy-uuid", "creation_time": "2025-01-01T00:00:00Z"},
                }
            ]
        },
    )
    env = _collect(OvaCollector, conn, _env_with_cluster())
    assert env.ovas[0].api_source == "v3"
    assert env.ovas[0].cluster_name == "lab-cluster-01"
    assert env.collection_notes["ovas"] == "ok"


def test_ovas_note_the_skip_when_neither_api_answers() -> None:
    env = _collect(OvaCollector, _Conn(v4=False))
    assert env.ovas == []
    assert env.collection_notes["ovas"].startswith("skipped")
