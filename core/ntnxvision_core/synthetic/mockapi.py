"""The mock Nutanix API, independent of HTTP.

`MockApi.handle()` takes (host, method, path, query, body, credentials) and returns
(status, json, headers). `server.MockNutanixServer` puts it behind a real HTTPS socket;
tests can also call it directly.

What lives at a hostname is a `Target`: a Prism Central, a Prism Element cluster or an
NDB server, each backed by a lazily built synthetic estate. Behaviour the collectors have
to cope with on real systems is reproduced deliberately:

* **Auth** — HTTP basic; an empty password or the literal ``wrong`` gets 401.
* **Paging** — v3 ``offset``/``length`` (body), v4 ``$page``/``$limit`` (``$limit`` > 100
  is a 400, as on a real PC), v2.0 ``offset``/``count``.
* **Version gating** — below the v4 GA line (pc.2024.3 / 7.0) every ``v4.0`` path 404s.
* **Namespaces** — absent namespaces 404, unavailable ones 503 (the Objects case).
* **Partial failures** — ESXi VM records flagged ``esxi_bad`` make any page containing
  them answer 500 (PLAT-10006, as on the lab PC), so the tolerant pager has to salvage
  the rest record by record; their single GET 500s too. With ``Features.groups_mh_vm``
  the groups API serves ``mh_vm`` rows (``is_cvm`` and ``cluster=in=`` filters honoured)
  so the VM collector can recover them; without it ``mh_vm`` is a 400.
* **Rate limiting** — the v4 alerts endpoint enforces 2 requests/second with 429s, and
  can be told to 429 once on its second page so the retry note appears.
* **Products not deployed** — Files / Objects / Flow VN / X-Play / NCM 404 on v3.
* **Cluster filters** — the v4 VM, storage container and alert lists honour an OData
  ``$filter`` of ``<field> eq '<cluster uuid>'`` terms joined by ``or`` on their cluster
  reference (`FILTERABLE`); any other ``$filter`` is a 400, as a real PC answers for a
  field it does not filter on.
"""

from __future__ import annotations

import base64
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ntnxvision_core.synthetic import render as r
from ntnxvision_core.synthetic.estate import NdbEstate, PcEstate, PeEstate, uid

Response = tuple[int, Any, dict[str, str]]


@dataclass
class Target:
    """Something answering at a demo hostname."""

    host: str
    kind: str  # "pc" | "pe" | "ndb"
    build: Callable[[int], Any]
    epoch: int = 0
    #: "ca" = served with the demo-CA-signed cert; "self" = a self-signed cert.
    tls: str = "ca"
    #: False = DNS failure (the router never connects).
    reachable: bool = True
    _cache: dict[int, Any] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def estate(self) -> Any:
        with self._lock:
            est = self._cache.get(self.epoch)
            if est is None:
                est = self.build(self.epoch)
                self._cache = {self.epoch: est}
            return est

    def advance(self) -> None:
        with self._lock:
            self.epoch += 1


class _Derived:
    """Per-estate lookups computed once (VM counts per host, partitions by hypervisor)."""

    def __init__(self, est: PcEstate | PeEstate) -> None:
        self.vms_per_host: dict[str, int] = {}
        for vm in est.vms:
            if vm["power"] == "ON":
                self.vms_per_host[vm["host_uuid"]] = self.vms_per_host.get(vm["host_uuid"], 0) + 1
        self.ahv = [v for v in est.vms if v["hypervisor"] == "AHV"]
        self.esxi = [v for v in est.vms if v["hypervisor"] == "ESXi"]
        # v3 only knows ESXi VMs that exist as Prism Central VM intents — a few of them,
        # under a different uuid than v4 reports (see collectors/vms.py).
        self.v3_vms: list[tuple[dict[str, Any], str | None]] = [(v, None) for v in self.ahv]
        self.v3_vms += [(v, uid(v["uuid"], "intent")) for v in self.esxi[:3]]


