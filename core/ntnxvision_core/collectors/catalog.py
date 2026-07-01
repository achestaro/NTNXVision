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
            clusters = [
                self._name_of(ref.get("cluster_reference", {}))
                for ref in resources.get("current_cluster_reference_list", []) or []
                if isinstance(ref, dict)
            ]
            env.images.append(
                Image(
                    uuid=entity.get("metadata", {}).get("uuid", ""),
                    name=status.get("name"),
                    image_type=resources.get("image_type"),
                    size_bytes=resources.get("size_bytes"),
                    architecture=resources.get("architecture"),
                    source=resources.get("source_uri"),
                    cluster_names=[c for c in clusters if c],
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
                    created_by=t.get("createdBy"),
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
        if data is None:
            env.collection_notes[self.name] = "skipped: OVA API not available"
            return
        for o in data:
            info = o.get("ovaInfo") or {}
            env.ovas.append(
                Ova(
                    uuid=o.get("extId") or o.get("uuid", ""),
                    name=o.get("name"),
                    size_bytes=o.get("sizeBytes") or info.get("sizeBytes"),
                    disk_count=len(o.get("diskList") or info.get("diskList") or []) or None,
                    cluster_name=self._name_of(o.get("clusterReference", {})),
                    created=o.get("createTime"),
                    api_source="v4",
                )
            )
        env.collection_notes[self.name] = "ok"

    def _fetch(self) -> list[dict[str, Any]] | None:
        for path in ("vmm/v4.0/content/ovas", "vmm/v4.0.b1/content/ovas"):
            try:
                return self.conn.v4_list(path)
            except NutanixApiError:
                continue
        return None


# ─── helpers ───────────────────────────────────────────────────────────


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
