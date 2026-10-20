"""Reports are byte-stable and the Word document stays small enough to mail.

* Two exports of one snapshot give identical .docx and .pdf bytes (fixed zip entry
  times, document dates from the snapshot's collection time).
* Architecture figures embed a palette PNG fallback next to the vector SVG, so even
  the very-large estate stays well under the mailer's 20 MB attachment limit.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Any

import pytest

from ntnxvision_core.exporters import export_environment

#: The mailer's attachment limit (mailer.py) and the cap this suite holds Word to.
MAIL_LIMIT = 20 * 1024 * 1024
WORD_CAP = 12 * 1024 * 1024
#: Largest palette PNG fallback per figure.
PNG_CAP = 350 * 1024


@pytest.mark.parametrize("fmt", ["word", "pdf"])
def test_two_exports_are_byte_identical(design_envs: dict[str, Any], tmp_path: Path,
                                        fmt: str) -> None:
    env = design_envs["medium"]
    a = export_environment(env, fmt, tmp_path / "a", "r").read_bytes()
    b = export_environment(env, fmt, tmp_path / "b", "r").read_bytes()
    assert a == b


def test_docx_entries_carry_fixed_times_and_collection_date(design_envs: dict[str, Any],
                                                            tmp_path: Path) -> None:
    env = design_envs["small"]
    out = export_environment(env, "word", tmp_path, "r")
    with zipfile.ZipFile(out) as z:
        assert {i.date_time for i in z.infolist()} == {(1980, 1, 1, 0, 0, 0)}
        core = z.read("docProps/core.xml").decode()
    assert env.collected_at[:10] in core


def test_xlarge_word_stays_under_the_mail_limit(design_envs: dict[str, Any],
                                                tmp_path: Path) -> None:
    out = export_environment(design_envs["xlarge"], "word", tmp_path, "x")
    size = out.stat().st_size
    assert size < WORD_CAP < MAIL_LIMIT, f"{size:,} bytes"
    with zipfile.ZipFile(io.BytesIO(out.read_bytes())) as z:
        body = z.read("word/document.xml").decode()
        rels = z.read("word/_rels/document.xml.rels").decode()
        # The PNG of every picture that also carries an SVG (the architecture figures).
        ids = re.findall(r'r:embed="(rId\d+)"><a:extLst><a:ext uri="\{96DAC541', body)
        targets = dict(re.findall(r'Id="(rId\d+)"[^>]*Target="([^"]+)"', rels))
        sizes = [z.getinfo(f"word/{targets[i]}").file_size for i in ids]
    assert sizes and max(sizes) <= PNG_CAP, max(sizes)
