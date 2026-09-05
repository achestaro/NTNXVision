"""The LCM collector: the derived update rule, class re-casing, and cluster resolution."""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.lcm import LcmCollector
from ntnxvision_core.models import Cluster, Environment

# Trimmed from a live pc.7.5.1.1 `lifecycle/v4.0/resources/entities` response. The
# AvailableVersion objects keep the fields the collector actually reads (plus `status`,
# which it deliberately does not filter on).
AHV = {
    "extId": "812ede62-6610-4b0c-bb80-6c260cade676",
    "entityClass": "Hypervisor",
    "entityModel": "AHV hypervisor",
    "entityType": "SOFTWARE",
    "entityVersion": "11.0",
    # Equal to entityVersion on every live entity: LCM's *current* target, not an offer.
    "targetVersion": "11.0",
    "lastUpdatedTime": "2025-12-11T09:40:36.682668Z",
    "clusterExtId": "0006442f-1fc6-3ee1-0000-000000016d34",
    "availableVersions": [
        {"version": "11.0.0.1", "status": "AVAILABLE", "isEnabled": True, "order": 135},
        {"version": "11.0.0.2", "status": "AVAILABLE", "isEnabled": True, "order": 136},
    ],
}

# The live FSM entity — source order is jumbled, so `order` is the only sane sort key.
FSM = {
    "extId": "3a1f0a4e-6c1f-4f2f-9a4b-2c9f3d0a1b77",
    "entityClass": "Core Cluster",
    "entityModel": "FSM",
    "entityType": "SOFTWARE",
    "entityVersion": "5.2.1",
    "targetVersion": "5.2.1",
    "clusterExtId": "0006442f-1fc6-3ee1-0000-000000016d34",
    "availableVersions": [
        {"version": "5.3.0.2", "status": "AVAILABLE", "isEnabled": True, "order": 23},
        {"version": "5.2.1.2", "status": "AVAILABLE", "isEnabled": True, "order": 20},
        {"version": "5.3.0.1", "status": "AVAILABLE", "isEnabled": True, "order": 22},
        {"version": "5.3", "status": "AVAILABLE", "isEnabled": True, "order": 21},
    ],
}

# The Prism Central pseudo-cluster, and the second spelling of its class.
PC_ENTITY = {
    "extId": "de44eeec-9777-4e1b-b309-0b11ee49dfd2",
    "entityClass": "PC CORE CLUSTER",
    "entityModel": "Network Controller",
    "entityType": "SOFTWARE",
    "entityVersion": "7.0.0",
    "targetVersion": "7.0.0",
    "clusterExtId": "e807ab87-2889-49b8-b883-cd7b4afb2814",
    "availableVersions": [
        {"version": "7.0.1", "status": "AVAILABLE", "isEnabled": True, "order": 14},
    ],
}

# 19 of the 34 live entities look like this: no availableVersions key at all.
SECURITY_AOS = {
    "extId": "a69ef20d-21af-4ca9-a9cd-f9c85410f13d",
    "entityClass": "Cluster Service",
    "entityModel": "Security AOS",
    "entityType": "SOFTWARE",
    "entityVersion": "security_aos.2022.9",
    "targetVersion": "security_aos.2022.9",
    "clusterExtId": "0006442f-1fc6-3ee1-0000-000000016d34",
}

STATUS = {
    "data": {
        "frameworkVersion": {
            "currentVersion": "3.4.0.1.90933",
            "availableVersion": "3.4.0.1.90933",
            "isUpdateNeeded": False,
        }
    }
}


class _Conn:
    def __init__(self, entities: list[dict[str, Any]], v4: bool = True) -> None:
        self._v4 = v4
        self._entities = entities
        self.negotiated: dict[str, str] = {} if v4 else {"lifecycle": "absent"}

    def v4_ready(self, area: str) -> bool:
        return self._v4

    def v4_list(self, path: str) -> list[dict[str, Any]]:
        assert path == "lifecycle/v4.0/resources/entities"
        return [dict(e) for e in self._entities]

    def v4_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return STATUS


def _collect(conn: _Conn, clusters: list[Cluster] | None = None) -> Environment:
    env = Environment()
    env.clusters.extend(clusters or [])
    LcmCollector(conn).collect(env)  # type: ignore[arg-type]
    return env


def _one(entity: dict[str, Any], **kw: Any) -> Any:
    return _collect(_Conn([entity]), **kw).lcm_entities[0]


# ─── field mapping ─────────────────────────────────────────────────────


def test_entity_fields_map_from_v4() -> None:
    e = _one(AHV)
    assert e.uuid == AHV["extId"]
    assert e.name == "AHV hypervisor"
    assert e.entity_type == "SOFTWARE"
    assert e.current_version == "11.0"
    assert e.last_updated == "2025-12-11T09:40:36.682668Z"
    assert e.api_source == "v4"


def test_target_version_is_reported_but_is_not_the_update_signal() -> None:
    """The trap: `targetVersion` equals the running version on every live entity.

    It is LCM's current target, not an offer, so it is carried through as-is and
    `update_available` is derived from the catalogue instead.
    """
    e = _one(AHV)
    assert e.target_version == "11.0" == e.current_version
    assert e.update_available is True


# ─── available_versions ────────────────────────────────────────────────


def test_available_versions_are_newest_first_by_lcm_order() -> None:
    assert _one(FSM).available_versions == ["5.3.0.2", "5.3.0.1", "5.3", "5.2.1.2"]


