"""Portable snapshot files (``.ntnxsnap``): export a snapshot, send it, import it elsewhere.

A ``.ntnxsnap`` file is a ZIP container that works the same on macOS and Windows:

``manifest.json`` (always plain JSON)
    ``format: "ntnxvision-snapshot"``, ``format_version``, the app and Environment schema
    versions, ``exported_at`` (UTC), the exporter's app version, platform and optional
    note, the encryption parameters (or null), one entry per snapshot (``snapshot_id``,
    ``name``, ``collected_at`` in UTC, ``source`` summary, member paths) and a SHA-256
    checksum of every other member. A single-snapshot file repeats that snapshot's
    ``snapshot_id`` / ``name`` / ``collected_at`` / ``source`` at the top level.
``snapshots/<snapshot_id>/environment.json``
    The `Environment` (the snapshot itself), compact UTF-8 JSON.
``snapshots/<snapshot_id>/meta.json``
    The portable part of the ``.ntnxmeta.json`` index: name, kind, source, summary,
    headline stats, the run's report *names*, any earlier import provenance and the
    saved Diagrams-page edits (``diagrams``, re-validated on import like a local file).

Portability rules: UTF-8 everywhere, forward-slash relative member names, compact JSON
(so line endings never matter), UTC timestamps, and no local file paths or OS-specific
fields (report paths are reduced to file names). Credentials are never part of a
snapshot, so they can never be part of the file; run metadata is scrubbed of any
secret-looking key as a second line of defence.

Optional password encryption: the two payload members become ``…json.enc``, each
``nonce (12 bytes) || AES-256-GCM ciphertext+tag``, authenticated with the member name
as associated data, under a key derived from the password with scrypt (per-file random
salt). The manifest then carries only ``snapshot_id`` and ``collected_at`` per snapshot;
the name, source and counts are inside the encrypted ``meta.json``.

Import validates before it writes anything: archive and member sizes and compression
ratios (zip bombs), member names (zip-slip: ``..``, absolute paths, drive letters,
backslashes), the exact member set, every checksum, the format and schema versions
(older ones are migrated forward, newer ones are refused with an "update NTNXVision"
message) and, for an encrypted file, the password. Nothing is ever extracted by member
name: payloads are read into memory and saved through `SnapshotStore` under a sanitised,
collision-free name.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import sys
import unicodedata
import uuid
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ntnxvision_core import __version__
from ntnxvision_core.models import Environment
from ntnxvision_core.models.environment import ENVIRONMENT_SCHEMA_VERSION
from ntnxvision_core.store import snapshot as snapstore
from ntnxvision_core.store.names import max_stem_for, safe_name, unique_name
from ntnxvision_core.store.snapshot import SnapshotStore

FORMAT = "ntnxvision-snapshot"
#: Bump when the container layout changes; add a step to `_MANIFEST_MIGRATIONS` so files
#: written by older versions keep importing.
FORMAT_VERSION = 1
FILE_SUFFIX = ".ntnxsnap"
MANIFEST = "manifest.json"
MAX_NOTE = 200
MAX_SNAPSHOTS_PER_FILE = 200
CONFLICT_CHOICES = ("keep_both", "replace", "skip", "ask")

# scrypt cost for new files (32 MiB, ~0.1 s), and the most an imported file may demand.
_SCRYPT = {"n": 2**15, "r": 8, "p": 1}
_SCRYPT_MAX = {"n": 2**20, "r": 16, "p": 4}
_KEY_CHECK = b"ntnxvision-snapshot-key-check"
_SECRET_KEY = re.compile(r"pass(word|wd|phrase)?$|secret|token|api[_-]?key|credential|"
                         r"private[_-]?key|authorization|cookie", re.I)
_SNAPSHOT_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


# ─── errors ─────────────────────────────────────────────────────────────


class TransferError(Exception):
    """A snapshot file could not be exported or imported. The message is user-facing."""


class InvalidSnapshotFile(TransferError):
    """Not a snapshot file, damaged, tampered with, or unsafe."""


class UnsupportedVersion(TransferError):
    """Written by a newer NTNXVision than this one."""


class PasswordRequired(TransferError):
    """The file is encrypted and no password was given."""


class WrongPassword(TransferError):
    """The file is encrypted and the password does not open it."""


# ─── limits ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Limits:
    """Ceilings applied before and while reading an archive (zip-bomb protection).

    The defaults leave ample room for a 20 000-VM estate (tens of MB of JSON)."""

    max_archive_bytes: int = 2 * 1024**3
    max_members: int = 2 * MAX_SNAPSHOTS_PER_FILE + 1
    max_member_bytes: int = 1024**3
    max_total_bytes: int = 4 * 1024**3
    max_manifest_bytes: int = 8 * 1024**2
    #: Uncompressed / compressed, checked for members over 1 MiB. JSON reaches ~30:1.
    max_ratio: int = 400


DEFAULT_LIMITS = Limits()


# ─── small helpers ──────────────────────────────────────────────────────


def platform_name() -> str:
    """The exporting OS in portable terms."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def utc_iso(value: Any) -> str | None:
    """An ISO 8601 timestamp converted to UTC (``…+00:00``); unparseable values unchanged."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Full precision: truncating would reorder snapshots collected within one second.
    return dt.astimezone(timezone.utc).isoformat()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_note(note: Any) -> str | None:
    """An "exported by" note: one line of printable text, at most MAX_NOTE characters."""
    if not isinstance(note, str):
        return None
    text = unicodedata.normalize("NFC", note)
    text = "".join(c if c.isprintable() else " " for c in text)
    text = re.sub(r"\s+", " ", text).strip()[:MAX_NOTE].strip()
    return text or None


def _scrub(value: Any) -> Any:
    """Drop secret-looking keys anywhere in a JSON-like structure."""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()
                if not (isinstance(k, str) and _SECRET_KEY.search(k))}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _report_names(reports: Any) -> list[dict[str, Any]]:
    """Report history reduced to names (no local paths)."""
    out = []
    for r in reports or []:
        if not isinstance(r, dict):
            continue
        name = r.get("name") or (Path(str(r["path"])).name if r.get("path") else None)
        if not name:
            continue
        out.append({"format": r.get("format"), "name": str(name).replace("\\", "/").split("/")[-1],
                    "size_bytes": r.get("size_bytes"), "detail": r.get("detail")})
    return out


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(obj: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        return (json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode()
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# ─── encryption ─────────────────────────────────────────────────────────


def _derive_key(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    secret = unicodedata.normalize("NFC", password).encode("utf-8")
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(secret)


def _encrypt(key: bytes, data: bytes, aad: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, data, aad.encode("utf-8"))


def _decrypt(key: bytes, blob: bytes, aad: str) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if len(blob) < 12 + 16:
        raise InvalidSnapshotFile("The snapshot file is damaged (an encrypted part is truncated).")
    try:
        return AESGCM(key).decrypt(blob[:12], blob[12:], aad.encode("utf-8"))
    except InvalidTag as exc:
        raise WrongPassword("The password is not correct.") from exc


def _new_encryption(password: str) -> tuple[dict[str, Any], bytes]:
    salt = os.urandom(16)
    key = _derive_key(password, salt, **_SCRYPT)
    params = {
        "algorithm": "AES-256-GCM",
        "kdf": "scrypt",
        # Ciphertext doesn't compress, so each member is zlib-compressed before encryption.
        "compression": "zlib",
        **_SCRYPT,
        "salt": base64.b64encode(salt).decode("ascii"),
        "check": base64.b64encode(_encrypt(key, _KEY_CHECK, "check")).decode("ascii"),
    }
    return params, key


def _key_from(params: dict[str, Any], password: str | None) -> bytes:
    if not password:
        raise PasswordRequired("This snapshot file is encrypted. Enter its password.")
    try:
        if params.get("algorithm") != "AES-256-GCM" or params.get("kdf") != "scrypt":
            raise InvalidSnapshotFile("The snapshot file uses an unknown encryption method.")
        n, r, p = int(params["n"]), int(params["r"]), int(params["p"])
        salt = base64.b64decode(str(params["salt"]), validate=True)
        check = base64.b64decode(str(params["check"]), validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidSnapshotFile("The snapshot file's encryption settings are damaged.") from exc
    if not (2 <= n <= _SCRYPT_MAX["n"] and n & (n - 1) == 0 and 1 <= r <= _SCRYPT_MAX["r"]
            and 1 <= p <= _SCRYPT_MAX["p"] and 8 <= len(salt) <= 64):
        raise InvalidSnapshotFile("The snapshot file's encryption settings are out of range.")
    key = _derive_key(password, salt, n, r, p)
    if _decrypt(key, check, "check") != _KEY_CHECK:
        raise WrongPassword("The password is not correct.")
    return key


# ─── export ─────────────────────────────────────────────────────────────


@dataclass
class ExportItem:
    """One snapshot to export: its Environment (with a snapshot_id) and its index entry."""

    env: Environment
    meta: dict[str, Any]


def portable_environment(env: Environment) -> Environment:
    """A copy of `env` fit to leave this machine: UTC timestamps, no local paths, and run
    metadata scrubbed of anything secret-looking."""
    out = env.model_copy(deep=True)
    out.schema_version = ENVIRONMENT_SCHEMA_VERSION
    out.collected_at = utc_iso(out.collected_at)
    if isinstance(out.run, dict):
        run = _scrub(dict(out.run))
        for key in ("started_at", "finished_at"):
            if run.get(key):
                run[key] = utc_iso(run[key])
        if "reports" in run:
            run["reports"] = _report_names(run.get("reports"))
        out.run = run
    return out


def _source_summary(meta: dict[str, Any], env: Environment) -> dict[str, Any]:
    return {
        "kind": meta.get("kind") or snapstore.kind_of(env),
        "host": meta.get("source"),
        "version": meta.get("version"),
        "sources": list(meta.get("sources") or []),
        "counts": env.summary(),
    }


def portable_meta(item: ExportItem, env: Environment) -> dict[str, Any]:
    """The index data that travels with a snapshot (no paths, no mtimes)."""
    meta = item.meta
    provenance = meta.get("provenance")
    if isinstance(provenance, dict):
        provenance = {k: v for k, v in provenance.items() if k != "file_name"}
    reports = [*_report_names(meta.get("exports")),
               *_report_names((env.run or {}).get("reports"))]
    return _scrub({
        "name": meta.get("name"),
        "snapshot_id": env.snapshot_id,
        "collected_at": env.collected_at,
        "source_type": env.source_type,
        "source": _source_summary(meta, env),
        "stats": meta.get("stats") or {},
        "reports": reports[-50:],
        "provenance": provenance if isinstance(provenance, dict) else None,
        "diagrams": _diagram_edits(meta),
    })


def _diagram_edits(meta: dict[str, Any]) -> dict[str, Any] | None:
    """The snapshot's saved Diagrams-page edits (`diagrams.store.portable`), if any."""
    from ntnxvision_core.diagrams import store as diagram_store

    path = meta.get("path")
    return diagram_store.portable(Path(str(path))) if path else None


