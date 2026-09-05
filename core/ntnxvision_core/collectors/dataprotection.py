"""Data Protection / DR collector.

Protection policies, recovery plans, and availability zones. These live in the v3 API
(and, on newer PC, the v4 `dataprotection` namespace). Phase 2 uses v3, which is present
on every PC version that supports Leap/DR.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
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
        if self.conn.v4_ready("dataprotection"):
            self._protection_policies_v4(env)
        else:
            self._protection_policies_v3(env)

    def _protection_policies_v4(self, env: Any) -> None:
        """Policies from the `datapolicies` namespace.

        Note the namespace: `dataprotection/v4.0/config/protection-policies` 404s despite
        the `ntnx-dataprotection-py-client` package name. Recovery plans and availability
        zones have no v4 endpoint at all on this build, so those stay on v3 below.

        Categories come from v3: v4 reports `categoryIds` as bare uuids where v3 gives the
        readable `DRTier:PortOps` form the reports show. Both APIs agree on the policy's
        uuid, so the merge is exact.
        """
        categories = self._v3_categories_by_uuid()
        for entity in self.conn.v4_list("datapolicies/v4.0/config/protection-policies"):
            uuid = entity.get("extId") or ""
            configs = entity.get("replicationConfigurations") or []
            schedule = (configs[0].get("schedule") or {}) if configs else {}
            retention = schedule.get("retention") or {}
            rpo = schedule.get("recoveryPointObjectiveTimeSeconds")

            # `label` mirrors v3's availability_zone_label, so ordering by it reproduces
            # v3's ordered_availability_zone_list. domainManagerExtId carries the same
            # identifier v3 puts in availability_zone_url, so the engine's AZ-name
            # resolution keeps working untouched.
            locations = sorted(
                entity.get("replicationLocations") or [],
                key=lambda loc: str(loc.get("label") or ""),
            )
            azs = [loc.get("domainManagerExtId") for loc in locations]

            env.protection_policies.append(
                ProtectionPolicy(
                    uuid=uuid,
                    name=entity.get("name"),
                    rpo_seconds=rpo,
                    replication_type=_repl_from_rpo(rpo),
                    local_retention=retention.get("local"),
                    remote_retention=retention.get("remote"),
                    source_az=azs[0] if azs else None,
                    target_az=azs[1] if len(azs) > 1 else None,
                    categories=categories.get(uuid, []),
                    api_source="v4",
                )
            )

    def _v3_categories_by_uuid(self) -> dict[str, list[str]]:
        """Readable `key:value` category filters, which v4 exposes only as uuids."""
        try:
            entities = self.conn.v3_list("protection_rules")
        except NutanixApiError:
            return {}
        out: dict[str, list[str]] = {}
        for entity in entities:
            uuid = entity.get("metadata", {}).get("uuid")
            if not uuid:
                continue
            params = (
                (entity.get("spec", {}).get("resources", {}).get("category_filter") or {})
                .get("params")
                or {}
            )
            out[uuid] = [f"{k}:{','.join(v)}" for k, v in params.items()]
        return out

    def _protection_policies_v3(self, env: Any) -> None:
        for entity in self.conn.v3_list("protection_rules"):
            spec = entity.get("spec", {})
            resources = spec.get("resources", {})
            schedules = resources.get("availability_zone_connectivity_list", []) or []
            rpo, repl_type, local_ret, remote_ret = _first_schedule(schedules)

            src_az, tgt_az = _az_pair(resources.get("ordered_availability_zone_list", []))
            cat_filter = (resources.get("category_filter") or {}).get("params") or {}
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
            # Network mappings and the AZ order live under resources.parameters in v3
            # (reading them off resources directly always yielded 0 / no locations).
            parameters = resources.get("parameters", {}) or {}
            network_maps = (
                parameters.get("network_mapping_list")
                or resources.get("network_mapping_list")
                or []
            )
            az_order = (
                parameters.get("availability_zone_list")
                or resources.get("availability_zone_list")
                or []
            )
            locations = [
                az.get("availability_zone_url")
                for az in az_order
                if isinstance(az, dict) and az.get("availability_zone_url")
            ] or _plan_locations(network_maps)

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
            status_res = (entity.get("status", {}) or {}).get("resources", {}) or {}
            name = (
                spec.get("name")
                or resources.get("display_name")
                or status_res.get("display_name")
            )
            env.availability_zones.append(
                AvailabilityZone(
                    uuid=entity.get("metadata", {}).get("uuid"),
                    name=name,
                    az_type=resources.get("availability_zone_type")
                    or status_res.get("availability_zone_type"),
                    region=resources.get("region") or status_res.get("region"),
                    url=resources.get("management_url") or status_res.get("management_url"),
                    connection_status=_az_connectivity(status_res, entity),
                    api_source="v3",
                )
            )


# ─── helpers ───────────────────────────────────────────────────────────


def _az_connectivity(status_res: dict[str, Any], entity: dict[str, Any]) -> str | None:
    """AZ connectivity across v3 shape variants.

    Different PC builds report it as status.resources.connection_status, a nested
    connectivity_status object, or status.state.
    """
    direct = status_res.get("connection_status")
    if isinstance(direct, str) and direct:
        return direct
    nested = status_res.get("connectivity_status")
    if isinstance(nested, dict):
        for key in ("connection_status", "status", "state"):
            v = nested.get(key)
            if isinstance(v, str) and v:
                return v
    elif isinstance(nested, str) and nested:
        return nested
    state = (entity.get("status", {}) or {}).get("state")
    return state if isinstance(state, str) and state else None


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
    rollup = policy.get("rollup_retention_policy", {}) or {}
    multiple = rollup.get("multiple")
    return multiple if isinstance(multiple, int) else None


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
