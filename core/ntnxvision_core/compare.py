"""Snapshot comparison: what changed between two collections.

Objects are matched per type by their stable id (uuid / extId) — the same id Nutanix
keeps across collections — with (name, place) as the fallback for the few types that
have none. For matched objects every column marked ``diff`` in `exporters.tables` is
compared on its display value, so a change reads exactly as the report shows it
("32 GB" → "64 GB", not a raw byte count).

Raw changes are then **grouped** for the screen:

* consecutive added (or removed) objects of one type in one place whose names share a
  stem collapse into one row — ``k8s-worker-09 … 40`` with ``count`` 32;
* the same field change (same before → after) on several objects of one type in one place
  collapses likewise.

Summary counts are per object, never per row. `export_comparison` writes the ungrouped
list (one row per object and field) plus a summary sheet, styled like the inventory
workbook (Runway report theme), or the same rows as CSV.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from ntnxvision_core.exporters.tables import COMPARE_TYPES, SPECS, Ctx, TableSpec
from ntnxvision_core.inventory import display, fmt_bytes
from ntnxvision_core.models import Environment
from ntnxvision_core.numfmt import NA, fmt_count

_TRAILING_NUM = re.compile(r"^(.*?)(\d+)$")
_KIND_ORDER = {"changed": 0, "added": 1, "removed": 2}


def _where(spec: TableSpec, obj: Any, env: Environment) -> str:
    place = spec.where(obj)
    if place:
        return str(place)
    source = getattr(obj, "source_pc", None) or env.prism_central.host or ""
    return source.split(".")[0] if source else NA


def _key(spec: TableSpec, obj: Any, env: Environment) -> str:
    ident = spec.id(obj)
    if ident:
        return str(ident)
    return f"{spec.name(obj)}@{_where(spec, obj, env)}"


def _left_out(env: Environment) -> tuple[set[str], set[str]]:
    """(uuids, lower-cased names) of the registered clusters `env`'s run left out."""
    scope = env.cluster_scope
    if scope is None:
        return set(), set()
    kept = {(c.name or "").casefold() for c in scope.selected}
    return ({c.uuid for c in scope.excluded},
            {(c.name or "").casefold() for c in scope.excluded} - kept - {""})


def _on_left_out(spec: TableSpec, obj: Any, left_out: tuple[set[str], set[str]]) -> bool:
    uuids, names = left_out
    if not uuids:
        return False
    if spec.type == "clusters":
        uuid, name = getattr(obj, "uuid", None), getattr(obj, "name", None)
    else:
        uuid, name = getattr(obj, "cluster_uuid", None), getattr(obj, "cluster_name", None)
    if uuid:
        return str(uuid) in uuids
    if not name:
        return False
    return str(name).casefold() in names or str(name) in uuids