def _zip_info(name: str, when: datetime) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=when.timetuple()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # same attributes whichever OS wrote the file
    info.external_attr = 0o644 << 16
    return info


def export_file(items: list[ExportItem], dest: str | Path, *, password: str | None = None,
                note: str | None = None, app_version: str | None = None) -> dict[str, Any]:
    """Write `items` (one snapshot, or several as a bundle) to the ``.ntnxsnap`` at `dest`.

    The file is written to a temporary name and renamed into place, so a failed export
    never leaves a half-written file behind. Returns a summary of what was written.
    """
    if not items:
        raise TransferError("Choose at least one snapshot to export.")
    if len(items) > MAX_SNAPSHOTS_PER_FILE:
        raise TransferError(f"A snapshot file holds at most {MAX_SNAPSHOTS_PER_FILE:,} snapshots.")
    dest = Path(dest)
    if dest.suffix.lower() != FILE_SUFFIX:
        dest = dest.with_name(dest.name + FILE_SUFFIX)
    if not dest.parent.is_dir():
        raise TransferError(f"The folder {dest.parent.name or dest.parent} does not exist.")
    exported_at = now_utc()
    when = datetime.now(timezone.utc)
    encryption, key = (_new_encryption(password) if password else (None, None))
    app_version = str(app_version or __version__)

    members: list[tuple[str, bytes]] = []
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        env = portable_environment(item.env)
        sid = env.snapshot_id or str(uuid.uuid4())
        if sid in seen:  # the same snapshot twice in one bundle
            continue
        seen.add(sid)
        env.snapshot_id = sid
        meta = portable_meta(item, env)
        suffix = ".enc" if key else ""
        env_member = f"snapshots/{sid}/environment.json{suffix}"
        meta_member = f"snapshots/{sid}/meta.json{suffix}"
        env_bytes = env.model_dump_json(exclude_none=False).encode("utf-8")
        meta_bytes = _json_bytes(meta)
        if key:
            env_bytes = _encrypt(key, zlib.compress(env_bytes, 6), env_member)
            meta_bytes = _encrypt(key, zlib.compress(meta_bytes, 6), meta_member)
        members += [(env_member, env_bytes), (meta_member, meta_bytes)]
        entry: dict[str, Any] = {
            "snapshot_id": sid,
            "collected_at": env.collected_at,
            "environment": env_member,
            "meta": meta_member,
        }
        if not key:
            entry["name"] = meta["name"]
            entry["source"] = meta["source"]
        entries.append(entry)

    manifest: dict[str, Any] = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "app_version": app_version,
        "schema_version": ENVIRONMENT_SCHEMA_VERSION,
        "exported_at": exported_at,
        "exporter": {"app_version": app_version, "platform": platform_name(),
                     "note": clean_note(note)},
        "encryption": encryption,
        "snapshots": entries,
        "checksums": {name: _sha256(data) for name, data in members},
    }
    if len(entries) == 1:
        for k in ("snapshot_id", "name", "collected_at", "source"):
            if k in entries[0]:
                manifest[k] = entries[0][k]

    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(_zip_info(MANIFEST, when), _json_bytes(manifest, pretty=True))
            for name, data in members:
                zf.writestr(_zip_info(name, when), data)
        os.replace(tmp, dest)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    return {
        "path": str(dest),
        "name": dest.name,
        "size_bytes": dest.stat().st_size,
        "encrypted": key is not None,
        "snapshots": [{"snapshot_id": e["snapshot_id"],
                       "name": next((it.meta.get("name") for it in items
                                     if it.env.snapshot_id == e["snapshot_id"]), None)}
                      for e in entries],
    }


