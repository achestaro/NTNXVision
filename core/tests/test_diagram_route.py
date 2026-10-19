"""The channel router (`diagrams/route.py`) and the crossing budget of the figures that
route edges through gutters and channels (management plane, DR design)."""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from ntnxvision_core.diagrams import TYPES, check_scene
from ntnxvision_core.diagrams import route as R
from ntnxvision_core.diagrams.scene import Edge, Style

#: Crossings left (drawn as hops) per scene, at most.
BUDGET = {"dr": 1, "management": 3}


def _edge(eid: str, pts: list[tuple[float, float]], dst: str = "t") -> Edge:
    return Edge(id=eid, src=eid, dst=dst, points=pts, style=Style(stroke="#000000"))


def test_spread_orders_attachments_by_their_other_end() -> None:
    got = R.spread(0, 30, [(50.0, "low"), (10.0, "high"), (30.0, "mid")])
    assert got == {"high": 7.5, "mid": 15.0, "low": 22.5}


def test_lane_order_avoids_crossings() -> None:
    # Edge 0 runs from y 10 down to 40, edge 1 from 20 down to 50: with edge 0's lane
    # on the left both runs cross the other lane; with edge 1's lane on the left none.
    assert R.lane_order([(10, 40), (20, 50)]) == [1, 0]


def test_nested_channels_put_inner_spans_first() -> None:
    assert R.nested_channels({"outer": (0, 100), "inner": (20, 40)}) == ["inner", "outer"]


def test_hop_lifts_a_horizontal_run_over_a_vertical_one() -> None:
    h = _edge("h", [(0, 50), (100, 50)], dst="a")
    v = _edge("v", [(40, 0), (40, 100)], dst="b")
    assert len(R.crossings([h, v])) == 1
    assert R.hop([h, v]) == 1
    assert R.crossings([h, v]) == []
    assert min(y for _, y in h.points) < 50  # the hop rises above the run


def test_channel_order_finds_the_crossing_free_order() -> None:
    # Two trunks from one column going right: the one reaching farther must run on
    # the channel nearer the columns (its lane is the outer one).
    near = R.Trunk(stubs=((0, 10),), lane=10, lane_top=10, entry=50, bottom=200)
    far = R.Trunk(stubs=((0, 5),), lane=15, lane_top=5, entry=150, bottom=200)
    order = R.channel_order([near, far], 100, 10)
    ys = [100 + k * 10 for k in order]
    assert R.trunk_crossings([near, far], ys) == 0


@pytest.mark.parametrize("name", ["small", "medium", "enterprise", "xlarge", "multi3",
                                  "pc01", "large1"])
def test_crossing_budget(design_envs: dict[str, Any], name: str) -> None:
    env = design_envs[name]
    for tid, budget in BUDGET.items():
        t = TYPES[tid]
        if not t.applies(env):
            continue
        for sc in t.layout(t.build_model(env), t.page):
            assert sc.meta.get("hops", 0) <= budget, (tid, sc.id, sc.meta.get("hops"))
            assert check_scene(sc) == [], sc.id


@pytest.mark.parametrize("name", ["small", "medium", "enterprise", "xlarge", "large1"])
def test_report_figures_cross_nothing(design_envs: dict[str, Any], name: str) -> None:
    """In the report (part 1 fitted under its summary), no connector of the overview,
    the management plane or the DR design crosses another: lanes take the side of the
    column with fewer gutter flows, trunks from one card nest by where they head, and a
    zone's drops leave its bottom edge in the order of their targets. (Where two buses
    interleave by nature, as on pc01 and multi3, `test_crossing_budget` bounds the hops.)"""
    from ntnxvision_core.exporters import ExportOptions
    from ntnxvision_core.exporters.content import SceneFigure, build_report

    report = build_report(design_envs[name], ExportOptions(), "x")
    for ch in report.chapters:
        for s in ch.sections:
            for b in s.blocks:
                if isinstance(b, SceneFigure) and b.scene.type in ("conceptual", "dr",
                                                                    "management"):
                    sc = b.scene
                    assert not sc.meta.get("hops"), (sc.id, sc.meta.get("hops"))
                    assert R.crossings(sc.edges) == [], sc.id


