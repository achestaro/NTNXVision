"""Diagrams do not depend on Python's per-process hash seed (set / dict-of-set order).

Each run is a fresh interpreter with its own PYTHONHASHSEED; every registered diagram
type is laid out and rendered (scene JSON, live and outline SVG, the .vsdx and the
.drawio) for estates with many nested lists, and the digests must agree.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = r"""
import hashlib, sys
from pathlib import Path
from ntnxvision_core.diagrams import TYPES
from ntnxvision_core.diagrams.render.drawio import render_drawio
from ntnxvision_core.diagrams.render.svg import render_svg
from ntnxvision_core.diagrams.render.vsdx import render_vsdx
from ntnxvision_core.diagrams.scene import scene_json
from ntnxvision_core.models import Environment

h = hashlib.sha256()
for path in sys.argv[1:]:
    env = Environment.model_validate_json(Path(path).read_text())
    scenes = []
    for tid, t in TYPES.items():
        if t.applies(env):
            scenes += t.layout(t.build_model(env), t.page)
    for sc in scenes:
        h.update(scene_json(sc))
        h.update(render_svg(sc))
        h.update(render_svg(sc, mode="outline"))
    h.update(render_vsdx(scenes, created="2026-10-01T00:00:00Z"))
    h.update(render_drawio(scenes))
print(h.hexdigest())
"""


@pytest.mark.parametrize("names", [("medium", "multi3", "enterprise")])
def test_output_is_independent_of_the_hash_seed(design_envs: dict[str, Any], tmp_path: Path,
                                                names: tuple[str, ...]) -> None:
    paths = []
    for n in names:
        p = tmp_path / f"{n}.json"
        p.write_text(design_envs[n].model_dump_json())
        paths.append(str(p))
    digests = set()
    for seed in ("0", "1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run([sys.executable, "-c", SCRIPT, *paths], env=env,
                             capture_output=True, text=True, check=True,
                             cwd=Path(__file__).parents[1])
        digests.add(out.stdout.strip())
    assert len(digests) == 1, digests