# ─── reading ────────────────────────────────────────────────────────────


def check_member_name(name: str) -> None:
    """Reject anything but a plain relative forward-slash path (zip-slip protection)."""
    bad = (
        not name or len(name) > 512 or "\x00" in name or "\\" in name
        or name.startswith("/") or re.match(r"^[A-Za-z]:", name) is not None
        or any(part in ("", ".", "..") for part in name.split("/"))
    )
    if bad:
        raise InvalidSnapshotFile(f"The snapshot file contains an unsafe path ({name[:80]!r}).")


@dataclass
class Archive:
    """An opened, structurally validated snapshot file."""

    path: Path
    zf: zipfile.ZipFile
    manifest: dict[str, Any]
    limits: Limits
    infos: dict[str, zipfile.ZipInfo] = field(default_factory=dict)

    def read(self, name: str) -> bytes:
        """A member's bytes, size-bounded and checked against the manifest checksum."""
        info = self.infos.get(name)
        if info is None:
            raise InvalidSnapshotFile(f"The snapshot file is missing {name}.")
        data = _bounded_read(self.zf, info, self.limits)
        expected = (self.manifest.get("checksums") or {}).get(name)
        ok = isinstance(expected, str) and _sha256(data) == expected.lower()
        if name != MANIFEST and not ok:
            raise InvalidSnapshotFile(
                "The snapshot file failed its integrity check: it was damaged or changed "
                "after export.")
        return data

    def close(self) -> None:
        self.zf.close()

    def __enter__(self) -> Archive:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _bounded_read(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limits: Limits) -> bytes:
    cap = min(info.file_size, limits.max_member_bytes)
    chunks: list[bytes] = []
    total = 0
    try:
        with zf.open(info) as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > cap:
                    raise InvalidSnapshotFile("The snapshot file is larger than it declares.")
                chunks.append(chunk)
    except (zipfile.BadZipFile, OSError, EOFError, ValueError) as exc:
        raise InvalidSnapshotFile(f"The snapshot file is damaged ({exc}).") from exc
    return b"".join(chunks)