def _through(x0: float, y0: float, x1: float, y1: float,
             p0: tuple[float, float], p1: tuple[float, float], m: float = 0.5) -> bool:
    """Whether the axis-aligned run p0-p1 passes through the box (x0, y0)-(x1, y1)."""
    (ax, ay), (bx, by) = p0, p1
    if abs(ax - bx) < 0.01:
        return x0 + m < ax < x1 - m and min(ay, by) < y1 - m and max(ay, by) > y0 + m
    if abs(ay - by) < 0.01:
        return y0 + m < ay < y1 - m and min(ax, bx) < x1 - m and max(ax, bx) > x0 + m
    return False


def _on(p0: tuple[float, float], p1: tuple[float, float],
        runs: list[tuple[tuple[float, float], tuple[float, float]]]) -> bool:
    """Whether run p0-p1 lies along one of `runs` (flows sharing one trunk)."""
    for (a, b) in runs:
        if abs(p0[0] - p1[0]) < 0.01 and abs(a[0] - b[0]) < 0.01 and abs(a[0] - p0[0]) < 0.01:
            return True
        if abs(p0[1] - p1[1]) < 0.01 and abs(a[1] - b[1]) < 0.01 and abs(a[1] - p0[1]) < 0.01:
            return True
    return False


def label_overlaps(sc: Any) -> list[tuple[str, str]]:
    """(edge, label owner) for every run of one edge through another edge's label; a
    run along the owner's own line (flows sharing a trunk) does not count."""
    out = []
    for e in sc.edges:
        own = list(zip(e.points, e.points[1:], strict=False))
        for lb in e.labels:
            for o in sc.edges:
                if o is e:
                    continue
                for p0, p1 in zip(o.points, o.points[1:], strict=False):
                    if _through(lb.x, lb.y, lb.x + lb.w, lb.y + lb.h, p0, p1) and \
                            not _on(p0, p1, own):
                        out.append((o.id, e.id))
    return out


@pytest.mark.parametrize("name", ["small", "medium", "enterprise", "xlarge", "pc01"])
def test_no_run_through_another_flows_label(design_envs: dict[str, Any], name: str) -> None:
    """No connector runs through another connector's label, in the report (part 1
    fitted under its summary) or on a page of its own: a trunk's label beside its drop
    takes a side and height clear of the other trunks (xlarge's dr-2 had a drop
    through the Dallas flow's label)."""
    from ntnxvision_core.exporters import ExportOptions
    from ntnxvision_core.exporters.content import SceneFigure, build_report

    env = design_envs[name]
    report = build_report(env, ExportOptions(), "x")
    scenes = [b.scene for ch in report.chapters for s in ch.sections for b in s.blocks
              if isinstance(b, SceneFigure)]
    for t in TYPES.values():
        if t.applies(env):
            scenes += t.layout(t.build_model(env), t.page)
    for sc in scenes:
        assert label_overlaps(sc) == [], (sc.type, sc.id)


@pytest.mark.parametrize("name", ["pc01", "xlarge"])
def test_dr_down_lanes_read_as_separate_lines(design_envs: dict[str, Any], name: str) -> None:
    """Trunks dropping beside one column keep a 12 pt pitch: pc01's three flows to
    pc-dr and its flow to pc02 ran 6 pt apart."""
    t = TYPES["dr"]
    for sc in t.layout(t.build_model(design_envs[name]), t.page):
        vs = [(e.id, p0[0], min(p0[1], p1[1]), max(p0[1], p1[1])) for e in sc.edges
              if e.data.get("kind") == "replication"
              for p0, p1 in zip(e.points, e.points[1:], strict=False)
              if abs(p0[0] - p1[0]) < 0.01 and abs(p0[1] - p1[1]) > 0.01]
        for (i1, x1, a1, b1), (i2, x2, a2, b2) in itertools.combinations(vs, 2):
            if i1 != i2 and 0.5 < abs(x1 - x2) < 11.5 and a1 < b2 and a2 < b1:
                pytest.fail(f"{sc.id}: {i1} and {i2} run {abs(x1 - x2):.1f} pt apart")
