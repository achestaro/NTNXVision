"""v4 API capability negotiation.

Nutanix retires the legacy v3/v2 APIs in Q4-2026, so collectors need to move onto the v4
namespaces. They cannot simply switch: v4 is GA only from ``pc.2024.3`` / AOS 7.0, while
NTNXVision's supported floor is ``pc.2024.1``. Every feature area therefore needs a v4
path *and* a legacy fallback, chosen per target rather than per build of this tool.

This module owns that choice. Collectors ask :meth:`PrismConnection.v4_ready` for a
feature area and take the v4 path only when the answer is yes; otherwise they keep using
the v3/v2 code that already works.

**Why probing rather than a version table.** Version is necessary but not sufficient. The
lab PC runs 7.5.1.1 — comfortably past the v4 GA line — yet ``objects`` answers 503
(deployed service not running) and several namespaces are not where their SDK package
names suggest. A version check alone would route collectors to endpoints that are not
there. So the version gate is applied first because it is free, and a single cheap probe
per namespace settles the rest. Results are cached on the connection: one extra request
per namespace per run, and only for namespaces something actually asks about.

**Namespace paths are recorded from a live PC**, not inferred from the SDK package names,
because the two disagree: storage containers live under ``clustermgmt`` rather than
``storage``, and protection policies under ``datapolicies`` rather than ``dataprotection``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.compat import parse_version

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from ntnxvision_core.api.connection import PrismConnection

#: v4 is GA from pc.2024.3 (calendar scheme) / 7.0 (semantic scheme).
V4_GA_CALENDAR = (2024, 3)
V4_GA_SEMANTIC = (7, 0)

#: Negotiation outcomes, recorded per area in ``PrismConnection.negotiated``.
AVAILABLE = "v4"
BELOW_GA = "legacy: v4 not GA on this PC"
ABSENT = "legacy: namespace absent"
UNAVAILABLE = "legacy: service unavailable"


@dataclass(frozen=True)
class V4Namespace:
    """A v4 feature area and the cheap list endpoint used to test for it."""

    area: str
    probe_path: str
    sdk_package: str | None = None


#: Feature area → v4 namespace. Probe paths verified against a live pc.7.5.1.1.
NAMESPACES: dict[str, V4Namespace] = {
    "clusters": V4Namespace(
        "clusters", "clustermgmt/v4.0/config/clusters", "ntnx-clustermgmt-py-client"
    ),
    "hosts": V4Namespace(
        "hosts", "clustermgmt/v4.0/config/hosts", "ntnx-clustermgmt-py-client"
    ),
    # Storage containers are served by clustermgmt, not the `storage` namespace the
    # ntnx-storage-py-client package name implies.
    "storage_containers": V4Namespace(
        "storage_containers",
        "clustermgmt/v4.0/config/storage-containers",
        "ntnx-clustermgmt-py-client",
    ),
    "vms": V4Namespace("vms", "vmm/v4.0/ahv/config/vms", "ntnx-vmm-py-client"),
    "subnets": V4Namespace(
        "subnets", "networking/v4.0/config/subnets", "ntnx-networking-py-client"
    ),
    "volumes": V4Namespace(
        "volumes", "volumes/v4.0/config/volume-groups", "ntnx-volumes-py-client"
    ),
    # Protection policies are served by `datapolicies`, not `dataprotection`.
    "dataprotection": V4Namespace(
        "dataprotection",
        "datapolicies/v4.0/config/protection-policies",
        "ntnx-dataprotection-py-client",
    ),
    "microseg": V4Namespace(
        "microseg", "microseg/v4.0/config/policies", "ntnx-microseg-py-client"
    ),
    "files": V4Namespace(
        "files", "files/v4.0/config/file-servers", "ntnx-files-py-client"
    ),
    "objects": V4Namespace(
        "objects", "objects/v4.0/config/object-stores", "ntnx-objects-py-client"
    ),
    "iam": V4Namespace("iam", "iam/v4.0/authn/users", "ntnx-iam-py-client"),
    "lifecycle": V4Namespace(
        "lifecycle", "lifecycle/v4.0/resources/entities", "ntnx-lifecycle-py-client"
    ),
    "licensing": V4Namespace(
        "licensing", "licensing/v4.0/config/licenses", "ntnx-licensing-py-client"
    ),
    "monitoring": V4Namespace(
        "monitoring",
        "monitoring/v4.0/serviceability/alerts",
        "ntnx-monitoring-py-client",
    ),
}


def v4_is_ga(pc_version: str | None) -> bool:
    """Is v4 generally available on this Prism Central build?

    Handles both version schemes Nutanix ships in the supported window: calendar builds
    (``pc.2024.3``) and semantic ones (``7.5.1.1``).
    """
    parsed, scheme = parse_version(pc_version)
    if parsed is None:
        return False
    if scheme == "calendar":
        return parsed >= V4_GA_CALENDAR
    return parsed >= V4_GA_SEMANTIC


def negotiate(conn: PrismConnection, area: str) -> str:
    """Decide whether `area` can be served over v4, caching the verdict.

    Returns one of the module-level outcome constants. Anything other than
    :data:`AVAILABLE` means the caller should stay on its legacy path.
    """
    cached = conn.negotiated.get(area)
    if cached is not None:
        return cached

    namespace = NAMESPACES.get(area)
    if namespace is None:
        raise KeyError(f"Unknown v4 feature area: {area!r}")

    verdict = _probe(conn, namespace)
    conn.negotiated[area] = verdict
    return verdict


def _probe(conn: PrismConnection, namespace: V4Namespace) -> str:
    """Test one namespace with the cheapest possible request."""
    # The version gate is free, so apply it before spending a round-trip.
    if not v4_is_ga(conn.pc_version):
        return BELOW_GA
    try:
        conn.v4_get(namespace.probe_path, params={"$limit": 1})
    except NutanixApiError as exc:
        # 404 means the namespace genuinely is not routed on this PC. Any other error
        # (503 for a deployed-but-down service, gateway hiccups) is not evidence that
        # v4 is missing, so it is reported separately and the caller falls back.
        return ABSENT if exc.status_code == 404 else UNAVAILABLE
    except Exception:  # noqa: BLE001 — a probe must never abort a collection run
        return UNAVAILABLE
    return AVAILABLE
