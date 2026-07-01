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
        try:
            data = self.conn.v4_list("licensing/v4.0/config/licenses")
        except NutanixApiError:
            data = []

        if not data:
            env.collection_notes[self.name] = (
                "skipped: licensing API not available on this PC version "
                "(will be covered by the v4 licensing SDK in a later phase)"
            )
            return

        for lic in data:
            env.licenses.append(
                LicenseInfo(
                    product=lic.get("category") or lic.get("name"),
                    edition=lic.get("type") or lic.get("edition"),
                    category=lic.get("category"),
                    expiry=lic.get("expiryDate") or lic.get("expiry"),
                    quantity=lic.get("quantity"),
                    compliance=lic.get("complianceStatus"),
                )
            )
        env.collection_notes[self.name] = "ok"
