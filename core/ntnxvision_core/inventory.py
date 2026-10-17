"""Inventory browsing: server-side filter / sort / page over a snapshot, and object detail.

Columns come from `exporters.tables` — the same definitions the Excel workbook uses — so
the grid and the report agree. Each (snapshot, mtime, type) is indexed once: every row's
cells, sort keys, lower-cased search text and facet labels are precomputed, which keeps a
query on a 20,000-VM snapshot to a filtered list comprehension plus one sort.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

from ntnxvision_core.capacity import coerce_ratio, target_label
from ntnxvision_core.exporters.tables import (
    INVENTORY_TYPES,
    SPECS,
    Col,
    Ctx,
    TableSpec,
    joined,
)
from ntnxvision_core.models import Environment
from ntnxvision_core.numfmt import NA, PLAIN_HEADERS, fmt_count, fmt_ratio

GIB = 1024**3
TIB = 1024**4
MAX_LIMIT = 50_000


# ─── formatting (display strings for detail panes, compare rows) ───────


def fmt_bytes(value: int | float | None) -> str | None:
    if value is None:
        return None
    v = float(value)
    for unit, size in (("PB", 1024**5), ("TB", TIB), ("GB", GIB), ("MB", 1024**2)):
        if abs(v) >= size:
            n = v / size
            return f"{n:.1f} {unit}".replace(".0 ", " ") if n < 100 else f"{n:,.0f} {unit}"
    return f"{int(v):,} B"


def display(col: Col, raw: Any) -> str | None:
    """A column value as a display string (bytes → "64 GB", bools → Yes/No, …)."""
    if raw is None or raw == "" or raw == []:
        return None
    if col.kind == "bytes":
        return fmt_bytes(raw)
    if col.kind == "ratio":
        return fmt_ratio(raw)
    if col.cell is not None:
        cell = col.cell(raw)
        if isinstance(cell, dict):
            return str(cell.get("label"))
        return None if cell is None else str(cell)
    if isinstance(raw, bool):
        return "Yes" if raw else "No"
    if isinstance(raw, list):
        return joined(raw) or None
    if isinstance(raw, (int, float)) and col.header not in PLAIN_HEADERS \
            and col.label not in PLAIN_HEADERS:
        return fmt_count(raw)
    return str(raw)


# ─── index ─────────────────────────────────────────────────────────────


class _Row:
    __slots__ = ("id", "cells", "sort", "text", "facets", "tone", "obj")

    def __init__(self, id_: str, cells: dict[str, Any], sort: dict[str, Any], text: str,
                 facets: dict[str, str], tone: str | None, obj: Any) -> None:
        self.id = id_
        self.cells = cells
        self.sort = sort
        self.text = text
        self.facets = facets
        self.tone = tone
        self.obj = obj


class _Index:
    def __init__(self, spec: TableSpec, env: Environment, ctx: Ctx) -> None:
        self.spec = spec
        self.env = env
        cols = spec.ui_cols
        search_cols = [c for c in spec.cols if c.search]
        facet_cols = [c for c in spec.cols if c.facet and c.label]
        self.rows: list[_Row] = []
        self.by_id: dict[str, _Row] = {}
        seen: dict[str, int] = {}
        for obj in spec.items(env):
            raw = {c.key: c.get(obj, ctx) for c in spec.cols}
            cells: dict[str, Any] = {}
            sort: dict[str, Any] = {}
            for c in cols:
                value = raw[c.key]
                if c.cell is not None:
                    cells[c.key] = c.cell(value)
                elif c.kind in ("number", "bytes", "ratio"):
                    cells[c.key] = value if isinstance(value, (int, float)) else None
                elif isinstance(value, bool):
                    cells[c.key] = "Yes" if value else "No"
                elif value in ("", None):
                    cells[c.key] = None
                else:
                    cells[c.key] = value if isinstance(value, (int, float)) else str(value)
                sort[c.key] = _sort_key(cells[c.key])
            text = " ".join(str(raw[c.key]) for c in search_cols
                            if raw[c.key] not in (None, "")).lower()
            facets = {}
            for c in facet_cols:
                label = display(c, raw[c.key])
                facets[c.key] = label if label is not None else NA
            base = str(spec.id(obj) or spec.name(obj) or len(self.rows))
            n = seen.get(base, 0)
            seen[base] = n + 1
            row_id = base if n == 0 else f"{base}#{n}"
            row = _Row(row_id, cells, sort, text, facets, _tone(spec, obj, cells), obj)
            self.rows.append(row)
            self.by_id[row_id] = row
        self.facets = []
        for c in facet_cols:
            counts: dict[str, int] = {}
            for row in self.rows:
                counts[row.facets[c.key]] = counts.get(row.facets[c.key], 0) + 1
            options = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:50]
            self.facets.append({
                "key": c.key, "label": c.label,
                "options": [{"value": v, "label": v, "count": n} for v, n in options],
            })


def _sort_key(cell: Any) -> Any:
    if isinstance(cell, dict):
        return str(cell.get("label") or "").lower()
    if isinstance(cell, str):
        return cell.lower()
    return cell


def _tone(spec: TableSpec, obj: Any, cells: dict[str, Any]) -> str | None:
    if spec.type == "alerts":
        if not getattr(obj, "resolved", False):
            return {"CRITICAL": "error", "WARNING": "warn"}.get(str(obj.severity))
        return None
    if spec.type == "lcm_entities" and getattr(obj, "update_available", None):
        return "warn"
    if spec.type == "licenses" and str(getattr(obj, "compliance", "") or "").upper() == "EXPIRED":
        return "error"
    if spec.type == "hosts" and str(obj.maintenance_state or "NORMAL").upper() != "NORMAL":
        return "warn"
    return None


class InventoryService:
    """Holds the per-snapshot indexes. Thread-safe; bounded."""

    def __init__(self, capacity: int = 24) -> None:
        self._indexes: OrderedDict[tuple[int, str, float], _Index] = OrderedDict()
        self._ctx: OrderedDict[tuple[int, float], Ctx] = OrderedDict()
        self._lock = threading.Lock()
        self.capacity = capacity

    def ctx(self, env: Environment, vcpu_ratio: Any = None) -> Ctx:
        ratio = coerce_ratio(vcpu_ratio)
        key = (id(env), ratio)
        with self._lock:
            ctx = self._ctx.get(key)
            if ctx is not None and ctx.env is env:
                return ctx
        ctx = Ctx(env, ratio)
        with self._lock:
            self._ctx[key] = ctx
            while len(self._ctx) > 8:
                self._ctx.popitem(last=False)
        return ctx

    def index(self, env: Environment, object_type: str, vcpu_ratio: Any = None) -> _Index:
        spec = SPECS.get(object_type)
        if spec is None:
            raise KeyError(f"Unknown object type: {object_type}")
        ratio = coerce_ratio(vcpu_ratio)
        key = (id(env), object_type, ratio)
        with self._lock:
            idx = self._indexes.get(key)
        if idx is not None and idx.env is env:
            return idx
        idx = _Index(spec, env, self.ctx(env, ratio))
        with self._lock:
            self._indexes[key] = idx
            self._indexes.move_to_end(key)
            while len(self._indexes) > self.capacity:
                self._indexes.popitem(last=False)
        return idx

    def clear(self) -> None:
        with self._lock:
            self._indexes.clear()
            self._ctx.clear()

    # ─── query ─────────────────────────────────────────────────────

    def query(
        self,
        env: Environment,
        object_type: str,
        filter_: dict[str, Any] | None = None,
        sort: dict[str, Any] | None = None,
        page: dict[str, Any] | None = None,
        vcpu_ratio: Any = None,
    ) -> dict[str, Any]:
        if object_type not in SPECS:
            object_type = "vms"
        idx = self.index(env, object_type, vcpu_ratio)
        spec = idx.spec
        filter_ = filter_ or {}
        text = str(filter_.get("text") or "").strip().lower()
        facets = {k: str(v) for k, v in (filter_.get("facets") or {}).items() if v not in
                  (None, "")}
        rows = idx.rows
        if text:
            terms = text.split()
            rows = [r for r in rows if all(t in r.text for t in terms)]
        for key, value in facets.items():
            rows = [r for r in rows if r.facets.get(key) == value]
        if sort and sort.get("key") in {c.key for c in spec.ui_cols}:
            key = sort["key"]
            present = [r for r in rows if r.sort.get(key) is not None]
            missing = [r for r in rows if r.sort.get(key) is None]
            try:
                present.sort(key=lambda r: r.sort[key], reverse=sort.get("dir") == "desc")
            except TypeError:  # mixed types in one column — compare as text
                present.sort(key=lambda r: str(r.sort[key]), reverse=sort.get("dir") == "desc")
            rows = present + missing
        page = page or {}
        offset = max(0, int(page.get("offset") or 0))
        limit = min(MAX_LIMIT, max(1, int(page.get("limit") or 25)))
        window = rows[offset: offset + limit]
        return {
            "object_type": object_type,
            "label": spec.label,
            "unit_plural": spec.unit_plural,
            "facts": _facts(spec, env, len(idx.rows)),
            "columns": [_column(c) for c in spec.ui_cols],
            "rows": [{"id": r.id, "tone": r.tone, "cells": r.cells} for r in window],
            "total": len(idx.rows),
            "matching": len(rows),
            "offset": offset,
            "limit": limit,
            "type_counts": type_counts(env),
            "facets": idx.facets,
        }

    def detail(self, env: Environment, object_type: str, object_id: str,
               vcpu_ratio: Any = None) -> dict[str, Any]:
        idx = self.index(env, object_type, vcpu_ratio)
        row = idx.by_id.get(object_id)
        if row is None:
            raise KeyError(f"No {idx.spec.singular.lower()} with id {object_id}")
        return object_detail(env, idx.spec, row.obj, row.id, self.ctx(env, vcpu_ratio))


def _column(c: Col) -> dict[str, Any]:
    out: dict[str, Any] = {"key": c.key, "label": c.label, "kind": c.kind}
    if not c.sortable:
        out["sortable"] = False
    return out


def _facts(spec: TableSpec, env: Environment, total: int) -> str:
    parts = [f"{fmt_count(total)} {spec.unit_plural}"]
    if spec.type in ("vms", "hosts", "storage_containers", "subnets", "volume_groups",
                     "system_vms"):
        n = len(env.clusters)
        parts.append(f"{fmt_count(n)} cluster{'s' if n != 1 else ''}")
    if spec.type == "vms":
        on = sum(1 for v in env.vms if (v.power_state or "").upper() == "ON")
        parts.append(f"{fmt_count(on)} powered on")
    if spec.type == "clusters":
        parts.append(f"{fmt_count(len(env.hosts))} hosts")
    if env.is_combined:
        parts.append(f"{fmt_count(len(env.source_environments))} Prism Centrals")
    return " · ".join(parts)


def type_counts(env: Environment) -> list[dict[str, Any]]:
    """The left-hand object-type list: count per type, None when not collected."""
    out = []
    selected = None
    if env.run and isinstance(env.run.get("areas"), list):
        selected = {a.get("id") for a in env.run["areas"]}
    for type_id in INVENTORY_TYPES:
        spec = SPECS[type_id]
        count: int | None = len(spec.items(env))
        if count == 0 and not _collected(env, spec, selected):
            count = None
        out.append({"type": type_id, "label": spec.label, "group": spec.group or "Other",
                    "count": count})
    return out


def _collected(env: Environment, spec: TableSpec, selected: set[Any] | None) -> bool:
    notes = env.collection_notes
    for key in spec.note_keys:
        values = [v for k, v in notes.items() if k == key or k.endswith(f": {key}")]
        if any(v == "ok" or v.startswith("ok") for v in values):
            return True
    return False


# ─── object detail ─────────────────────────────────────────────────────


def object_detail(env: Environment, spec: TableSpec, obj: Any, row_id: str, ctx: Ctx
                  ) -> dict[str, Any]:
    special = _DETAIL.get(spec.type)
    if special is not None:
        out = special(env, obj, ctx)
    else:
        out = {"fields": _generic_fields(spec, obj, ctx)}
    name = spec.name(obj)
    out.setdefault("status", None)
    out.setdefault("context", spec.where(obj) or getattr(obj, "cluster_name", None))
    out.setdefault("categories", [])
    out.setdefault("notes", [])
    out.setdefault("prism_url", None)
    if getattr(obj, "source_pc", None):
        out["fields"].insert(0, {"label": "Prism Central", "value": obj.source_pc})
    return {"id": row_id, "object_type": spec.type, "type_label": spec.singular,
            "name": str(name), **out}


def _generic_fields(spec: TableSpec, obj: Any, ctx: Ctx) -> list[dict[str, Any]]:
    fields = []
    seen: set[str] = set()
    for c in spec.cols:
        label = c.label or c.header or c.key
        if label in seen or c.kind == "name" or c.key in ("name", "ip_ui"):
            continue
        seen.add(label)
        value = display(c, c.get(obj, ctx))
        if c.key == "api" and value:
            label = "Collected via"
        fields.append({"label": _clean_label(label), "value": value,
                       "mono": c.kind in ("mono", "number", "bytes") or c.key in ("uuid",)})
    return fields


def _field_cols(spec: TableSpec) -> list[Col]:
    """The columns `_generic_fields` turns into fields, in the same order."""
    out, seen = [], set()
    for c in spec.cols:
        label = c.label or c.header or c.key
        if label in seen or c.kind == "name" or c.key in ("name", "ip_ui"):
            continue
        seen.add(label)
        out.append(c)
    return out


def _clean_label(label: str) -> str:
    for unit in (" (GiB)", " (GB)", " (s)"):
        label = label.replace(unit, "")
    return label


def _pc_host(env: Environment, obj: Any) -> str | None:
    host = getattr(obj, "source_pc", None) or env.prism_central.host
    if not host or " " in host:
        return None
    return host


def _prism_url(env: Environment, obj: Any, page: str) -> str | None:
    host = _pc_host(env, obj)
    if host is None:
        return None
    if env.source_type == "prism_element":
        return f"https://{host}:9440/console/#page/vms"
    return f"https://{host}:9440/console/#page/explore/s/{page}"


def _vm_detail(env: Environment, vm: Any, ctx: Ctx) -> dict[str, Any]:
    disks = [d for d in vm.disks if not d.is_cdrom]
    total = sum(d.disk_size_bytes or 0 for d in disks)
    policy = ctx.vm_policy(vm)
    protection = ctx.vm_protection(vm)
    ips = [ip for n in vm.nics for ip in n.ip_addresses]
    on = (vm.power_state or "").upper() == "ON"
    fields: list[dict[str, Any]] = [
        {"label": "Host", "value": vm.host_name},
        {"label": "Cluster", "value": vm.cluster_name},
        {"label": "Size", "value": f"{fmt_count(vm.vcpu_total or NA)} vCPU · "
                                   f"{fmt_bytes(vm.memory_bytes) or NA}", "mono": True},
        {"label": "Storage", "value": f"{fmt_bytes(total) or '0 GB'} · {fmt_count(len(disks))} "
                                      f"disk{'s' if len(disks) != 1 else ''}", "mono": True},
        {"label": "OS", "value": vm.guest_os},
        {"label": "IP", "value": ", ".join(ips) or None, "mono": True},
        {"label": "Protection", "value": protection},
    ]
    for i, d in enumerate(disks, start=1):
        fields.append({"label": f"Disk {i}", "value": " · ".join(x for x in (
            d.label, fmt_bytes(d.disk_size_bytes), d.storage_container_name) if x),
            "mono": True})
    for i, n in enumerate(vm.nics, start=1):
        parts = [n.subnet_name, f"VLAN {n.vlan_id}" if n.vlan_id is not None else None,
                 ", ".join(n.ip_addresses) or None, n.mac_address]
        fields.append({"label": f"NIC {i}", "value": " · ".join(p for p in parts if p),
                       "mono": True})
    fields += [
        {"label": "Hypervisor", "value": vm.hypervisor},
        {"label": "Boot", "value": vm.boot_type},
        {"label": "Created", "value": vm.creation_time},
        {"label": "UUID", "value": vm.uuid, "mono": True},
        {"label": "Collected via", "value": vm.api_source},
    ]
    notes = []
    if not policy and protection == "None" and vm.vm_type == "USER":
        notes.append({"tone": "warn", "text": "Not in any protection policy or recovery plan."})
    if not on:
        notes.append({"tone": "idle", "text": "Powered off at collection time."})
    if not ips and on:
        notes.append({"tone": "idle", "text": "No IP address reported (guest tools may be "
                                              "missing)."})
    return {
        "status": {"tone": "ok" if on else "idle", "label": "Powered on" if on
                   else "Powered off"},
        "context": vm.cluster_name,
        "fields": fields,
        "categories": [f"{k}: {v}" for k, vals in vm.categories.items() for v in vals],
        "notes": notes,
        "prism_url": _prism_url(env, vm, "vms"),
    }


def _host_detail(env: Environment, h: Any, ctx: Ctx) -> dict[str, Any]:
    out: dict[str, Any] = {"fields": _generic_fields(SPECS["hosts"], h, ctx)}
    vms = [v for v in env.vms if (v.host_uuid and v.host_uuid == h.uuid)
           or (v.host_name and v.host_name == h.name)]
    on = sum(1 for v in vms if (v.power_state or "").upper() == "ON")
    out["fields"].insert(1, {"label": "VMs on host",
                             "value": f"{fmt_count(len(vms))} ({fmt_count(on)} on)",
                             "mono": True})
    normal = str(h.maintenance_state or "NORMAL").upper() == "NORMAL"
    out["status"] = {"tone": "ok" if normal else "warn",
                     "label": "Normal" if normal else "In maintenance"}
    out["prism_url"] = _prism_url(env, h, "hosts")
    return out


#: Capacity column keys the detail pane always renders through `capacity_fields` (the
#: Clusters spec no longer carries them; kept so a re-added one is not shown twice).
_CAPACITY_KEYS = frozenset({"cpu_ratio", "cpu_ratio_n1", "vcpu_total", "vcpu_total_n1",
                            "storage_ratio", "storage_ratio_n1", "storage_total",
                            "storage_total_n1"})


def _with_n1(full: str, n1: str | None) -> str:
    return f"{full} · N-1 {n1 or NA}"


def capacity_fields(cap: Any) -> list[dict[str, Any]]:
    """The cluster detail pane's capacity block: short values, N-1 after a "·"."""
    if cap is None:
        return []

    def count(v: Any) -> str:
        return NA if v is None else fmt_count(int(round(v)))

    def n1(full: str, value: Any, text: str) -> str:
        return _with_n1(full, text if value is not None else None)

    return [
        {"label": "Usable cores", "value": (
            f"{count(cap.usable_cores)} of {count(cap.physical_cores)}"
            if cap.usable_cores is not None else NA), "mono": True},
        {"label": "CVM vCPUs", "value": count(cap.cvm_vcpus), "mono": True},
        {"label": "Target ratio", "value": target_label(cap.target_ratio, cap.target_custom),
         "mono": True},
        {"label": "vCPU Total",
         "value": n1(count(cap.vcpu_total), cap.vcpu_total_n1, count(cap.vcpu_total_n1)),
         "mono": True},
        {"label": "vCPU allocated", "value": f"{fmt_count(cap.provisioned_vcpus)} · "
                                             f"{fmt_count(cap.provisioned_vcpus_on)} on",
         "mono": True},
        {"label": "vCPU:pCore", "value": n1(fmt_ratio(cap.cpu_ratio), cap.cpu_ratio_n1,
                                            fmt_ratio(cap.cpu_ratio_n1)), "mono": True},
        {"label": "vCPU:pCore (on)", "value": fmt_ratio(cap.cpu_ratio_on), "mono": True},
        {"label": "Raw storage", "value": fmt_bytes(cap.raw_bytes) or NA, "mono": True},
        {"label": f"Storage Total (RF {cap.rf or NA})", "value": n1(
            fmt_bytes(cap.storage_total_bytes) or NA, cap.storage_total_n1_bytes,
            fmt_bytes(cap.storage_total_n1_bytes) or NA), "mono": True},
        {"label": "Provisioned disk", "value": fmt_bytes(cap.provisioned_disk_bytes) or "0 GB",
         "mono": True},
        {"label": "Storage ratio", "value": n1(fmt_ratio(cap.storage_ratio),
                                               cap.storage_ratio_n1,
                                               fmt_ratio(cap.storage_ratio_n1)), "mono": True},
    ]