def open_archive(path: str | Path, limits: Limits = DEFAULT_LIMITS) -> Archive:
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise TransferError(f"The file {path.name} could not be read.") from exc
    if size > limits.max_archive_bytes:
        raise InvalidSnapshotFile("The snapshot file is too large to import.")
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise InvalidSnapshotFile(
            f"{path.name} is not an NTNXVision snapshot file (it is not a valid archive).") from exc
    try:
        archive = Archive(path, zf, {}, limits)
        infos = zf.infolist()
        if len(infos) > limits.max_members:
            raise InvalidSnapshotFile("The snapshot file has too many parts.")
        total = 0
        for info in infos:
            check_member_name(info.filename)
            if info.filename in archive.infos:
                raise InvalidSnapshotFile(f"The snapshot file repeats {info.filename}.")
            if info.flag_bits & 0x1:
                raise InvalidSnapshotFile("The snapshot file uses unsupported ZIP encryption.")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise InvalidSnapshotFile("The snapshot file contains a symbolic link.")
            if info.file_size > limits.max_member_bytes:
                raise InvalidSnapshotFile("A part of the snapshot file is too large to import.")
            if info.file_size > 1024**2 and info.file_size > limits.max_ratio * max(
                    info.compress_size, 1):
                raise InvalidSnapshotFile(
                    "The snapshot file is compressed suspiciously well (possible zip bomb).")
            total += info.file_size
            archive.infos[info.filename] = info
        if total > limits.max_total_bytes:
            raise InvalidSnapshotFile("The snapshot file expands to more than the import limit.")
        if MANIFEST not in archive.infos:
            raise InvalidSnapshotFile(f"{path.name} is not an NTNXVision snapshot file "
                                      "(no manifest).")
        if archive.infos[MANIFEST].file_size > limits.max_manifest_bytes:
            raise InvalidSnapshotFile("The snapshot file's manifest is too large.")
        try:
            manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise InvalidSnapshotFile("The snapshot file's manifest is not valid JSON.") from exc
        archive.manifest = _validate_manifest(manifest, set(archive.infos))
        return archive
    except BaseException:
        zf.close()
        raise


