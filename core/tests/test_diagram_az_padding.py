"""Every availability-zone container fits its content: the same padding left and right
of its boxes (no empty run on the right of a part with fewer cards than columns, no
lane margin on one side only)."""

from __future__ import annotations

from typing import Any

import pytest

from ntnxvision_core.diagrams import TYPES
from ntnxvision_core.diagrams import scene as S

ESTATES = ["small", "medium", "enterprise", "xlarge", "pc01", "pc02", "pc03", "large1",
           "large2", "large3", "multi3"]


def _gaps(sc: S.Scene) -> list[tuple[str, float, float]]:
    out = []
    for n in sc.nodes:
        if n.kind != "container" or not n.id.startswith("site:"):
            continue
        kids = [c for c in sc.nodes if c.parent == n.id and c.kind in ("box", "container")]
        if kids:
            out.append((n.id, min(c.x for c in kids) - n.x,
                        n.x + n.w - max(c.x + c.w for c in kids)))
    return out


@pytest.mark.parametrize("name", ESTATES)
def test_az_padding_is_even(design_envs: dict[str, Any], name: str) -> None:
    env = design_envs[name]
    for tid, t in TYPES.items():
        if not t.applies(env):
            continue
        for sc in t.layout(t.build_model(env), t.page):
            for cid, left, right in _gaps(sc):
                assert abs(left - right) <= 1.0, (tid, sc.id, cid, left, right)
