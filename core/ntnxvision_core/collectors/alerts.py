"""Health alerts collector (v4 `monitoring` when served, else the v3 `alerts` kind).

Severity is normalized to CRITICAL / WARNING / INFO / OTHER so exporters don't need to
know either API's raw enum spelling — v3 says `warning`, v4 says `WARNING`.

**The v4 payload is a superset.** Every field the model carries is served by
`monitoring/v4.0/serviceability/alerts`, uuid-for-uuid with v3 on the lab PC (2425 in
both, zero on either side alone), and it fills two the v3 shape leaves empty:
`isAutoResolved` is an explicit False on an unresolved alert where v3 simply omits it,
and an affected entity with no name comes back as null rather than an empty string.

**Two v4 fields are deliberately ignored.**

* `clusterName` is a snapshot taken when the alert fired, not a live lookup: on the lab
  PC one cluster uuid appears under two different names across its own alerts, and one
  alert names the wrong cluster entirely. The engine resolves the name from
  `cluster_uuid` against the collected clusters, which is correct for every row.
* `clusterUUID` is the cluster that *reported* the alert; six lab alerts raised by a PE
  about a peer disagree with `originatingClusterUUID`, which is the one v3 reported and
  the one the reports group on.

**This endpoint is rate-limited where the others are not.** It advertises
`X-Api-Ratelimit-Limit: 2` per one-second window and answers HTTP 429 above it, which
`v4_list` (no pacing, no retry) trips on the fifth page. At the v4 maximum of 100 records
a page, 2425 alerts is 25 requests, so the collector paces its own paging. Even paced it
is the faster path here: 14s against 26s for the eleven fat v3 pages.
"""

from __future__ import annotations

import time
from typing import Any

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import Alert, AlertSummary, Environment

_SEVERITY_MAP = {
    "CRITICAL": "CRITICAL",
    "KCRITICAL": "CRITICAL",
    "WARNING": "WARNING",
    "KWARNING": "WARNING",
    "INFO": "INFO",
    "KINFO": "INFO",
}

_V4_PATH = "monitoring/v4.0/serviceability/alerts"

#: The endpoint's hard ceiling — `$limit=101` is rejected with HTTP 400.
_V4_PAGE_SIZE = 100

#: Seconds to leave between page reads. The advertised budget is two requests per
#: second; pacing just under that keeps a 25-page walk inside it.
_V4_MIN_INTERVAL = 0.55

#: Extra attempts per page when a 429 lands anyway — the budget is per Prism Central,
#: so anything else talking to it can push a paced walk over the edge.
_V4_RATE_LIMIT_RETRIES = 4
_V4_BACKOFF = 1.0


