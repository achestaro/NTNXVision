"""Prism Central catalog collector — Images, Templates, and OVAs.

Images come from the v3 `images/list` endpoint (universally present). VM Templates and
OVAs are v4 features (VMM namespace); this collector tries their v3/v4 endpoints and
degrades gracefully when they are not available on the target Prism Central.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import Image, Ova, Template

GIB = 1024**3


class ImageCollector(Collector):
    name = "images"
    label = "Images"

    def collect(self, env: Any) -> None:
        try:
            entities = self.conn.v3_list("images")
        except NutanixApiError:
            env.collection_notes[self.name] = "skipped: images endpoint not available"
            return
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
        env.collection_notes[self.name] = "ok"


class TemplateCollector(Collector):
    name = "templates"
    label = "VM Templates"

    def collect(self, env: Any) -> None:
        data = self._fetch()
        if data is None:
            env.collection_notes[self.name] = "skipped: templates API not available"
            return
        for t in data:
            cfg = t.get("guestCustomizationConfig") or {}
            vm_spec = (t.get("templateVersionSpec") or {}).get("vmSpec") or t.get("vmSpec") or {}
            active = t.get("activeVersionId") or (
                t.get("templateVersionSpec") or {}
            ).get("versionName")
            env.templates.append(
                Template(
                    uuid=t.get("extId") or t.get("uuid", ""),
                    name=t.get("templateName") or t.get("name"),
                    description=t.get("templateDescription") or t.get("description"),
                    guest_os=cfg.get("osType") or vm_spec.get("guestOsType"),
                    num_vcpus=_vcpus(vm_spec),
                    memory_bytes=_mem_bytes(vm_spec),
                    active_version=str(active) if active is not None else None,
                    version_count=t.get("versionCount"),
                    created_by=_user_str(t.get("createdBy")),
                    api_source="v4",
                )
            )
        env.collection_notes[self.name] = "ok"

    def _fetch(self) -> list[dict[str, Any]] | None:
        # v4 VMM templates endpoint (pc.2024.3+/AOS 7.0+).
        for path in ("vmm/v4.0/content/templates", "vmm/v4.0.b1/content/templates"):
            try:
                return self.conn.v4_list(path)
            except NutanixApiError:
                continue
        return None


class OvaCollector(Collector):
    name = "ovas"
    label = "OVAs"

    def collect(self, env: Any) -> None:
        data = self._fetch()
        if data is not None:
            for o in data:
                info = o.get("ovaInfo") or {}
                env.ovas.append(
                    Ova(
                        uuid=o.get("extId") or o.get("uuid", ""),
                        name=o.get("name"),
                        size_bytes=o.get("sizeBytes") or info.get("sizeBytes"),
                        disk_count=len(o.get("diskList") or info.get("diskList") or [])
                        or None,
                        cluster_name=self._name_of(o.get("clusterReference", {})),
                        created=o.get("createTime"),
                        api_source="v4",
                    )
                )
            env.collection_notes[self.name] = "ok"
            return
        if self._collect_v3(env):
            env.collection_notes[self.name] = "ok"
        else:
            env.collection_notes[self.name] = "skipped: OVA API not available"

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

    def _fetch(self) -> list[dict[str, Any]] | None:
        for path in ("vmm/v4.0/content/ovas", "vmm/v4.0.b1/content/ovas"):
            try:
                return self.conn.v4_list(path)
            except NutanixApiError:
                continue
        return None


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
