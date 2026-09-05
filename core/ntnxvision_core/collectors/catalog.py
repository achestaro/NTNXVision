"""Prism Central catalog collector — Images, Templates, and OVAs.

All three are served by the v4 **vmm** namespace under ``vmm/v4.0/content/…``, so the
three collectors share one negotiation (``v4_ready("vms")``) instead of probing a path
each. Only images have a full legacy equivalent (``images/list``); v3's ``ovas/list`` is
far thinner than the v4 one, and templates have no v3 endpoint at all — ``vm_templates``,
``templates`` and ``vm_template`` all 404 — so a Prism Central below the v4 GA line simply
reports no templates, as it did before.

**Three image fields do not survive the move.** ``architecture``, ``state`` and the source
URI are absent from the v4 image list payload; the per-image detail adds ``source`` but
still neither of the other two, and would cost one request per image. Blanking the
Architecture, State and Source columns would be a visible regression, so the v4 path fills
them from a single v3 ``images/list`` call while v3 exists and leaves them None once it
does not. Everything else — identity, type, size, cluster placement — matches v3
byte-for-byte on a live pc.7.5.1.1 and comes from v4.

**Templates and OVAs pay per entity for what their list payloads omit.** Both list
responses are deliberately thin: a template row's embedded ``templateVersionSpec.vmSpec``
carries sizing only (no ``guestTools``, hence no guest OS), and an OVA row carries neither
its cluster placement nor its disks. The detail endpoints carry both. Unlike storage
containers or VMs these are small collections — a Prism Central holds a handful of
templates and OVAs, each a deliberate act of an administrator — so one detail request each
is affordable here where it would not be elsewhere. Every one is best-effort: a template
or OVA whose detail cannot be read still appears, with the list fields it did serve.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import Image, Ova, Template

GIB = 1024**3

#: v4 catalog list endpoints, all in the `vmm` namespace probed as the `vms` area.
V4_IMAGES = "vmm/v4.0/content/images"
V4_TEMPLATES = "vmm/v4.0/content/templates"
V4_OVAS = "vmm/v4.0/content/ovas"


class _V3ImageDetail:
    """The image fields only v3 carries, looked up from a v4 image.

    Matching is by uuid, which is safe here: on a live pc.7.5.1.1 the v3 and v4 image
    uuid sets are identical, unlike ESXi VMs where the two APIs disagree. The name
    fallback is insurance against a build where they drift, and it fires only for a name
    that is unique across the whole v3 result — this lab has two images sharing the name
    ``__curie_image_disk_ubuntu2204-x86_64_os``, and attributing one image's source URI
    to the other is worse than an empty cell.
    """

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.by_uuid: dict[str, dict[str, Any]] = {}
        by_name: dict[str, list[dict[str, Any]]] = {}
        for entity in rows:
            status = entity.get("status") or {}
            resources = status.get("resources") or {}
            fields = {
                "architecture": resources.get("architecture"),
                "source": resources.get("source_uri"),
                "state": status.get("state"),
            }
            uuid = (entity.get("metadata") or {}).get("uuid")
            if uuid:
                self.by_uuid[uuid] = fields
            name = status.get("name")
            if name:
                by_name.setdefault(name, []).append(fields)
        self.unique_by_name = {n: rows_[0] for n, rows_ in by_name.items() if len(rows_) == 1}

    def for_image(self, uuid: str, name: str | None) -> dict[str, Any]:
        found = self.by_uuid.get(uuid)
        if found is not None:
            return found
        return self.unique_by_name.get(name or "", {})


class ImageCollector(Collector):
    name = "images"
    label = "Images"

    def collect(self, env: Any) -> None:
        if self.conn.v4_ready("vms"):
            self._collect_v4(env)
        elif not self._collect_v3(env):
            env.collection_notes[self.name] = "skipped: images endpoint not available"
            return
        env.collection_notes[self.name] = "ok"

    # ─── v4 ────────────────────────────────────────────────────────

    def _collect_v4(self, env: Any) -> None:
        detail = self._v3_detail()
        for entity in self.conn.v4_list(V4_IMAGES):
            uuid = entity.get("extId") or ""
            name = entity.get("name")
            extra = detail.for_image(uuid, name)
            env.images.append(
                Image(
                    uuid=uuid,
                    name=name,
                    # Same vocabulary in both APIs (DISK_IMAGE / ISO_IMAGE); verified
                    # identical for all 22 images on the lab PC, so no normalization.
                    image_type=entity.get("type"),
                    size_bytes=entity.get("sizeBytes"),
                    architecture=extra.get("architecture"),
                    source=extra.get("source"),
                    cluster_uuids=[
                        u for u in (entity.get("clusterLocationExtIds") or []) if u
                    ],
                    state=extra.get("state"),
                    api_source="v4",
                )
            )

    def _v3_detail(self) -> _V3ImageDetail:
        """The v3-only image fields, in one call. Empty after the legacy sunset."""
        try:
            return _V3ImageDetail(self.conn.v3_list("images"))
        except NutanixApiError:
            return _V3ImageDetail([])

    # ─── v3 ────────────────────────────────────────────────────────

    def _collect_v3(self, env: Any) -> bool:
        try:
            entities = self.conn.v3_list("images")
        except NutanixApiError:
            return False
        for entity in entities:
            status = entity.get("status", {}) or {}
            resources = status.get("resources", {}) or {}
            # current_cluster_reference_list holds the references themselves
            # ({kind, uuid[, name]}), not wrappers around a cluster_reference key.
            names: list[str] = []
            uuids: list[str] = []
            for ref in resources.get("current_cluster_reference_list", []) or []:
                if not isinstance(ref, dict):
                    continue
                inner = ref.get("cluster_reference") if "cluster_reference" in ref else ref
                if not isinstance(inner, dict):
                    continue
                if inner.get("name"):
                    names.append(inner["name"])
                if inner.get("uuid"):
                    uuids.append(inner["uuid"])
            env.images.append(
                Image(
                    uuid=entity.get("metadata", {}).get("uuid", ""),
                    name=status.get("name"),
                    image_type=resources.get("image_type"),
                    size_bytes=resources.get("size_bytes"),
                    architecture=resources.get("architecture"),
                    source=resources.get("source_uri"),
                    cluster_names=names,
                    cluster_uuids=uuids,  # engine resolves these to names post-collection
                    state=status.get("state"),
                    api_source="v3",
                )
            )
        return True


class TemplateCollector(Collector):
    name = "templates"
    label = "VM Templates"

    def collect(self, env: Any) -> None:
        if not self.conn.v4_ready("vms"):
            env.collection_notes[self.name] = "skipped: templates API not available"
            return
        try:
            entities = self.conn.v4_list(V4_TEMPLATES)
        except NutanixApiError:
            # The namespace answered its probe but this endpoint is not routed — a
            # pc.2024.3-era build with the VM APIs but not the content ones.
            env.collection_notes[self.name] = "skipped: templates API not available"
            return
        for entity in entities:
            self._add(env, entity)
        env.collection_notes[self.name] = "ok"

    def _add(self, env: Any, entity: dict[str, Any]) -> None:
        uuid = entity.get("extId") or ""
        # The list row's vmSpec is trimmed to sizing; the detail carries guestTools.
        full = self._detail(uuid) or entity
        spec = full.get("templateVersionSpec") or {}
        vm_spec = spec.get("vmSpec") or full.get("vmSpec") or {}
        # `templateVersionSpec` on a template is its *active* version (isActiveVersion is
        # true on it), so its name is the active version even without an activeVersionId.
        active = entity.get("activeVersionId") or spec.get("versionName")
        env.templates.append(
            Template(
                uuid=uuid,
                name=entity.get("templateName") or entity.get("name"),
                description=full.get("templateDescription") or full.get("description"),
                guest_os=_guest_os(vm_spec, full.get("guestCustomizationConfig")),
                num_vcpus=_vcpus(vm_spec),
                memory_bytes=_mem_bytes(vm_spec),
                active_version=str(active) if active is not None else None,
                version_count=entity.get("versionCount") or self._version_count(uuid),
                created_by=_user_str(entity.get("createdBy")),
                api_source="v4",
            )
        )

    def _detail(self, uuid: str) -> dict[str, Any] | None:
        """The full template, whose vmSpec includes the guest tools the list drops."""
        if not uuid:
            return None
        try:
            return self.conn.v4_get(f"{V4_TEMPLATES}/{uuid}").get("data") or None
        except NutanixApiError:
            return None

    def _version_count(self, uuid: str) -> int | None:
        """How many versions the template has.

        Nothing on the template itself reports this, but the versions sub-collection
        totals them in its paging metadata — so ask for a single row and read the total
        rather than pulling every version spec.
        """
        if not uuid:
            return None
        try:
            payload = self.conn.v4_get(f"{V4_TEMPLATES}/{uuid}/versions", params={"$limit": 1})
        except NutanixApiError:
            return None
        total = (payload.get("metadata") or {}).get("totalAvailableResults")
        return total if isinstance(total, int) else None


class OvaCollector(Collector):
    name = "ovas"
    label = "OVAs"

    def collect(self, env: Any) -> None:
        if self.conn.v4_ready("vms") and self._collect_v4(env):
            env.collection_notes[self.name] = "ok"
            return
        if self._collect_v3(env):
            env.collection_notes[self.name] = "ok"
        else:
            env.collection_notes[self.name] = "skipped: OVA API not available"

    # ─── v4 ────────────────────────────────────────────────────────

    def _collect_v4(self, env: Any) -> bool:
        try:
            entities = self.conn.v4_list(V4_OVAS)
        except NutanixApiError:
            return False
        # Clusters are collected in phase 1, so `env.clusters` is already populated and
        # the placement uuid the OVA carries can be named without a further request.
        cluster_names = {c.uuid: c.name for c in env.clusters if c.uuid and c.name}
        for entity in entities:
            uuid = entity.get("extId") or ""
            full = self._detail(uuid) or entity
            # v4 splits an OVA's virtual hardware into disks and cdRoms; only the disks
            # are disks. v3's disk_list conflated the two, but v3 does not serve a vm_spec
            # here at all, so there is no figure to stay consistent with.
            vm_config = full.get("vmConfig") or {}
            disks = [d for d in (vm_config.get("disks") or []) if d]
            cluster_uuid = next(
                (u for u in (full.get("clusterLocationExtIds") or []) if u), None
            )
            env.ovas.append(
                Ova(
                    uuid=uuid,
                    name=entity.get("name"),
                    size_bytes=entity.get("sizeBytes"),
                    disk_count=len(disks) or None,
                    # Fall back to the raw uuid so the column says *something* about
                    # placement when the cluster is not one this PC has registered.
                    cluster_name=(
                        cluster_names.get(cluster_uuid, cluster_uuid)
                        if cluster_uuid
                        else None
                    ),
                    created=entity.get("createTime"),
                    api_source="v4",
                )
            )
        return True

    def _detail(self, uuid: str) -> dict[str, Any] | None:
        """The full OVA — the list row omits both vmConfig and cluster placement."""
        if not uuid:
            return None
        try:
            return self.conn.v4_get(f"{V4_OVAS}/{uuid}").get("data") or None
        except NutanixApiError:
            return None

    # ─── v3 ────────────────────────────────────────────────────────

    def _collect_v3(self, env: Any) -> bool:
        """v3 `ovas/list` — present on every PC that has the OVA feature (pc.2020.7+);
        the v4 content endpoint above only exists on much newer builds."""
        try:
            entities = self.conn.v3_list("ovas")
        except NutanixApiError:
            return False
        for entity in entities:
            info = entity.get("info", {}) or {}
            metadata = entity.get("metadata", {}) or {}
            vm_spec = (
                (info.get("vm_spec") or {}).get("spec", {}).get("resources", {})
                if isinstance(info.get("vm_spec"), dict)
                else {}
            )
            cluster_refs = info.get("current_cluster_reference_list") or []
            cluster = None
            for ref in cluster_refs:
                if isinstance(ref, dict) and ref.get("name"):
                    cluster = ref["name"]
                    break
            env.ovas.append(
                Ova(
                    uuid=metadata.get("uuid") or entity.get("uuid", ""),
                    name=info.get("name") or entity.get("name"),
                    size_bytes=info.get("size_bytes"),
                    disk_count=len(vm_spec.get("disk_list") or []) or None,
                    cluster_name=cluster,
                    created=info.get("create_time") or metadata.get("creation_time"),
                    api_source="v3",
                )
            )
        return True


# ─── helpers ───────────────────────────────────────────────────────────


def _user_str(value: Any) -> str | None:
    """Extract a username from a v4 'created_by' value.

    Newer PC builds return a user *object* (sometimes wrapped in a '$reserved'
    envelope) where older ones returned a plain string; find the first plausible
    string identity inside.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("username", "userName", "name", "extId"):
            v = value.get(key)
            if isinstance(v, str) and v:
                return v
        for v in value.values():
            found = _user_str(v)
            # Prefer something that looks like an identity over reserved markers.
            if found and not found.startswith("$") and not found.startswith("v4"):
                return found
    if isinstance(value, list):
        for v in value:
            found = _user_str(v)
            if found:
                return found
    return None