def _update_message(what: str, found: Any) -> str:
    return (f"This snapshot file was made by a newer version of NTNXVision ({what} {found}). "
            "Update NTNXVision to import it.")


#: Forward migrations of the manifest, keyed by the version they upgrade *from*.
_MANIFEST_MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def _validate_manifest(manifest: Any, names: set[str]) -> dict[str, Any]:
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise InvalidSnapshotFile("This is not an NTNXVision snapshot file.")
    version = manifest.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise InvalidSnapshotFile("The snapshot file has no valid format version.")
    if version > FORMAT_VERSION:
        raise UnsupportedVersion(_update_message("format", version))
    while version < FORMAT_VERSION:
        manifest = _MANIFEST_MIGRATIONS[version](manifest)
        version = manifest["format_version"]
    schema = manifest.get("schema_version", 0)
    if isinstance(schema, int) and schema > ENVIRONMENT_SCHEMA_VERSION:
        raise UnsupportedVersion(_update_message("schema", schema))
    entries = manifest.get("snapshots")
    checksums = manifest.get("checksums")
    if not isinstance(entries, list) or not entries or len(entries) > MAX_SNAPSHOTS_PER_FILE:
        raise InvalidSnapshotFile("The snapshot file lists no snapshots.")
    if not isinstance(checksums, dict):
        raise InvalidSnapshotFile("The snapshot file has no checksums.")
    encrypted = manifest.get("encryption") is not None
    if encrypted and not isinstance(manifest.get("encryption"), dict):
        raise InvalidSnapshotFile("The snapshot file's encryption settings are damaged.")
    referenced: set[str] = set()
    ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise InvalidSnapshotFile("The snapshot file's manifest is damaged.")
        sid = entry.get("snapshot_id")
        if not isinstance(sid, str) or not _SNAPSHOT_ID.match(sid) or sid in ids:
            raise InvalidSnapshotFile("The snapshot file has an invalid snapshot id.")
        ids.add(sid)
        for key in ("environment", "meta"):
            member = entry.get(key)
            if not isinstance(member, str):
                raise InvalidSnapshotFile("The snapshot file's manifest is damaged.")
            check_member_name(member)
            if member.endswith(".enc") != encrypted:
                raise InvalidSnapshotFile("The snapshot file's manifest is damaged.")
            referenced.add(member)
    if set(checksums) != referenced:
        raise InvalidSnapshotFile("The snapshot file's checksums do not match its contents.")
    if names != referenced | {MANIFEST}:
        raise InvalidSnapshotFile(
            "The snapshot file contains unexpected or missing parts; it may have been changed.")
    return manifest


