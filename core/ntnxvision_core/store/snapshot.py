"""Snapshot store.

A collection run (`Environment`) is serialized to a single JSON file so the environment
can be re-exported to any format offline, and so two runs can be diffed later. Snapshots
live under the user profile by default and are git-ignored (they can contain sensitive
topology).

Next to each ``<name>.ntnxsnap.json`` the store keeps a small ``<name>.ntnxmeta.json``
index: name, collection time, summary counts, kind/source/version, the run metadata and
the export history. Listing snapshots reads only these, so the Snapshots screen stays
instant even when a 20 000-VM snapshot is tens of megabytes. The index is derived data:
it is rebuilt from the snapshot whenever it is missing or older than the snapshot, and
only the export history (which lives nowhere else) is carried over.

`EnvironmentCache` keeps the most recently loaded Environments in memory, keyed by path
and modification time, so paging through inventory or re-running a comparison does not
re-parse the file on every request.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

from ntnxvision_core.models import Environment
from ntnxvision_core.paths import private_dir, write_private_text
from ntnxvision_core.store.names import max_stem_for, safe_name, unique_name

SNAP_SUFFIX = ".ntnxsnap.json"
META_SUFFIX = ".ntnxmeta.json"


class SnapshotStore:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        private_dir(self.directory)

    def path_for(self, name: str) -> Path:
        return self.directory / f"{_safe(name, self.directory)}{SNAP_SUFFIX}"

    def taken_names(self) -> list[str]:
        """Names of the snapshots in the store."""
        return [snapshot_name(p) for p in self.list_snapshots()]

    def name_taken(self, name: str) -> bool:
        """Is `name` (as sanitized for a file name) already used, ignoring case?

        Both file systems default to case-insensitive, so ``Q4`` and ``q4`` collide."""
        safe = _safe(name, self.directory).casefold()
        return any(t.casefold() == safe for t in self.taken_names())

    def save(self, env: Environment, name: str, *, path: Path | None = None,
             unique: bool = False) -> Path:
        """Write the snapshot atomically (temp file + rename) and refresh its index.

        A snapshot without a `snapshot_id` gets one here, so every saved snapshot has a
        stable identity that survives export and import. `path` rewrites an existing
        snapshot in place (same name) instead of deriving the file from `name`.
        `unique` never replaces another snapshot: a name already taken gets ``-2``,
        ``-3``, ... (a collection checks its name up front; this covers a snapshot of the
        same name saved while the run was collecting).
        """
        if not env.snapshot_id:
            env.snapshot_id = str(uuid.uuid4())
        if path is None and unique:
            stem = unique_name(_safe(name, self.directory), self.taken_names(),
                               max_len=max_stem_for(self.directory))
            path = self.directory / f"{stem}{SNAP_SUFFIX}"
        path = path or self.path_for(name)
        tmp = path.with_name(path.name + ".tmp")
        write_private_text(tmp, env.model_dump_json(exclude_none=False))
        os.replace(tmp, path)
        # The index is a cache; listing rebuilds it if this write fails.
        with contextlib.suppress(OSError):
            write_meta(path, env)
        return path

    def load(self, path: str | Path) -> Environment:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return Environment.model_validate(data)

    def list_snapshots(self) -> list[Path]:
        return sorted(self.directory.glob(f"*{SNAP_SUFFIX}"))


# ─── metadata index ────────────────────────────────────────────────────


def snapshot_name(path: Path) -> str:
    return path.name[: -len(SNAP_SUFFIX)] if path.name.endswith(SNAP_SUFFIX) else path.stem


def meta_path(path: Path) -> Path:
    return path.with_name(snapshot_name(path) + META_SUFFIX)


def kind_of(env: Environment) -> str:
    if env.is_combined:
        return "combined"
    return "pe" if env.source_type == "prism_element" else "pc"


def describe(env: Environment, path: Path) -> dict[str, Any]:
    """The `SnapshotInfo`-shaped part of the index for one Environment."""
    from ntnxvision_core.scope import scope_dict

    kind = kind_of(env)
    source: str | None
    version: str | None
    if kind == "combined":
        source = f"{len(env.source_environments):,} Prism Centrals"
        version = None
    else:
        source = env.prism_central.host
        version = env.prism_central.version
        if kind == "pe" and version and not version.upper().startswith("AOS"):
            version = f"AOS {version}"
    return {
        "name": snapshot_name(path),
        "snapshot_id": env.snapshot_id,
        "collected_at": env.collected_at,
        "summary": env.summary(),
        "kind": kind,
        "source": source,
        "version": version,
        "sources": [s.prism_central.host or "unknown" for s in env.source_environments],
        "source_type": env.source_type,
        "run": env.run,
        "stats": stats(env),
        "scope": scope_dict(env.cluster_scope),
    }


def stats(env: Environment) -> dict[str, Any]:
    """The handful of derived numbers the Overview figure strip needs, so the Overview
    never has to load a large snapshot just to render its headline."""
    from ntnxvision_core.compare import capacity
    from ntnxvision_core.compat import parse_version
    from ntnxvision_core.exporters.tables import Ctx

    ctx = Ctx(env)
    aos = sorted({c.aos_version for c in env.clusters if c.aos_version},
                 key=lambda v: parse_version(v)[0] or ())
    cap, used = capacity(env)
    protected = sum(1 for v in env.vms if ctx.vm_protection(v) != "None")
    return {
        "aos_min": aos[0] if aos else None,
        "aos_max": aos[-1] if aos else None,
        "vms_on": sum(1 for v in env.vms if (v.power_state or "").upper() == "ON"),
        "capacity_bytes": cap,
        "used_bytes": used,
        "protected_vms": protected,
        "unprotected_vms": len(env.vms) - protected,
        "notes": dict(env.collection_notes),
    }


def write_meta(path: Path, env: Environment, exports: list[dict[str, Any]] | None = None,
               provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write the index for `path`. The export history and the import provenance live
    nowhere else, so unless given they are carried over from the previous index."""
    previous = read_meta_raw(path) or {}
    meta = describe(env, path)
    meta["snapshot_mtime"] = path.stat().st_mtime
    meta["exports"] = exports if exports is not None else previous.get("exports", [])
    meta["provenance"] = provenance if provenance is not None else previous.get("provenance")
    write_private_text(meta_path(path), json.dumps(meta))
    return meta