def _guest_os(vm_spec: dict[str, Any], custom: dict[str, Any] | None = None) -> str | None:
    """Guest OS of a template's VM spec, spelled as the VM collector spells it.

    `guestTools.guestOsVersion` is the same string v3 returns as
    `guest_tools.nutanix_guest_tools.guest_os_version` and the VM collector already
    reports (e.g. "windows:64:WindowsServer2025Standard"), so a template and the VM it
    was captured from read identically in the report. The guest-customization `osType`
    and `guestOsType` are older spellings of the same idea and stay as fallbacks.
    """
    tools = vm_spec.get("guestTools") or {}
    guest_info = tools.get("guestInfo") or {}
    for candidate in (
        tools.get("guestOsVersion"),
        guest_info.get("guestOsFullName"),
        (custom or {}).get("osType"),
        vm_spec.get("guestOsType"),
    ):
        # v4 pads absent values with empty strings rather than omitting them.
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _vcpus(vm_spec: dict[str, Any]) -> int | None:
    sockets = vm_spec.get("numSockets")
    per = vm_spec.get("numCoresPerSocket") or 1
    if sockets:
        return sockets * per
    return vm_spec.get("numVcpus")


def _mem_bytes(vm_spec: dict[str, Any]) -> int | None:
    mib = vm_spec.get("memorySizeBytes")
    if mib:
        return mib
    m = vm_spec.get("memorySizeMib")
    return int(m) * 1024 * 1024 if m else None