# ─── schema migration ───────────────────────────────────────────────────


def _env_v0_to_v1(data: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """Version 0 = written before `schema_version` / `snapshot_id` existed."""
    data["schema_version"] = 1
    data.setdefault("snapshot_id", entry.get("snapshot_id"))
    return data


#: Forward migrations of the Environment JSON, keyed by the version they upgrade *from*.
_ENV_MIGRATIONS: dict[int, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
    0: _env_v0_to_v1,
}


#: Longest free-text string kept from an imported snapshot; longer ones are truncated.
MAX_TEXT_CHARS = 16_384


def cap_strings(value: Any, limit: int = MAX_TEXT_CHARS) -> Any:
    """Truncate over-long strings (with an ellipsis) anywhere in parsed snapshot JSON."""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit - 1] + "\u2026"
    if isinstance(value, list):
        return [cap_strings(v, limit) for v in value]
    if isinstance(value, dict):
        return {k: cap_strings(v, limit) for k, v in value.items()}
    return value


def migrate_environment(data: Any, entry: dict[str, Any]) -> Environment:
    if not isinstance(data, dict):
        raise InvalidSnapshotFile("The snapshot in the file is not valid.")
    schema = data.get("schema_version", 0)
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 0:
        raise InvalidSnapshotFile("The snapshot in the file has an invalid schema version.")
    if schema > ENVIRONMENT_SCHEMA_VERSION:
        raise UnsupportedVersion(_update_message("schema", schema))
    while schema < ENVIRONMENT_SCHEMA_VERSION:
        data = _ENV_MIGRATIONS[schema](data, entry)
        schema = data["schema_version"]
    try:
        env = Environment.model_validate(cap_strings(data))
    except ValueError as exc:
        raise InvalidSnapshotFile("The snapshot in the file is not valid.") from exc
    if env.snapshot_id != entry["snapshot_id"]:
        raise InvalidSnapshotFile("The snapshot file's manifest does not match its contents.")
    return env


# ─── inspect & import ───────────────────────────────────────────────────


@dataclass
class LoadedSnapshot:
    entry: dict[str, Any]
    meta: dict[str, Any]
    env: Environment | None = None


def _load_json(archive: Archive, member: str, key: bytes | None) -> Any:
    data = archive.read(member)
    if key is not None:
        data = _decrypt(key, data, member)
        if archive.manifest["encryption"].get("compression") == "zlib":
            data = _inflate(data, archive.limits.max_member_bytes)
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise InvalidSnapshotFile(f"The snapshot file's {member.split('/')[-1]} is not valid "
                                  "JSON.") from exc


def _inflate(data: bytes, limit: int) -> bytes:
    """zlib-decompress at most `limit` bytes (an encrypted member is a zip bomb too)."""
    inflater = zlib.decompressobj()
    try:
        out = inflater.decompress(data, limit + 1)
    except zlib.error as exc:
        raise InvalidSnapshotFile("The snapshot file is damaged (bad compressed data).") from exc
    if len(out) > limit or inflater.unconsumed_tail:
        raise InvalidSnapshotFile("A part of the snapshot file is too large to import.")
    return out


def _load(archive: Archive, password: str | None, *, with_env: bool) -> list[LoadedSnapshot]:
    enc = archive.manifest.get("encryption")
    key = _key_from(enc, password) if enc else None
    out = []
    for entry in archive.manifest["snapshots"]:
        meta = _load_json(archive, entry["meta"], key)
        if not isinstance(meta, dict):
            raise InvalidSnapshotFile("The snapshot file's metadata is damaged.")
        env = migrate_environment(_load_json(archive, entry["environment"], key), entry) \
            if with_env else None
        out.append(LoadedSnapshot(entry, meta, env))
    return out


