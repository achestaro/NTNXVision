"""Feature usage vs. licensing analysis.

RVTools never did this: cross-reference what's actually *configured* in the environment
against the Nutanix product that licenses it. The result highlights which licensable
features are in use — useful for right-sizing licenses and spotting features running
without an obvious entitlement.

Returns a list of rows the exporters render on a dedicated sheet/section. This is a pure
function over a populated Environment (no API calls).

"No" is only claimed for an area that was actually collected. When the collector did not
run (a Prism Element collection, where DR / Flow / Files / NDB are Prism Central scoped,
or an area left unticked) the row says "Not collected"; when it failed, "Unknown". An
empty list from a collector that never ran is not evidence the feature is unused.
"""

from __future__ import annotations

from typing import Any

#: The `collection_notes` key whose collector decides each feature's rows.
_SOURCES = {
    "clusters": "clusters", "storage": "storage_containers", "hosts": "hosts",
    "dr": "data_protection", "flow": "flow_security", "fvn": "flow_networking",
    "files": "files", "objects": "objects", "volumes": "volume_groups", "ndb": "ndb",
    "xplay": "xplay", "ncm": "ncm_reports",
}


class FeatureUsage:
    def __init__(self, product: str, feature: str, in_use: bool, detail: str,
                 collected: bool | None = True) -> None:
        self.product = product
        self.feature = feature
        self.in_use = in_use
        self.detail = detail
        #: True = collected, False = not collected, None = collection failed.
        self.collected = collected

    @property
    def status(self) -> str:
        """Yes / No / Not collected / Unknown: the "In Use" cell every format renders."""
        if self.in_use:
            return "Yes"
        if self.collected is None:
            return "Unknown"
        if not self.collected:
            return "Not collected"
        return "No"

    def as_row(self) -> list[Any]:
        return [self.product, self.feature, self.status, self.detail]


def _outcome(env: Any, note_key: str) -> bool | None:
    """True when the area was collected (by any source of a combined run), None when it
    errored everywhere it ran, False when no collector for it ran at all."""
    notes = env.collection_notes or {}
    values = [v for k, v in notes.items() if k == note_key or k.endswith(f": {note_key}")]
    if not values:
        # Snapshots from before collection notes existed: trust the data.
        return not notes
    if any(v == "ok" or v.startswith(("ok:", "skipped")) for v in values):
        return True
    return None


def analyze_feature_usage(env: Any) -> list[FeatureUsage]:
    """Derive a feature-usage matrix keyed by the licensing product."""
    rows: list[FeatureUsage] = []
    seen = {name: _outcome(env, key) for name, key in _SOURCES.items()}

    def row(product: str, feature: str, items: list[Any], unit: str, source: str) -> None:
        collected = seen[source]
        detail = f"{len(items):,} {unit}"
        if not items and collected is False:
            detail = ("Prism Central scoped: not collected in Prism Element mode"
                      if env.source_type == "prism_element" else "Area not collected")
        elif not items and collected is None:
            detail = "Collection failed"
        rows.append(FeatureUsage(product, feature, bool(items), detail, collected))

    # NCI edition indicators
    sync = [p for p in env.protection_policies if p.replication_type in ("SYNC", "NEARSYNC")]
    metro = [p for p in env.protection_policies if p.replication_type == "METRO"]
    ec = [s for s in env.storage_containers
          if s.erasure_coding and str(s.erasure_coding).lower() not in ("off", "none", "false")]
    encryption = [c for c in env.clusters if c.encryption_enabled]
    rf3 = [c for c in env.clusters if c.redundancy_factor and c.redundancy_factor >= 3]
    gpus = [h for h in env.hosts if h.gpu_list]

    row("NCI Ultimate", "Sync/NearSync replication", sync, "policy(ies)", "dr")
    row("NCI Ultimate", "Metro Availability", metro, "policy(ies)", "dr")
    row("NCI Ultimate", "Erasure Coding (EC-X)", ec, "container(s)", "storage")
    row("NCI Ultimate", "Redundancy Factor 3", rf3, "cluster(s)", "clusters")
    row("NCI Pro", "Async DR replication", env.protection_policies, "policy(ies)", "dr")
    row("NCI Pro", "Data-at-Rest Encryption", encryption, "cluster(s)", "clusters")
    row("NCI Pro", "GPU passthrough", gpus, "host(s)", "hosts")

    # Feature-based products
    row("Flow Network Security", "Microsegmentation policies", env.security_policies,
        "policy(ies)", "flow")
    row("Flow Virtual Networking", "VPCs", env.vpcs, "VPC(s)", "fvn")
    row("NUS - Files", "File servers", env.file_servers, "server(s)", "files")
    row("NUS - Objects", "Object stores", env.object_stores, "store(s)", "objects")
    row("NUS - Volumes", "Volume groups", env.volume_groups, "group(s)", "volumes")
    row("NDB", "Managed databases", env.databases, "database(s)", "ndb")
    row("NCM", "X-Play playbooks", env.playbooks, "playbook(s)", "xplay")
    row("NCM", "Reports", env.ncm_reports, "report(s)", "ncm")
    row("Leap / DR", "Recovery plans", env.recovery_plans, "plan(s)", "dr")

    return rows
