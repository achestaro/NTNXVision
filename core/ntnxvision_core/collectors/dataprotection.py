"""Data Protection / DR collector.

Protection policies, recovery plans, availability zones and each cluster's NearSync
capability.

* **Protection policies**: v4 ``datapolicies`` when served, else v3 ``protection_rules``.
  Both carry the policy's sites (an AZ plus the clusters protected or replicated to) and
  its replication directions with their RPO. Neither API has a sync / async enum: the
  type is derived from the RPO by `models.replication_type_for_rpo`, the one place that
  rule lives (0 Sync, up to 15 minutes NearSync, longer Async).
* **Recovery plans**: v4 ``datapolicies/v4.0/config/recovery-plans`` when served (it is on
  pc.7.5.1.1; an earlier version of this module said there was no v4 endpoint, which was
  wrong). The v4 list names each side's AZ and clusters but not the stages or network
  mappings (those are v4.3 sub-resources), so those counts are merged from v3 by uuid
  while v3 exists. Below the v4 line, v3 only.
* **Availability zones**: v3 ``availability_zones`` (AZ pairing in v4 is the PC's
  registrations list, collected with the Prism Central configuration).
* **NearSync capability**: v4 ``dataprotection/v4.0/config/cluster-capabilities``,
  best-effort, recorded on the collected clusters.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.api.v4_client import first_served
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import (
    AvailabilityZone,
    ProtectionPolicy,
    RecoveryPlan,
    ReplicationLocation,
    ReplicationRule,
    replication_type_for_rpo,
)

RECOVERY_PLANS_V4 = "datapolicies/v4.0/config/recovery-plans"
CAPABILITIES_V4 = "dataprotection/v4.0/config/cluster-capabilities"


class DataProtectionCollector(Collector):
    name = "data_protection"
    label = "Data Protection / DR"

    def collect(self, env: Any) -> None:
        self._protection_policies(env)
        self._recovery_plans(env)
        self._availability_zones(env)
        self._nearsync_capability(env)

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
        the `ntnx-dataprotection-py-client` package name.

        Categories come from v3: v4 reports `categoryIds` as bare uuids where v3 gives the
        readable `DRTier:PortOps` form the reports show. Both APIs agree on the policy's
        uuid, so the merge is exact.
        """
        categories = self._v3_categories_by_uuid()
        for entity in self.conn.v4_list("datapolicies/v4.0/config/protection-policies"):
            uuid = entity.get("extId") or ""
            # `label` mirrors v3's availability_zone_label; the primary site first, then
            # by label, reproduces v3's ordered_availability_zone_list. domainManagerExtId
            # carries the identifier v3 puts in availability_zone_url, so the engine's
            # AZ-name resolution works on either.
            raw = [loc for loc in entity.get("replicationLocations") or []
                   if isinstance(loc, dict)]
            raw.sort(key=lambda loc: (not loc.get("isPrimary"), str(loc.get("label") or "")))
            locations = [ReplicationLocation(
                label=_str(loc.get("label")),
                az_ref=loc.get("domainManagerExtId"),
                is_primary=loc.get("isPrimary"),
                cluster_uuids=[str(c) for c in ((loc.get("replicationSubLocation") or {})
                                                .get("clusterExtIds") or [])],
            ) for loc in raw]
            rules = [_rule_v4(c) for c in entity.get("replicationConfigurations") or []
                     if isinstance(c, dict)]
            # The headline schedule: the primary site's outgoing one when there is one.
            primary = locations[0].label if locations else None
            head = next((r for r in rules if r.source_label == primary),
                        rules[0] if rules else None)

            env.protection_policies.append(
                ProtectionPolicy(
                    uuid=uuid,
                    name=entity.get("name"),
                    rpo_seconds=head.rpo_seconds if head else None,
                    replication_type=head.replication_type if head else None,
                    local_retention=head.local_retention if head else None,
                    remote_retention=head.remote_retention if head else None,
                    source_az=locations[0].az_ref if locations else None,
                    target_az=locations[1].az_ref if len(locations) > 1 else None,
                    categories=categories.get(uuid, []),
                    locations=locations,
                    replications=rules,
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

            ordered = [az for az in resources.get("ordered_availability_zone_list", []) or []
                       if isinstance(az, dict)]
            src_az, tgt_az = _az_pair(ordered)
            cat_filter = (resources.get("category_filter") or {}).get("params") or {}
            categories = [f"{k}:{','.join(v)}" for k, v in cat_filter.items()]
            locations = [ReplicationLocation(
                label=str(i), az_ref=az.get("availability_zone_url"), is_primary=i == 0,
                cluster_uuids=[str(az["cluster_uuid"])] if az.get("cluster_uuid") else [],
            ) for i, az in enumerate(ordered)]
            rules = [r for c in schedules if isinstance(c, dict)
                     if (r := _rule_v3(c)) is not None]

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
                    locations=locations,
                    replications=rules,
                    api_source="v3",
                )
            )

    # ─── recovery plans ────────────────────────────────────────────

    def _recovery_plans(self, env: Any) -> None:
        if self.conn.v4_ready("dataprotection"):
            try:
                plans = self.conn.v4_list(RECOVERY_PLANS_V4)
            except NutanixApiError:
                plans = None
            if plans is not None:
                self._recovery_plans_v4(env, plans)
                return
        env.recovery_plans.extend(self._recovery_plans_v3())

    def _recovery_plans_v4(self, env: Any, plans: list[dict[str, Any]]) -> None:
        """v4 plans, with the stage / entity / network-mapping counts merged from v3."""
        try:
            details = {p.uuid: p for p in self._recovery_plans_v3()}
        except NutanixApiError:
            details = {}
        for entity in plans:
            uuid = str(entity.get("extId") or "")
            primary = entity.get("primaryLocation") or {}
            recovery = entity.get("recoveryLocation") or {}
            v3 = details.get(uuid)
            env.recovery_plans.append(RecoveryPlan(
                uuid=uuid,
                name=entity.get("name"),
                description=entity.get("description") or None,
                stage_count=v3.stage_count if v3 else None,
                entities_count=v3.entities_count if v3 else None,
                primary_location=primary.get("domainManagerExtId")
                or (v3.primary_location if v3 else None),
                recovery_location=recovery.get("domainManagerExtId")
                or (v3.recovery_location if v3 else None),
                primary_cluster_uuids=_ext_ids(primary.get("clusters")),
                recovery_cluster_uuids=_ext_ids(recovery.get("clusters")),
                witness_uuid=(entity.get("witness") or {}).get("extId") or None,
                network_mappings=v3.network_mappings if v3 else None,
                api_source="v4",
            ))

    def _recovery_plans_v3(self) -> list[RecoveryPlan]:
        out: list[RecoveryPlan] = []
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

            out.append(
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
        return out

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
                    management_plane_type=resources.get("management_plane_type")
                    or status_res.get("management_plane_type"),
                    api_source="v3",
                )
            )


    # ─── NearSync capability ───────────────────────────────────────

    def _nearsync_capability(self, env: Any) -> None:
        # The capabilities live in the `dataprotection` namespace, which the negotiated
        # "dataprotection" area does not probe (it probes `datapolicies`, where policies
        # and recovery plans live): probe this path on its own.
        if not env.clusters or not first_served(self.conn, [CAPABILITIES_V4]):
            return
        try:
            rows = self.conn.v4_list(CAPABILITIES_V4)
        except NutanixApiError:
            return
        by_cluster: dict[str, bool] = {}
        for row in rows:
            for cap in row.get("capabilities") or []:
                if isinstance(cap, dict) and cap.get("capabilityName") == "SUPPORTS_NEAR_SYNC":
                    by_cluster[str(row.get("clusterExtId") or row.get("extId"))] = bool(
                        cap.get("isSupported"))
        for cluster in env.clusters:
            if cluster.uuid in by_cluster:
                cluster.nearsync_capable = by_cluster[cluster.uuid]


