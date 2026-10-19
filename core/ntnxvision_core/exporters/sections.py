"""The report sections ("resources") the user can include, exclude and reorder.

A report section is one chapter of the PDF / Word report (`content.Chapter.key`). The
catalogue below lists them in the default document order. Two are fixed: the Executive
Summary always opens the report and the Collection Coverage appendix always closes it
(the cover and the table of contents are not sections at all). Every other section can be
moved; a chapter's tables and figures, design figures included, move with it, and the
numbering is assigned after the order is applied (`content._number`).

`order(requested)` turns a requested order into a full one, forward compatible both
ways: unknown ids are ignored and ids the request leaves out are appended in the default
order, so an order saved by an older version still works after a section is added.

The Excel workbook follows the same choice where it maps cleanly: each object-type sheet
belongs to one section (`SHEET_SECTION`), so an excluded section drops its sheets and a
custom order regroups them. The summary sheets (Overview, Prism Central, Feature Usage,
Collection Notes, API Routing) are fixed.

The mirror of this catalogue for the app is `app/src/shared/reportSections.ts`
(`tests/test_report_sections.py` keeps the two in step).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from ntnxvision_core.models import Environment


@dataclass(frozen=True)
class ReportSection:
    id: str
    label: str
    #: The coarse export-dialog group (`ExportOptions.sections`) the chapter belongs to.
    group: str
    #: Fixed position: "first" or "last"; None = movable.
    fixed: str | None = None


#: Every report section, in the default document order.
CATALOG: tuple[ReportSection, ...] = (
    ReportSection("executive_summary", "Executive Summary", "executive_summary", "first"),
    ReportSection("licensing", "Licensing & Feature Usage", "licensing_lcm"),
    ReportSection("assumptions", "Assumptions & Constraints", "collection_notes"),
    ReportSection("prism_central", "Prism Central: Management & Sizing", "clusters_hosts"),
    ReportSection("logical_design", "Logical Design", "clusters_hosts"),
    ReportSection("physical_design", "Physical Design", "clusters_hosts"),
    ReportSection("workloads", "Workloads", "workloads_storage"),
    ReportSection("catalog", "Catalog: Templates, Images & OVAs", "workloads_storage"),
    ReportSection("nus", "Unified Storage Services (NUS)", "platform_services"),
    ReportSection("networking", "Networking", "security_networking"),
    ReportSection("data_protection", "Data Protection & Disaster Recovery", "data_protection"),
    ReportSection("security", "Security Posture (Flow)", "security_networking"),
    ReportSection("ndb", "Database Services (NDB)", "platform_services"),
    ReportSection("identity", "Identity & Access Management", "security_networking"),
    ReportSection("automation", "Automation & Reporting (NCM)", "platform_services"),
    ReportSection("health", "Health & Alerts", "clusters_hosts"),
    ReportSection("lcm", "Platform Lifecycle (LCM)", "licensing_lcm"),
    ReportSection("appendix", "Collection Coverage & API Routing", "collection_notes", "last"),
)

BY_ID = {s.id: s for s in CATALOG}
DEFAULT_ORDER: tuple[str, ...] = tuple(s.id for s in CATALOG)
_FIRST = tuple(s.id for s in CATALOG if s.fixed == "first")
_LAST = tuple(s.id for s in CATALOG if s.fixed == "last")


def order(requested: Iterable[Any] | None) -> tuple[str, ...]:
    """The full section order for `requested`: known movable ids in the requested order
    (duplicates dropped), then the missing ones in default order; fixed sections keep
    their place. None gives the default order."""
    if requested is None:
        return DEFAULT_ORDER
    seen: list[str] = []
    for raw in requested:
        sid = str(raw)
        s = BY_ID.get(sid)
        if s is not None and s.fixed is None and sid not in seen:
            seen.append(sid)
    rest = [s.id for s in CATALOG if s.fixed is None and s.id not in seen]
    return (*_FIRST, *seen, *rest, *_LAST)


def rank(requested: Iterable[Any] | None) -> dict[str, int]:
    """Section id -> position in the full order (for sorting)."""
    return {sid: i for i, sid in enumerate(order(requested))}


# ─── availability (which sections have data in a snapshot) ─────────────────────

_ALWAYS: Callable[[Environment], bool] = lambda _env: True  # noqa: E731

#: Whether a section has content for `env`; mirrors when the builders in content.py
#: return an empty chapter (tests/test_report_sections.py checks they agree).
_HAS_DATA: dict[str, Callable[[Environment], bool]] = {
    "executive_summary": _ALWAYS,
    "licensing": _ALWAYS,
    "assumptions": _ALWAYS,
    "prism_central": lambda e: e.source_type != "prism_element",
    "logical_design": _ALWAYS,
    "physical_design": lambda e: bool(e.hosts or e.storage_containers),
    "workloads": lambda e: bool(e.vms or e.system_vms),
    "catalog": lambda e: bool(e.templates or e.images or e.ovas),
    "nus": lambda e: bool(e.file_servers or e.file_shares or e.object_stores or e.buckets
                          or e.volume_groups),
    "networking": lambda e: bool(e.subnets or e.port_groups or e.vpcs),
    "data_protection": lambda e: bool(e.protection_policies or e.recovery_plans),
    "security": lambda e: bool(e.security_policies or e.categories),
    "ndb": lambda e: bool(e.databases or e.database_servers or e.time_machines),
    "identity": lambda e: bool(e.users or e.roles or e.projects),
    "automation": lambda e: bool(e.playbooks or e.ncm_reports),
    "health": lambda e: bool(e.alerts),
    "lcm": lambda e: bool(e.lcm_entities),
    "appendix": _ALWAYS,
}


def availability(env: Environment) -> list[dict[str, Any]]:
    """Each section with whether this snapshot has content for it, and why not."""
    out: list[dict[str, Any]] = []
    for s in CATALOG:
        ok = _HAS_DATA[s.id](env)
        hint = None
        if not ok:
            hint = ("Not part of a Prism Element collection"
                    if s.id == "prism_central" else "No data in this snapshot")
        out.append({"id": s.id, "available": ok, "hint": hint})
    return out


# ─── Excel: the section each object-type sheet belongs to ──────────────────────

#: TableSpec.type -> section id. Sheets not listed here are fixed summary sheets.
SHEET_SECTION: dict[str, str] = {
    "clusters": "logical_design",
    "capacity": "physical_design",
    "hosts": "physical_design",
    "storage_containers": "physical_design",
    "external_storages": "physical_design",
    "pc_vms": "prism_central",
    "vms": "workloads",
    "system_vms": "workloads",
    "subnets": "networking",
    "port_groups": "networking",
    "virtual_switches": "networking",
    "host_nics": "networking",
    "vpcs": "networking",
    "floating_ips": "networking",
    "templates": "catalog",
    "images_only": "catalog",
    "ovas": "catalog",
    "protection_policies": "data_protection",
    "recovery_plans": "data_protection",
    "availability_zones": "data_protection",
    "remote_sites": "data_protection",
    "alerts": "health",
    "lcm_entities": "lcm",
    "security_policies": "security",
    "categories": "security",
    "service_groups": "security",
    "address_groups": "security",
    "file_servers": "nus",
    "file_shares": "nus",
    "object_stores": "nus",
    "buckets": "nus",
    "volume_groups": "nus",
    "databases": "ndb",
    "database_servers": "ndb",
    "time_machines": "ndb",
    "users": "identity",
    "roles": "identity",
    "projects": "identity",
    "playbooks": "automation",
    "ncm_reports": "automation",
    "licenses": "licensing",
}


__all__ = ["BY_ID", "CATALOG", "DEFAULT_ORDER", "SHEET_SECTION", "ReportSection",
           "availability", "order", "rank"]
