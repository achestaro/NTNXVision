#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Cross-OS check of portable snapshot files (.ntnxsnap), used by CI.

    python scripts/snapshot-roundtrip.py export <dir>
    python scripts/snapshot-roundtrip.py import <dir> [--expect-platform macos|windows]

``export`` collects pc01 from the built-in demo estate (NTNXVISION_DEMO=1, no network),
then writes ``<dir>/pc01.ntnxsnap`` (plain), ``<dir>/pc01-encrypted.ntnxsnap`` (password
``roundtrip``) and ``<dir>/expected.json`` (counts, snapshot id, inventory totals).

``import`` imports both files into a fresh profile and checks that the snapshot id, the
counts and the inventory totals match what the exporting machine recorded, that Compare
and an Excel re-export work, and (with --expect-platform) that the files really came from
the other OS. Run it with the core's venv Python. Exits 0 on success, 1 on any mismatch.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PASSWORD = "roundtrip"


def _setup(data_dir: Path) -> Any:
    os.environ["NTNXVISION_DEMO"] = "1"
    os.environ["NTNXVISION_DATA_DIR"] = str(data_dir)
    os.environ.setdefault("NTNXVISION_DEMO_CERT_DIR", str(data_dir / "certs"))
    from ntnxvision_core.synthetic import demo

    demo.enable(latency_ms=0)
    import ntnxvision_core.sidecar as sc

    sc._send = lambda message: None  # in-process: no stdout notifications
    return sc


def _totals(sc: Any, snapshot: str) -> dict[str, Any]:
    out = sc._m_query_inventory(None, {"snapshot": snapshot, "object_type": "vms",
                                       "filter": {}, "sort": None,
                                       "page": {"offset": 0, "limit": 5}})
    return {t["type"]: t["count"] for t in out["type_counts"]}


def do_export(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    sc = _setup(Path(tempfile.mkdtemp(prefix="ntnxvision-rt-export-")))
    result = sc._m_inventory(None, {"host": "pc01.demo.local", "username": "admin",
                                    "password": "demo", "port": 9440, "insecure": False,
                                    "name": "pc01-roundtrip", "formats": []})
    snap = result["snapshot_path"]
    plain = sc._m_export_snapshot_file(None, {"snapshot": snap,
                                              "dest_path": str(out_dir / "pc01.ntnxsnap"),
                                              "note": f"CI on {sys.platform}"})
    enc = sc._m_export_snapshot_file(None, {"snapshot": snap, "password": PASSWORD,
                                            "dest_path": str(out_dir / "pc01-encrypted")})
    from ntnxvision_core.store import transfer

    expected = {
        "snapshot_id": plain["snapshots"][0]["snapshot_id"],
        "name": "pc01-roundtrip",
        "summary": result["summary"],
        "totals": _totals(sc, snap),
        "platform": transfer.platform_name(),
    }
    (out_dir / "expected.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
    print(f"exported {plain['name']} ({plain['size_bytes']:,} bytes) and {enc['name']} "
          f"on {expected['platform']}")
    return 0


def do_import(in_dir: Path, expect_platform: str | None) -> int:
    expected = json.loads((in_dir / "expected.json").read_text(encoding="utf-8"))
    sc = _setup(Path(tempfile.mkdtemp(prefix="ntnxvision-rt-import-")))
    failures: list[str] = []

    def check(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, expected {want!r}")

    preview = sc._m_inspect_snapshot_file(None, {"path": str(in_dir / "pc01.ntnxsnap")})
    check("exporter platform", preview["exporter"]["platform"], expected["platform"])
    if expect_platform:
        check("file came from", preview["exporter"]["platform"], expect_platform)
    check("preview name", preview["snapshots"][0]["name"], expected["name"])

    first = sc._m_import_snapshot_file(None, {"path": str(in_dir / "pc01.ntnxsnap")})
    imported = first["imported"][0]
    check("snapshot id", imported["snapshot_id"], expected["snapshot_id"])
    check("name", imported["name"], expected["name"])
    detail = sc._m_get_snapshot(None, {"snapshot": imported["path"]})
    check("summary", detail["summary"], expected["summary"])
    check("inventory totals", _totals(sc, imported["path"]), expected["totals"])
    check("provenance platform", detail["imported"]["imported_from"]["platform"],
          expected["platform"])

    second = sc._m_import_snapshot_file(None, {"path": str(in_dir / "pc01-encrypted.ntnxsnap"),
                                               "password": PASSWORD,
                                               "on_conflict": "keep_both"})
    copy = second["imported"][0]
    check("encrypted copy action", copy["action"], "kept_both")
    check("encrypted copy totals", _totals(sc, copy["path"]), expected["totals"])

    cmp = sc._m_compare_snapshots(None, {"baseline": imported["path"], "current": copy["path"]})
    check("compare of identical snapshots", cmp["summary"]["total"], 0)
    report = sc._m_export(None, {"snapshot": imported["path"], "formats": ["excel"]})
    check("excel re-export errors", report["errors"], {})

    for line in failures:
        print(f"FAIL {line}")
    if failures:
        return 1
    print(f"imported {expected['name']} from {expected['platform']} on "
          f"{sys.platform}: counts, inventory, compare and re-export match")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("mode", choices=["export", "import"])
    parser.add_argument("dir", type=Path)
    parser.add_argument("--expect-platform", choices=["macos", "windows", "linux"])
    args = parser.parse_args()
    if args.mode == "export":
        return do_export(args.dir)
    return do_import(args.dir, args.expect_platform)


if __name__ == "__main__":
    sys.exit(main())
