"""HTTP 429 handling in the shared Prism Central client, and the tolerant pager's bounds.

Every v4 namespace is rate limited per Prism Central. Before the shared retry only the
alerts collector backed off: Flow failed outright on a 429 from the microseg service
groups list, and the VM pager counted rate-limited reads as skipped VMs.
"""

from __future__ import annotations

from typing import Any

import pytest

from ntnxvision_core.api import PrismConnection, PrismCredentials
from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.engine import _noting_rate_limits
from ntnxvision_core.models import Environment


class _Resp:
    def __init__(self, status: int, payload: Any = None,
                 headers: dict[str, str] | None = None) -> None:
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.url = "https://pc.example.lab:9440/api/x"
        self.text = ""

    def json(self) -> Any:
        return self._payload


class _Session:
    """Answers 429 `throttle` times, then 200."""

    def __init__(self, throttle: int, headers: dict[str, str] | None = None) -> None:
        self.throttle = throttle
        self.headers_429 = headers or {}
        self.calls = 0

    def request(self, method: str, url: str, **_kw: Any) -> _Resp:
        self.calls += 1
        if self.calls <= self.throttle:
            return _Resp(429, {"message": "Too many requests"}, self.headers_429)
        return _Resp(200, {"data": [{"extId": "a"}], "metadata": {"totalAvailableResults": 1}})


def _conn(session: _Session) -> PrismConnection:
    conn = PrismConnection(PrismCredentials("pc.example.lab", "admin", "x"),
                           session=session)  # type: ignore[arg-type]
    conn.rate_limit_backoff = 0
    return conn


def test_a_429_is_retried_and_counted() -> None:
    session = _Session(throttle=3)
    conn = _conn(session)
    payload = conn.v4_get("microseg/v4.0/config/service-groups", params={"$page": 5})
    assert payload["data"] == [{"extId": "a"}]
    assert session.calls == 4
    assert conn.throttled == 3


def test_retries_are_bounded_then_the_429_surfaces() -> None:
    conn = _conn(_Session(throttle=100))
    with pytest.raises(NutanixApiError) as info:
        conn.v4_get("microseg/v4.0/config/service-groups")
    assert info.value.status_code == 429


def test_the_wait_honours_the_servers_hint_and_is_capped() -> None:
    conn = PrismConnection(PrismCredentials("pc.example.lab", "admin", "x"))
    assert conn._rate_limit_wait(_Resp(429, headers={"Retry-After": "2"}), 0) == 2
    assert conn._rate_limit_wait(
        _Resp(429, headers={"X-Api-Ratelimit-Refresh-Period-Seconds": "1"}), 2) == 3
    assert conn._rate_limit_wait(_Resp(429, headers={"Retry-After": "600"}), 0) == 10
    assert conn._rate_limit_wait(_Resp(429), 1) == 2  # default backoff grows


def test_the_engine_notes_rate_limited_areas() -> None:
    class _C:
        throttled = 0

    conn, env = _C(), Environment()

    def work() -> None:
        conn.throttled += 2
        env.collection_notes["flow_security"] = "ok"

    _noting_rate_limits(conn, env, "flow_security", work)()
    assert env.collection_notes["flow_security"] == (
        "ok: 2 request(s) retried after API rate limiting")


def test_a_note_that_already_reports_rate_limiting_is_kept() -> None:
    class _C:
        throttled = 0

    conn, env = _C(), Environment()

    def work() -> None:
        conn.throttled += 1
        env.collection_notes["alerts"] = "ok: 1 page read(s) retried after v4 API rate limiting"

    _noting_rate_limits(conn, env, "alerts", work)()
    assert env.collection_notes["alerts"].count("rate limiting") == 1


# ─── the tolerant pager's bounds ───────────────────────────────────────


class _Pager:
    """A v4 collection of `total` records; `bad` offsets 500, `throttled` offsets 429."""

    from ntnxvision_core.api.connection import PrismConnection as _P

    _salvage_page = _P._salvage_page
    _v4_total = _P._v4_total
    v4_list_tolerant = _P.v4_list_tolerant
    del _P

    def __init__(self, total: int, bad: set[int], throttled: set[int] | None = None) -> None:
        self.total, self.bad, self.throttled = total, bad, throttled or set()
        self.offsets: list[int] = []

    def v4_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        page, limit = params.get("$page", 0), params.get("$limit", 100)
        start, stop = page * limit, min(page * limit + limit, self.total)
        if limit == 1:
            self.offsets.append(start)
        window = range(start, stop)
        if limit == 1 and start in self.throttled:
            raise NutanixApiError("HTTP 429", status_code=429)
        if any(i in self.bad for i in window):
            raise NutanixApiError("HTTP 500", status_code=500)
        return {"data": [{"extId": f"vm-{i}"} for i in window],
                "metadata": {"totalAvailableResults": self.total}}


def test_a_failing_first_page_never_reads_past_the_collection() -> None:
    """The lab case: 57 VMs, page size 100, two broken records on the first page."""
    pager = _Pager(57, {3, 8})
    items, skipped = pager.v4_list_tolerant("p", page_size=100)
    assert skipped == 2
    assert len(items) == 55
    assert max(pager.offsets) == 56  # nothing beyond the last record


def test_a_rate_limited_salvage_read_is_not_a_skipped_vm() -> None:
    """A 429 that outlives the client's retries is an error, never a skipped record."""
    with pytest.raises(NutanixApiError) as info:
        _Pager(57, {3}, throttled={10}).v4_list_tolerant("p", page_size=100)
    assert info.value.status_code == 429
