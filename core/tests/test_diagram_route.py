"""The channel router (`diagrams/route.py`) and the crossing budget of the figures that
route edges through gutters and channels (management plane, DR design)."""

from __future__ import annotations

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