def raw_changes(base: Environment, cur: Environment,
                stats: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One entry per added / removed object and per changed field (ungrouped).

    Snapshots can cover different clusters (cluster scope). An object on a cluster the
    *other* run left out is not "added" or "removed": it was simply not looked at. Those
    are skipped and counted in ``stats["out_of_scope"]`` so the comparison can say so.
    """
    base_ctx, cur_ctx = Ctx(base), Ctx(cur)
    out: list[dict[str, Any]] = []
    base_left_out, cur_left_out = _left_out(base), _left_out(cur)
    hidden = 0
    for type_id in COMPARE_TYPES:
        spec = SPECS[type_id]
        # A type one of the two runs did not collect (area unselected, product not
        # deployed, collector failed) has no "before" or "after" — diffing it would
        # report every object as added or removed.
        if not (collected(base, spec) and collected(cur, spec)):
            continue
        before = {_key(spec, o, base): o for o in spec.items(base)}
        after = {_key(spec, o, cur): o for o in spec.items(cur)}
        # Fallback match by (name, place) for objects whose id changed between runs
        # (ESXi VMs re-registered, a PE-mode uuid vs a PC-mode one).
        unmatched_before = {k: o for k, o in before.items() if k not in after}
        by_name = {(spec.name(o), _where(spec, o, base)): k for k, o in unmatched_before.items()}
        for k, o in list(after.items()):
            if k in before:
                continue
            alt = by_name.get((spec.name(o), _where(spec, o, cur)))
            if alt is not None and alt in before:
                before[k] = before.pop(alt)
        for k, obj in after.items():
            if k not in before:
                if _on_left_out(spec, obj, base_left_out):
                    hidden += 1
                    continue
                out.append(_entry("added", spec, obj, cur, cur_ctx))
        for k, obj in before.items():
            if k not in after:
                if _on_left_out(spec, obj, cur_left_out):
                    hidden += 1
                    continue
                out.append(_entry("removed", spec, obj, base, base_ctx, cur=cur))
        diff_cols = [c for c in spec.cols if c.diff]
        for k, new in after.items():
            old = before.get(k)
            if old is None:
                continue
            for col in diff_cols:
                a = display(col, col.get(old, base_ctx))
                b = display(col, col.get(new, cur_ctx))
                if a != b:
                    out.append({
                        "kind": "changed", "type": type_id, "object_type": spec.singular,
                        "object": str(spec.name(new)), "where": _where(spec, new, cur),
                        "field": col.diff, "before": a or NA, "after": b or NA,
                        "detail": None, "id": k,
                    })
    if stats is not None:
        stats["out_of_scope"] = hidden
    return out


def scope_notice(base: Environment, cur: Environment, hidden: int = 0
                 ) -> dict[str, Any] | None:
    """What to tell the user when the two snapshots cover different clusters.

    None when both cover the same clusters (or neither recorded a scope)."""
    from ntnxvision_core.scope import describe, names_label

    def selected(env: Environment) -> set[str] | None:
        return {c.uuid for c in env.cluster_scope.selected} if env.cluster_scope else None

    b, c = selected(base), selected(cur)
    differs = b is not None and c is not None and b != c
    partial = any(e.cluster_scope is not None and e.cluster_scope.is_partial
                  for e in (base, cur))
    if not (differs or hidden or (partial and (b is None or c is None))):
        return None
    gap: list[str] = []
    for env, other in ((base, cur), (cur, base)):
        if env.cluster_scope is None:
            continue
        other_sel = selected(other)
        for cl in env.cluster_scope.excluded:
            if other_sel is None or cl.uuid in other_sel:
                gap.append(cl.name or cl.uuid)
    gap = sorted(dict.fromkeys(gap), key=str.casefold)
    base_label = describe(base.cluster_scope) or "Every cluster (no scope recorded)"
    cur_label = describe(cur.cluster_scope) or "Every cluster (no scope recorded)"
    detail = "The two snapshots cover different clusters."
    if gap:
        detail += (f" Objects on {names_label(gap)}, which only one snapshot collected, are "
                   "left out of the comparison instead of being reported as added or removed")
        detail += f" ({fmt_count(hidden)} objects)." if hidden else "."
    return {"title": "Different cluster scopes", "baseline": base_label, "current": cur_label,
            "clusters": gap, "out_of_scope": hidden, "detail": detail}


def collected(env: Environment, spec: TableSpec) -> bool:
    """Did this snapshot's run collect `spec`'s area at all?"""
    if spec.items(env):
        return True
    if not spec.note_keys:
        return True
    notes = env.collection_notes
    for key in spec.note_keys:
        for k, v in notes.items():
            if (k == key or k.endswith(f": {key}")) and v.startswith("ok"):
                return True
    return False


def _entry(kind: str, spec: TableSpec, obj: Any, env: Environment, ctx: Ctx,
           cur: Environment | None = None) -> dict[str, Any]:
    return {
        "kind": kind, "type": spec.type, "object_type": spec.singular,
        "object": str(spec.name(obj)), "where": _where(spec, obj, env),
        "field": None, "before": None, "after": None,
        "detail": _describe(spec, obj, ctx, kind, cur), "id": _key(spec, obj, env),
        "_obj": obj,
    }


def _describe(spec: TableSpec, obj: Any, ctx: Ctx, kind: str, cur: Environment | None
              ) -> str | None:
    t = spec.type
    if t == "vms":
        return " · ".join(x for x in (f"{fmt_count(obj.vcpu_total or NA)} vCPU",
                                      fmt_bytes(obj.memory_bytes), obj.guest_os) if x)
    if t == "hosts":
        return " · ".join(x for x in (obj.block_model, f"{fmt_count(obj.num_cpu_cores)} cores"
                                      if obj.num_cpu_cores else None) if x)
    if t == "storage_containers":
        return " · ".join(x for x in (fmt_bytes(obj.max_capacity_bytes),
                                      "compression on" if obj.compression_enabled
                                      else "compression off") if x)
    if t == "security_policies":
        return f"{(obj.policy_type or 'Policy').title()} · {(obj.state or '').lower()}"
    if t == "protection_policies" and kind == "removed" and cur is not None:
        cats = set(obj.categories)
        cur_ctx = Ctx(cur)
        orphaned = sum(
            1 for v in cur.vms
            if any(f"{k}:{val}" in cats for k, vals in v.categories.items() for val in vals)
            and cur_ctx.vm_policy(v) is None)
        if orphaned:
            return f"{fmt_count(orphaned)} VMs it covered are now unprotected"
        return "No VMs were relying on it"
    if t == "clusters":
        return f"AOS {obj.aos_version}" if obj.aos_version else None
    parts = []
    # The first two columns after the name (the Cluster column now leads most tables).
    for col in [c for c in spec.ui_cols if c.kind != "name"][:2]:
        value = display(col, col.get(obj, ctx))
        if value:
            parts.append(value)
    return " · ".join(parts) or None


# ─── grouping ──────────────────────────────────────────────────────────


def _stem(name: str) -> tuple[str, int | None]:
    m = _TRAILING_NUM.match(name)
    if not m:
        return name, None
    return m.group(1), int(m.group(2))


def range_label(names: list[str]) -> str:
    """'k8s-worker-09 … 40' — first full name, then the last one's tail after the
    common prefix (backed off to the last separator so 'n5 … n8' keeps its letter)."""
    first, last = names[0], names[-1]
    if first == last:
        return f"{first} ×{fmt_count(len(names))}" if len(names) > 1 else first
    i = 0
    while i < min(len(first), len(last)) and first[i] == last[i]:
        i += 1
    cut = max(first.rfind(sep, 0, i) for sep in "-_. ") + 1
    return f"{first} … {last[cut:]}"


def _names_label(names: list[str]) -> str:
    """A run of same-stem names as a range; anything else as a short list."""
    if len(names) == 1:
        return names[0]
    stems = {_stem(n)[0] for n in names}
    if len(stems) == 1 and all(_stem(n)[1] is not None for n in names):
        return range_label(names)
    if len(names) <= 3:
        return ", ".join(names)
    return f"{names[0]}, {names[1]} + {fmt_count(len(names) - 2)} more"


def group_changes(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    # Added / removed: runs of same-stem names within (kind, type, where).
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    changed: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    for c in raw:
        if c["kind"] == "changed":
            changed.setdefault((c["type"], c["where"], c["field"], c["before"], c["after"]),
                               []).append(c)
        else:
            buckets.setdefault((c["kind"], c["type"], c["where"]), []).append(c)
    for (kind, type_id, where), items in buckets.items():
        items.sort(key=lambda c: (_stem(c["object"])[0], _stem(c["object"])[1] or -1,
                                  c["object"]))
        i = 0
        while i < len(items):
            stem, num = _stem(items[i]["object"])
            j = i + 1
            if num is not None:
                while j < len(items):
                    s2, n2 = _stem(items[j]["object"])
                    if s2 != stem or n2 is None:
                        break
                    j += 1
            rows.append(_group_row(kind, type_id, where, items[i:j]))
            i = j
    for (type_id, where, field, before, after), items in changed.items():
        names = sorted((c["object"] for c in items), key=lambda n: (_stem(n)[0],
                                                                   _stem(n)[1] or -1, n))
        rows.append({
            "kind": "changed", "object_type": items[0]["object_type"],
            "object": _names_label(names),
            "where": where, "field": field, "before": before, "after": after,
            "detail": None, "count": len(items), "_type": type_id,
        })
    rows.sort(key=lambda r: (COMPARE_TYPES.index(r["_type"]) if r["_type"] in COMPARE_TYPES
                             else 99, _KIND_ORDER[r["kind"]], r["where"] or "", r["object"]))
    for r in rows:
        r.pop("_type", None)
    return rows


def _group_row(kind: str, type_id: str, where: str, items: list[dict[str, Any]]
               ) -> dict[str, Any]:
    spec = SPECS[type_id]
    if len(items) == 1:
        c = items[0]
        return {"kind": kind, "object_type": c["object_type"], "object": c["object"],
                "where": where, "field": None, "before": None, "after": None,
                "detail": c["detail"], "count": 1, "_type": type_id}
    names = [c["object"] for c in items]
    detail = f"{fmt_count(len(items))} {spec.unit_plural}"
    if type_id == "vms":
        objs = [c["_obj"] for c in items]
        sizes = {(o.vcpu_total, o.memory_bytes) for o in objs}
        if len(sizes) == 1:
            vcpu, mem = sizes.pop()
            detail += f" · {fmt_count(vcpu or NA)} vCPU · {fmt_bytes(mem)}"
        else:
            detail += f" · {fmt_count(sum(o.vcpu_total or 0 for o in objs))} vCPU · " \
                      f"{fmt_bytes(sum(o.memory_bytes or 0 for o in objs))} total"
        if kind == "removed":
            freed = sum(o.total_disk_bytes for o in objs)
            if freed:
                detail += f" · {fmt_bytes(freed)} freed"
    elif type_id == "hosts":
        objs = [c["_obj"] for c in items]
        models = {o.block_model for o in objs if o.block_model}
        detail = f"{fmt_count(len(items))} nodes" + (f" · {models.pop()}"
                                                     if len(models) == 1 else "")
        cores = {o.num_cpu_cores for o in objs}
        if len(cores) == 1 and None not in cores:
            detail += f" · {fmt_count(cores.pop())} cores each"
    return {"kind": kind, "object_type": spec.singular, "object": range_label(names),
            "where": where, "field": None, "before": None, "after": None, "detail": detail,
            "count": len(items), "_type": type_id}


# ─── figures ───────────────────────────────────────────────────────────


def capacity(env: Environment) -> tuple[int, int]:
    """(capacity, used) bytes across clusters.

    Containers on one cluster share its storage pool and each reports the pool as its
    ceiling, so the pool is counted once per cluster (the largest ceiling) while usage is
    summed across containers.
    """
    pools: dict[str, int] = {}
    used = 0
    for s in env.storage_containers:
        key = f"{s.source_pc or ''}/{s.cluster_uuid or s.cluster_name or s.uuid}"
        pools[key] = max(pools.get(key, 0), s.max_capacity_bytes or 0)
        used += s.used_bytes or 0
    return sum(pools.values()), used


def _capacity(env: Environment) -> int:
    return capacity(env)[0]


def _tb(value: int) -> str:
    return f"{value / 1024**4:,.0f}" if value >= 10 * 1024**4 else f"{value / 1024**4:,.1f}"


def _delta(added: int, removed: int, was: int) -> str:
    parts = []
    if added and removed:
        parts.append(f"+{fmt_count(added)} / −{fmt_count(removed)}")
    elif added:
        parts.append(f"+{fmt_count(added)}")
    elif removed:
        parts.append(f"−{fmt_count(removed)}")
    else:
        parts.append("no change")
    parts.append(f"was {fmt_count(was)}")
    return " · ".join(parts)


def compare(base: Environment, cur: Environment) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    raw = raw_changes(base, cur, stats)
    summary = {
        "added": sum(1 for c in raw if c["kind"] == "added"),
        "removed": sum(1 for c in raw if c["kind"] == "removed"),
        # Per object, not per field: two fields changed on one VM is one changed VM.
        "changed": len({(c["type"], c["id"]) for c in raw if c["kind"] == "changed"}),
    }
    summary["total"] = summary["added"] + summary["removed"] + summary["changed"]

    def counts(type_id: str) -> tuple[int, int]:
        return (sum(1 for c in raw if c["type"] == type_id and c["kind"] == "added"),
                sum(1 for c in raw if c["type"] == type_id and c["kind"] == "removed"))

    va, vr = counts("vms")
    ha, hr = counts("hosts")
    pa, pr = counts("protection_policies")
    cap_b, cap_c = _capacity(base), _capacity(cur)
    cap_delta = cap_c - cap_b
    figures = [
        {"label": "Changes", "value": summary["total"],
         "qualifier": f"{fmt_count(summary['added'])} added · "
                      f"{fmt_count(summary['removed'])} removed · "
                      f"{fmt_count(summary['changed'])} changed"},
        {"label": "VMs", "value": len(cur.vms), "qualifier": _delta(va, vr, len(base.vms))},
        {"label": "Hosts", "value": len(cur.hosts), "qualifier": _delta(ha, hr, len(base.hosts))},
        {"label": "Storage", "value": _tb(cap_c), "unit": "TB",
         "qualifier": (f"{'+' if cap_delta >= 0 else '−'}{_tb(abs(cap_delta))} TB · "
                       if cap_delta else "no change · ") + f"was {_tb(cap_b)} TB"},
        {"label": "Protection policies", "value": len(cur.protection_policies),
         "qualifier": _delta(pa, pr, len(base.protection_policies)),
         "qualifier_tone": "warn" if pr else None},
    ]
    return {"summary": summary, "figures": figures, "changes": group_changes(raw),
            "days": _days(base.collected_at, cur.collected_at),
            "scope_notice": scope_notice(base, cur, int(stats.get("out_of_scope") or 0))}


def _days(a: str | None, b: str | None) -> float:
    try:
        da = datetime.fromisoformat(str(a).replace("Z", "+00:00"))
        db = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return round(abs((db - da).total_seconds()) / 86400, 2)


# ─── export ────────────────────────────────────────────────────────────
#
# The comparison workbook follows the inventory workbook's Runway treatment (see
# `exporters.excel`): the same Overview-style cover (iris band, eyebrow, title, ruled
# sections), and a Changes sheet written by the very table helper the inventory sheets use
# (light header fill, iris rule, frozen header, auto-filter, comma grouping, N/A in
# ink-3, landscape fit-to-width printing). Only the change kind carries colour: Added in
# the ok colour, Removed in the error colour, Changed in the warn colour.

_HEADERS = ["Change", "Object type", "Object", "Location", "Field", "Before", "After",
            "Detail", "ID"]
_KIND_LABEL = {"added": "Added", "removed": "Removed", "changed": "Changed"}
_KIND_TONE = {"added": "ok", "removed": "error", "changed": "warn"}


def _export_rows(raw: list[dict[str, Any]]) -> list[list[Any]]:
    """One row per added / removed object and per changed field, changed first, then
    added, then removed, each in report (object type) order. Missing values stay None so
    every format renders them as N/A."""
    type_rank = {t: i for i, t in enumerate(COMPARE_TYPES)}
    ordered = sorted(raw, key=lambda c: (_KIND_ORDER[c["kind"]], type_rank.get(c["type"], 99),
                                         c["where"] or "", c["object"], c["field"] or ""))

    def value(v: Any) -> Any:
        return None if v in (None, "", NA) else v

    return [[_KIND_LABEL[c["kind"]], c["object_type"], c["object"], value(c["where"]),
             value(c["field"]), value(c["before"]), value(c["after"]), value(c["detail"]),
             c["id"]] for c in ordered]


def _by_type(raw: list[dict[str, Any]]) -> list[list[Any]]:
    """Objects added / removed / changed per object type (per object, not per field)."""
    out = []
    for type_id in COMPARE_TYPES:
        items = [c for c in raw if c["type"] == type_id]
        if not items:
            continue
        changed = len({c["id"] for c in items if c["kind"] == "changed"})
        added = sum(1 for c in items if c["kind"] == "added")
        removed = sum(1 for c in items if c["kind"] == "removed")
        out.append([SPECS[type_id].label, added, removed, changed, added + removed + changed])
    return out


def export_comparison(base: Environment, cur: Environment, base_name: str, cur_name: str,
                      fmt: str, directory: Path, prefix: str = "") -> Path:
    """Write the comparison as an Excel workbook (Summary + Changes, every object and
    field), a CSV of the same Changes rows, or a PDF report (summary figures and the
    grouped change rows, see `exporters.compare_pdf`); returns the written path.

    The file is named like every report (`exporters.naming`):
    ``<prefix>-changes-<baseline>-to-<current>-<YYYY-MM-DD>.<ext>``, never overwriting."""
    from ntnxvision_core.exporters.naming import report_path

    raw = raw_changes(base, cur)
    directory.mkdir(parents=True, exist_ok=True)
    name = f"changes-{base_name}-to-{cur_name}"
    rows = _export_rows(raw)
    ext = fmt if fmt in ("pdf", "csv") else "xlsx"
    out = report_path(directory, name, ext, prefix)
    if fmt == "pdf":
        from ntnxvision_core.exporters.compare_pdf import ComparisonPdfExporter

        return ComparisonPdfExporter().export(base, cur, base_name, cur_name, out)
    if fmt == "csv":
        with out.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(_HEADERS)
            writer.writerows([[NA if v is None else v for v in row] for row in rows])
        return out

    from ntnxvision_core.exporters import excel as XL
    from ntnxvision_core.exporters import theme as TH

    result = compare(base, cur)
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Summary"
    _summary_sheet(ws, base, cur, base_name, cur_name, result, raw)
    exporter = XL.ExcelExporter()
    title = exporter._table(wb, "Changes", list(_HEADERS), rows,
                            wrap=("Detail", "Before", "After"))
    if title is None:  # nothing changed: keep the sheet, say so
        sheet = wb.create_sheet("Changes")
        sheet.append(_HEADERS)
        for cell in sheet[1]:
            cell.fill, cell.font, cell.border = XL._HEADER_FILL, XL._HEADER_FONT, \
                XL._HEADER_BORDER
        note = sheet.cell(row=2, column=1, value="No changes between the two snapshots.")
        note.font = XL._NA_FONT
    else:
        sheet = wb[title]
        for r_idx, row in enumerate(rows, start=2):
            kind = next(k for k, v in _KIND_LABEL.items() if v == row[0])
            sheet.cell(row=r_idx, column=1).font = XL._STATUS_FONT[_KIND_TONE[kind]]
    props = wb.properties
    props.title = f"Snapshot comparison: {base_name} to {cur_name}"
    props.subject = "What changed between two NTNXVision inventory snapshots"
    props.creator = props.lastModifiedBy = "NTNXVision"
    props.keywords = "Nutanix; inventory; comparison; NTNXVision"
    props.category = "Comparison"
    props.description = (f"Generated by NTNXVision from snapshots collected "
                         f"{TH.collected_label(base)} and {TH.collected_label(cur)}.")
    wb.save(out)
    return out


def _scope_label(env: Environment) -> str:
    from ntnxvision_core.scope import describe

    return describe(env.cluster_scope, limit=None) or "Every cluster (no scope recorded)"


def _summary_sheet(ws: Any, base: Environment, cur: Environment, base_name: str,
                   cur_name: str, result: dict[str, Any], raw: list[dict[str, Any]]) -> None:
    """The workbook's cover, laid out like the inventory workbook's Overview sheet."""
    
    from ntnxvision_core.exporters import excel as XL
    from ntnxvision_core.exporters import theme as TH

    font = TH.OFFICE_FONT
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = TH.IRIS
    for col, width in zip("ABCDE", (30, 14, 14, 14, 30), strict=True):
        ws.column_dimensions[col].width = width
    band = PatternFill("solid", fgColor=TH.IRIS)
    for col in "ABCDE":
        ws[f"{col}1"].fill = band
    ws.row_dimensions[1].height = 6
    ws["A3"] = "COMPARISON WORKBOOK"
    ws["A3"].font = Font(name=font, size=8.5, bold=True, color=TH.INK_3)
    ws["A4"] = "What changed"
    ws["A4"].font = Font(name=font, size=18, color=TH.INK)
    ws.row_dimensions[4].height = 28
    ws["A5"] = f"{base_name} to {cur_name}"
    ws["A5"].font = Font(name=font, size=11, color=TH.INK_2)

    def label(row: int, text: str) -> None:
        ws.cell(row=row, column=1, value=text).font = Font(name=font, size=10,
                                                           color=TH.INK_3)

    def value(row: int, v: Any, tone: str | None = None, number: bool = False) -> None:
        cell = ws.cell(row=row, column=2, value=NA if TH.is_missing(v) else v)
        cell.font = (XL._NA_FONT if TH.is_missing(v)
                     else XL._STATUS_FONT[tone] if tone else XL._BODY_FONT)
        if number:
            cell.number_format = "#,##0"
            cell.alignment = XL._LEFT
        else:
            ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=5)
            cell.alignment = XL._LEFT

    def source(env: Environment) -> str:
        if env.is_combined:
            return f"{fmt_count(len(env.source_environments))} Prism Centrals (combined)"
        return env.prism_central.host or NA

    row = XL.ExcelExporter._section(ws, 7, "Snapshots")
    days = result["days"]
    for text, v in (
        ("Baseline", base_name), ("Baseline collected (UTC)",
                                  TH.fmt_datetime(base.collected_at) or base.collected_at),
        ("Baseline source", source(base)),
        ("Current", cur_name), ("Current collected (UTC)",
                                TH.fmt_datetime(cur.collected_at) or cur.collected_at),
        ("Current source", source(cur)),
        ("Baseline scope", (result.get("scope_notice") or {}).get("baseline")
         or _scope_label(base)),
        ("Current scope", (result.get("scope_notice") or {}).get("current")
         or _scope_label(cur)),
        ("Time between", f"{days:,.1f} days"),
        ("Report date", TH.report_date()),
        ("Generated by", f"NTNXVision {cur.tool_version}"),
    ):
        label(row, text)
        value(row, v)
        row += 1

    notice = result.get("scope_notice")
    if notice:
        cell = ws.cell(row=row, column=1, value=notice["detail"])
        cell.font = XL._STATUS_FONT["warn"]
        cell.alignment = XL._TOP_LEFT
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
        ws.row_dimensions[row].height = 40
        row += 1

    s = result["summary"]
    row = XL.ExcelExporter._section(ws, row + 1, "Changes")
    for text, v, tone in (("Total", s["total"], None), ("Added", s["added"], "ok"),
                          ("Removed", s["removed"], "error"),
                          ("Changed", s["changed"], "warn")):
        label(row, text)
        value(row, v, tone if v else None, number=True)
        row += 1
    note = ws.cell(row=row, column=1, value=(
        "Objects, not fields: an object with two changed fields counts once. The Changes "
        "sheet lists one row per object and changed field."))
    note.font = Font(name=font, size=9, italic=True, color=TH.INK_3)
    note.alignment = XL._TOP_LEFT
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
    ws.row_dimensions[row].height = 26
    row += 1

    row = XL.ExcelExporter._section(ws, row + 1, "Headline figures")
    for fig in result["figures"][1:]:
        label(row, fig["label"])
        unit = f" {fig['unit']}" if fig.get("unit") else ""
        shown = fig["value"] if isinstance(fig["value"], str) else fmt_count(fig["value"])
        cell = ws.cell(row=row, column=2, value=f"{shown}{unit}")
        cell.font, cell.alignment = XL._BODY_FONT, XL._LEFT
        q = ws.cell(row=row, column=3, value=TH.clean(fig.get("qualifier")) or None)
        tone = fig.get("qualifier_tone")
        q.font = XL._STATUS_FONT.get(tone or "", XL._NA_FONT)
        ws.merge_cells(start_row=row, start_column=3, end_row=row, end_column=5)
        row += 1

    by_type = _by_type(raw)
    row = XL.ExcelExporter._section(ws, row + 1, "By object type")
    headers = ("Object type", "Added", "Removed", "Changed", "Total")
    for ci, header in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=ci, value=header)
        cell.font, cell.fill, cell.border = XL._HEADER_FONT, XL._HEADER_FILL, XL._HEADER_BORDER
        cell.alignment = XL._LEFT if ci == 1 else XL._RIGHT
    row += 1
    if not by_type:
        cell = ws.cell(row=row, column=1, value="No changes between the two snapshots.")
        cell.font = XL._NA_FONT
        row += 1
    for line in by_type:
        for ci, v in enumerate(line, start=1):
            cell = ws.cell(row=row, column=ci, value=v)
            cell.font = XL._BODY_FONT
            if ci > 1:
                cell.number_format, cell.alignment = "#,##0", XL._RIGHT
        row += 1

    row += 1
    note = ws.cell(row=row, column=1, value=(
        "Timestamps are UTC. Objects are matched by their Nutanix ID, then by name and "
        "location. Areas either run did not collect are not compared. Generated from "
        "read-only inventory snapshots; no configuration was changed."))
    note.font = Font(name=font, size=9, italic=True, color=TH.INK_3)
    note.alignment = XL._TOP_LEFT
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=5)
    ws.row_dimensions[row].height = 30
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    if ws.sheet_properties.pageSetUpPr is not None:
        ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_area = f"A1:E{row}"
