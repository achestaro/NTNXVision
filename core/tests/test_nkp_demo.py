"""NKP end to end in demo mode: pc01's NKP cluster seen from Prism Central, its own
Kubernetes API at nkp01.demo.local, the sidecar contract, the exports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from openpyxl import load_workbook

import ntnxvision_core.sidecar as sc
from ntnxvision_core.api import PrismCredentials
from ntnxvision_core.api.kube_client import KubeCredentials
from ntnxvision_core.areas import AREAS_BY_ID, catalogue
from ntnxvision_core.engine import collect_environment, combine_environments
from ntnxvision_core.exporters import ExportOptions, export_environment
from ntnxvision_core.exporters.content import build_report

PC = PrismCredentials(host="pc01.demo.local", username="admin", password="demo")
NKP = KubeCredentials(host="nkp01.demo.local", token="demo")
AREAS = ["clusters", "vms", "nkp"]


def test_area_catalogue() -> None:
    nkp = AREAS_BY_ID["nkp"]
    assert nkp.label == "NKP Clusters (Prism Central View)" and nkp.requires == ("vms",)
    assert nkp.default_on and nkp.group == "Platform services"
    ids = [a["id"] for a in catalogue()["areas"]]  # type: ignore[index]
    assert "nkp" in ids and "kubernetes" not in ids  # the API step is no checkbox
    kube = AREAS_BY_ID["kubernetes"]
    assert kube.label == "NKP Clusters (Kubernetes API)" and kube.legacy_api == "kubernetes"


def test_pc_view_only(demo: Any) -> None:
    env = collect_environment(PC, areas=AREAS)
    assert env.collection_notes["nkp"] == "ok"
    [k] = env.kubernetes_clusters
    assert k.name == "nkp-prod-01" and k.api_source == "pc"
    assert k.cluster_name == "PHX-PROD-02"
    assert (k.control_plane_nodes, k.worker_nodes) == (1, 3)
    assert k.control_plane_endpoint == "10.21.40.10"
    # From the Self-Service app (the bastion host's app is not a cluster).
    assert k.nkp_version == "2.18.0" and k.deployed_by == "Marketplace (Self-Service)"
    assert len(env.kubernetes_nodes) == 4
    assert all(n.api_source == "pc" for n in env.kubernetes_nodes)


def test_nkp_area_pulls_in_vms(demo: Any) -> None:
    env = collect_environment(PC, areas=["nkp"])
    assert env.vms and env.kubernetes_clusters


def test_no_nkp_on_a_pc_without_it(demo: Any) -> None:
    env = collect_environment(PrismCredentials(host="tiny.demo.local", username="a",
                                               password="demo"), areas=["vms", "nkp"])
    assert env.kubernetes_clusters == []
    assert env.collection_notes["nkp"].startswith("skipped")


def test_both_sources_merge(demo: Any) -> None:
    env = collect_environment(PC, areas=AREAS, kube_creds=[NKP])
    assert env.collection_notes["kubernetes"] == "ok"
    [k] = env.kubernetes_clusters
    assert k.api_source == "pc+kubernetes"
    assert k.cni == "Cilium" and k.license == "NKP Starter" and k.management_cluster
    assert k.namespace_count == len(env.kubernetes_namespaces) > 20
    assert len(env.kubernetes_nodes) == 4
    assert {n.api_source for n in env.kubernetes_nodes} == {"pc+kubernetes"}
    assert {n.status for n in env.kubernetes_nodes} == {"Ready"}
    assert len(env.kubernetes_storage_classes) == 2 and len(env.kubernetes_services) == 3
    assert env.kubernetes_apps and env.kubernetes_volumes


def test_forbidden_list_is_a_note_not_a_failure(demo: Any) -> None:
    target = demo.targets["nkp01.demo.local"]
    est = target.estate()
    est.forbidden = {"/api/v1/persistentvolumes"}
    try:
        env = collect_environment(PC, areas=AREAS, kube_creds=[NKP])
    finally:
        est.forbidden = set()
    note = env.collection_notes["kubernetes"]
    assert note.startswith("ok:") and "pvs" in note and "403" in note
    assert env.kubernetes_volumes == [] and env.kubernetes_namespaces


def test_one_bad_endpoint_never_stops_the_others(demo: Any) -> None:
    bad = KubeCredentials(host="down.demo.local", token="demo")
    env = collect_environment(PC, areas=AREAS, kube_creds=[bad, NKP])
    note = env.collection_notes["kubernetes"]
    assert note.startswith("ok:") and "down.demo.local" in note
    assert env.kubernetes_clusters[0].api_source == "pc+kubernetes"
    only_bad = collect_environment(PC, areas=AREAS, kube_creds=[bad])
    assert only_bad.collection_notes["kubernetes"].startswith("error")
    assert only_bad.kubernetes_clusters[0].api_source == "pc"  # the PC view survives


def test_wrong_token_fails_the_area(demo: Any) -> None:
    env = collect_environment(PC, areas=AREAS,
                              kube_creds=[KubeCredentials(host="nkp01.demo.local",
                                                          token="wrong")])
    assert env.collection_notes["kubernetes"].startswith("error")


def test_sidecar_inventory_contract(demo: Any, data_dir: Path) -> None:
    run = sc._m_inventory(None, {
        "host": "pc01.demo.local", "username": "admin", "password": "demo", "port": 9440,
        "insecure": False, "name": "nkp", "formats": ["excel"], "areas": AREAS,
        "export_dir": str(data_dir),
        "kubernetes": [{"name": "nkp-prod-01", "host": "nkp01.demo.local", "port": 6443,
                        "token": "demo", "insecure": False, "pinned_sha256": None}]})
    env = sc._cache.get(run["snapshot_path"])
    assert env.kubernetes_clusters[0].api_source == "pc+kubernetes"
    areas = {a["id"]: a for a in run["areas"]}
    assert areas["nkp"]["status"] == "collected" and areas["nkp"]["objects"] == 1
    assert areas["kubernetes"]["status"] == "collected"
    assert areas["kubernetes"]["api"] == "kubernetes"
    assert any(r["api"] == "kubernetes" for r in run["api_routing"])
    wb = load_workbook(run["exports"]["excel"])
    assert "Kubernetes Clusters" in wb.sheetnames and "Kubernetes Nodes" in wb.sheetnames
    rows = sc._m_query_inventory(None, {"snapshot": run["snapshot_path"],
                                        "object_type": "kubernetes_nodes",
                                        "page": {"limit": 10}})
    assert rows["total"] == 4


def test_sidecar_multi_contract(demo: Any, data_dir: Path) -> None:
    out = sc._m_inventory_multi(None, {
        "targets": [
            {"host": "pc01.demo.local", "username": "admin", "password": "demo",
             "name": "k-pc01", "kubernetes": [{"host": "nkp01.demo.local", "token": "demo"}]},
            {"host": "pc02.demo.local", "username": "admin", "password": "demo",
             "name": "k-pc02"},
        ],
        "areas": AREAS, "formats": [], "export_dir": str(data_dir), "combine": True,
        "combined_name": "k-both", "parallel": False})
    combined = sc._cache.get(out["combined"]["snapshot_path"])
    [k] = combined.kubernetes_clusters
    assert k.source_pc == "pc01.demo.local" and k.api_source == "pc+kubernetes"
    assert {n.source_pc for n in combined.kubernetes_nodes} == {"pc01.demo.local"}


@pytest.fixture(scope="module")
def nkp_env(_demo_state: Any) -> Any:
    from ntnxvision_core.synthetic import demo as demo_mode

    demo_mode.reset()
    return collect_environment(PC, areas=AREAS, kube_creds=[NKP])


def test_report_chapter(nkp_env: Any) -> None:
    report = build_report(nkp_env, ExportOptions(), "x")
    ch = next(c for c in report.chapters if c.key == "kubernetes")
    assert ch.title == "Kubernetes (NKP)"
    assert [s.title for s in ch.sections] == ["Kubernetes Platform Design", "Clusters",
                                              "Nodes", "Namespaces", "Storage",
                                              "Applications", "Platform Apps"]
    for section in ch.sections:
        for block in section.blocks:
            headers = getattr(block, "headers", None)
            if headers and "Cluster" in headers:
                assert headers[0] == "Cluster"


def test_exports(nkp_env: Any, tmp_path: Path) -> None:
    xlsx = export_environment(nkp_env, "excel", tmp_path, "nkp")
    wb = load_workbook(xlsx)
    for sheet in ("Kubernetes Clusters", "Kubernetes Nodes", "Kubernetes Namespaces",
                  "Kubernetes Storage Classes", "Kubernetes Volumes",
                  "Kubernetes LB Services", "Kubernetes Platform Apps",
                  "Kubernetes Applications", "Kubernetes Workloads", "Kubernetes Ingresses"):
        ws = wb[sheet]
        assert ws["A1"].value == "Cluster" and ws["A2"].value == "PHX-PROD-02", sheet
    assert export_environment(nkp_env, "word", tmp_path, "nkp").stat().st_size > 10_000
    assert export_environment(nkp_env, "pdf", tmp_path, "nkp").stat().st_size > 10_000


def test_combined_attribution(nkp_env: Any) -> None:
    other = nkp_env.model_copy(deep=True)
    other.prism_central.host = "pc99.example.lab"
    combined = combine_environments([nkp_env.model_copy(deep=True), other])
    assert {k.source_pc for k in combined.kubernetes_clusters} == {"pc01.demo.local",
                                                                   "pc99.example.lab"}
    xlsx_rows = build_report(combined, ExportOptions(), "x")
    assert any(c.key == "kubernetes" for c in xlsx_rows.chapters)


def test_applications_and_workloads(nkp_env: Any) -> None:
    apps = {(a.namespace, a.name): a for a in nkp_env.kubernetes_applications}
    assert apps[("demo-shop", "shop")].workloads == 11
    assert apps[("demo-shop", "shop")].addresses == ["10.21.40.22"]
    assert apps[("demo-blog", "blog")].ingress_hosts == ["blog.shop.example.lab"]
    users = [w for w in nkp_env.kubernetes_workloads if not w.system]
    assert len(users) == 22 and all(w.status in ("Ready", "Scheduled") for w in users)
    assert {i.name for i in nkp_env.kubernetes_ingresses if not i.system} == {
        "web-frontend", "blog-wordpress"}


def test_report_lists_user_namespaces_and_one_system_row(nkp_env: Any) -> None:
    report = build_report(nkp_env, ExportOptions(), "x")
    ch = next(c for c in report.chapters if c.key == "kubernetes")
    ns_table = next(b for s in ch.sections if s.title == "Namespaces" for b in s.blocks
                    if getattr(b, "headers", None))
    first = [str(r[1]) for r in ns_table.rows]  # Cluster is moved to the front
    system_rows = [r for r in first if r.startswith("System namespaces")]
    assert system_rows == ["System namespaces (20)"]
    user = {n.name for n in nkp_env.kubernetes_namespaces if not n.system}
    assert user <= set(first) and len(first) == len(user) + 1
    apps = next(s for s in ch.sections if s.title == "Applications")
    workloads = next(b for b in apps.blocks if getattr(b, "headers", None)
                     and "Images" in b.headers)
    assert len(workloads.rows) == 22  # user workloads only


def test_inventory_lists_workloads(demo: Any, data_dir: Path) -> None:
    run = sc._m_inventory(None, {
        "host": "pc01.demo.local", "username": "admin", "password": "demo",
        "name": "wl", "formats": [], "areas": AREAS, "export_dir": str(data_dir),
        "kubernetes": [{"host": "nkp01.demo.local", "token": "demo"}]})
    rows = sc._m_query_inventory(None, {"snapshot": run["snapshot_path"],
                                        "object_type": "kubernetes_workloads",
                                        "page": {"limit": 200}})
    assert rows["total"] == 96
