"""Licensing collector.

Best-effort license/edition detection. v4 exposes a dedicated `licensing` namespace;
older PC surfaces licensing via internal endpoints that vary by version. This collector
degrades gracefully: if it cannot read licensing, it records a skip note rather than
failing the run.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import LicenseInfo


class LicenseCollector(Collector):
    name = "licenses"
    label = "Licensing"

    def collect(self, env: Any) -> None:
        if not self.conn.v4_ready("licensing"):
            env.collection_notes[self.name] = (
                "skipped: the v4 licensing namespace is not served by this Prism Central "
                f"({self.conn.negotiated.get('licensing', 'unavailable')})"
            )
            return

        try:
            data = self.conn.v4_list("licensing/v4.0/config/licenses")
        except NutanixApiError as exc:
            env.collection_notes[self.name] = f"failed: {exc}"
            return

        if not data:
            # An empty list is a real answer, not a missing API: a PC with no licences
            # applied returns 200 + []. Reporting that as "API not available" sent the
            # lab run chasing a version problem that did not exist.
            env.collection_notes[self.name] = "ok: no licenses reported by this PC"
            return

        for lic in data:
            expiry = lic.get("expiryDate") or lic.get("expiry")
            env.licenses.append(
                LicenseInfo(
                    product=lic.get("category") or lic.get("name"),
                    edition=lic.get("type") or lic.get("edition"),
                    category=lic.get("category"),
                    expiry=expiry,
                    quantity=lic.get("quantity"),
                    compliance=_compliance(lic, expiry),
                )
            )
        env.collection_notes[self.name] = "ok"


def _compliance(lic: dict[str, Any], expiry: Any) -> str | None:
    """Compliance from the API when present, else derived from the expiry date.

    The licensing config API frequently omits complianceStatus, which left the
    Compliance column empty even for visibly expired licenses.
    """
    for key in ("complianceStatus", "compliance_status", "compliance"):
        v = lic.get(key)
        if isinstance(v, str) and v:
            return v
    if isinstance(expiry, str) and expiry:
        from datetime import datetime, timezone

        raw = expiry.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            try:
                dt = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return "EXPIRED" if dt < datetime.now(timezone.utc) else "COMPLIANT"
    return None