def _local_index(store: SnapshotStore) -> list[dict[str, Any]]:
    metas = []
    for path in store.list_snapshots():
        try:
            metas.append(snapstore.read_meta(path))
        except Exception:  # noqa: BLE001 — an unreadable local snapshot can't collide
            continue
    return metas


def _describe_entry(entry: dict[str, Any], meta: dict[str, Any] | None) -> dict[str, Any]:
    source = (meta or {}).get("source") or entry.get("source") or {}
    return {
        "snapshot_id": entry["snapshot_id"],
        "name": (meta or {}).get("name") or entry.get("name"),
        "collected_at": utc_iso((meta or {}).get("collected_at") or entry.get("collected_at")),
        "kind": source.get("kind"),
        "source": source.get("host"),
        "version": source.get("version"),
        "sources": source.get("sources") or [],
        "summary": source.get("counts") or {},
    }


def inspect_file(path: str | Path, store: SnapshotStore, *, password: str | None = None,
                 limits: Limits = DEFAULT_LIMITS) -> dict[str, Any]:
    """Read a snapshot file's manifest (and, with the password, its metadata) for the
    import preview, without importing anything.

    An encrypted file without a password, or with a wrong one, is not an error here: the
    result says ``locked: true`` and ``password_error`` explains why, so the preview can
    ask again.
    """
    with open_archive(path, limits) as archive:
        m = archive.manifest
        encrypted = m.get("encryption") is not None
        locked = False
        password_error: str | None = None
        metas: list[dict[str, Any] | None]
        try:
            metas = [s.meta for s in _load(archive, password, with_env=False)]
        except PasswordRequired:
            locked, metas = True, [None] * len(m["snapshots"])
        except WrongPassword as exc:
            locked, metas, password_error = True, [None] * len(m["snapshots"]), str(exc)
        local = _local_index(store)
        by_id = {lm.get("snapshot_id"): lm for lm in local if lm.get("snapshot_id")}
        taken = [str(lm.get("name")) for lm in local]
        snaps = []
        for entry, meta in zip(m["snapshots"], metas, strict=True):
            item = _describe_entry(entry, meta)
            hit = by_id.get(entry["snapshot_id"])
            item["conflict"] = {"name": hit.get("name"), "path": hit.get("path")} if hit else None
            if item["name"]:
                stem = safe_name(item["name"], max_len=max_stem_for(store.directory))
                item["import_name"] = unique_name(stem, taken)
            else:
                item["import_name"] = None
            item["size_bytes"] = archive.infos[entry["environment"]].file_size
            snaps.append(item)
        exporter = m.get("exporter") or {}
        return {
            "path": str(Path(path)),
            "file_name": Path(path).name,
            "size_bytes": Path(path).stat().st_size,
            "format_version": m["format_version"],
            "schema_version": m.get("schema_version", 0),
            "app_version": m.get("app_version"),
            "exported_at": utc_iso(m.get("exported_at")),
            "exporter": {"app_version": exporter.get("app_version") or m.get("app_version"),
                         "platform": exporter.get("platform"),
                         "note": clean_note(exporter.get("note"))},
            "encrypted": encrypted,
            "locked": locked,
            "password_error": password_error,
            "snapshots": snaps,
            "conflicts": sum(1 for s in snaps if s["conflict"]),
        }


