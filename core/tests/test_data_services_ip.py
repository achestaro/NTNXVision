"""The cluster Data Services IP, end to end: every API version, the grid, every report.

Field names per API (read-only GETs, routing as for every cluster field):

* v4 ``clustermgmt/v4.0/config/clusters``: ``network.externalDataServiceIp`` (an IPAddress)
* v3 ``clusters/list``: ``status.resources.network.external_data_services_ip``
* v2.0 ``cluster`` (Prism Element): ``cluster_external_data_services_ipaddress``

A cluster without one (no Volumes / Files clients) reads N/A everywhere.
"""

from __future__ import annotations

import copy
from typing import Any

from ntnxvision_core.collectors.clusters import ClusterCollector
from ntnxvision_core.exporters import tables as T
from ntnxvision_core.models import Cluster, Environment
from ntnxvision_core.numfmt import NA

from .test_v4_clusters import V4_CLUSTER, _Conn


def _collect(conn: Any) -> Environment:
    env = Environment()
    ClusterCollector(conn).collect(env)
    return env


def test_v4_reads_external_data_service_ip() -> None:
    payload = copy.deepcopy(V4_CLUSTER)
    payload["network"]["externalDataServiceIp"] = {"ipv4": {"value": "198.51.100.233"}}
    env = _collect(_Conn(v4=True, clusters=[payload]))
    assert env.clusters[0].data_services_ip == "198.51.100.233"


def test_v4_without_one_is_none() -> None:
    env = _collect(_Conn(v4=True))
    assert env.clusters[0].data_services_ip is None


class _V3Conn:
    negotiated: dict[str, str] = {}

    def __init__(self, dsip: str | None) -> None:
        self.dsip = dsip

    def v4_ready(self, area: str) -> bool:
        return False

    def v3_list(self, kind: str) -> list[dict[str, Any]]:
        return [{"metadata": {"uuid": "c1"}, "status": {"name": "c1", "resources": {
            "config": {"service_list": ["AOS"]},
            "network": {"external_ip": "10.0.0.10", "external_data_services_ip": self.dsip},
        }}}]


def test_v3_reads_external_data_services_ip() -> None:
    assert _collect(_V3Conn("10.0.0.11")).clusters[0].data_services_ip == "10.0.0.11"
    assert _collect(_V3Conn("")).clusters[0].data_services_ip is None


def test_cluster_spec_and_grid_show_it_with_na() -> None:
    from ntnxvision_core.inventory import display

    col = next(c for c in T.CLUSTERS.cols if c.key == "data_services_ip")
    assert col.header == col.label == "Data Services IP" and col.kind == "mono"
    env = Environment(clusters=[Cluster(uuid="a", name="A", data_services_ip="10.1.1.1"),
                                Cluster(uuid="b", name="B")])
    rows = T.table_rows(T.CLUSTERS, env)
    i = [c.key for c in T.CLUSTERS.excel_cols].index("data_services_ip")
    assert [r[i] for r in rows] == ["10.1.1.1", None]  # Excel renders None as N/A
    assert display(col, None) is None  # the grid shows N/A for None
    assert NA == "N/A"


def test_demo_estate_serves_it_on_every_api(demo: Any) -> None:
    from ntnxvision_core.api import PrismCredentials
    from ntnxvision_core.api.element_client import ElementCredentials
    from ntnxvision_core.engine import collect_element_environment, collect_environment
    from ntnxvision_core.exporters import ExportOptions
    from ntnxvision_core.exporters.content import build_report

    env = collect_environment(PrismCredentials(host="pc01.demo.local", username="a",
                                               password="p"))
    dsips = [c.data_services_ip for c in env.clusters]
    assert any(dsips) and None in dsips  # one cluster has none: N/A path exercised
    pe = collect_element_environment(ElementCredentials(host="pe01.demo.local",
                                                        username="a", password="p"))
    assert pe.clusters[0].data_services_ip
    report = build_report(env, ExportOptions(), eyebrow="x")
    table = next(b for ch in report.chapters for s in ch.sections for b in s.blocks
                 if getattr(b, "headers", None) and "Data Services IP" in b.headers)
    i = table.headers.index("Data Services IP")
    from ntnxvision_core.exporters.content import cell_text

    assert NA in [cell_text(r[i], "Data Services IP") for r in table.rows]
