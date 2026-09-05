"""LCM (Life Cycle Manager) inventory — firmware and software with available updates.

LCM is the only place that answers "what version of everything is actually running, and
what would LCM let me move it to": AOS, AHV, NCC, Foundation, the Prism Central services,
and on hardware-backed clusters the disk/NIC/BMC firmware too.

**v4-only by design.** There is no v3 equivalent — LCM's pre-v4 interface was a set of
internal `/lcm/v1.r0.b1/...` RPCs that are not part of any supported API — so when the
`lifecycle` namespace is not served this collector records a skip note and returns rather
than reaching for a legacy path that does not exist.

Two things about the payload are not what their names suggest, both verified against a
live pc.7.5.1.1:

* **`targetVersion` is not an upgrade target.** It is the version LCM is currently
  aiming at, which equals `entityVersion` on every one of the 34 entities on that PC
  because no upgrade is staged. It is reported as-is but must never be used to decide
  whether an update exists.
* **`availableVersions` being non-empty does not mean an update exists.** It is the
  catalogue of versions LCM could install, which can legitimately include the running
  one. `update_available` is therefore derived by comparing versions — see
  `_update_available`.
"""

from __future__ import annotations

import re
from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import LcmEntity

_ENTITIES_PATH = "lifecycle/v4.0/resources/entities"
_STATUS_PATH = "lifecycle/v4.0/resources/status"

#: Words that stay upper-case when an entity class is re-cased. LCM's `entityClass` is
#: free text assembled by whichever module registered the entity, so the same class
#: arrives in different cases from different modules — the live PC returns both
#: "PC Core Cluster" and "PC CORE CLUSTER", which would otherwise split one class into
#: two in any grouping or filter. Everything is title-cased except these.
_ACRONYMS = frozenset(
    {
        "AHV",
        "AOS",
        "BIOS",
        "BMC",
        "CVE",
        "CPU",
        "ESX",
        "ESXI",
        "FSM",
        "HBA",
        "HDD",
        "LCM",
        "MSP",
        "NCC",
        "NIC",
        "NVME",
        "NX",
        "PC",
        "SSD",
        "VM",
    }
)

#: The label used where LCM reports an entity against the Prism Central pseudo-cluster.
#: Half the entities on a PC-only inventory sit there, and the cluster collector
#: deliberately keeps that pseudo-cluster out of `env.clusters`, so without this they
#: would all render with an empty cluster column.
_PC_CLUSTER_LABEL = "Prism Central"


class LcmCollector(Collector):
    name = "lcm_entities"
    label = "LCM (Firmware & Software)"

    def collect(self, env: Any) -> None:
        if not self.conn.v4_ready("lifecycle"):
            env.collection_notes[self.name] = (
                "skipped: the v4 lifecycle namespace is not served by this Prism Central "
                f"({self.conn.negotiated.get('lifecycle', 'unavailable')}); LCM has no v3 "
                "equivalent to fall back to"
            )
            return

        cluster_names = {c.uuid: c.name for c in env.clusters if c.uuid and c.name}
        pc_uuid = getattr(env.prism_central, "uuid", None)

        for entity in self.conn.v4_list(_ENTITIES_PATH):
            versions = _available_versions(entity)
            current = _text(entity.get("entityVersion"))
            cluster_uuid = _text(entity.get("clusterExtId"))
            env.lcm_entities.append(
                LcmEntity(
                    uuid=entity.get("extId") or "",
                    # entityModel is the component name ("AOS", "AHV hypervisor", a disk
                    # model); LCM has no separate display-name field.
                    name=_text(entity.get("entityModel")),
                    entity_class=_canonical_class(entity.get("entityClass")),
                    entity_type=_text(entity.get("entityType")),
                    current_version=current,
                    target_version=_text(entity.get("targetVersion")),
                    available_versions=versions,
                    update_available=_update_available(current, versions),
                    cluster_uuid=cluster_uuid,
                    cluster_name=_cluster_name(cluster_uuid, cluster_names, pc_uuid),
                    last_updated=_text(entity.get("lastUpdatedTime")),
                )
            )

        env.collection_notes[self.name] = self._note(len(env.lcm_entities))

    def _note(self, count: int) -> str:
        """Report the count plus LCM's own framework version.

        The framework version is worth one extra GET: an out-of-date LCM framework is
        itself a finding, and it explains an inventory that looks stale.
        """
        try:
            data = self.conn.v4_get(_STATUS_PATH).get("data") or {}
        except NutanixApiError:
            return f"ok: {count} entities"
        framework = (data.get("frameworkVersion") or {}).get("currentVersion")
        if not framework:
            return f"ok: {count} entities"
        stale = " (framework update available)" if _needs_update(data) else ""
        return f"ok: {count} entities, LCM framework {framework}{stale}"


def _needs_update(status: dict[str, Any]) -> bool:
    return bool((status.get("frameworkVersion") or {}).get("isUpdateNeeded"))


# ─── field helpers ─────────────────────────────────────────────────────