def _cluster_detail(env: Environment, c: Any, ctx: Ctx) -> dict[str, Any]:
    fields = [f for f, col in zip(
        _generic_fields(SPECS["clusters"], c, ctx), _field_cols(SPECS["clusters"]),
        strict=True) if col.key not in _CAPACITY_KEYS]
    ctrs = [s for s in env.storage_containers if s.cluster_uuid == c.uuid
            or (not s.cluster_uuid and s.cluster_name == c.name)]
    # Containers share the cluster's storage pool and each reports it as its ceiling.
    cap = max((s.max_capacity_bytes or 0 for s in ctrs), default=0)
    used = sum(s.used_bytes or 0 for s in ctrs)
    fields.insert(3, {"label": "Storage", "value": f"{fmt_bytes(cap) or NA} · "
                                                   f"{round(100 * used / cap) if cap else 0}% used",
                      "mono": True})
    fields.insert(4, {"label": "Containers", "value": fmt_count(len(ctrs)), "mono": True})
    capacity = ctx.capacity(c)
    fields += capacity_fields(capacity)
    notes = []
    if c.encryption_enabled is False:
        notes.append({"tone": "warn", "text": "Data-at-rest encryption is off."})
    for text in (capacity.notes if capacity is not None else []):
        tone = "warn" if ("exceed" in text or "thin provisioning" in text) else "idle"
        notes.append({"tone": tone, "text": text})
    return {"fields": fields, "status": {"tone": "ok", "label": "Collected"},
            "context": c.aos_version and f"AOS {c.aos_version}", "notes": notes,
            "prism_url": _prism_url(env, c, "clusters")}


