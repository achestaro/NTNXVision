"""`list_clusters`: the clusters a collection can be scoped to.

Prism Central: every Prism Element cluster registered to it (v4 ``clustermgmt`` when served,
else v3 ``clusters/list``), without the PC's own pseudo-cluster. Prism Element: the one
cluster the connection reaches. Read-only (GET and v3 ``list`` calls); credentials are used
for this request only and never stored.

Unlike `connectivity.test_connection`, failures are errors (the UI shows them with a
retry), not classified outcomes: the picker only asks once the connection details are
known to work.
"""

from __future__ import annotations

from typing import Any

from ntnxvision_core.api import PrismConnection, PrismCredentials
from ntnxvision_core.api.element_client import ElementConnection, ElementCredentials
from ntnxvision_core.api.errors import AuthenticationError, NutanixApiError
from ntnxvision_core.api.transport import require_pin
from ntnxvision_core.scope import element_cluster, registered_clusters
from ntnxvision_core.versions import pc_version

#: Seconds per request: the picker should fail fast rather than hang the form.
LIST_TIMEOUT = 20


def list_clusters(params: dict[str, Any]) -> dict[str, Any]:
    mode = str(params.get("mode") or "prism_central")
    host = str(params.get("host") or "").strip()
    port = int(params.get("port") or 9440)
    username = str(params.get("username") or "")
    password = str(params.get("password") or "")
    insecure = bool(params.get("insecure"))
    if not host:
        raise NutanixApiError("Enter a host name or IP.")
    if not password:
        raise AuthenticationError("No password. Type one, or save it to the keychain first.")

    pin = require_pin(host, insecure, params.get("pinned_sha256"))

    if mode == "prism_element":
        creds = ElementCredentials(host=host, username=username, password=password, port=port,
                                   verify_ssl=not insecure, pinned_sha256=pin)
        with ElementConnection(creds, timeout=LIST_TIMEOUT) as conn:
            cluster = element_cluster(conn)
        return {"mode": mode, "host": host, "api": "v2", "version": cluster.aos_version,
                "clusters": [cluster.as_dict()]}

    pc = PrismCredentials(host=host, username=username, password=password, port=port,
                          verify_ssl=not insecure, pinned_sha256=pin)
    with PrismConnection(pc, timeout=LIST_TIMEOUT) as conn:
        clusters, api = registered_clusters(conn)
        version = pc_version(conn.pc_version)
    return {"mode": mode, "host": host, "api": api, "version": version,
            "clusters": [c.as_dict() for c in clusters]}
