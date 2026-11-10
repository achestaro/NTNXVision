"""Licensing collector.

Best-effort license/edition detection. v4 exposes a dedicated `licensing` namespace;
older PC surfaces licensing via internal endpoints that vary by version. This collector
degrades gracefully: if it cannot read licensing, it records a skip note rather than
failing the run.
"""

from __future__ import annotations

from datetime import UTC
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
            # An empty list is a real answer, not a missing API: `licenses` holds only the
            # license keys applied from the portal, so a PC on trials and free licences
            # answers 200 + []. Prism's own Licensing page reads what each cluster runs
            # under from PrismGateway's v1 `license` (Prism Ultimate Trial, with its
            # expiry); the v4 entitlements say only "Prism Starter" there, so they are the
            # fallback for a PC whose v1 answer cannot be read.
            found = self._prism_license()
            if found is None:
                found = self._entitlements()
            if found is None:
                env.collection_notes[self.name] = "ok: no licenses reported by this PC"
            elif not found:
                env.collection_notes[self.name] = (
                    "ok: no license keys or entitlements reported by this PC")
            else:
                env.licenses.extend(found)
                env.collection_notes[self.name] = "ok"
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

    def _prism_license(self) -> list[LicenseInfo] | None:
        """Rows from PrismGateway v1 `license?showAllClusters=true`; None when unreadable.

        Shape recorded from pc.7.5.1.6: `licenseInfoDTO.pcDetailsDTO.clusters[]` carry
        uuid, name and `licenseDetails[]` of {type "PRISM", category "ULTIMATE_TRIAL",
        name "Prism Ultimate Trial", expiryDate "2054-03-27", capacity}; licences of the
        Prism Central itself sit in `pcDetailsDTO.licenseDetails`.
        """
        try:
            body = self.conn.get_json(f"{self.conn.creds.base}{PRISM_LICENSE}")
            pc = body["licenseInfoDTO"]["pcDetailsDTO"]
            holders = [(c.get("uuid"), c.get("name"), c.get("licenseDetails") or [])
                       for c in pc.get("clusters") or [] if isinstance(c, dict)]
        except (NutanixApiError, AttributeError, KeyError, TypeError, ValueError):
            return None
        holders.append((None, "Prism Central", pc.get("licenseDetails") or []))
        out: list[LicenseInfo] = []
        for uuid, name, details in holders:
            for d in details:
                if not isinstance(d, dict):
                    continue
                expiry = _known(d.get("expiryDate"))
                capacity = d.get("capacity")
                out.append(LicenseInfo(
                    cluster_uuid=uuid, cluster_name=name,
                    product=_title(_known(d.get("type"))),
                    edition=_known(d.get("name")),
                    category=_words(_known(d.get("category"))),
                    expiry=expiry,
                    quantity=_int(capacity) if not isinstance(capacity, dict) else None,
                    compliance=_compliance(d, expiry),
                ))
        return out

    def _entitlements(self) -> list[LicenseInfo] | None:
        """One row per entitlement detail; None when the call fails.

        Shape recorded from pc.7.5.1.6: one entitlement per registered cluster (`extId`
        is the cluster's uuid, `name` its name) plus one for the Prism Central itself
        (`isMulticluster`), each with `details` of {name "Prism Starter", type "PRISM",
        category "STARTER", meter, quantity}. Enum values the server cannot name come back
        as "$UNKNOWN"; a Prism Central entry with no details adds no row.
        """
        try:
            rows = self.conn.v4_list(ENTITLEMENTS)
        except NutanixApiError:
            return None
        out: list[LicenseInfo] = []
        for ent in rows:
            pc = bool(ent.get("isMulticluster"))
            for d in ent.get("details") or []:
                expiry = _known(d.get("earliestExpiryDate") or d.get("expiryDate"))
                out.append(LicenseInfo(
                    cluster_uuid=None if pc else ent.get("extId"),
                    cluster_name=(f"Prism Central {ent['name']}" if pc and ent.get("name")
                                  else ent.get("name")),
                    product=_title(_known(d.get("type"))),
                    edition=_known(d.get("name")),
                    category=_words(_known(d.get("category"))),
                    expiry=expiry,
                    # Prism shows a quantity of 0 as "-": nothing is counted against it.
                    quantity=_int(d.get("quantity")) or None,
                    unit=_known(d.get("meter")),
                    compliance=_compliance(d, expiry),
                ))
        return out


ENTITLEMENTS = "licensing/v4.0/config/entitlements"
PRISM_LICENSE = "/PrismGateway/services/rest/v1/license?showAllClusters=true"


def _known(v: Any) -> Any:
    """None for a v4 enum the server could not name ("$UNKNOWN") or an empty value."""
    return None if v in (None, "", "$UNKNOWN", "$REDACTED") else v


def _title(v: Any) -> str | None:
    """`OBJECTS` → `Objects`; anything that is not one plain word is kept as sent."""
    if not isinstance(v, str) or not v:
        return None
    return v.capitalize() if v.isalpha() else v


def _words(v: Any) -> str | None:
    """`ULTIMATE_TRIAL` → `Ultimate trial`."""
    return v.replace("_", " ").capitalize() if isinstance(v, str) and v else None


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


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
        from datetime import datetime

        raw = expiry.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            try:
                dt = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=UTC)
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return "EXPIRED" if dt < datetime.now(UTC) else "COMPLIANT"
    return None