def _text(value: Any) -> str | None:
    """A non-empty string, else None — v4 pads absent values with empty strings."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _canonical_class(value: Any) -> str | None:
    """Re-case LCM's free-text `entityClass` so one class is one value.

    Words containing digits or punctuation (part numbers, model strings) are left
    untouched — re-casing those would corrupt them.
    """
    text = _text(value)
    if text is None:
        return None
    words: list[str] = []
    for word in text.split():
        if word.upper() in _ACRONYMS:
            words.append(word.upper())
        elif word.isalpha():
            words.append(word.capitalize())
        else:
            words.append(word)
    return " ".join(words)


def _cluster_name(
    cluster_uuid: str | None, cluster_names: dict[str, str | None], pc_uuid: str | None
) -> str | None:
    """Resolve LCM's `clusterExtId` to a name.

    Clusters are collected in phase 1, so `env.clusters` is populated by the time this
    runs. The PC's own pseudo-cluster is not in there (the cluster collector filters it
    out), so it is labelled rather than left blank. Anything still unresolved falls back
    to the raw uuid: an unfamiliar uuid in the column is recoverable, an empty cell is
    not, and `cluster_uuid` keeps the raw value either way.
    """
    if not cluster_uuid:
        return None
    if pc_uuid and cluster_uuid == pc_uuid:
        return _PC_CLUSTER_LABEL
    return cluster_names.get(cluster_uuid) or cluster_uuid


def _available_versions(entity: dict[str, Any]) -> list[str]:
    """Reduce LCM's AvailableVersion objects to a newest-first list of version strings.

    Two choices worth stating:

    * **Filter on `isEnabled`, not on `status`.** A disabled entry is one LCM will not
      let you select, so it is not an available update; every entry on the live PC is
      enabled, so this only guards the case that matters. `status` is *not* filtered:
      the live values are AVAILABLE and RECOMMENDED — RECOMMENDED is the more important
      of the two, and the enum is open, so dropping unknown statuses would silently
      lose future ones.
    * **Sort by LCM's `order`, descending.** Source order is not meaningful (the live
      FSM entity lists 5.3.0.2, 5.2.1.2, 5.3.0.1, 5.3), while `order` is LCM's own
      monotonic ranking of the catalogue and puts the newest first. Entries without an
      `order` keep their source order, after the ranked ones.
    """
    rows = [
        row
        for row in (entity.get("availableVersions") or [])
        if isinstance(row, dict) and row.get("isEnabled") is not False
    ]
    rows.sort(key=lambda row: row["order"] if isinstance(row.get("order"), int) else -1)
    out: list[str] = []
    for row in reversed(rows):
        version = _text(row.get("version"))
        if version and version not in out:
            out.append(version)
    return out


# ─── the update rule ───────────────────────────────────────────────────

_LEADING_DIGIT = re.compile(r"\d")
_DIGITS = re.compile(r"\d+")


def _version_parts(value: str) -> tuple[str, tuple[int, ...]] | None:
    """Split a version into (series prefix, numeric components).

    LCM version strings carry a product prefix on some entities and none on others —
    "pc.7.5.1.1", "LM.2025.9.2", "security_aos.2022.9", "7.0.3-21930508", "2026.03.1".
    The prefix identifies the series and only versions in the same series can be
    ordered; the digits after it order within it. Deliberately not a semver parser: LCM
    versions are not semver, and pulling in a comparison library would fail on exactly
    the strings above.
    """
    match = _LEADING_DIGIT.search(value)
    if match is None:
        return None
    prefix = value[: match.start()].strip(" ._-").lower()
    numbers = tuple(int(n) for n in _DIGITS.findall(value[match.start() :]))
    if not numbers:
        return None
    return prefix, numbers


def _is_newer(candidate: str, current: str) -> bool | None:
    """Is `candidate` a newer version than `current`? None when they cannot be ordered."""
    left, right = _version_parts(candidate), _version_parts(current)
    if left is None or right is None or left[0] != right[0]:
        return None
    a, b = left[1], right[1]
    # Pad so "11.0" and "11.0.0.1" compare component-wise rather than by length.
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    return a > b


def _update_available(current: str | None, offered: list[str]) -> bool | None:
    """True only when LCM offers a version that is genuinely newer than the running one.

    The rule, in order:

    1. Nothing offered → False. LCM answering with an empty catalogue is a real "you are
       up to date", not a missing answer.
    2. No running version → None. Something is offered but there is nothing to compare
       it against, so the honest answer is "unknown" rather than a guess.
    3. Any offer that orders strictly above the running version → True.
    4. Otherwise an offer that cannot be ordered against the running version but is a
       different string → True. LCM does not offer builds it considers identical to the
       running one, so a differently-named build is still an update; the string check is
       what keeps case 5 from ever being reported as one.
    5. Otherwise → False. This covers the case that makes a non-empty list insufficient
       on its own: the only version offered *is* the running one.
    """
    if not offered:
        return False
    if not current:
        return None
    differs = False
    for candidate in offered:
        newer = _is_newer(candidate, current)
        if newer:
            return True
        if newer is None and candidate.strip() != current.strip():
            differs = True
    return differs