# ─── helpers ───────────────────────────────────────────────────────────


def _str(value: Any) -> str | None:
    return None if value is None else str(value)


def _ext_ids(refs: Any) -> list[str]:
    return [str(r.get("extId")) for r in refs or [] if isinstance(r, dict) and r.get("extId")]


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _rule_v4(config: dict[str, Any]) -> ReplicationRule:
    schedule = config.get("schedule") or {}
    retention = schedule.get("retention") or {}
    rpo = schedule.get("recoveryPointObjectiveTimeSeconds")
    suspend = schedule.get("syncReplicationAutoSuspendTimeoutSeconds")
    kind = replication_type_for_rpo(rpo)
    if suspend is not None and rpo in (None, 0):
        kind = "SYNC"
    return ReplicationRule(
        source_label=_str(config.get("sourceLocationLabel")),
        target_label=_str(config.get("remoteLocationLabel")),
        rpo_seconds=rpo,
        replication_type=kind,
        recovery_point_type=schedule.get("recoveryPointType"),
        # Linear retention is a count; auto-rollup retention has no single count.
        local_retention=_count(retention.get("local")),
        remote_retention=_count(retention.get("remote")),
        sync_auto_suspend_timeout_secs=suspend,
    )


def _rule_v3(conn: dict[str, Any]) -> ReplicationRule | None:
    scheds = [x for x in conn.get("snapshot_schedule_list", []) or [] if isinstance(x, dict)]
    if not scheds:
        return None
    sched = scheds[0]
    rpo = sched.get("recovery_point_objective_secs")
    return ReplicationRule(
        source_label=_str(conn.get("source_availability_zone_index")),
        target_label=_str(conn.get("destination_availability_zone_index")),
        rpo_seconds=rpo,
        replication_type=replication_type_for_rpo(rpo),
        recovery_point_type=sched.get("snapshot_type"),
        local_retention=_retention(sched.get("local_snapshot_retention_policy", {})),
        remote_retention=_retention(sched.get("remote_snapshot_retention_policy", {})),
    )



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
            repl = replication_type_for_rpo(rpo)
            local = _retention(sched.get("local_snapshot_retention_policy", {}))
            remote = _retention(sched.get("remote_snapshot_retention_policy", {}))
            return rpo, repl, local, remote
    return None, None, None, None


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
