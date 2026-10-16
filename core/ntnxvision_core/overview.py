"""The Overview screen: the latest snapshot's headline figures, coverage and API routing.

Everything here is read from the snapshot metadata index (`store.snapshot.read_meta`),
never from the snapshots themselves, so the first screen of the app stays instant
however large the estate is.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ntnxvision_core.inventory import fmt_count
from ntnxvision_core.runinfo import api_counts, classify, coverage, sunset_areas
from ntnxvision_core.store.snapshot import EnvironmentCache, info_from_meta, read_meta

TIB = 1024**4


def all_metas(paths: list[Path], cache: EnvironmentCache | None = None) -> list[dict[str, Any]]:
    """Index entries for every readable snapshot, newest first. Corrupt files are skipped."""
    metas = []
    for path in paths:
        try:
            metas.append(read_meta(path, cache))
        except Exception:  # noqa: BLE001 — one unreadable snapshot must not hide the rest
            continue
    metas.sort(key=lambda m: str(m.get("collected_at") or ""), reverse=True)
    return metas


def _short_date(iso: str | None) -> str:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return "the last run"
    return dt.strftime("%b %d").replace(" 0", " ")


def baseline_for(meta: dict[str, Any], metas: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The previous snapshot of the same source (what Compare should default to)."""
    for other in metas:
        if other is meta or other.get("path") == meta.get("path"):
            continue
        if other.get("kind") == meta.get("kind") and other.get("source") == meta.get("source") \
                and str(other.get("collected_at") or "") < str(meta.get("collected_at") or ""):
            return other
    return None


def _areas(meta: dict[str, Any]) -> list[dict[str, Any]]:
    run = meta.get("run") or {}
    if isinstance(run.get("areas"), list) and run["areas"]:
        return list(run["areas"])
    notes = (meta.get("stats") or {}).get("notes") or {}
    out = []
    for i, (key, note) in enumerate(notes.items(), start=1):
        status, detail = classify(key, note)
        out.append({"id": key, "label": key, "index": i, "status": status, "detail": detail})
    return out


def build(metas: list[dict[str, Any]]) -> dict[str, Any]:
    if not metas:
        return {"latest": None, "figures": [], "recent": [], "coverage": None,
                "api_routing": None, "sunset_areas": [], "compare_baseline": None}
    latest = metas[0]
    base = baseline_for(latest, metas)
    summary = latest.get("summary") or {}
    st = latest.get("stats") or {}
    figures: list[dict[str, Any]] = []

    aos_min, aos_max = st.get("aos_min"), st.get("aos_max")
    aos = None
    if aos_min:
        aos = f"AOS {aos_min}" if aos_min == aos_max else f"AOS {aos_min} – {aos_max}"
    figures.append({"label": "Clusters", "value": summary.get("clusters", 0), "qualifier": aos})

    hosts = summary.get("hosts", 0)
    host_q = None
    if base is not None:
        delta = hosts - (base.get("summary") or {}).get("hosts", 0)
        when = _short_date(base.get("collected_at"))
        host_q = (f"{'+' if delta > 0 else '−'}{abs(delta)} since {when}" if delta
                  else f"no change since {when}")
    figures.append({"label": "Hosts", "value": hosts, "qualifier": host_q})

    figures.append({"label": "VMs", "value": summary.get("vms", 0),
                    "qualifier": f"{fmt_count(st.get('vms_on', 0))} powered on"
                    if "vms_on" in st else None})

    cap, used = st.get("capacity_bytes") or 0, st.get("used_bytes") or 0
    if cap:
        tb = cap / TIB
        figures.append({"label": "Storage", "value": f"{tb:.0f}" if tb >= 10 else f"{tb:.1f}",
                        "unit": "TB", "qualifier": f"{round(100 * used / cap)}% used"})

    vms = summary.get("vms", 0)
    if vms and latest.get("kind") != "pe":
        protected = st.get("protected_vms", 0)
        unprotected = st.get("unprotected_vms", 0)
        figures.append({"label": "Protected VMs", "value": f"{round(100 * protected / vms)}%",
                        "qualifier": f"{fmt_count(unprotected)} unprotected",
                        "qualifier_tone": "warn" if unprotected else None})

    areas = _areas(latest)
    cov = coverage(areas)
    errors = cov["failed"]
    notes_count = cov["skipped"] + cov["with_notes"] + errors
    figures.append({"label": "Run notes", "value": notes_count,
                    "qualifier": f"{cov['skipped']} skipped · {cov['with_notes']} with notes · "
                                 f"{errors} error{'s' if errors != 1 else ''}",
                    "qualifier_tone": "error" if errors else None})

    run = latest.get("run") or {}
    routing = run.get("api_routing") or []
    return {
        "latest": info_from_meta(latest),
        "figures": figures,
        "recent": [info_from_meta(m) for m in metas[:5]],
        "coverage": cov if areas else None,
        "api_routing": api_counts(areas) if run.get("areas") else None,
        "sunset_areas": sunset_areas(routing),
        "compare_baseline": base.get("path") if base else None,
    }