def test_disabled_versions_are_dropped_but_recommended_ones_are_kept() -> None:
    entity = dict(FSM)
    entity["availableVersions"] = [
        {"version": "5.3.0.2", "status": "AVAILABLE", "isEnabled": False, "order": 23},
        {"version": "5.3", "status": "RECOMMENDED", "isEnabled": True, "order": 21},
        # An unknown future status must not be filtered out.
        {"version": "5.2.1.2", "status": "SOMETHING_NEW", "isEnabled": True, "order": 20},
    ]
    assert _one(entity).available_versions == ["5.3", "5.2.1.2"]


def test_missing_available_versions_is_an_empty_list_not_a_failure() -> None:
    e = _one(SECURITY_AOS)
    assert e.available_versions == []
    assert e.update_available is False


# ─── the update rule ───────────────────────────────────────────────────


def test_update_available_is_true_only_for_a_genuinely_newer_offer() -> None:
    assert _one(AHV).update_available is True
    assert _one(FSM).update_available is True


def test_offering_only_the_running_version_is_not_an_update() -> None:
    """A non-empty catalogue is not enough: LCM may list the version already running."""
    entity = dict(AHV)
    entity["entityVersion"] = "11.0.0.2"
    entity["availableVersions"] = [
        {"version": "11.0.0.2", "status": "AVAILABLE", "isEnabled": True, "order": 136},
    ]
    e = _one(entity)
    assert e.available_versions == ["11.0.0.2"]
    assert e.update_available is False


def test_older_offers_alone_are_not_an_update() -> None:
    """Downgrade targets are still listed; they must not read as an available update."""
    entity = dict(AHV)
    entity["entityVersion"] = "11.0.0.5"
    assert _one(entity).update_available is False


def test_shorter_running_version_compares_component_wise() -> None:
    """"11.0" vs "11.0.0.1" — padding, not string length, decides."""
    entity = dict(AHV)
    entity["availableVersions"] = [
        {"version": "11.0.0.0", "status": "AVAILABLE", "isEnabled": True, "order": 1},
    ]
    assert _one(entity).update_available is False


def test_prefixed_versions_compare_within_their_series() -> None:
    entity = dict(PC_ENTITY)
    entity["entityVersion"] = "pc.7.5.1.1"
    entity["availableVersions"] = [
        {"version": "pc.7.5.1.6", "status": "AVAILABLE", "isEnabled": True, "order": 14},
    ]
    assert _one(entity).update_available is True


def test_unorderable_but_different_offer_counts_as_an_update() -> None:
    """Different series, so no ordering — but LCM does not offer the running build."""
    entity = dict(SECURITY_AOS)
    entity["availableVersions"] = [
        {"version": "LM.2025.9.2", "status": "AVAILABLE", "isEnabled": True, "order": 3},
    ]
    assert _one(entity).update_available is True


def test_unknown_running_version_leaves_the_answer_unknown() -> None:
    """v4 pads absent values with empty strings; guessing from that would be a lie."""
    entity = dict(AHV)
    entity["entityVersion"] = ""
    e = _one(entity)
    assert e.current_version is None
    assert e.update_available is None


# ─── entity_class normalization ────────────────────────────────────────


def test_entity_class_case_is_canonicalized_without_wrecking_acronyms() -> None:
    """The live PC returns both "PC Core Cluster" and "PC CORE CLUSTER"."""
    mixed = dict(PC_ENTITY)
    titled = dict(PC_ENTITY, entityClass="PC Core Cluster")
    classes = {e.entity_class for e in _collect(_Conn([mixed, titled])).lcm_entities}
    assert classes == {"PC Core Cluster"}


def test_shouty_class_is_title_cased() -> None:
    assert _one(dict(AHV, entityClass="LICENSING SERVICE")).entity_class == "Licensing Service"


def test_class_words_with_digits_are_left_alone() -> None:
    e = _one(dict(AHV, entityClass="NX-1065 FIRMWARE"))
    assert e.entity_class == "NX-1065 Firmware"


# ─── cluster resolution ────────────────────────────────────────────────


def test_cluster_name_resolves_from_phase_one_clusters() -> None:
    clusters = [Cluster(uuid=AHV["clusterExtId"], name="lab-cluster-01")]
    e = _collect(_Conn([AHV]), clusters=clusters).lcm_entities[0]
    assert e.cluster_name == "lab-cluster-01"
    assert e.cluster_uuid == AHV["clusterExtId"]


def test_prism_centrals_own_pseudo_cluster_is_labelled() -> None:
    """The cluster collector keeps it out of env.clusters, so half the rows need this."""
    env = Environment()
    env.prism_central.uuid = PC_ENTITY["clusterExtId"]
    LcmCollector(_Conn([PC_ENTITY])).collect(env)  # type: ignore[arg-type]
    assert env.lcm_entities[0].cluster_name == "Prism Central"


def test_unknown_cluster_falls_back_to_the_raw_uuid() -> None:
    e = _one(AHV)  # no clusters collected
    assert e.cluster_name == AHV["clusterExtId"]
    assert e.cluster_uuid == AHV["clusterExtId"]


# ─── skip path ─────────────────────────────────────────────────────────


def test_skips_cleanly_when_v4_lifecycle_is_not_served() -> None:
    """LCM is v4-only — there is no v3 path to fall back to."""
    env = _collect(_Conn([AHV], v4=False))
    assert env.lcm_entities == []
    assert env.collection_notes["lcm_entities"].startswith("skipped:")


def test_note_reports_the_count_and_the_lcm_framework_version() -> None:
    note = _collect(_Conn([AHV, FSM])).collection_notes["lcm_entities"]
    assert note == "ok: 2 entities, LCM framework 3.4.0.1.90933"
