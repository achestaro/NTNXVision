"""Checks shared by the per-AZ network and security figure tests (N1 to N3)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ntnxvision_core.diagrams import TYPES, check_scene, model_envelope, model_from_envelope
from ntnxvision_core.diagrams import scene as S
from ntnxvision_core.diagrams.edits import apply_ops, clean_ops, walk
from ntnxvision_core.diagrams.render.drawio import render_drawio
from ntnxvision_core.diagrams.render.svg import render_svg
from ntnxvision_core.diagrams.render.vsdx import render_vsdx
from ntnxvision_core.diagrams.text import MIN_SIZE

from .diagram_helpers import shuffled

GOLDEN = Path(__file__).parent / "golden" / "diagrams"
UPDATE = os.environ.get("NTNXVISION_UPDATE_GOLDEN") == "1"
EM_DASH = chr(0x2014)
#: Estates every figure must lay out cleanly (scenario and demo estates).
ESTATES = ["small", "medium", "enterprise", "xlarge", "pc01", "pc02", "pc03", "large1",
           "large2", "large3", "multi3"]


def scenes(type_id: str, env: Any) -> list[S.Scene]:
    t = TYPES[type_id]
    return t.layout(t.build_model(env), t.page) if t.applies(env) else []


def texts(sc: S.Scene) -> list[str]:
    return [t.text for n in sc.nodes for t in n.lines] + \
        [t.text for e in sc.edges for lb in e.labels for t in lb.lines]


def assert_clean(scs: list[S.Scene], page_h: float = 440.0) -> None:
    for sc in scs:
        assert check_scene(sc) == [], (sc.id, check_scene(sc)[:5])
        assert sc.width <= 672.0 and sc.height <= page_h + 0.01, (sc.id, sc.height)
        for t in texts(sc):
            assert EM_DASH not in t
        assert min((t.size for n in sc.nodes for t in n.lines), default=MIN_SIZE) >= MIN_SIZE


def assert_deterministic(type_id: str, env: Any) -> None:
    base = scenes(type_id, env)
    again = scenes(type_id, env)
    assert [S.scene_json(x) for x in again] == [S.scene_json(x) for x in base]
    for seed in (1, 2, 3):
        sh = scenes(type_id, shuffled(env, seed))
        assert [S.scene_json(x) for x in sh] == [S.scene_json(x) for x in base], seed
    if base:
        names = [f"p{i}" for i in range(len(base))]
        assert render_vsdx(base, names, title="t", created="2026-10-01T00:00:00Z") == \
            render_vsdx(scenes(type_id, shuffled(env, 4)), names, title="t",
                        created="2026-10-01T00:00:00Z")
        assert render_drawio(base, names) == render_drawio(scenes(type_id, shuffled(env, 5)),
                                                           names)


def assert_golden(type_id: str, env: Any, name: str) -> None:
    sc = scenes(type_id, env)[0]
    for path, data in ((GOLDEN / f"{type_id}-{name}.scene.json", S.scene_json(sc)),
                       (GOLDEN / f"{type_id}-{name}.svg", render_svg(sc))):
        if UPDATE or not path.exists():
            path.write_bytes(data)
        assert path.read_bytes() == data, (
            f"{path.name} changed; rerun with NTNXVISION_UPDATE_GOLDEN=1 after reviewing")


def assert_round_trip(type_id: str, env: Any) -> None:
    t = TYPES[type_id]
    m = t.build_model(env)
    tid, back = model_from_envelope(model_envelope(type_id, m))
    assert tid == type_id and t.to_doc(back) == t.to_doc(m)


def edit(type_id: str, env: Any, ops: list[dict[str, Any]]) -> tuple[dict[str, Any], Any]:
    """(edited payload, problems) of `ops` replayed on `env`'s generated model."""
    t = TYPES[type_id]
    applied = apply_ops(t.to_doc(t.build_model(env)), clean_ops(ops), t.edit_spec)
    return applied.payload, applied.problems


def laid_out(type_id: str, payload: dict[str, Any]) -> list[S.Scene]:
    t = TYPES[type_id]
    return t.layout(t.from_doc(payload), t.page)


def ids(type_id: str, env: Any) -> set[str]:
    t = TYPES[type_id]
    return set(walk(t.to_doc(t.build_model(env)), t.edit_spec))


def report_titles(env: Any) -> tuple[list[str], list[Any]]:
    """Section headings and blocks of the report for `env`."""
    from ntnxvision_core.exporters import ExportOptions
    from ntnxvision_core.exporters.content import build_report

    r = build_report(env, ExportOptions(), "Report")
    assert r.warnings == []
    heads = [s.heading for ch in r.chapters for s in ch.sections]
    blocks = [b for ch in r.chapters for s in ch.sections for b in s.blocks]
    return heads, blocks