def _policy_detail(env: Environment, p: Any, ctx: Ctx) -> dict[str, Any]:
    # Categories render as chips below, not as a field as well.
    fields = [f for f in _generic_fields(SPECS["protection_policies"], p, ctx)
              if f["label"] != "Categories"]
    cats = set(p.categories)
    members = [v for v in env.vms
               if any(f"{k}:{val}" in cats for k, vals in v.categories.items() for val in vals)]
    fields.insert(0, {"label": "Protected VMs", "value": fmt_count(len(members)),
                      "mono": True})
    return {"fields": fields, "categories": list(p.categories),
            "status": {"tone": "ok", "label": p.replication_type or "Policy"}}


def _alert_detail(env: Environment, a: Any, ctx: Ctx) -> dict[str, Any]:
    fields = _generic_fields(SPECS["alerts"], a, ctx)
    fields.insert(0, {"label": "Message", "value": a.message})
    tone = {"CRITICAL": "error", "WARNING": "warn"}.get(str(a.severity), "live")
    return {"fields": fields, "status": {"tone": "ok" if a.resolved else tone,
                                         "label": "Resolved" if a.resolved
                                         else str(a.severity or "Open").title()},
            "categories": list(a.classifications)}


_DETAIL = {
    "vms": _vm_detail,
    "system_vms": _vm_detail,
    "hosts": _host_detail,
    "clusters": _cluster_detail,
    "protection_policies": _policy_detail,
    "alerts": _alert_detail,
}
