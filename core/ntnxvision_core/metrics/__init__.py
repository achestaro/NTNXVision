"""Insights > Metrics: live health, utilization, runway and trends of a saved target.

`get_metrics(params)` is the sidecar's ``metrics`` operation (and the CLI's ``metrics``
command): connect to the Prism Central with the target's credentials (filled in by the
main process from the keychain, never stored here), collect every card (`live.collect`),
compute runway (`runway`), and cache the result for `CACHE_TTL` seconds per endpoint and
account so moving between pages does not re-query Prism. ``refresh: true`` skips the cache.
``part`` is ``now`` (every card but trends) or ``trends`` (90 days of daily points and the
runway); the page asks for both at once (see `live.PARTS`).

Requests are cancellable: each carries a ``request_id`` and `cancel(request_id)` (the
sidecar's ``cancel_metrics``, answered inline) stops it before its next HTTP request; the
call then fails with `RunCancelled`.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import Any

from ntnxvision_core.api import PrismConnection, PrismCredentials
from ntnxvision_core.api.errors import AuthenticationError, NutanixApiError
from ntnxvision_core.api.transport import require_pin
from ntnxvision_core.cancel import CancelToken
from ntnxvision_core.metrics.live import PARTS, collect

#: Seconds a result is served from the cache.
CACHE_TTL = 120
#: Seconds per HTTP request (one slow call must not hang the page).
REQUEST_TIMEOUT = 25

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()
_active: dict[str, CancelToken] = {}
_active_lock = threading.Lock()


def _key(host: str, port: int, username: str, password: str, pin: str | None) -> str:
    # The password is part of the key (hashed, in memory only) so a changed password is
    # never answered from the cache of the old one.
    raw = "\0".join([host.lower(), str(port), username, password, pin or ""])
    return hashlib.sha256(raw.encode()).hexdigest()


def cancel(request_id: Any) -> bool:
    """Cancel a running metrics request; False when no such request is running."""
    with _active_lock:
        token = _active.get(str(request_id or ""))
    if token is None:
        return False
    token.cancel()
    return True


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def get_metrics(params: dict[str, Any], *, allow_unpinned: bool = False) -> dict[str, Any]:
    """Metrics for one target; `allow_unpinned` is the command line's --insecure without --pin."""
    host = str(params.get("host") or "").strip()
    port = int(params.get("port") or 9440)
    username = str(params.get("username") or "")
    password = str(params.get("password") or "")
    insecure = bool(params.get("insecure"))
    if not host:
        raise NutanixApiError("Choose a saved target.")
    if str(params.get("mode") or "prism_central") != "prism_central":
        raise NutanixApiError("Metrics need a Prism Central target.")
    if not password:
        raise AuthenticationError("No password is saved for this target. Edit it on the "
                                  "Targets page.")
    unpinned = allow_unpinned and insecure and not params.get("pinned_sha256")
    pin = None if unpinned else require_pin(host, insecure, params.get("pinned_sha256"))
    part = str(params.get("part") or "now")
    if part not in PARTS:
        raise NutanixApiError(f"Unknown metrics part: {part}")
    key = _key(host, port, username, password, pin) + ":" + part
    now = time.time()
    if not params.get("refresh"):
        with _cache_lock:
            hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return {**hit[1], "cached": True, "age_seconds": int(now - hit[0]),
                    "ttl_seconds": CACHE_TTL}

    request_id = str(params.get("request_id") or "")
    token = CancelToken()
    if request_id:
        with _active_lock:
            _active[request_id] = token
    try:
        creds = PrismCredentials(host=host, username=username, password=password, port=port,
                                 verify_ssl=not insecure, pinned_sha256=pin,
                                 allow_unpinned=unpinned)
        conn = PrismConnection(creds, timeout=REQUEST_TIMEOUT, cancel=token)
        with conn:
            result = collect(conn, part)
    finally:
        if request_id:
            with _active_lock:
                _active.pop(request_id, None)
    with _cache_lock:
        _cache[key] = (time.time(), result)
    return {**result, "cached": False, "age_seconds": 0, "ttl_seconds": CACHE_TTL}
