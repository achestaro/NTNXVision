"""Data Protection / DR collector.

Protection policies, recovery plans, and availability zones. These live in the v3 API
(and, on newer PC, the v4 `dataprotection` namespace). Phase 2 uses v3, which is present
on every PC version that supports Leap/DR.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import (
    AvailabilityZone,
    ProtectionPolicy,
    RecoveryPlan,
)


class DataProtectionCollector(Collector):
    name = "data_protection"
    label = "Data Protection / DR"

    def collect(self, env: Any) -> None:
        self._protection_policies(env)
        self._recovery_plans(env)
        self._availability_zones(env)

        env.data_protection.protection_policy_count = len(env.protection_policies)
        env.data_protection.recovery_plan_count = len(env.recovery_plans)
        env.data_protection.availability_zone_count = len(env.availability_zones)
        env.collection_notes[self.name] = "ok"

    # ─── protection policies (async/nearsync/sync/metro) ───────────

    def _protection_policies(self, env: Any) -> None:
        for entity in self.conn.v3_list("protection_rules"):
            spec = entity.get("spec", {})
            resources = spec.get("resources", {})
            schedules = resources.get("availability_zone_connectivity_list", []) or []
            rpo, repl_type, local_ret, remote_ret = _first_schedule(schedules)

            src_az, tgt_az = _az_pair(resources.get("ordered_availability_zone_list", []))
            cat_filter = resources.get("category_filter", {}).get("params", {}) or {}
            categories = [f"{k}:{','.join(v)}" for k, v in cat_filter.items()]

            env.protection_policies.append(
                ProtectionPolicy(
                    uuid=entity.get("metadata", {}).get("uuid", ""),
                    name=spec.get("name"),
                    rpo_seconds=rpo,
                    replication_type=repl_type,
                    local_retention=local_ret,
                    remote_retention=remote_ret,
                    source_az=src_az,
                    target_az=tgt_az,
                    categories=categories,
                    api_source="v3",
                )
            )

    # ─── recovery plans ────────────────────────────────────────────

    def _recovery_plans(self, env: Any) -> None:
        for entity in self.conn.v3_list("recovery_plans"):
            spec = entity.get("spec", {})
            resources = spec.get("resources", {})
            stages = resources.get("stage_list", []) or []
            entities = sum(_stage_entity_count(s) for s in stages)
            network_maps = resources.get("network_mapping_list", []) or []
            locations = _plan_locations(network_maps)

            env.recovery_plans.append(
                RecoveryPlan(
                    uuid=entity.get("metadata", {}).get("uuid", ""),
                    name=spec.get("name"),
                    stage_count=len(stages),
                    entities_count=entities,
                    primary_location=locations[0] if locations else None,
                    recovery_location=locations[1] if len(locations) > 1 else None,
                    network_mappings=len(network_maps),
                    api_source="v3",
                )
            )

    # ─── availability zones ────────────────────────────────────────

    def _availability_zones(self, env: Any) -> None:
        try:
            entities = self.conn.v3_list("availability_zones")
        except Exception:  # noqa: BLE001 — AZ list may be unavailable pre-Leap
            return
        for entity in entities:
            spec = entity.get("spec", {})
            resources = spec.get("resources", {})
            env.availability_zones.append(
                AvailabilityZone(
                    uuid=entity.get("metadata", {}).get("uuid"),
                    name=spec.get("name") or resources.get("display_name"),
                    az_type=resources.get("availability_zone_type"),
                    region=resources.get("region"),
                    url=resources.get("management_url"),
                    connection_status=entity.get("status", {})
                    .get("resources", {})
                    .get("connection_status"),
                    api_source="v3",
                )
            )


# ─── helpers ───────────────────────────────────────────────────────────


def _stage_entity_count(stage: dict[str, Any]) -> int:
    work = stage.get("stage_work", {}).get("recover_entities", {})
    return len(work.get("entity_info_list", []) or [])


def _first_schedule(
    schedules: list[dict[str, Any]],
) -> tuple[int | None, str | None, int | None, int | None]:
    for conn in schedules:
        for sched in conn.get("snapshot_schedule_list", []) or []:
            rpo = sched.get("recovery_point_objective_secs")
            repl = "SYNC" if rpo == 0 else _repl_from_rpo(rpo)
            local = _retention(sched.get("local_snapshot_retention_policy", {}))
            remote = _retention(sched.get("remote_snapshot_retention_policy", {}))
            return rpo, repl, local, remote
    return None, None, None, None


def _repl_from_rpo(rpo: int | None) -> str | None:
    if rpo is None:
        return None
    if rpo <= 900:  # <= 15 min
        return "NEARSYNC"
    return "ASYNC"


def _retention(policy: dict[str, Any]) -> int | None:
    if not policy:
        return None
    linear = policy.get("num_snapshots")
    if linear is not None:
        return linear
    rollup = policy.get("rollup_retention_policy", {})
    return rollup.get("snapshot_interval_type") and rollup.get("multiple")


def _az_pair(az_list: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    names = [az.get("availability_zone_url") for az in az_list if az.get("availability_zone_url")]
    src = names[0] if names else None
    tgt = names[1] if len(names) > 1 else None
    return src, tgt


def _plan_locations(network_maps: list[dict[str, Any]]) -> list[str]:
    locations: list[str] = []
    for nm in network_maps:
        for az in nm.get("availability_zone_network_mapping_list", []) or []:
            url = az.get("availability_zone_url")
            if url and url not in locations:
                locations.append(url)
    return locations