#: v4 list path → the one field its ``$filter`` accepts (each item's ``cluster_uuid``).
FILTERABLE: dict[str, str] = {
    "vmm/v4.0/ahv/config/vms": "cluster/extId",
    "vmm/v4.0/esxi/config/vms": "cluster/extId",
    "clustermgmt/v4.0/config/storage-containers": "clusterExtId",
    "monitoring/v4.0/serviceability/alerts": "originatingClusterUUID",
}

_FILTER_TERM = re.compile(r"^\s*([A-Za-z][\w/]*)\s+eq\s+'([^']*)'\s*$")


def parse_filter(expr: str) -> tuple[str, set[str]] | None:
    """``a eq 'x' or a eq 'y'`` → ("a", {"x", "y"}); None for anything else."""
    fields: set[str] = set()
    values: set[str] = set()
    for term in expr.split(" or "):
        m = _FILTER_TERM.match(term)
        if not m:
            return None
        fields.add(m.group(1))
        values.add(m.group(2))
    return (fields.pop(), values) if len(fields) == 1 else None


def _page(items: list[Any], offset: int, length: int) -> list[Any]:
    return items[offset: offset + length]


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


_V4_NAMESPACE_OF = [
    ("clustermgmt/v4.0/config/clusters", "clusters"),
    ("clustermgmt/v4.0/config/hosts", "hosts"),
    ("clustermgmt/v4.0/config/storage-containers", "storage_containers"),
    ("vmm/", "vms"),
    ("networking/", "subnets"),
    ("volumes/", "volumes"),
    ("datapolicies/", "dataprotection"),
    ("microseg/", "microseg"),
    ("files/", "files"),
    ("objects/", "objects"),
    ("iam/", "iam"),
    ("lifecycle/", "lifecycle"),
    ("licensing/", "licensing"),
    ("monitoring/", "monitoring"),
    ("opsmgmt/", "ncm_reports"),
    ("prism/", None),
]


