"""Portable snapshot files (.ntnxsnap): export, inspect, import, and their safety checks."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any

import pytest

import ntnxvision_core.sidecar as sc
from ntnxvision_core.models import Environment
from ntnxvision_core.store import snapshot as snapstore
from ntnxvision_core.store import transfer
from ntnxvision_core.store.names import is_reserved, max_stem_for, safe_name, unique_name
from ntnxvision_core.store.snapshot import SnapshotStore

SECRET = "Sup3r-S3cret-Pa55word!"


def _run(host: str, name: str, data_dir: Path, **extra: Any) -> dict[str, Any]:
    return sc._m_inventory(None, {
        "host": host, "username": "admin", "password": SECRET, "port": 9440,
        "insecure": False, "name": name, "formats": extra.pop("formats", []),
        "export_dir": str(data_dir / "exports"), **extra,
    })


@pytest.fixture
def snaps(demo: Any, data_dir: Path) -> tuple[Path, Path]:
    first = _run("pc01.demo.local", "pc01-week1", data_dir, formats=["excel"])
    second = _run("pc01.demo.local", "pc01-week2", data_dir)
    return Path(first["snapshot_path"]), Path(second["snapshot_path"])


def _store() -> SnapshotStore:
    return sc._store()


def _export(snap: Path, dest: Path, **params: Any) -> dict[str, Any]:
    return sc._m_export_snapshot_file(None, {"snapshot": str(snap), "dest_path": str(dest),
                                             **params})


def _import(path: Path, **params: Any) -> dict[str, Any]:
    return sc._m_import_snapshot_file(None, {"path": str(path), **params})


def _members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


def _rewrite(path: Path, members: dict[str, bytes], *, fix_checksums: bool = False) -> None:
    if fix_checksums:
        manifest = json.loads(members["manifest.json"])
        manifest["checksums"] = {n: transfer._sha256(d) for n, d in members.items()
                                 if n != "manifest.json"}
        members["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(zipfile.ZipInfo(name), data)


def _query(snap: Path) -> dict[str, Any]:
    out = sc._m_query_inventory(None, {"snapshot": str(snap), "object_type": "vms",
                                       "filter": {"text": "", "facets": {}},
                                       "sort": {"key": "name", "dir": "asc"},
                                       "page": {"offset": 0, "limit": 50}})
    return {k: out[k] for k in ("rows", "total", "type_counts", "facets", "columns")}


# ─── round trip ─────────────────────────────────────────────────────────


def test_round_trip_is_identical(snaps: tuple[Path, Path], tmp_path: Path) -> None:
    week1, week2 = snaps
    before_query = _query(week1)
    before_compare = sc._m_compare_snapshots(None, {"baseline": str(week1),
                                                    "current": str(week2)})
    originals = {p.name: sc._cache.get(p) for p in snaps}
    expected = {name: transfer.portable_environment(env).model_dump_json()
                for name, env in originals.items()}

    files = []
    for snap in snaps:
        out = _export(snap, tmp_path / snap.name.replace(".ntnxsnap.json", ""),
                      note="Shared for the design review")
        assert out["path"].endswith(".ntnxsnap") and out["size_bytes"] > 0
        files.append(Path(out["path"]))
    for snap in snaps:
        sc._m_delete_snapshot(None, {"snapshot": str(snap)})
    assert sc._m_list_snapshots(None, {})["snapshots"] == []

    for f in files:
        result = _import(f)
        assert result["count"] == 1 and result["imported"][0]["action"] == "imported"

    listed = sc._m_list_snapshots(None, {})["snapshots"]
    assert [s["name"] for s in listed] == ["pc01-week2", "pc01-week1"]
    for s in listed:
        assert s["imported"]["imported_from"]["note"] == "Shared for the design review"
        assert s["imported"]["imported_from"]["platform"] == transfer.platform_name()
        env = sc._cache.get(s["path"])
        assert env.model_dump_json() == expected[Path(s["path"]).name]
        # The payload in the file is exactly what was imported.
        payload = _members(next(f for f in files if f.name.startswith(s["name"])))
        env_member = next(k for k in payload if k.endswith("environment.json"))
        assert Environment.model_validate_json(payload[env_member]) == env

    new1 = Path(next(s["path"] for s in listed if s["name"] == "pc01-week1"))
    new2 = Path(next(s["path"] for s in listed if s["name"] == "pc01-week2"))
    assert _query(new1) == before_query
    after_compare = sc._m_compare_snapshots(None, {"baseline": str(new1), "current": str(new2)})
    for key in ("summary", "figures", "changes", "days"):
        assert after_compare[key] == before_compare[key]
    # Overview orders by collection time and treats imported snapshots like local ones.
    overview = sc._m_get_overview(None, {})
    assert overview["latest"]["name"] == "pc01-week2"
    assert overview["compare_baseline"] == str(new1)
    detail = sc._m_get_snapshot(None, {"snapshot": str(new1)})
    assert detail["imported"]["original_name"] == "pc01-week1"
    assert detail["run"] is not None
    # Re-export to the report formats works offline from the imported snapshot.
    exported = sc._m_export(None, {"snapshot": str(new1), "formats": ["excel"],
                                   "export_dir": str(tmp_path / "reports")})
    assert exported["errors"] == {} and Path(exported["path"]).is_file()
    renamed = sc._m_rename_snapshot(None, {"snapshot": str(new1), "new_name": "renamed"})
    assert sc._m_get_snapshot(None, {"snapshot": renamed["path"]})["imported"] is not None


def test_manifest_fields(snaps: tuple[Path, Path], tmp_path: Path) -> None:
    out = _export(snaps[0], tmp_path / "one", note="  Alberto\nat Acme  ")
    members = _members(Path(out["path"]))
    m = json.loads(members["manifest.json"])
    assert m["format"] == "ntnxvision-snapshot" and m["format_version"] == 1
    assert m["schema_version"] == 1 and m["app_version"]
    assert m["exporter"] == {"app_version": m["app_version"], "platform": transfer.platform_name(),
                             "note": "Alberto at Acme"}
    assert m["exported_at"].endswith("+00:00") and m["collected_at"].endswith("+00:00")
    assert m["name"] == "pc01-week1" and m["source"]["kind"] == "pc"
    assert m["source"]["host"] == "pc01.demo.local" and m["source"]["counts"]["vms"] > 0
    assert m["encryption"] is None
    assert set(m["checksums"]) == set(members) - {"manifest.json"}
    for name in members:
        assert "\\" not in name and not name.startswith("/")
    # Every saved snapshot has a stable id, reused by every export.
    again = _members(Path(_export(snaps[0], tmp_path / "two")["path"]))
    assert json.loads(again["manifest.json"])["snapshot_id"] == m["snapshot_id"]


def test_snapshot_without_id_gets_one_on_export(snaps: tuple[Path, Path],
                                                tmp_path: Path) -> None:
    snap = snaps[0]
    data = json.loads(snap.read_text(encoding="utf-8"))
    data.pop("snapshot_id")
    data.pop("schema_version")
    snap.write_text(json.dumps(data), encoding="utf-8")
    sc._cache.invalidate()
    out = _export(snap, tmp_path / "legacy")
    sid = out["snapshots"][0]["snapshot_id"]
    assert sid and json.loads(snap.read_text(encoding="utf-8"))["snapshot_id"] == sid
    assert _export(snap, tmp_path / "legacy2")["snapshots"][0]["snapshot_id"] == sid


def test_batch_export_to_folder_and_bundle(snaps: tuple[Path, Path], tmp_path: Path) -> None:
    folder = tmp_path / "out"
    folder.mkdir()
    (folder / "pc01-week1.ntnxsnap").write_bytes(b"existing")
    out = sc._m_export_snapshot_files(None, {"snapshots": [str(p) for p in snaps],
                                             "dest_dir": str(folder)})
    assert out["errors"] == []
    assert sorted(Path(f["path"]).name for f in out["files"]) == [
        "pc01-week1-2.ntnxsnap", "pc01-week2.ntnxsnap"]

    bundle = sc._m_export_snapshot_file(None, {"snapshots": [str(p) for p in snaps],
                                               "dest_path": str(tmp_path / "both.ntnxsnap")})
    assert len(bundle["snapshots"]) == 2
    preview = sc._m_inspect_snapshot_file(None, {"path": bundle["path"]})
    assert [s["conflict"] is not None for s in preview["snapshots"]] == [True, True]
    for p in snaps:
        sc._m_delete_snapshot(None, {"snapshot": str(p)})
    assert _import(Path(bundle["path"]))["count"] == 2
    assert len(sc._m_list_snapshots(None, {})["snapshots"]) == 2


# ─── inspect ────────────────────────────────────────────────────────────


def test_inspect_preview(snaps: tuple[Path, Path], tmp_path: Path) -> None:
    path = Path(_export(snaps[0], tmp_path / "x", note="from me")["path"])
    p = sc._m_inspect_snapshot_file(None, {"path": str(path)})
    assert p["encrypted"] is False and p["locked"] is False
    assert p["exporter"]["note"] == "from me" and p["size_bytes"] == path.stat().st_size
    (s,) = p["snapshots"]
    assert s["name"] == "pc01-week1" and s["kind"] == "pc" and s["source"] == "pc01.demo.local"
    assert s["summary"]["vms"] > 0 and s["collected_at"].endswith("+00:00")
    assert s["conflict"]["name"] == "pc01-week1"
    assert p["conflicts"] == 1
    # Inspect never writes.
    assert len(sc._m_list_snapshots(None, {})["snapshots"]) == 2


# ─── duplicates and names ───────────────────────────────────────────────


def test_duplicate_choices(snaps: tuple[Path, Path], tmp_path: Path) -> None:
    path = Path(_export(snaps[0], tmp_path / "dup")["path"])
    original_id = sc._cache.get(snaps[0]).snapshot_id

    asked = _import(path, on_conflict="ask")
    assert asked["needs_decision"] and asked["conflicts"][0]["name"] == "pc01-week1"
    skipped = _import(path, on_conflict="skip")
    assert skipped["skipped"] == 1 and skipped["count"] == 0
    assert len(sc._m_list_snapshots(None, {})["snapshots"]) == 2

    both = _import(path, on_conflict="keep_both")["imported"][0]
    assert both["action"] == "kept_both" and both["name"] == "pc01-week1-2"
    assert both["snapshot_id"] != original_id
    meta = snapstore.read_meta(Path(both["path"]))
    assert meta["provenance"]["original_snapshot_id"] == original_id

    replaced = _import(path, on_conflict="replace")["imported"][0]
    assert replaced["action"] == "replaced" and replaced["snapshot_id"] == original_id
    ids = [sc._cache.get(s["path"]).snapshot_id
           for s in sc._m_list_snapshots(None, {})["snapshots"]]
    assert ids.count(original_id) == 1 and len(ids) == 3


def test_name_collision_with_other_snapshot_is_suffixed(snaps: tuple[Path, Path],
                                                        tmp_path: Path) -> None:
    path = Path(_export(snaps[0], tmp_path / "c")["path"])
    sc._m_delete_snapshot(None, {"snapshot": str(snaps[0])})
    # A different snapshot now holds the name, in another case.
    sc._m_rename_snapshot(None, {"snapshot": str(snaps[1]), "new_name": "PC01-WEEK1"})
    result = _import(path)["imported"][0]
    assert result["action"] == "imported" and result["name"] == "pc01-week1-2"
    assert result["renamed"] is True


def test_windows_reserved_and_illegal_names() -> None:
    for name in ("CON", "con", "Nul", "COM1", "lpt9", "AUX", "prn", "COM¹"):
        assert is_reserved(name)
        assert not is_reserved(safe_name(name))
    assert safe_name("nul.txt") == "nul_.txt" and not is_reserved(safe_name("nul.txt"))
    assert safe_name('a<b>c:d"e/f\\g|h?i*j') == "a_b_c_d_e_f_g_h_i_j"
    assert safe_name("trailing. . ") == "trailing"
    assert safe_name("...") == "snapshot"
    assert safe_name("tab\there\x00") == "tab_here_"
    assert safe_name("Cafe\u0301") == "Caf\u00e9"  # macOS NFD → NFC
    assert len(safe_name("x" * 400)) == 120
    assert max_stem_for("C:\\" + "d" * 200) == 37
    assert max_stem_for("C:\\" + "d" * 230) == 16
    assert unique_name("pc01", ["PC01", "pc01-2"]) == "pc01-3"


def test_traversal_in_name_is_neutralised() -> None:
    assert safe_name("..\\..\\evil") == "_.._evil"
    assert safe_name("../../evil") == "_.._evil"


def test_import_sanitises_hostile_name(snaps: tuple[Path, Path], tmp_path: Path) -> None:
    sc._m_rename_snapshot(None, {"snapshot": str(snaps[0]), "new_name": "keep"})
    item = sc._export_item(str(_store().path_for("keep")))
    item.meta["name"] = "CON"
    path = Path(transfer.export_file([item], tmp_path / "con")["path"])
    members = _members(path)
    meta_name = next(k for k in members if k.endswith("meta.json"))
    meta = json.loads(members[meta_name])
    meta["name"] = "CON. "
    members[meta_name] = json.dumps(meta).encode()
    _rewrite(path, members, fix_checksums=True)
    result = _import(path, on_conflict="keep_both")["imported"][0]
    assert result["name"] == "CON_"
    saved = Path(result["path"])
    assert saved.parent == _store().directory and saved.name == "CON_.ntnxsnap.json"


# ─── safety ─────────────────────────────────────────────────────────────


def test_checksum_tamper_detected(snaps: tuple[Path, Path], tmp_path: Path) -> None:
    path = Path(_export(snaps[0], tmp_path / "t")["path"])
    members = _members(path)
    env_name = next(k for k in members if k.endswith("environment.json"))
    members[env_name] = members[env_name].replace(b"pc01.demo.local", b"pc99.demo.local", 1)
    _rewrite(path, members)
    with pytest.raises(transfer.InvalidSnapshotFile, match="integrity"):
        _import(path)
    # An extra, unlisted member is refused too.
    members = _members(Path(_export(snaps[0], tmp_path / "t2")["path"]))
    members["snapshots/extra.json"] = b"{}"
    _rewrite(tmp_path / "t2.ntnxsnap", members)
    with pytest.raises(transfer.InvalidSnapshotFile, match="unexpected"):
        _import(tmp_path / "t2.ntnxsnap")


@pytest.mark.parametrize("name", ["../evil.json", "snapshots/../../evil.json", "/etc/passwd",
                                  "C:/Windows/evil.json", "c:evil.json", "a\\..\\evil.json",
                                  "snapshots//x.json", "./x.json", "nul\x00.json"])
def test_unsafe_member_names_rejected(name: str, tmp_path: Path, data_dir: Path) -> None:
    with pytest.raises(transfer.InvalidSnapshotFile):
        transfer.check_member_name(name)
    if "\x00" in name:
        return
    path = tmp_path / "slip.ntnxsnap"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("manifest.json"), b"{}")
        zf.writestr(zipfile.ZipInfo(name), b"{}")
    with pytest.raises(transfer.InvalidSnapshotFile):
        _import(path)
    assert not (tmp_path.parent / "evil.json").exists()


def test_zip_bomb_limits(snaps: tuple[Path, Path], tmp_path: Path) -> None:
    path = Path(_export(snaps[0], tmp_path / "b")["path"])
    store = _store()
    with pytest.raises(transfer.InvalidSnapshotFile, match="too large"):
        transfer.import_file(path, store, limits=transfer.Limits(max_member_bytes=1000))
    with pytest.raises(transfer.InvalidSnapshotFile, match="import limit"):
        transfer.import_file(path, store, limits=transfer.Limits(max_total_bytes=1000))
    with pytest.raises(transfer.InvalidSnapshotFile, match="too large"):
        transfer.import_file(path, store, limits=transfer.Limits(max_archive_bytes=100))
    bomb = tmp_path / "bomb.ntnxsnap"
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", b"{}")
        zf.writestr("snapshots/x/environment.json", b"\0" * (64 * 1024**2))
    with pytest.raises(transfer.InvalidSnapshotFile, match="zip bomb"):
        _import(bomb)
    many = tmp_path / "many.ntnxsnap"
    with zipfile.ZipFile(many, "w") as zf:
        for i in range(transfer.DEFAULT_LIMITS.max_members + 1):
            zf.writestr(f"m{i}", b"")
    with pytest.raises(transfer.InvalidSnapshotFile, match="too many"):
        _import(many)


def test_not_a_snapshot_file(tmp_path: Path, data_dir: Path) -> None:
    junk = tmp_path / "junk.ntnxsnap"
    junk.write_bytes(b"PK not really a zip")
    with pytest.raises(transfer.InvalidSnapshotFile, match="not an NTNXVision"):
        _import(junk)
    other = tmp_path / "other.ntnxsnap"
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": "something-else"}))
    with pytest.raises(transfer.InvalidSnapshotFile, match="not an NTNXVision"):
        _import(other)


def test_newer_versions_refused(snaps: tuple[Path, Path], tmp_path: Path) -> None:
    path = Path(_export(snaps[0], tmp_path / "n")["path"])
    members = _members(path)
    m = json.loads(members["manifest.json"])
    m["format_version"] = transfer.FORMAT_VERSION + 1
    members["manifest.json"] = json.dumps(m).encode()
    _rewrite(path, members)
    with pytest.raises(transfer.UnsupportedVersion, match="Update NTNXVision"):
        _import(path)
    with pytest.raises(transfer.UnsupportedVersion):
        sc._m_inspect_snapshot_file(None, {"path": str(path)})

    members = _members(Path(_export(snaps[0], tmp_path / "s")["path"]))
    env_name = next(k for k in members if k.endswith("environment.json"))
    data = json.loads(members[env_name])
    data["schema_version"] = 99
    members[env_name] = json.dumps(data).encode()
    _rewrite(tmp_path / "s.ntnxsnap", members, fix_checksums=True)
    with pytest.raises(transfer.UnsupportedVersion, match="Update NTNXVision"):
        _import(tmp_path / "s.ntnxsnap")


def test_older_schema_is_migrated(snaps: tuple[Path, Path], tmp_path: Path) -> None:
    path = Path(_export(snaps[0], tmp_path / "old")["path"])
    members = _members(path)
    env_name = next(k for k in members if k.endswith("environment.json"))
    data = json.loads(members[env_name])
    sid = data.pop("snapshot_id")
    data.pop("schema_version")  # a payload from before either field existed
    members[env_name] = json.dumps(data).encode()
    m = json.loads(members["manifest.json"])
    m["schema_version"] = 0
    members["manifest.json"] = json.dumps(m).encode()
    _rewrite(path, members, fix_checksums=True)
    sc._m_delete_snapshot(None, {"snapshot": str(snaps[0])})
    result = _import(path)["imported"][0]
    env = sc._cache.get(result["path"])
    assert env.snapshot_id == sid and env.schema_version == 1


def test_no_secrets_in_archive(snaps: tuple[Path, Path], tmp_path: Path) -> None:
    path = Path(_export(snaps[0], tmp_path / "s", note="hello")["path"])
    raw = path.read_bytes()
    members = _members(path)
    blob = b"".join([raw, *members.values()])
    assert SECRET.encode() not in blob
    assert b"keychain" not in blob.lower() and b"keytar" not in blob.lower()

    def keys(value: Any) -> list[str]:
        if isinstance(value, dict):
            return [*value.keys(), *(k for v in value.values() for k in keys(v))]
        if isinstance(value, list):
            return [k for v in value for k in keys(v)]
        return []

    for data in members.values():
        for key in keys(json.loads(data)):
            assert not re.search(r"password|secret|token|credential|api_?key", key, re.I), key
    # Local paths never leave the machine: report history travels as names only.
    text = blob.decode("utf-8", "replace")
    assert str(tmp_path) not in text and str(_store().directory) not in text
    meta = json.loads(next(v for k, v in members.items() if k.endswith("meta.json")))
    assert meta["reports"] and all("path" not in r for r in meta["reports"])


def test_run_metadata_is_scrubbed() -> None:
    env = Environment(snapshot_id="abc", collected_at="2026-10-15T09:45:24+02:00",
                      run={"started_at": "2026-10-15T09:41:12+02:00", "token": "x",
                           "reports": [{"format": "excel", "name": "r.xlsx",
                                        "path": "C:\\Users\\me\\r.xlsx"}],
                           "nested": {"apiKey": "k", "password": "p", "ok": 1}})
    out = transfer.portable_environment(env)
    assert out.collected_at == "2026-10-15T07:45:24+00:00"
    assert out.run == {"started_at": "2026-10-15T07:41:12+00:00",
                       "reports": [{"format": "excel", "name": "r.xlsx", "size_bytes": None,
                                    "detail": None}],
                       "nested": {"ok": 1}}
    assert env.run and env.run["token"] == "x"  # the local snapshot is untouched


# ─── encryption ─────────────────────────────────────────────────────────


def test_encrypted_round_trip(snaps: tuple[Path, Path], tmp_path: Path) -> None:
    expected = transfer.portable_environment(sc._cache.get(snaps[0])).model_dump_json()
    path = Path(_export(snaps[0], tmp_path / "enc", password="correct horse")["path"])
    members = _members(path)
    m = json.loads(members["manifest.json"])
    assert m["encryption"]["algorithm"] == "AES-256-GCM" and m["encryption"]["kdf"] == "scrypt"
    assert "name" not in m and "source" not in m
    assert all(k.endswith(".enc") for k in members if k != "manifest.json")
    blob = b"".join(members.values())
    assert b"pc01.demo.local" not in blob and b"pc01-week1" not in blob

    locked = sc._m_inspect_snapshot_file(None, {"path": str(path)})
    assert locked["encrypted"] and locked["locked"] and locked["password_error"] is None
    assert locked["snapshots"][0]["name"] is None
    assert locked["snapshots"][0]["conflict"] is not None  # ids are visible, details are not
    wrong = sc._m_inspect_snapshot_file(None, {"path": str(path), "password": "nope"})
    assert wrong["locked"] and "not correct" in wrong["password_error"]
    unlocked = sc._m_inspect_snapshot_file(None, {"path": str(path),
                                                  "password": "correct horse"})
    assert not unlocked["locked"] and unlocked["snapshots"][0]["name"] == "pc01-week1"

    with pytest.raises(transfer.PasswordRequired):
        _import(path)
    with pytest.raises(transfer.WrongPassword):
        _import(path, password="nope")
    sc._m_delete_snapshot(None, {"snapshot": str(snaps[0])})
    result = _import(path, password="correct horse")["imported"][0]
    assert sc._cache.get(result["path"]).model_dump_json() == expected


def test_encrypted_member_tamper_detected(snaps: tuple[Path, Path], tmp_path: Path) -> None:
    path = Path(_export(snaps[0], tmp_path / "enc", password="pw")["path"])
    members = _members(path)
    env_name = next(k for k in members if k.endswith("environment.json.enc"))
    blob = bytearray(members[env_name])
    blob[40] ^= 0xFF
    members[env_name] = bytes(blob)
    _rewrite(path, members, fix_checksums=True)  # checksums fixed: GCM must still catch it
    with pytest.raises(transfer.WrongPassword):
        _import(path, password="pw")


def test_encrypted_member_inflation_is_bounded(snaps: tuple[Path, Path],
                                               tmp_path: Path) -> None:
    """Encrypted members are compressed inside the ciphertext, so the zip-level size
    checks can't see their real size: decompression is capped separately."""
    path = Path(_export(snaps[0], tmp_path / "enc", password="pw")["path"])
    with zipfile.ZipFile(path) as zf:
        stored = max(i.file_size for i in zf.infolist())
    limits = transfer.Limits(max_member_bytes=stored + 1024)
    with pytest.raises(transfer.InvalidSnapshotFile, match="too large"):
        transfer.import_file(path, _store(), password="pw", limits=limits)
    assert path.stat().st_size < 400_000  # compressed before encryption