class AlertsCollector(Collector):
    name = "alerts"
    label = "Health Alerts"

    def collect(self, env: Environment) -> None:
        if self.conn.v4_ready("monitoring"):
            note = self._collect_v4(env)
        else:
            self._collect_v3(env)
            note = "ok"
        env.alerts_summary = _summarize(env.alerts)
        env.collection_notes[self.name] = note

    # ─── v4 ────────────────────────────────────────────────────────

    def _collect_v4(self, env: Environment) -> str:
        try:
            entities, throttled = self._v4_pages()
        except NutanixApiError as exc:
            # Typically the retries exhausted against a Prism Central busy enough to
            # keep answering 429. Re-reading over v3 costs a few seconds; an empty
            # Health & Alerts chapter costs the whole section.
            self._collect_v3(env)
            reason = "rate-limited" if exc.status_code == 429 else f"HTTP {exc.status_code}"
            return f"ok: v4 alerts endpoint {reason}, collected over v3"

        for entity in entities:
            env.alerts.append(_from_v4(entity))
        if throttled:
            return f"ok: {throttled} page read(s) retried after v4 API rate limiting"
        return "ok"

    def _v4_pages(self) -> tuple[list[dict[str, Any]], int]:
        """Walk every page of the alerts endpoint, paced under its rate limit.

        Returns `(entities, retries)`; the retry count is surfaced in the collection
        note rather than hidden, because a run that had to back off says something
        about how loaded the PC was.
        """
        from ntnxvision_core.scope import server_filter

        entities: list[dict[str, Any]] = []
        throttled = 0
        page = 0
        # Far enough in the past that the first request is never delayed.
        last = 0.0
        # A scoped run asks for the selected clusters' alerts only. The filter is probed
        # once, and that probe spends one request of the rate budget.
        self._filter = server_filter(self.conn, _V4_PATH, "originatingClusterUUID")
        if self._filter:
            last = time.monotonic()
        while True:
            delay = _V4_MIN_INTERVAL - (time.monotonic() - last)
            if delay > 0:
                time.sleep(delay)
            last = time.monotonic()

            payload, retries = self._v4_page(page)
            throttled += retries
            data = payload.get("data") or []
            entities.extend(data)
            total = (payload.get("metadata") or {}).get("totalAvailableResults")
            page += 1
            if not data or (total is not None and len(entities) >= total):
                break
        return entities, throttled

    def _v4_page(self, page: int) -> tuple[dict[str, Any], int]:
        """One page, retried with a growing backoff while the answer is 429."""
        params: dict[str, Any] = {"$page": page, "$limit": _V4_PAGE_SIZE}
        if getattr(self, "_filter", None):
            params["$filter"] = self._filter
        for attempt in range(_V4_RATE_LIMIT_RETRIES):
            try:
                return self.conn.v4_get(_V4_PATH, params=params), attempt
            except NutanixApiError as exc:
                if exc.status_code != 429:
                    raise
                time.sleep(_V4_BACKOFF * (attempt + 1))
        # Out of patience: let this attempt's error reach the caller.
        return self.conn.v4_get(_V4_PATH, params=params), _V4_RATE_LIMIT_RETRIES

    # ─── v3 ────────────────────────────────────────────────────────

    def _collect_v3(self, env: Environment) -> None:
        for entity in self.conn.v3_list("alerts"):
            env.alerts.append(_to_alert(entity))


# ─── helpers ───────────────────────────────────────────────────────────


def _severity(raw: str | None) -> str:
    if not raw:
        return "OTHER"
    return _SEVERITY_MAP.get(raw.upper(), "OTHER")


def _text(value: Any) -> str | None:
    """A non-empty string, or None.

    v3 reports an unnamed affected entity as `""` and v4 omits the key; the reports
    should not show one as blank and the other as missing.
    """
    return value if isinstance(value, str) and value else None


# ─── v4 mapping ────────────────────────────────────────────────────────


def _v4_affected(entity: dict[str, Any]) -> dict[str, Any]:
    """The entity an alert is about.

    `affectedEntities[0]` and `sourceEntity` agree on every lab alert, but only the
    first is what v3's `affected_entity_list` maps to, so it leads.
    """
    affected = entity.get("affectedEntities") or []
    if isinstance(affected, list) and affected and isinstance(affected[0], dict):
        return affected[0]
    source = entity.get("sourceEntity")
    return source if isinstance(source, dict) else {}


def _v4_timestamp(value: Any) -> str | None:
    """v4's RFC-3339 instant, trimmed to the second resolution v3 reports.

    The same alert is `2026-09-05T12:17:26.118249Z` over v4 and `2026-09-05T12:17:26Z`
    over v3. The model passes the string straight through to the Excel and Word tables,
    so keeping the microseconds would change how every Created cell prints depending on
    which path happened to run.
    """
    if not isinstance(value, str) or not value:
        return None
    head, sep, tail = value.partition(".")
    if not sep:
        return value
    # Preserve whatever followed the fraction — "Z", or a "+02:00" style offset.
    return f"{head}{tail.lstrip('0123456789') or 'Z'}"