class MockApi:
    def __init__(self, resolve: Callable[[str], Target | None], latency_ms: float = 0.0) -> None:
        self.resolve = resolve
        self.latency_ms = latency_ms
        self._lock = threading.Lock()
        self._alert_hits: dict[str, deque[float]] = {}
        self._throttled_once: set[tuple[str, int]] = set()
        #: (host, method, path) → request count, for tests that assert paging happened.
        self.hits: dict[tuple[str, str, str], int] = {}
        #: (host, path) → the last ``$filter`` served, for tests of server-side scoping.
        self.filters: dict[tuple[str, str], str] = {}

    def reset(self) -> None:
        """Forget rate-limit history, so the one-off 429 replays for a fresh session."""
        with self._lock:
            self._alert_hits.clear()
            self._throttled_once.clear()

    # ─── entry point ───────────────────────────────────────────────

    def handle(
        self,
        host: str,
        method: str,
        path: str,
        query: dict[str, str],
        body: Any,
        authorization: str | None,
    ) -> Response:
        target = self.resolve(host)
        if target is None:
            return 404, {"message": f"No synthetic system at {host}"}, {}
        with self._lock:
            key = (host, method, path)
            self.hits[key] = self.hits.get(key, 0) + 1
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000.0)
        if not _authorized(authorization):
            return 401, {"api_version": "3.1", "code": 401, "state": "ERROR",
                         "message_list": [{"message": "Authentication required.",
                                           "reason": "AUTHENTICATION_REQUIRED"}]}, \
                {"WWW-Authenticate": 'Basic realm="Intent Gateway Login Required"'}
        est = target.estate()
        try:
            if target.kind == "pc":
                return self._pc(target, est, method, path, query, body or {})
            if target.kind == "pe":
                return self._pe(est, method, path, query)
            if target.kind == "ndb":
                return self._ndb(est, method, path)
        except _HttpError as exc:
            return exc.status, exc.body, exc.headers
        return 404, {"message": "Not found"}, {}

    def derived(self, est: PcEstate | PeEstate) -> _Derived:
        with self._lock:
            d: _Derived | None = getattr(est, "_derived", None)
            if d is None:
                d = _Derived(est)
                est._derived = d  # type: ignore[union-attr]
            return d

    # ─── Prism Central ─────────────────────────────────────────────

    def _pc(self, target: Target, est: PcEstate, method: str, path: str,
            query: dict[str, str], body: dict[str, Any]) -> Response:
        if method == "GET" and path == "/PrismGateway/services/rest/v1/cluster":
            return 200, r.pc_gateway_v1(est), {}
        if method == "GET" and query.get("proxyClusterUuid") and \
                path.startswith("/PrismGateway/services/rest/v2.0/"):
            return self._pe_proxy(est, path, query)
        m = re.fullmatch(r"/api/nutanix/v3/(.+)/list", path)
        if method == "POST" and m:
            return self._v3_list(est, m.group(1), body)
        if method == "POST" and path == "/api/nutanix/v3/groups":
            return self._v3_groups(est, body)
        if method == "GET" and path.startswith("/api/") and "/v4.0/" in path:
            return self._v4(target, est, path[len("/api/"):], query)
        return _not_found_v3(path)

    def _pe_proxy(self, est: PcEstate, path: str, query: dict[str, str]) -> Response:
        """Prism Element v2.0 of one registered cluster, through Prism Central.

        Serves ``vms`` (disk / NIC config on request) and ``networks`` (Nutanix subnets
        on AHV, vSphere port groups on ESXi) of the cluster named by
        ``proxyClusterUuid``; paging by ``offset`` plus ``length`` or ``count``.
        """
        if not est.features.pe_proxy:
            return 403, {"message": "Access denied for the proxied cluster."}, {}
        cluster_uuid = query.get("proxyClusterUuid")
        cluster = next((c for c in est.clusters if c["uuid"] == cluster_uuid), None)
        if cluster is None:
            return 404, {"message": f"Cluster {cluster_uuid} is not registered."}, {}
        what = path[len("/PrismGateway/services/rest/v2.0/"):].strip("/")
        offset = _int(query.get("offset"), 0)
        count = min(_int(query.get("length") or query.get("count"), 20), 500)
        items: list[dict[str, Any]]
        if what == "vms":
            disks = query.get("include_vm_disk_config") == "true"
            nics = query.get("include_vm_nic_config") == "true"
            items = [r.pe_vm_v2(v, disks, nics) for v in est.vms
                     if v["cluster_uuid"] == cluster_uuid]
        elif what == "networks":
            if cluster["hypervisor"] == "ESXi":
                items = [r.pe_port_group_v2(p) for p in est.port_groups
                         if p["cluster_uuid"] == cluster_uuid]
            else:
                items = [r.pe_network_v2(s) for s in est.subnets
                         if s["cluster_uuid"] == cluster_uuid]
        else:
            return 404, {"message": f"Not found: {path}"}, {}
        return 200, r.v2_list(_page(items, offset, count), len(items), offset), {}

    def _v3_list(self, est: PcEstate, kind: str, body: dict[str, Any]) -> Response:
        offset = _int(body.get("offset"), 0)
        length = min(_int(body.get("length"), 20), 500)
        f = est.features
        d = self.derived(est)
        microseg_v4 = est.v4_ga and "microseg" not in f.v4_absent
        table: dict[str, tuple[bool, list[Any], Callable[[Any], dict[str, Any]]]] = {
            "clusters": (True, [None, *est.clusters],
                         lambda c: r.pc_cluster_v3(est) if c is None else r.cluster_v3(est, c)),
            "hosts": (True, [*est.hosts, None],
                      lambda h: r.pc_pseudo_host_v3(est) if h is None
                      else r.host_v3(h, d.vms_per_host.get(h["uuid"], 0))),
            "vms": (True, d.v3_vms, lambda pair: r.vm_v3(est, pair[0], pair[1])),
            "subnets": (True, est.subnets, lambda s: r.subnet_v3(est, s)),
            "images": (True, est.images, lambda i: r.image_v3(est, i)),
            "ovas": (True, est.ovas, lambda o: r.ova_v3(est, o)),
            "protection_rules": (True, est.protection_policies, r.protection_rule_v3),
            "recovery_plans": (True, est.recovery_plans, r.recovery_plan_v3),
            "availability_zones": (True, est.availability_zones, r.availability_zone_v3),
            "alerts": (True, est.alerts, r.alert_v3),
            # Once the microseg service owns policies the legacy endpoint stops listing them.
            "network_security_rules": (True, [] if microseg_v4 else est.security_policies,
                                       r.security_policy_v3),
            "categories": (True, r.category_keys_v3(est), lambda c: c),
            "service_groups": (True, est.service_groups, r.service_group_v3),
            "address_groups": (True, est.address_groups, r.address_group_v3),
            "vpcs": (f.fvn, est.vpcs, r.vpc_v3),
            "floating_ips": (f.fvn, est.floating_ips, lambda x: r.floating_ip_v3(est, x)),
            "file_server": (f.files, est.file_servers, r.file_server_v3),
            "mount_target": (f.files, est.file_shares, r.file_share_v3),
            "objectstores": (f.objects, est.object_stores, r.object_store_v3),
            "volume_groups": (True, est.volume_groups, r.volume_group_v3),
            "users": (True, est.users, r.user_v3),
            "roles": (True, est.roles, r.role_v3),
            "projects": (True, est.projects, r.project_v3),
            "action_rules": (f.xplay, est.playbooks, r.playbook_v3),
            "report_configs": (f.ncm, est.report_configs, r.report_config_v3),
            "report_instances": (f.ncm, est.report_configs, r.report_instance_v3),
        }
        entry = table.get(kind)
        if entry is None or not entry[0]:
            return _not_found_v3(f"/api/nutanix/v3/{kind}/list")
        _, items, render = entry
        page = [render(x) for x in _page(items, offset, length)]
        singular = kind.rstrip("s") if kind not in ("file_server", "mount_target") else kind
        return 200, r.v3_list(singular, page, len(items), offset), {}

    def _v3_groups(self, est: PcEstate, body: dict[str, Any]) -> Response:
        etype = body.get("entity_type")
        attrs = [a.get("attribute") for a in body.get("group_member_attributes") or []]
        offset = _int(body.get("group_member_offset"), 0)
        count = min(_int(body.get("group_member_count"), 20), 500)
        rows: list[dict[str, Any]]
        if etype == "storage_container":
            rows = est.containers
        elif etype == "vm" and "is_cvm==1" in str(body.get("filter_criteria") or ""):
            rows = est.cvms
        elif etype == "host":
            rows = est.hosts
        elif etype == "mh_vm" and est.features.groups_mh_vm:
            rows = _mh_vm_rows(est, str(body.get("filter_criteria") or ""))
        elif etype in ("virtual_nic", "virtual_disk") and est.features.groups_mh_vm:
            rows = _vm_part_rows(est, etype, str(body.get("filter_criteria") or ""))
        else:
            return 400, {"message": f"Unsupported groups query for {etype}"}, {}
        page = _page(rows, offset, count)
        return 200, r.groups_response(etype, page, [a for a in attrs if a], len(rows)), {}

    # ─── v4 ────────────────────────────────────────────────────────

    def _v4(self, target: Target, est: PcEstate, path: str, query: dict[str, str]) -> Response:
        page = _int(query.get("$page"), 0)
        limit = _int(query.get("$limit"), 50)
        if limit > 100 or limit < 1:
            return 400, _v4_error("VALIDATION_ERROR",
                                  f"$limit must be between 1 and 100, got {limit}"), {}
        if not est.v4_ga:
            return _not_found_v4(path)
        namespace = next((ns for prefix, ns in _V4_NAMESPACE_OF if path.startswith(prefix)),
                         "?")
        f = est.features
        if namespace == "?":
            return _not_found_v4(path)
        if namespace in f.v4_absent or (namespace == "ncm_reports" and not f.ncm):
            return _not_found_v4(path)
        if namespace in f.v4_unavailable:
            return 503, _v4_error("SERVICE_UNAVAILABLE",
                                  f"The {namespace} service is not running."), {}

        d = self.derived(est)
        ahv, esxi, containers, alerts = d.ahv, d.esxi, est.containers, est.alerts
        flt = query.get("$filter")
        if flt:
            parsed = parse_filter(flt)
            if parsed is None or FILTERABLE.get(path) != parsed[0]:
                return 400, _v4_error("VALIDATION_ERROR",
                                      f"Unsupported $filter for /api/{path}: {flt}"), {}
            with self._lock:
                self.filters[(target.host, path)] = flt
            keep = parsed[1]
            ahv = [v for v in ahv if v["cluster_uuid"] in keep]
            esxi = [v for v in esxi if v["cluster_uuid"] in keep]
            containers = [c for c in containers if c["cluster_uuid"] in keep]
            alerts = [a for a in alerts if a["cluster_uuid"] in keep]
        lists: dict[str, tuple[list[Any], Callable[[Any], dict[str, Any]]]] = {
            "clustermgmt/v4.0/config/clusters": (
                [None, *est.clusters],
                lambda c: r.pc_cluster_v4(est) if c is None else r.cluster_v4(est, c)),
            "clustermgmt/v4.0/config/hosts": (
                est.hosts, lambda h: r.host_v4(h, d.vms_per_host.get(h["uuid"], 0))),
            "clustermgmt/v4.0/config/storage-containers": (containers, r.container_v4),
            "vmm/v4.0/ahv/config/vms": (ahv, r.vm_v4_ahv),
            "vmm/v4.0/content/images": (est.images, r.image_v4),
            "vmm/v4.0/content/templates": (est.templates, r.template_v4),
            "vmm/v4.0/content/ovas": (est.ovas, lambda o: r.ova_v4(est, o)),
            "networking/v4.0/config/subnets": (est.subnets, r.subnet_v4),
            "networking/v4.0/config/virtual-switches": (est.vswitches, r.vswitch_v4),
            "networking/v4.0/config/route-tables": (est.vpcs, r.route_table_v4),
            "networking/v4.0/config/routing-policies": (
                [(v, i) for v in est.vpcs for i in range(v["policies"])],
                lambda pair: r.routing_policy_v4(*pair)),
            "volumes/v4.0/config/volume-groups": (est.volume_groups, r.volume_group_v4),
            "datapolicies/v4.0/config/protection-policies": (
                est.protection_policies, lambda p: r.protection_policy_v4(est, p)),
            "microseg/v4.0/config/policies": (est.security_policies, r.security_policy_v4),
            "microseg/v4.0/config/service-groups": (est.service_groups, r.service_group_v4),
            "microseg/v4.0/config/address-groups": (est.address_groups, r.address_group_v4),
            "prism/v4.0/config/categories": (est.categories, r.category_v4),
            "prism/v4.0/config/domain-managers": ([None], lambda _x: r.domain_manager_v4(est)),
            "files/v4.0/config/file-servers": (est.file_servers, r.file_server_v4),
            "objects/v4.0/config/object-stores": (est.object_stores, lambda o: {
                "extId": o["uuid"], "name": o["name"]}),
            "iam/v4.0/authn/users": (est.users, lambda u: r.user_v4(est, u)),
            "iam/v4.0/authn/directory-services": (
                [x for x in est.directories if x["kind"] == "ldap"], r.directory_v4),
            "iam/v4.0/authn/saml-identity-providers": (
                [x for x in est.directories if x["kind"] == "saml"], r.directory_v4),
            "iam/v4.0/authz/roles": (est.roles, r.role_v4),
            "lifecycle/v4.0/resources/entities": (est.lcm_entities, r.lcm_entity_v4),
            "licensing/v4.0/config/licenses": (est.licenses, r.license_v4),
            "opsmgmt/v4.0/config/report-configs": (est.report_configs, r.report_config_v4),
            "opsmgmt/v4.0/config/reports": (est.report_configs, r.report_instance_v4),
        }
        if path == "monitoring/v4.0/serviceability/alerts" and limit > 1:
            throttled = self._rate_limit(target, page)
            if throttled is not None:
                return throttled
            return self._v4_page(alerts, r.alert_v4, path, page, limit)
        if path == "vmm/v4.0/esxi/config/vms":
            window = _page(esxi, page * limit, limit)
            if any(v["esxi_bad"] for v in window):
                return 500, _internal_error(), {}
            return self._v4_page(esxi, r.vm_v4_esxi, path, page, limit)
        m = re.fullmatch(r"vmm/v4\.0/(ahv|esxi)/config/vms/([^/]+)", path)
        if m:
            pool, render_vm = (ahv, r.vm_v4_ahv) if m.group(1) == "ahv" \
                else (esxi, r.vm_v4_esxi)
            vm = next((v for v in pool if v["uuid"] == m.group(2)), None)
            if vm is None:
                return _not_found_v4(path)
            if vm["esxi_bad"]:
                return 500, _internal_error(), {}
            return 200, r.v4_single(render_vm(vm)), {}
        if path == "lifecycle/v4.0/resources/status":
            return 200, r.v4_single(r.lcm_status_v4(f.lcm_framework_stale)), {}
        if path == "monitoring/v4.0/serviceability/alerts":
            return self._v4_page(alerts, r.alert_v4, path, page, limit)
        if path in lists:
            items, render = lists[path]
            return self._v4_page(items, render, path, page, limit)
        return self._v4_detail(est, path, page, limit)

    def _v4_page(self, items: list[Any], render: Callable[[Any], dict[str, Any]], path: str,
                 page: int, limit: int) -> Response:
        window = [render(x) for x in _page(items, page * limit, limit)]
        return 200, r.v4_list(window, len(items), path, page, limit), {}

    def _v4_detail(self, est: PcEstate, path: str, page: int, limit: int) -> Response:
        m = re.fullmatch(r"vmm/v4\.0/content/templates/([^/]+)(/versions)?", path)
        if m:
            t = next((x for x in est.templates if x["uuid"] == m.group(1)), None)
            if t is None:
                return _not_found_v4(path)
            if m.group(2):
                versions = [{"extId": f"{t['uuid']}-v{i}", "versionName": f"v{i}"}
                            for i in range(1, t["versions"] + 1)]
                return self._v4_page(versions, lambda v: v, path, page, limit)
            return 200, r.v4_single(r.template_v4(t, detail=True)), {}
        m = re.fullmatch(r"vmm/v4\.0/content/ovas/([^/]+)", path)
        if m:
            o = next((x for x in est.ovas if x["uuid"] == m.group(1)), None)
            return (200, r.v4_single(r.ova_v4(est, o, detail=True)), {}) if o \
                else _not_found_v4(path)
        m = re.fullmatch(r"microseg/v4\.0/config/policies/([^/]+)", path)
        if m:
            p = next((x for x in est.security_policies if x["uuid"] == m.group(1)), None)
            return (200, r.v4_single(r.security_policy_v4(p, detail=True)), {}) if p \
                else _not_found_v4(path)
        m = re.fullmatch(r"volumes/v4\.0/config/volume-groups/([^/]+)/(disks|vm-attachments)",
                         path)
        if m:
            vg = next((x for x in est.volume_groups if x["uuid"] == m.group(1)), None)
            if vg is None:
                return _not_found_v4(path)
            if m.group(2) == "disks":
                disks = [{"extId": f"{vg['uuid']}-d{i}", "index": i,
                          "diskSizeBytes": mib * 1024 * 1024}
                         for i, mib in enumerate(vg["disks_mib"])]
                return self._v4_page(disks, lambda x: x, path, page, limit)
            atts = [{"extId": f"{vg['uuid']}-a{i}"} for i in range(vg["attachments"])]
            return self._v4_page(atts, lambda x: x, path, page, limit)
        return _not_found_v4(path)

    def _rate_limit(self, target: Target, page: int) -> Response | None:
        """2 requests per second on the alerts endpoint, plus an optional forced 429."""
        now = time.monotonic()
        headers = {"X-Api-Ratelimit-Limit": "2", "X-Api-Ratelimit-Window": "1s",
                   "Retry-After": "1"}
        with self._lock:
            est = target.estate()
            if est.features.alerts_rate_limited and page == 1:
                key = (target.host, target.epoch)
                if key not in self._throttled_once:
                    self._throttled_once.add(key)
                    return 429, _v4_error("RATE_LIMITED", "Too many requests"), headers
            hits = self._alert_hits.setdefault(target.host, deque())
            while hits and now - hits[0] > 1.0:
                hits.popleft()
            if len(hits) >= 2:
                return 429, _v4_error("RATE_LIMITED", "Too many requests"), headers
            hits.append(now)
        return None

    # ─── Prism Element v2.0 ────────────────────────────────────────

    def _pe(self, est: PeEstate, method: str, path: str, query: dict[str, str]) -> Response:
        if method == "GET" and path.rstrip("/") == "/PrismGateway/services/rest/v1/vms":
            # Only the Controller VMs: the collector reads v1 for nothing else.
            page_no = max(1, _int(query.get("page"), 1))
            count = min(max(1, _int(query.get("count"), 20)), 500)
            host_uuid = {h["name"]: h["uuid"] for h in est.hosts}
            rows = _page(est.cvms, (page_no - 1) * count, count)
            return 200, r.v1_list([r.pe_cvm_v1(c, host_uuid.get(c["host_name"]))
                                   for c in rows], len(est.cvms), page_no, count), {}
        prefix = "/PrismGateway/services/rest/v2.0/"
        if method != "GET" or not path.startswith(prefix):
            return 404, {"message": f"Not found: {path}"}, {}
        what = path[len(prefix):].strip("/")
        if what == "cluster":
            return 200, r.pe_cluster_v2(est), {}
        offset = _int(query.get("offset"), 0)
        count = min(_int(query.get("count"), 20), 500)
        d = self.derived(est)
        tables: dict[str, tuple[list[Any], Callable[[Any], dict[str, Any]]]] = {
            "hosts": (est.hosts, lambda h: r.pe_host_v2(h, d.vms_per_host.get(h["uuid"], 0))),
            "storage_containers": (est.containers, r.pe_container_v2),
            "networks": (est.subnets, r.pe_network_v2),
            "vms": (est.vms, lambda v: r.pe_vm_v2(
                v, query.get("include_vm_disk_config") == "true",
                query.get("include_vm_nic_config") == "true")),
        }
        if what not in tables:
            return 404, {"message": f"Not found: {path}"}, {}
        items, render = tables[what]
        return 200, r.v2_list([render(x) for x in _page(items, offset, count)], len(items),
                              offset), {}

    # ─── NDB (Era) ─────────────────────────────────────────────────

    def _ndb(self, est: NdbEstate, method: str, path: str) -> Response:
        prefix = "/era/v0.9/"
        if method != "GET" or not path.startswith(prefix):
            return 404, {"errorCode": "ERA-404", "message": f"Not found: {path}"}, {}
        what = path[len(prefix):].strip("/")
        tables = {"clusters": est.clusters, "dbservers": est.dbservers,
                  "databases": est.databases, "tms": est.time_machines}
        if what == "about":
            return 200, {"version": est.version, "product": "Nutanix Database Service"}, {}
        if what not in tables:
            return 404, {"errorCode": "ERA-404", "message": f"Not found: {path}"}, {}
        return 200, tables[what], {}