# ─── portability ────────────────────────────────────────────────────────


def test_crlf_and_separator_independence(snaps: tuple[Path, Path], tmp_path: Path) -> None:
    """A file whose JSON was re-indented with CRLF line endings (as a Windows tool might
    leave it) imports to the same snapshot; member names are always forward-slash."""
    expected = transfer.portable_environment(sc._cache.get(snaps[0])).model_dump_json()
    path = Path(_export(snaps[0], tmp_path / "crlf")["path"])
    members = _members(path)
    for name in list(members):
        pretty = json.dumps(json.loads(members[name]), indent=2, ensure_ascii=False)
        members[name] = pretty.replace("\n", "\r\n").encode("utf-8")
    assert all("/" in n and "\\" not in n for n in members if n != "manifest.json")
    _rewrite(path, members, fix_checksums=True)
    sc._m_delete_snapshot(None, {"snapshot": str(snaps[0])})
    result = _import(path)["imported"][0]
    assert sc._cache.get(result["path"]).model_dump_json() == expected


def test_windows_style_report_paths_reduced_to_names() -> None:
    reports = [{"format": "word", "path": "C:\\Users\\me\\Documents\\NTNXVision\\x-ADD.docx",
                "size_bytes": 1},
               {"format": "pdf", "name": "/Users/me/y.pdf"}]
    assert [r["name"] for r in transfer._report_names(reports)] == ["x-ADD.docx", "y.pdf"]


