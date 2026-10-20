"""Run tracking and the run metadata a snapshot carries.

`RunTracker` follows one collection run area by area: it times each area, classifies
its outcome (collected / notes / skipped / failed / cancelled), records which API served
it, and emits the typed progress events of docs/IPC_CONTRACT.md through a callback. The
engine drives it; the sidecar turns its final state into the `areas`, `figures`,
`api_routing` and `compat` fields of the inventory result, and the same dict is stored on
the snapshot (`Environment.run`) so Results, Overview coverage and API routing can be
rendered again later from the snapshot alone.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ntnxvision_core.areas import AREAS_BY_ID, NDB_AREA, PE_LABELS, TYPICAL_SECONDS, Area
from ntnxvision_core.compat import check_aos, check_prism_central
from ntnxvision_core.notes import clean_error
from ntnxvision_core.numfmt import fmt_count
from ntnxvision_core.versions import pc_version

PC_RANGE = "pc.2024.1 to pc.7.5.1.6"
AOS_RANGE = "AOS 6.5 to 7.5"

Emit = Callable[[dict[str, Any]], None]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def classify(area_id: str, note: str | None) -> tuple[str, str | None]:
    """Map a collector's `collection_notes` value to (AreaStatus, detail)."""
    if note is None or note == "ok":
        return "collected", None
    if note.startswith("ok:"):
        detail = note[3:].strip()
        # LCM always reports its entity count and framework version: that is information,
        # not a caveat — unless the framework itself is out of date.
        if area_id == "lcm" and "update available" not in detail:
            return "collected", detail
        return "notes", _sentence(detail)
    if note.startswith("skipped"):
        detail = note.split(":", 1)[1].strip() if ":" in note else "Not available"
        return "skipped", _sentence(detail)
    if note.startswith(("error", "failed")):
        return "failed", clean_error(note.split(":", 1)[1].strip() if ":" in note else note)
    return "notes", _sentence(note)


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


class RunTracker:
    """Per-run area state plus the event stream for one target.

    Thread-safe: multi-PC runs collect targets on parallel threads, each with its own
    tracker, and all of them emit through one shared, locked writer.
    """

    def __init__(
        self,
        emit: Emit | None = None,
        *,
        run_id: str | None = None,
        mode: str = "prism_central",
        host: str = "",
        name: str = "",
        formats: list[str] | None = None,
        target: dict[str, Any] | None = None,
    ) -> None:
        self._emit = emit
        self.run_id = run_id
        self.mode = mode
        self.host = host
        self.name = name
        self.formats = list(formats or [])
        self.target = dict(target or {})
        self.started_at = now_iso()
        self._t0 = time.monotonic()
        self.finished_at: str | None = None
        self.duration_ms: int | None = None
        self.version: str | None = None
        self.areas: list[dict[str, Any]] = []
        self._by_id: dict[str, dict[str, Any]] = {}
        self._area_t0: dict[str, float] = {}
        self._lock = threading.Lock()
        self.objects_total = 0
        self.clusters = 0
        #: The LCM inventory's last state (`LcmInventoryRun` as a dict), when requested.
        self.inventory: dict[str, Any] | None = None

    # ─── events ────────────────────────────────────────────────────

    def _send(self, payload: dict[str, Any]) -> None:
        if self._emit is None:
            return
        payload = {**payload, "run_id": self.run_id, **self.target}
        self._emit(payload)

    def start(self, areas: list[Area], version: str | None, *,
              extra_seconds: int = 0) -> None:
        """Announce the run. `extra_seconds` adds to the typical duration (an LCM
        inventory requested for the run)."""
        self.version = version
        with self._lock:
            self.areas = []
            for i, area in enumerate(areas, start=1):
                label = PE_LABELS.get(area.id, area.label) if self.mode == "prism_element" \
                    else area.label
                entry: dict[str, Any] = {
                    "id": area.id, "label": label, "index": i, "status": "pending",
                    "api": None, "objects": None, "duration_ms": None, "detail": None,
                }
                self.areas.append(entry)
                self._by_id[area.id] = entry
        self._send({
            "type": "started",
            "started_at": self.started_at,
            "mode": self.mode,
            "host": self.host,
            "name": self.name,
            "version": version,
            "formats": self.formats,
            "typical_seconds": TYPICAL_SECONDS.get(self.mode, 240) + extra_seconds,
            "areas": [dict(a) for a in self.areas],
        })

    def begin(self, area_id: str, detail: str | None = None) -> None:
        with self._lock:
            entry = self._by_id[area_id]
            entry["status"] = "running"
            entry["detail"] = detail
            self._area_t0[area_id] = time.monotonic()
            snapshot = dict(entry)
        self._area_event(snapshot)

    def end(
        self,
        area_id: str,
        status: str,
        *,
        api: str | None = None,
        objects: int | None = None,
        detail: str | None = None,
        clusters: int | None = None,
    ) -> None:
        with self._lock:
            entry = self._by_id[area_id]
            started = self._area_t0.get(area_id, time.monotonic())
            entry.update({
                "status": status,
                "api": api if status not in ("skipped", "cancelled") else None,
                "objects": objects if status in ("collected", "notes") else None,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "detail": detail,
            })
            if objects and status in ("collected", "notes"):
                self.objects_total += objects
            if clusters is not None:
                self.clusters = clusters
            snapshot = dict(entry)
        self._area_event(snapshot)

    def lcm_inventory(self, run: Any) -> None:
        """The LCM inventory changed (an `LcmInventoryRun`): emit its per-target state and
        show its progress on the LCM area while that is still waiting for it."""
        from ntnxvision_core.lcm_inventory import progress_line

        self.inventory = run.model_dump()
        snapshot = None
        with self._lock:
            entry = self._by_id.get("lcm")
            if entry is not None and entry["status"] in ("pending", "running") \
                    and run.targets:
                entry["detail"] = progress_line(run)
                snapshot = dict(entry)
        self._send({"type": "lcm_inventory", "inventory": self.inventory})
        if snapshot is not None:
            self._area_event(snapshot)

    def cancel_pending(self) -> None:
        """Mark everything that never finished as cancelled (no events: the run is over)."""
        with self._lock:
            for entry in self.areas:
                if entry["status"] in ("pending", "running"):
                    entry["status"] = "cancelled"
                    entry["api"] = None

    def phase(self, phase: str, label: str) -> None:
        self._send({"type": "phase", "phase": phase, "label": label})

    def _area_event(self, area: dict[str, Any]) -> None:
        self._send({
            "type": "area",
            "area": area,
            "objects_total": self.objects_total,
            "clusters": self.clusters,
        })

    def finish(self) -> None:
        self.finished_at = now_iso()
        self.duration_ms = int((time.monotonic() - self._t0) * 1000)

    def final_areas(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(a) for a in self.areas]