# ─── helpers ───────────────────────────────────────────────────────────


class _HttpError(Exception):
    def __init__(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        super().__init__(status)
        self.status = status
        self.body = body
        self.headers = headers or {}


def _authorized(header: str | None) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        user, _, password = base64.b64decode(header[6:]).decode().partition(":")
    except Exception:  # noqa: BLE001 — malformed header is just unauthorized
        return False
    return bool(user) and bool(password) and password != "wrong"


def _v4_error(code: str, message: str) -> dict[str, Any]:
    return {"$objectType": "common.v1.response.ApiResponse",
            "data": {"$objectType": "common.v1.response.ErrorResponse",
                     "error": [{"code": code, "message": message, "severity": "ERROR"}]}}


def _internal_error() -> dict[str, Any]:
    """The error a real PC answers for a v4 VM record it cannot serialize."""
    return _v4_error("PLAT-10006",
                     "Failed to perform the operation due to an internal error.")


def _mh_vm_rows(est: PcEstate, criteria: str) -> list[dict[str, Any]]:
    """Every VM and CVM as a flat ``mh_vm`` groups row (values as strings, like a PC).

    Honours ``is_cvm==0|1`` and ``cluster=in=<uuid>|<uuid>`` / ``cluster==<uuid>`` terms
    joined by ``;``; any other term is ignored (served unfiltered), never guessed at.
    """
    cluster_uuid = {c["name"]: c["uuid"] for c in est.clusters}
    rows: list[dict[str, Any]] = []
    for v in est.vms:
        rows.append({
            "uuid": v["uuid"], "vm_name": v["name"], "cluster_name": v["cluster_name"],
            "cluster": v["cluster_uuid"],
            "hypervisor_type": "kKvm" if v["hypervisor"] == "AHV" else "kVMware",
            "num_vcpus": str(v["sockets"] * v["cores"]),
            "memory_size_bytes": str(v["memory"]),
            "power_state": str(v["power"]).lower(), "is_cvm": "0", "vm_type": "kGuestVM",
            "host_name": None, "node": v["host_uuid"],
            # Like a real PC: only disks on Nutanix storage containers count.
            "capacity_bytes": str(sum(d["size"] for d in v["disks"] if d["container_uuid"])),
            "guest_os_name": v["guest_os"],
            "ip_addresses": [n["ip"] for n in v["nics"] if n["ip"]] or None,
        })
    for c in est.cvms:
        rows.append({
            "uuid": c["uuid"], "vm_name": c["name"], "cluster_name": c["cluster_name"],
            "cluster": cluster_uuid.get(c["cluster_name"]),
            "hypervisor_type": "kKvm" if c["hypervisor"] == "AHV" else "kVMware",
            "num_vcpus": str(c["vcpus"]), "memory_size_bytes": str(c["memory"]),
            "power_state": "on", "is_cvm": "1", "vm_type": "kCVM", "host_name": None,
            "node": None, "capacity_bytes": "0", "guest_os_name": "",
        })
    for term in criteria.split(";"):
        key, op, value = term.partition("=in=") if "=in=" in term else term.partition("==")
        if not op:
            continue
        wanted = set(value.split("|")) if op == "=in=" else {value}
        if key == "is_cvm":
            rows = [x for x in rows if x["is_cvm"] in wanted]
        elif key == "cluster":
            rows = [x for x in rows if x["cluster"] in wanted]
    return rows


def _vm_part_rows(est: PcEstate, etype: str, criteria: str) -> list[dict[str, Any]]:
    """``virtual_nic`` / ``virtual_disk`` groups rows, honouring ``vm=in=a|b``."""
    wanted: set[str] | None = None
    for term in criteria.split(";"):
        if term.startswith("vm=in="):
            wanted = set(term[len("vm=in="):].split("|"))
        elif term.startswith("vm=="):
            wanted = {term[len("vm=="):]}
    rows: list[dict[str, Any]] = []
    for v in est.vms:
        if wanted is not None and v["uuid"] not in wanted:
            continue
        if etype == "virtual_nic":
            for n in v["nics"]:
                rows.append({"uuid": n["uuid"], "vm": v["uuid"], "mac_address": n["mac"],
                             "ipv4_addresses": [n["ip"]] if n["ip"] else None,
                             "connected": "true" if n["connected"] else "false",
                             "virtual_network": n.get("port_group_uuid") or n["subnet_uuid"]})
        else:
            for d in v["disks"]:
                if not d["container_uuid"]:
                    continue  # groups knows only disks on Nutanix containers
                rows.append({"uuid": d["uuid"], "vm": v["uuid"],
                             "capacity_bytes": str(d["size"]),
                             "container": d["container_uuid"],
                             "container_name": d["container_name"],
                             "disk_address": f"{d['bus'].lower()}.{d['index']}",
                             "is_cdrom": "0"})
    return rows


def _not_found_v4(path: str) -> Response:
    return 404, _v4_error("NOT_FOUND", f"No route for /api/{path}"), {}


def _not_found_v3(path: str) -> Response:
    return 404, {"api_version": "3.1", "code": 404, "state": "ERROR",
                 "message_list": [{"message": f"{path} not found",
                                   "reason": "ENTITY_NOT_FOUND"}]}, {}