def test_sidecar_error_codes(snaps: tuple[Path, Path], tmp_path: Path,
                             monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(sc, "_send", sent.append)
    junk = tmp_path / "junk.ntnxsnap"
    junk.write_bytes(b"nope")
    enc = _export(snaps[0], tmp_path / "e", password="pw")["path"]
    sc._handle({"id": 1, "method": "import_snapshot_file", "params": {"path": str(junk)}})
    sc._handle({"id": 2, "method": "import_snapshot_file", "params": {"path": enc}})
    sc._handle({"id": 3, "method": "import_snapshot_file", "params": {"path": "relative.ntnxsnap"}})
    codes = [m["error"]["code"] for m in sent]
    assert codes == [sc.INVALID_FILE, sc.PASSWORD_ERROR, sc.TRANSFER_ERROR]


def test_export_needs_existing_folder(snaps: tuple[Path, Path], tmp_path: Path) -> None:
    with pytest.raises(transfer.TransferError, match="does not exist"):
        _export(snaps[0], tmp_path / "missing" / "x.ntnxsnap")
    out = _export(snaps[0], tmp_path / "noext")
    assert out["name"] == "noext.ntnxsnap"
    assert not list(tmp_path.glob(".*.tmp"))


def test_import_bytes_never_touch_outside_store(snaps: tuple[Path, Path],
                                                tmp_path: Path) -> None:
    path = Path(_export(snaps[0], tmp_path / "w")["path"])
    before = set(tmp_path.rglob("*"))
    _import(path, on_conflict="keep_both")
    created = {p for p in set(tmp_path.rglob("*")) - before if p.is_file()}
    assert created and all(_store().directory in p.parents for p in created)
