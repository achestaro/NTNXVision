"""Report file names: ``<prefix>-<snapshot>-<YYYY-MM-DD>.<ext>``, sanitized, never overwriting.

`exporters.naming` is the one place report names are built; the sidecar uses it for a
collection's reports, a re-export and the Compare export alike.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

import ntnxvision_core.sidecar as sc
from ntnxvision_core.exporters.naming import (
    PREFIX_MAX,
    export_date,
    report_path,
    report_stem,
    sanitize_prefix,
    unique_report_path,
)

DAY = date(2026, 10, 16)


# ─── sanitizing the prefix ─────────────────────────────────────────────


@pytest.mark.parametrize(("raw", "expected"), [
    ("", ""),
    (None, ""),
    ("   ", ""),
    ("ACME", "ACME"),
    ("  ACME  ", "ACME"),
    ("ACME Corp", "ACME Corp"),
    ('a<b>c:d"e/f\\g|h?i*j', "a_b_c_d_e_f_g_h_i_j"),
    ("tab\there", "tab_here"),
    ("trailing dots...", "trailing dots"),
    ("-dashes-", "dashes"),
    ("__under__", "under"),
    ("CON", "CON_"),
    ("nul", "nul_"),
    ("lpt1.txt", "lpt1_.txt"),
    ("Zürich", "Zürich"),
    ("..", ""),
    ("///", ""),
])
def test_sanitize_prefix(raw: Any, expected: str) -> None:
    assert sanitize_prefix(raw) == expected


def test_prefix_is_capped() -> None:
    out = sanitize_prefix("x" * 200)
    assert len(out) == PREFIX_MAX
    assert len(sanitize_prefix("ab " * 40)) <= PREFIX_MAX
    assert not sanitize_prefix("ab " * 40).endswith(" ")


# ─── the stem ──────────────────────────────────────────────────────────


def test_stem_with_and_without_prefix() -> None:
    assert report_stem("pc01-week1", "ACME", DAY) == "ACME-pc01-week1-2026-10-16"
    assert report_stem("pc01-week1", "", DAY) == "pc01-week1-2026-10-16"
    assert report_stem("pc01-week1", None, DAY) == "pc01-week1-2026-10-16"
    assert report_stem("pc01-week1", " / ", DAY) == "pc01-week1-2026-10-16"


def test_stem_sanitizes_the_snapshot_name_too() -> None:
    assert report_stem("a:b", "", DAY) == "a_b-2026-10-16"
    assert report_stem("", "", DAY) == "report-2026-10-16"


def test_export_date_is_iso_local_date() -> None:
    assert export_date(DAY) == "2026-10-16"
    assert export_date() == date.today().isoformat()


def test_long_names_keep_prefix_and_date() -> None:
    stem = report_stem("s" * 300, "ACME", DAY, max_len=60)
    assert len(stem) <= 60
    assert stem.startswith("ACME-s") and stem.endswith("-2026-10-16")


def test_deep_folders_shorten_the_stem(tmp_path: Path) -> None:
    deep = tmp_path / ("d" * max(1, 200 - len(str(tmp_path)) - 1))
    deep.mkdir()
    assert len(str(deep)) <= 200
    path = report_path(deep, "n" * 120, "docx", "ACME", DAY)
    assert len(str(path)) <= 259
    assert path.name.startswith("ACME-n") and path.name.endswith("-2026-10-16.docx")


# ─── never overwrite ───────────────────────────────────────────────────


def test_existing_files_get_a_numbered_suffix(tmp_path: Path) -> None:
    first = report_path(tmp_path, "pc01", "xlsx", "ACME", DAY)
    assert first.name == "ACME-pc01-2026-10-16.xlsx"
    first.write_bytes(b"x")
    second = report_path(tmp_path, "pc01", "xlsx", "ACME", DAY)
    assert second.name == "ACME-pc01-2026-10-16-2.xlsx"
    second.write_bytes(b"x")
    assert report_path(tmp_path, "pc01", "xlsx", "ACME", DAY).name == "ACME-pc01-2026-10-16-3.xlsx"
    # Another extension does not collide.
    assert report_path(tmp_path, "pc01", "pdf", "ACME", DAY).name == "ACME-pc01-2026-10-16.pdf"


def test_collisions_ignore_case(tmp_path: Path) -> None:
    (tmp_path / "ACME-PC01-2026-10-16.XLSX").write_bytes(b"x")
    assert unique_report_path(tmp_path, "acme-pc01-2026-10-16", "xlsx").name == \
        "acme-pc01-2026-10-16-2.xlsx"


def test_missing_folder_is_not_an_error(tmp_path: Path) -> None:
    assert unique_report_path(tmp_path / "nope", "a", "pdf").name == "a.pdf"


# ─── through the sidecar ───────────────────────────────────────────────


def _collect(data_dir: Path, **extra: Any) -> dict[str, Any]:
    return sc._m_inventory(None, {
        "host": "pc01.demo.local", "username": "admin", "password": "demo", "port": 9440,
        "insecure": False, "name": "pc01-week1", "export_dir": str(data_dir / "exports"),
        **extra,
    })


def test_collection_reports_use_the_prefix(demo: Any, data_dir: Path) -> None:
    day = date.today().isoformat()
    result = _collect(data_dir, formats=["excel"], file_prefix="ACME")
    assert [Path(r["path"]).name for r in result["reports"]] == [f"ACME-pc01-week1-{day}.xlsx"]
    assert result["reports"][0]["name"] == f"ACME-pc01-week1-{day}.xlsx"


def test_reexport_names_and_never_overwrites(demo: Any, data_dir: Path) -> None:
    day = date.today().isoformat()
    snap = _collect(data_dir, formats=[])["snapshot_path"]
    out = str(data_dir / "exports")
    first = sc._m_export(None, {"snapshot": snap, "formats": ["excel", "pdf"],
                                "export_dir": out})
    assert sorted(Path(p).name for p in first["paths"].values()) == [
        f"pc01-week1-{day}.pdf", f"pc01-week1-{day}.xlsx"]
    again = sc._m_export(None, {"snapshot": snap, "formats": ["excel"], "export_dir": out})
    assert Path(again["path"]).name == f"pc01-week1-{day}-2.xlsx"
    prefixed = sc._m_export(None, {"snapshot": snap, "formats": ["excel"], "export_dir": out,
                                   "file_prefix": " Q4 review: "})
    assert Path(prefixed["path"]).name == f"Q4 review-pc01-week1-{day}.xlsx"
    assert prefixed["files"][0]["name"] == f"Q4 review-pc01-week1-{day}.xlsx"


def test_multi_pc_reports_use_the_prefix(demo: Any, data_dir: Path) -> None:
    day = date.today().isoformat()
    res = sc._m_inventory_multi(None, {
        "targets": [
            {"host": h, "username": "admin", "password": "demo", "port": 9440,
             "insecure": False, "name": h.split(".")[0]}
            for h in ("pc01.demo.local", "pc02.demo.local")],
        "formats": ["excel"], "export_dir": str(data_dir / "exports"),
        "combine": True, "combined_name": "estate", "file_prefix": "ACME",
    })
    names = sorted(Path(r["path"]).name for t in res["targets"] for r in t["reports"])
    assert names == [f"ACME-pc01-{day}.xlsx", f"ACME-pc02-{day}.xlsx"]
    assert [Path(r["path"]).name for r in res["combined"]["reports"]] == [
        f"ACME-estate-{day}.xlsx"]