def _from_v4(entity: dict[str, Any]) -> Alert:
    affected = _v4_affected(entity)
    return Alert(
        uuid=entity.get("extId"),
        title=_text(entity.get("title")) or _text(entity.get("message")),
        message=_text(entity.get("message")),
        severity=_severity(entity.get("severity")),
        resolved=entity.get("isResolved"),
        acknowledged=entity.get("isAcknowledged"),
        auto_resolved=entity.get("isAutoResolved"),
        entity_type=_text(affected.get("type")),
        entity_name=_text(affected.get("name")),
        # See the module docstring: `clusterUUID` is the reporting cluster, this is the
        # one that raised the alert and the one v3 reported.
        cluster_uuid=entity.get("originatingClusterUUID") or entity.get("clusterUUID"),
        created_at=_v4_timestamp(entity.get("creationTime")),
        classifications=list(entity.get("classifications") or []),
        api_source="v4",
    )


# ─── v3 mapping ────────────────────────────────────────────────────────


def _v3_fields(entity: dict[str, Any]) -> dict[str, Any]:
    """The dict holding an alert's fields, whichever v3 shape answered.

    Prism Central's `alerts/list` nests everything under `status.resources` and leaves
    only the uuid outside it; other v3-era sources return the same keys flat. Reading
    only the flat shape is why the v3 path used to fill in nothing but the uuid.
    """
    resources = (entity.get("status") or {}).get("resources")
    return resources if isinstance(resources, dict) else entity


def _first_affected_entity(fields: dict[str, Any]) -> dict[str, Any]:
    affected = (
        fields.get("affected_entity_list")
        or fields.get("affected_entities")
        or fields.get("context_types")
        or []
    )
    return affected[0] if isinstance(affected, list) and affected else {}


def _flag(fields: dict[str, Any], status_key: str, flat_key: str, sub: str) -> bool | None:
    """A boolean that PC reports as a `{is_true: ...}` status block and others flat."""
    status = fields.get(status_key)
    if isinstance(status, dict):
        value = status.get(sub)
        return value if isinstance(value, bool) else None
    value = fields.get(flat_key)
    return value if isinstance(value, bool) else None


def _to_alert(entity: dict[str, Any]) -> Alert:
    fields = _v3_fields(entity)
    affected = _first_affected_entity(fields)
    return Alert(
        uuid=entity.get("id") or entity.get("uuid") or entity.get("metadata", {}).get("uuid"),
        title=_text(fields.get("title")) or _text(fields.get("message")),
        # `default_message` is the templated text PC serves; `message` is what the
        # flat shape calls the same thing.
        message=_text(fields.get("message"))
        or _text(fields.get("default_message"))
        or _text(fields.get("detailed_message")),
        severity=_severity(fields.get("severity")),
        resolved=_flag(fields, "resolution_status", "resolved", "is_true"),
        acknowledged=_flag(fields, "acknowledged_status", "acknowledged", "is_true"),
        auto_resolved=_flag(
            fields, "resolution_status", "auto_resolved", "is_auto_resolved"
        ),
        entity_type=_text(affected.get("entity_type")) or _text(affected.get("type")),
        entity_name=_text(affected.get("entity_name")) or _text(affected.get("name")),
        cluster_uuid=fields.get("originating_cluster_uuid") or fields.get("cluster_uuid"),
        created_at=fields.get("creation_time") or fields.get("created_time"),
        classifications=list(
            fields.get("classification_list") or fields.get("classifications") or []
        ),
        api_source="v3",
    )


def _summarize(alerts: list[Alert]) -> AlertSummary:
    summary = AlertSummary(total_count=len(alerts))
    for alert in alerts:
        if alert.severity == "CRITICAL":
            summary.critical_count += 1
        elif alert.severity == "WARNING":
            summary.warning_count += 1
        elif alert.severity == "INFO":
            summary.info_count += 1
        else:
            summary.other_count += 1
        if not alert.resolved:
            summary.unresolved_count += 1
    return summary


#: Public name: the cluster scope recomputes the roll-up after pruning alerts.
summarize = _summarize