def read_meta_raw(path: Path) -> dict[str, Any] | None:
    mp = meta_path(path)
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_meta(path: Path, cache: EnvironmentCache | None = None) -> dict[str, Any]:
    """The index entry for `path`, rebuilt from the snapshot if missing or stale."""
    meta = read_meta_raw(path)
    mtime = path.stat().st_mtime
    if meta is None or abs(float(meta.get("snapshot_mtime") or 0) - mtime) > 1e-6 \
            or meta.get("name") != snapshot_name(path):
        env = cache.get(path) if cache else SnapshotStore(path.parent).load(path)
        try:
            meta = write_meta(path, env)
        except OSError:
            meta = describe(env, path)
            meta["exports"] = []
            meta["provenance"] = (read_meta_raw(path) or {}).get("provenance")
    meta["name"] = snapshot_name(path)
    meta["path"] = str(path)
    meta["size_bytes"] = path.stat().st_size
    _shorten_versions(meta)
    return meta


def _shorten_versions(meta: dict[str, Any]) -> None:
    """Indexes written before `versions.short_version` hold full build strings."""
    from ntnxvision_core.versions import short_version

    version = meta.get("version")
    if isinstance(version, str):
        prefix = "AOS " if version.startswith("AOS ") else ""
        meta["version"] = prefix + (short_version(version.removeprefix(prefix)) or "")
    stats = meta.get("stats")
    if isinstance(stats, dict):
        for key in ("aos_min", "aos_max"):
            stats[key] = short_version(stats.get(key))


def record_exports(path: Path, files: list[dict[str, Any]]) -> None:
    """Append written report files to the snapshot's export history."""
    meta = read_meta_raw(path)
    if meta is None:
        return
    history = list(meta.get("exports") or [])
    history.extend(files)
    meta["exports"] = history[-50:]
    with contextlib.suppress(OSError):
        write_private_text(meta_path(path), json.dumps(meta))


def info_from_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """The `SnapshotInfo` fields of an index entry (what list/overview return)."""
    info = {k: meta.get(k) for k in ("name", "path", "snapshot_id", "collected_at", "summary",
                                     "kind", "source", "version", "size_bytes", "scope")}
    provenance = meta.get("provenance")
    info["imported"] = provenance if isinstance(provenance, dict) else None
    return info


def rename(path: Path, new_name: str) -> Path:
    target = path.with_name(f"{new_name}{SNAP_SUFFIX}")
    if target.exists() and target != path:
        raise FileExistsError(new_name)
    old_meta = meta_path(path)
    path.rename(target)
    if old_meta.exists():
        new_meta = meta_path(target)
        old_meta.rename(new_meta)
        try:
            data = json.loads(new_meta.read_text(encoding="utf-8"))
            data["name"] = new_name
            data["snapshot_mtime"] = target.stat().st_mtime
            write_private_text(new_meta, json.dumps(data))
        except (OSError, ValueError):
            pass
    return target


def delete(path: Path) -> None:
    path.unlink()
    with contextlib.suppress(FileNotFoundError):
        meta_path(path).unlink()


# ─── in-memory cache ───────────────────────────────────────────────────


class EnvironmentCache:
    """A tiny thread-safe LRU of loaded Environments, keyed by (path, mtime)."""

    def __init__(self, capacity: int = 4) -> None:
        self.capacity = capacity
        self._items: OrderedDict[str, tuple[float, Environment]] = OrderedDict()
        self._lock = threading.Lock()
        self._loading: dict[str, threading.Lock] = {}

    def get(self, path: str | Path) -> Environment:
        p = Path(path)
        key = str(p.resolve())
        mtime = p.stat().st_mtime
        with self._lock:
            hit = self._items.get(key)
            if hit and hit[0] == mtime:
                self._items.move_to_end(key)
                return hit[1]
            gate = self._loading.setdefault(key, threading.Lock())
        # One loader per path: concurrent requests for the same big snapshot wait for
        # the first parse instead of each parsing it again.
        with gate:
            with self._lock:
                hit = self._items.get(key)
                if hit and hit[0] == mtime:
                    return hit[1]
            env = Environment.model_validate_json(p.read_bytes())
            with self._lock:
                self._items[key] = (mtime, env)
                self._items.move_to_end(key)
                while len(self._items) > self.capacity:
                    self._items.popitem(last=False)
            return env

    def invalidate(self, path: str | Path | None = None) -> None:
        with self._lock:
            if path is None:
                self._items.clear()
            else:
                self._items.pop(str(Path(path).resolve()), None)


def _safe(name: str, directory: Path | None = None) -> str:
    return safe_name(name, max_len=max_stem_for(directory))
