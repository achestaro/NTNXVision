"""Feature usage vs. licensing analysis.

RVTools never did this: cross-reference what's actually *configured* in the environment
against the Nutanix product that licenses it. The result highlights which licensable
features are in use — useful for right-sizing licenses and spotting features running
without an obvious entitlement.

Returns a list of rows the exporters render on a dedicated sheet/section. This is a pure
function over a populated Environment (no API calls).
"""

from __future__ import annotations

from typing import Any


class FeatureUsage:
    def __init__(self, product: str, feature: str, in_use: bool, detail: str) -> None:
        self.product = product
        self.feature = feature
        self.in_use = in_use
        self.detail = detail

    def as_row(self) -> list[Any]:
        return [self.product, self.feature, "Yes" if self.in_use else "No", self.detail]


def analyze_feature_usage(env: Any) -> list[FeatureUsage]:
    """Derive a feature-usage matrix keyed by the licensing product."""
    rows: list[FeatureUsage] = []

    # NCI edition indicators
    sync = [p for p in env.protection_policies if p.replication_type in ("SYNC", "NEARSYNC")]
    metro = [p for p in env.protection_policies if p.replication_type == "METRO"]
    ec = [s for s in env.storage_containers if s.erasure_coding]
    encryption = [c for c in env.clusters if c.encryption_enabled]
    rf3 = [c for c in env.clusters if c.redundancy_factor and c.redundancy_factor >= 3]
    gpus = [h for h in env.hosts if h.gpu_list]

    rows += [
        FeatureUsage("NCI Ultimate", "Sync/NearSync replication", bool(sync),
                     f"{len(sync)} policy(ies)"),
        FeatureUsage("NCI Ultimate", "Metro Availability", bool(metro),
                     f"{len(metro)} policy(ies)"),
        FeatureUsage("NCI Ultimate", "Erasure Coding (EC-X)", bool(ec),
                     f"{len(ec)} container(s)"),
        FeatureUsage("NCI Ultimate", "Redundancy Factor 3", bool(rf3),
                     f"{len(rf3)} cluster(s)"),
        FeatureUsage("NCI Pro", "Async DR replication",
                     bool(env.protection_policies), f"{len(env.protection_policies)} policy(ies)"),
        FeatureUsage("NCI Pro", "Data-at-Rest Encryption", bool(encryption),
                     f"{len(encryption)} cluster(s)"),
        FeatureUsage("NCI Pro", "GPU passthrough", bool(gpus), f"{len(gpus)} host(s)"),
    ]

    # Feature-based products
    rows += [
        FeatureUsage("Flow Network Security", "Microsegmentation policies",
                     bool(env.security_policies), f"{len(env.security_policies)} policy(ies)"),
        FeatureUsage("Flow Virtual Networking", "VPCs",
                     bool(env.vpcs), f"{len(env.vpcs)} VPC(s)"),
        FeatureUsage("NUS - Files", "File servers",
                     bool(env.file_servers), f"{len(env.file_servers)} server(s)"),
        FeatureUsage("NUS - Objects", "Object stores",
                     bool(env.object_stores), f"{len(env.object_stores)} store(s)"),
        FeatureUsage("NUS - Volumes", "Volume groups",
                     bool(env.volume_groups), f"{len(env.volume_groups)} group(s)"),
        FeatureUsage("NDB", "Managed databases",
                     bool(env.databases), f"{len(env.databases)} database(s)"),
        FeatureUsage("NCM", "X-Play playbooks",
                     bool(env.playbooks), f"{len(env.playbooks)} playbook(s)"),
        FeatureUsage("NCM", "Reports",
                     bool(env.ncm_reports), f"{len(env.ncm_reports)} report(s)"),
        FeatureUsage("Leap / DR", "Recovery plans",
                     bool(env.recovery_plans), f"{len(env.recovery_plans)} plan(s)"),
    ]

    return rows