def import_file(path: str | Path, store: SnapshotStore, *, password: str | None = None,
                on_conflict: str = "keep_both", limits: Limits = DEFAULT_LIMITS,
                on_saved: Callable[[Path], None] | None = None) -> dict[str, Any]:
    """Import every snapshot in the file at `path` into `store`.

    `on_conflict` decides what happens when a snapshot with the same `snapshot_id` is
    already present: ``keep_both`` (import a copy with a new id), ``replace`` (delete the
    local one first), ``skip``, or ``ask`` (import nothing and report the conflicts).
    A name that is taken by a different snapshot gets a ``-2``, ``-3``… suffix. Every
    snapshot in the file is decrypted, verified and parsed before the first one is saved.
    """
    if on_conflict not in CONFLICT_CHOICES:
        raise TransferError(f"Unknown conflict choice: {on_conflict}")
    with open_archive(path, limits) as archive:
        loaded = _load(archive, password, with_env=True)
        manifest = archive.manifest
    local = _local_index(store)
    by_id: dict[str, list[dict[str, Any]]] = {}
    for lm in local:
        if lm.get("snapshot_id"):
            by_id.setdefault(str(lm["snapshot_id"]), []).append(lm)
    conflicts = [s.entry["snapshot_id"] for s in loaded if s.entry["snapshot_id"] in by_id]
    if on_conflict == "ask" and conflicts:
        return {"imported": [], "needs_decision": True,
                "conflicts": [{"snapshot_id": sid, "name": by_id[sid][0].get("name"),
                               "path": by_id[sid][0].get("path")} for sid in conflicts]}

    exporter = manifest.get("exporter") or {}
    imported_from = {
        "platform": exporter.get("platform"),
        "app_version": exporter.get("app_version") or manifest.get("app_version"),
        "note": clean_note(exporter.get("note")),
        "exported_at": utc_iso(manifest.get("exported_at")),
    }
    taken = {str(lm.get("name")) for lm in local}
    results = []
    for snap in loaded:
        env = snap.env
        assert env is not None
        sid = snap.entry["snapshot_id"]
        original_name = str(snap.meta.get("name") or snap.entry.get("name") or "imported")
        action = "imported"
        provenance: dict[str, Any] = {
            "imported_at": now_utc(),
            "imported_from": imported_from,
            "file_name": Path(path).name,
            "original_name": original_name,
        }
        if isinstance(snap.meta.get("provenance"), dict):
            provenance["previous"] = snap.meta["provenance"]
        if snap.meta.get("reports"):
            provenance["reports"] = _report_names(snap.meta.get("reports"))
        if sid in by_id:
            if on_conflict == "skip":
                results.append({"snapshot_id": sid, "name": by_id[sid][0].get("name"),
                                "path": by_id[sid][0].get("path"), "action": "skipped",
                                "original_name": original_name})
                continue
            if on_conflict == "replace":
                for lm in by_id[sid]:
                    with contextlib.suppress(FileNotFoundError):
                        snapstore.delete(Path(str(lm["path"])))
                    if on_saved is not None:
                        on_saved(Path(str(lm["path"])))
                    taken.discard(str(lm.get("name")))
                action = "replaced"
            else:
                env.snapshot_id = str(uuid.uuid4())
                provenance["original_snapshot_id"] = sid
                action = "kept_both"
        stem = safe_name(original_name, max_len=max_stem_for(store.directory))
        name = unique_name(stem, taken)
        target = store.path_for(name)
        saved = store.save(env, name, path=target)
        snapstore.write_meta(saved, env, exports=[], provenance=provenance)
        if snap.meta.get("diagrams"):
            from ntnxvision_core.diagrams import store as diagram_store

            diagram_store.restore(saved, snap.meta["diagrams"])
        if on_saved is not None:
            on_saved(saved)
        taken.add(name)
        results.append({"snapshot_id": env.snapshot_id, "name": name, "path": str(saved),
                        "action": action, "original_name": original_name,
                        "renamed": name != original_name})
    return {
        "imported": results,
        "needs_decision": False,
        "conflicts": [],
        "count": sum(1 for r in results if r["action"] != "skipped"),
        "skipped": sum(1 for r in results if r["action"] == "skipped"),
    }


def ensure_snapshot_id(store: SnapshotStore, path: Path, env: Environment) -> Environment:
    """Give a snapshot saved before `snapshot_id` existed a stable id, persisted in place."""
    if not env.snapshot_id:
        store.save(env, snapstore.snapshot_name(path), path=path)
    return env


def unique_file(directory: Path, stem: str) -> Path:
    """``directory/<stem>.ntnxsnap``, suffixed ``-2``, ``-3``… if a file already uses it."""
    taken = [p.name[: -len(FILE_SUFFIX)] for p in directory.glob(f"*{FILE_SUFFIX}")]
    stem = safe_name(stem, max_len=max_stem_for(directory))
    return directory / f"{unique_name(stem, taken)}{FILE_SUFFIX}"