class CombinedTracker:
    """The run of a combined multi-PC snapshot: every target's areas, host-prefixed,
    timed from the first target's start to the moment the combined result is written."""

    def __init__(self, parts: list[tuple[str, RunTracker]], emit: Emit | None = None,
                 run_id: str | None = None) -> None:
        self.parts = parts
        self._emit = emit
        self.run_id = run_id
        self.started_at = min((t.started_at for _, t in parts), default=now_iso())
        self._t0 = min((t._t0 for _, t in parts), default=time.monotonic())
        self.finished_at: str | None = None
        self.duration_ms: int | None = None

    def finish(self) -> None:
        self.finished_at = now_iso()
        self.duration_ms = int((time.monotonic() - self._t0) * 1000)

    def phase(self, phase: str, label: str) -> None:
        if self._emit is not None:
            self._emit({"type": "phase", "phase": phase, "label": label,
                        "run_id": self.run_id})

    def final_areas(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for host, tracker in self.parts:
            for area in tracker.final_areas():
                out.append({**area, "id": f"{host}: {area['id']}",
                            "label": f"{host} · {area['label']}", "index": len(out) + 1})
        return out


# ─── derived run metadata ──────────────────────────────────────────────


def compat_info(env: Any) -> dict[str, Any]:
    if env.is_combined:
        versions = [pc_version(s.prism_central.version) for s in env.source_environments]
        supported = all(s.prism_central.version_supported for s in env.source_environments)
        return {"version": None, "supported": supported, "range": PC_RANGE,
                "versions": versions}
    if env.source_type == "prism_element":
        result = check_aos(env.prism_central.version)
        return {"version": env.prism_central.version, "supported": result.in_range,
                "range": AOS_RANGE}
    result = check_prism_central(env.prism_central.version)
    return {"version": pc_version(env.prism_central.version), "supported": result.in_range,
            "range": PC_RANGE}


def figures(env: Any, areas: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The Results figure strip."""
    by_id = {a["id"]: a for a in (areas or [])}
    vm_area = by_id.get("vms")
    vm_qualifier = None
    vm_tone = None
    if vm_area and vm_area.get("status") == "notes" and vm_area.get("detail"):
        detail = str(vm_area["detail"])
        digits = "".join(ch for ch in detail.split(" ")[0] if ch.isdigit())
        vm_qualifier = f"{fmt_count(int(digits))} skipped" if digits else "with notes"
        vm_tone = "warn"
    out: list[dict[str, Any]] = [
        {"label": "Clusters", "value": len(env.clusters)},
        {"label": "Hosts", "value": len(env.hosts)},
        {"label": "VMs", "value": len(env.vms), "qualifier": vm_qualifier,
         "qualifier_tone": vm_tone},
        {"label": "Storage containers", "value": len(env.storage_containers)},
    ]
    if env.source_type != "prism_element":
        out.append({"label": "Protection policies", "value": len(env.protection_policies)})
        out.append({"label": "Flow policies", "value": len(env.security_policies)})
    else:
        out.append({"label": "Subnets", "value": len(env.subnets)})
    return out


_SUNSET_OK = {"tone": "ok", "label": "Unaffected"}
_SUNSET_UPGRADE = {"tone": "ok", "label": "Moves to v4 after a PC upgrade"}
_SUNSET_STOPS = {"tone": "warn", "label": "Stops collecting"}


def api_routing(env: Any, areas: list[dict[str, Any]],
                limit: int | None = 8) -> list[dict[str, Any]]:
    """Group the run's areas by API and reason, plus the always-v3 sub-features.

    "After sunset" says what happens to each group when Nutanix retires the legacy APIs
    in Q4 2026: v4 groups are unaffected; areas on v3 only because *this* PC is old move
    to v4 after an upgrade; areas with no v4 equivalent stop collecting.

    A combined run's area ids are ``"<host>: <area id>"``. Each is judged against its own
    Prism Central (version and negotiated v4 namespaces) and grouped per source, so a row
    carries ``source`` and an ``"<host>: "`` prefix on ``areas``. Judged against the
    combined Environment instead, an old PC's areas (v3 because v4 is not GA there) read
    as "No v4 equivalent yet / Stops collecting", which is wrong.

    `limit` caps how many labels a row lists before "+ N more"; None lists them all (the
    reports do, since they have no tooltip to show the rest).
    """
    groups: dict[tuple[str, str, str], list[str]] = {}
    sunset: dict[tuple[str, str, str], dict[str, str]] = {}
    sources = {s.prism_central.host: s.prism_central for s in env.source_environments}

    def add(source: str, label: str, api: str, reason: str, after: dict[str, str]) -> None:
        key = (source, api, reason)
        groups.setdefault(key, []).append(label)
        sunset[key] = after

    for entry in areas:
        if entry.get("status") not in ("collected", "notes") or not entry.get("api"):
            continue
        source, area_id = "", str(entry["id"])
        label = str(entry.get("label") or entry["id"])
        pc = env.prism_central
        if env.is_combined and ": " in area_id:
            source, area_id = area_id.split(": ", 1)
            pc = sources.get(source, pc)
            label = label.split(" · ", 1)[1] if label.startswith(f"{source} · ") else label
        area = AREAS_BY_ID.get(area_id)
        negotiated = pc.negotiated_api_versions or {}
        api = entry["api"]
        if api == "v4":
            add(source, label, "v4", "v4 SDK served by this Prism Central", _SUNSET_OK)
        elif api == "v2":
            add(source, label, "v2", "Direct Prism Element mode uses the v2.0 API",
                _SUNSET_STOPS)
        elif api == "era":
            add(source, label, "era", "NDB has its own Era API", _SUNSET_OK)
        elif api == "vcenter":
            add(source, label, "vcenter", "ESXi details from the VMware vCenter REST API",
                _SUNSET_OK)
        elif area is not None and area.v4_area:
            verdict = negotiated.get(area.v4_area, "")
            if "not GA" in verdict:
                reason = f"v4 not GA on {pc_version(pc.version) or 'this PC'}"
            elif "absent" in verdict:
                reason = "v4 namespace not served by this Prism Central"
            elif "unavailable" in verdict:
                reason = "v4 service unavailable during the run"
            else:
                reason = "v4 endpoint not usable on this Prism Central"
            add(source, label, "v3", reason, _SUNSET_UPGRADE)
        elif area is not None and area.legacy_only:
            for sub_label, reason in area.legacy_only:
                add(source, sub_label, "v3", reason, _SUNSET_STOPS)
            continue
        else:
            add(source, label, "v3", "No v4 equivalent yet", _SUNSET_STOPS)
        # Sub-features of a v4-served area that are still v3-only.
        if area is not None and area.legacy_only and api != "v3":
            for sub_label, reason in area.legacy_only:
                add(source, sub_label, "v3", reason, _SUNSET_STOPS)

    order = {"v4": 0, "era": 1, "vcenter": 2, "v3": 3, "v2": 4}
    host_order = {h: i for i, h in enumerate(sources)}
    rows = []
    for (source, api, reason), labels in sorted(
            groups.items(),
            key=lambda kv: (host_order.get(kv[0][0], -1), order.get(kv[0][1], 9))):
        joined = _join_labels(labels, limit)
        row: dict[str, Any] = {
            "areas": f"{source}: {joined}" if source else joined,
            "api": api,
            "reason": reason,
            "after_sunset": sunset[(source, api, reason)],
        }
        if source:
            row["source"] = source
        rows.append(row)
    return rows


def _join_labels(labels: list[str], limit: int | None = 8) -> str:
    seen = list(dict.fromkeys(labels))
    if limit is None or len(seen) <= limit:
        return ", ".join(seen) if len(seen) > 2 else " · ".join(seen)
    return ", ".join(seen[:limit]) + f" + {fmt_count(len(seen) - limit)} more"


def sunset_areas(routing: list[dict[str, Any]]) -> list[str]:
    """Lower-cased names of the areas that stop collecting at the v3 sunset."""
    out: list[str] = []
    for row in routing:
        if row.get("after_sunset", {}).get("tone") != "warn":
            continue
        areas = str(row["areas"])
        if row.get("source") and areas.startswith(f"{row['source']}: "):
            areas = areas[len(str(row["source"])) + 2:]
        for part in areas.replace(" · ", ", ").split(", "):
            part = part.strip()
            if not part or part.startswith("+"):
                continue
            proper = part.split(" ")[0] in ("X-Play", "IAM", "VPCs", "Nutanix", "NDB")
            out.append(part if proper else part[:1].lower() + part[1:])
    return list(dict.fromkeys(out))


def api_counts(areas: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"v4": 0, "v3": 0, "v2": 0}
    for a in areas:
        if a.get("status") in ("collected", "notes") and a.get("api") in counts:
            counts[a["api"]] += 1
    return counts


def coverage(areas: list[dict[str, Any]]) -> dict[str, int]:
    out = {"collected": 0, "with_notes": 0, "skipped": 0, "failed": 0, "total": len(areas)}
    for a in areas:
        status = a.get("status")
        if status == "collected":
            out["collected"] += 1
        elif status == "notes":
            out["with_notes"] += 1
        elif status == "skipped":
            out["skipped"] += 1
        elif status in ("failed", "cancelled"):
            out["failed"] += 1
    return out


def build_run_metadata(
    env: Any,
    tracker: RunTracker | CombinedTracker | None,
    *,
    mode: str | None = None,
    reports: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Everything Results needs, as stored in the snapshot's `run` field."""
    from ntnxvision_core.scope import scope_dict

    areas = tracker.final_areas() if tracker else areas_from_notes(env)
    routing = api_routing(env, areas)
    return {
        "started_at": tracker.started_at if tracker else env.collected_at,
        "finished_at": tracker.finished_at if tracker else env.collected_at,
        "duration_ms": tracker.duration_ms if tracker else None,
        "mode": mode or ("prism_element" if env.source_type == "prism_element"
                         else "prism_central"),
        "source_type": env.source_type,
        "areas": areas,
        "figures": figures(env, areas),
        "api_routing": routing,
        "compat": compat_info(env),
        "reports": list(reports or []),
        "warnings": list(env.warnings),
        # Which registered clusters the run collected (None: every cluster, recorded
        # before cluster selection existed, or a combined run).
        "scope": scope_dict(env.cluster_scope),
        # The optional LCM inventory (requested False: the run did not ask for one).
        "lcm_inventory": (env.lcm_inventory.model_dump() if env.lcm_inventory is not None
                          else {"requested": False}),
    }


def areas_from_notes(env: Any) -> list[dict[str, Any]]:
    """Best-effort area list for a run without a tracker (old snapshots, combined runs)."""
    out: list[dict[str, Any]] = []
    for i, (key, note) in enumerate(env.collection_notes.items(), start=1):
        area_key = key.split(": ", 1)[-1]
        area = next((a for a in AREAS_BY_ID.values() if a.note_key == area_key), None)
        area_id = area.id if area else area_key
        status, detail = classify(area_id, note)
        out.append({
            "id": key if env.is_combined else area_id,
            "label": (f"{key.split(': ', 1)[0]} · " if env.is_combined and ": " in key
                      else "") + (area.label if area else area_key.replace("_", " ").title()),
            "index": i, "status": status, "api": None, "objects": None,
            "duration_ms": None, "detail": detail,
        })
    return out


__all__ = [
    "NDB_AREA",
    "RunTracker",
    "api_counts",
    "api_routing",
    "build_run_metadata",
    "classify",
    "compat_info",
    "coverage",
    "figures",
    "sunset_areas",
]
