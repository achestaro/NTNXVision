"""NKP clusters: the Prism Central view, the Kubernetes API, their merge, and the plumbing.

The fixtures under ``tests/fixtures`` are a live NKP 2.18 management cluster (read with a
read-only token) and its Self-Service app record, sanitised: example names, 10.21.x
addresses, hashed identifiers, bulky workload lists reduced to their metadata.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ntnxvision_core.api.errors import NutanixApiError
from ntnxvision_core.api.kube_client import KubeCredentials, KubeForbidden, KubeNotFound
from ntnxvision_core.api.transport import ReadOnlyViolation, check_request
from ntnxvision_core.collectors.nkp import (
    KUBE_LISTS,
    app_variables,
    apply_app,
    derive_from_vms,
    map_kubernetes,
    merge_cluster,
    node_role,
    quantity_bytes,
    read_kubernetes,
)
from ntnxvision_core.models import Cluster, Environment, Nic, VirtualMachine

FIXTURES = Path(__file__).parent / "fixtures"
#: Fixture file key -> `KUBE_LISTS` key, where they differ.
_KEYS = {"sc": "storageclasses"}


def _k8s() -> dict[str, list[dict[str, Any]]]:
    raw = json.loads((FIXTURES / "nkp_k8s.json").read_text("utf-8"))
    return {_KEYS.get(k, k): v for k, v in raw.items()}


def _app() -> dict[str, Any]:
    out: dict[str, Any] = json.loads((FIXTURES / "nkp_app.json").read_text("utf-8"))
    return out


def test_fixtures_carry_no_lab_data() -> None:
    for name in ("nkp_k8s.json", "nkp_app.json"):
        text = (FIXTURES / name).read_text("utf-8")
        for marker in ("10.50.", "nvd", "ntnxv-", "NTNXV"):
            assert marker not in text, f"{name} contains {marker!r}"


# ─── Kubernetes API mapping ────────────────────────────────────────────


def test_cluster_identity_from_the_real_payloads() -> None:
    parsed = map_kubernetes(_k8s(), "fallback", "v1.35.2")
    k = parsed["cluster"]
    assert k.name == "demo-nkp" and k.api_source == "kubernetes"
    assert k.nkp_version == "2.18.0" and k.kubernetes_version == "1.35.2"
    assert k.control_plane_endpoint == "10.21.71.245"
    assert k.pod_cidr == "192.168.0.0/16" and k.service_cidr == "10.96.0.0/12"
    assert k.cni == "Cilium"
    assert k.load_balancer == "MetalLB 10.21.71.246-10.21.71.249"
    assert k.default_storage_class == "nutanix-volume"
    assert k.license == "NKP Starter"
    assert k.management_cluster is True and k.managed_clusters == []
    assert k.status == "Provisioned"
    assert (k.control_plane_nodes, k.worker_nodes) == (1, 3)
    assert k.total_vcpus == 4 + 3 * 8
    assert k.cluster_name == "PHX-PROD-02"  # the nodes' zone: the Nutanix cluster


def test_nodes_role_pool_versions_and_vm_link() -> None:
    nodes = {n.name: n for n in map_kubernetes(_k8s(), "x")["nodes"]}
    cp = nodes["demo-nkp-dsfhx-ctsnd"]
    assert cp.role == "control-plane" and cp.pool is None
    assert cp.ip_address == "10.21.67.241" and cp.vcpus == 4
    assert cp.memory_bytes == 16033620 * 1024
    assert cp.kubelet_version == "v1.35.2" and cp.os_image.startswith("Rocky Linux 9.7")
    assert cp.container_runtime == "containerd://2.1.6-d2iq.1"
    assert cp.status == "Ready" and cp.vm_uuid == "d70fcd3e-2803-48ba-2a2d-f0e08f950248"
    workers = [n for n in nodes.values() if n.role == "worker"]
    assert len(workers) == 3 and {n.pool for n in workers} == {"md-0"}


def test_namespaces_count_their_workloads() -> None:
    spaces = {n.name: n for n in map_kubernetes(_k8s(), "x")["namespaces"]}
    assert len(spaces) == 33
    data = spaces["shop-data"]
    assert (data.pods, data.statefulsets, data.volume_claims, data.services) == (2, 1, 2, 1)
    kube = spaces["kube-system"]
    assert kube.pods == 25 and kube.daemonsets == 3 and kube.deployments == 5
    assert spaces["kube-public"].pods == 0


def test_storage_volumes_services_and_apps() -> None:
    parsed = map_kubernetes(_k8s(), "x")
    classes = {c.name: c for c in parsed["storage_classes"]}
    assert classes["nutanix-volume"].default is True
    assert classes["nutanix-volume"].storage_container == "default-container-69411"
    assert classes["nutanix-volume-retain"].reclaim_policy == "Retain"
    assert classes["nutanix-volume"].provisioner == "csi.nutanix.com"
    vols = {v.claim: v for v in parsed["volumes"]}
    assert len(vols) == 7
    assert vols["shop-data/data-orders-db-0"].capacity_bytes == 5 * 1024**3
    assert vols["shop-data/reports-archive"].storage_class == "nutanix-volume-retain"
    lbs = {s.name: s for s in parsed["services"]}
    assert set(lbs) == {"kommander-traefik", "web-frontend"}
    assert lbs["web-frontend"].external_ips == ["10.21.71.247"]
    assert "443/TCP" in lbs["kommander-traefik"].ports
    apps = {(a.namespace, a.name): a for a in parsed["apps"]}
    assert all(a.kind == "AppDeployment" for a in apps.values())
    assert apps[("kommander", "kommander")].version == "0.18.0"
    assert apps[("kommander", "kommander")].status == "Ready"
    # A workspace app (deployed to attached clusters) has no release here and no status.
    workspace = apps[("kommander-default-workspace", "cert-manager")]
    assert workspace.version == "1.19.3" and workspace.status is None
    assert apps[("kommander", "dex")].status == "Ready"
    assert parsed["cluster"].platform_apps == len(apps)


def test_helm_releases_stand_in_without_kommander() -> None:
    data = _k8s()
    data["appdeployments"] = []
    apps = map_kubernetes(data, "x")["apps"]
    assert apps and all(a.kind == "HelmRelease" for a in apps)
    kommander = next(a for a in apps if a.name == "kommander")
    assert kommander.version == "2.18.0" and kommander.status == "Ready"


def test_plain_kubernetes_cluster_without_nkp_crds() -> None:
    data = {k: v for k, v in _k8s().items()
            if k in ("nodes", "namespaces", "pods", "services", "pvs", "pvcs",
                     "storageclasses", "deployments", "statefulsets", "daemonsets")}
    k = map_kubernetes(data, "plain-k8s", "v1.31.4")["cluster"]
    # The node annotations still name the Cluster API cluster; versions from /version.
    assert k.name == "demo-nkp" and k.kubernetes_version == "1.31.4"
    assert k.nkp_version is None and k.management_cluster is False
    assert k.cni == "Cilium"  # from the cilium DaemonSet


def test_quantities() -> None:
    assert quantity_bytes("16033620Ki") == 16033620 * 1024
    assert quantity_bytes("100Gi") == 100 * 1024**3
    assert quantity_bytes("5G") == 5 * 1000**3
    assert quantity_bytes("1024") == 1024
    assert quantity_bytes(None) is None and quantity_bytes("bogus") is None


# ─── tolerant reads ────────────────────────────────────────────────────


class FakeKube:
    """`list(path)` from the fixtures; CRDs in `missing` are 404s, `forbidden` 403s."""

    def __init__(self, missing: set[str] = frozenset(),  # type: ignore[assignment]
                 forbidden: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        self.data = _k8s()
        self.missing, self.forbidden = missing, forbidden
        self.paths = {path: key for key, path, _ in KUBE_LISTS}
        self.creds = KubeCredentials(host="k8s.example.lab", token="t")
        self.calls: list[str] = []

    def list(self, path: str, page_size: int = 500) -> list[dict[str, Any]]:
        self.calls.append(path)
        key = self.paths.get(path)
        if key is None or key in self.missing:
            raise KubeNotFound(f"Not served: {path}", status_code=404)
        if key in self.forbidden:
            raise KubeForbidden(f"The token may not read {path}.")
        return self.data.get(key, [])

    def version(self) -> dict[str, Any]:
        return {"gitVersion": "v1.35.2"}


def test_missing_crds_and_forbidden_lists_are_skipped_with_notes() -> None:
    kube = FakeKube(missing={"appdeployments", "licenses", "kommanderclusters"},
                    forbidden={"pvs", "helmreleases"})
    data, notes = read_kubernetes(kube)  # type: ignore[arg-type]
    assert data["appdeployments"] == [] and data["pvs"] == [] and data["nodes"]
    assert any("pvs" in n and "403" in n for n in notes)
    assert any("helmreleases" in n and "403" in n for n in notes)
    # An absent NKP CRD is normal on a plain cluster: no note for it.
    assert not any("appdeployments" in n or "licenses" in n for n in notes)
    # The fallback API versions are tried when the first is not served.
    assert "/apis/cluster.x-k8s.io/v1beta1/clusters" in kube.calls
    parsed = map_kubernetes(data, "x")
    assert parsed["volumes"] == [] and parsed["cluster"].license is None


def test_a_missing_core_list_is_noted() -> None:
    _data, notes = read_kubernetes(FakeKube(missing={"pods"}))  # type: ignore[arg-type]
    assert "pods: not served" in notes


# ─── Prism Central view ────────────────────────────────────────────────


def _vm(name: str, ips: list[str], cluster: str = "demo-nkp", vcpus: int = 8,
        uuid: str | None = None) -> VirtualMachine:
    return VirtualMachine(
        uuid=uuid or f"uuid-{name}", name=name, cluster_uuid="c-1", power_state="ON",
        num_vcpus=vcpus, num_cores_per_vcpu=1, memory_bytes=32 * 1024**3,
        categories={"KubernetesClusterName": [cluster]},
        nics=[Nic(ip_addresses=ips)])


def _pc_env() -> Environment:
    env = Environment(clusters=[Cluster(uuid="c-1", name="PHX-PROD-02")])
    env.vms = [
        _vm("demo-nkp-dsfhx-ctsnd", ["10.21.67.241", "10.21.71.245"], vcpus=4,
            uuid="d70fcd3e-2803-48ba-2a2d-f0e08f950248"),
        _vm("demo-nkp-md-0-c62sk-7dqrp-4j4p4", ["10.21.69.173"]),
        _vm("demo-nkp-md-0-c62sk-7dqrp-5b5g9", ["10.21.70.246"]),
        _vm("demo-nkp-md-0-c62sk-7dqrp-wdg9q", ["10.21.71.177"]),
        VirtualMachine(uuid="u-web", name="web-01"),
    ]
    return env


def test_node_role_from_the_vm_name() -> None:
    assert node_role("demo-nkp-md-0-c62sk-7dqrp-4j4p4", "demo-nkp") == ("worker", "md-0")
    assert node_role("demo-nkp-md-12-abcde-fghij-klmno", "demo-nkp") == ("worker", "md-12")
    assert node_role("demo-nkp-dsfhx-ctsnd", "demo-nkp") == ("control-plane", None)


def test_pc_view_from_categorised_vms() -> None:
    clusters, nodes = derive_from_vms(_pc_env())
    assert [c.name for c in clusters] == ["demo-nkp"]
    k = clusters[0]
    assert k.api_source == "pc" and k.cluster_name == "PHX-PROD-02"
    assert (k.control_plane_nodes, k.worker_nodes) == (1, 3)
    assert k.control_plane_endpoint == "10.21.71.245"  # the control plane's second IP
    assert k.total_vcpus == 4 + 3 * 8
    cp = next(n for n in nodes if n.role == "control-plane")
    assert cp.ip_address == "10.21.67.241" and cp.status == "Powered on"
    assert {n.pool for n in nodes if n.role == "worker"} == {"md-0"}
    assert all(n.cluster_name == "PHX-PROD-02" for n in nodes)


def test_self_service_app_variables_where_they_really_sit() -> None:
    values = app_variables(_app())
    assert values["nkp_cluster_name"] == "demo-nkp"
    assert values["nkp_version"] == "2.18.0" and values["kubernetes_version"] == "1.35.2"
    assert values["control_plane_endpoint_ip"] == "10.21.71.245"
    assert values["worker_replicas"] == "3" and values["control_plane_replicas"] == "1"
    assert values["kubernetes_service_load_balancer_ip_range"] == "10.21.71.246-10.21.71.249"


def test_app_with_empty_values_mid_deploy_is_tolerated() -> None:
    app = _app()
    for part in ("status", "spec"):
        for dep in app[part]["resources"]["deployment_list"]:
            for key in ("substrate_configuration", "substrate"):
                for var in (dep.get(key) or {}).get("variable_list") or []:
                    var["value"] = ""
            for svc in dep.get("service_list") or []:
                for var in svc.get("variable_list") or []:
                    var["value"] = ""
    assert app_variables(app) == {}


def test_app_enriches_the_pc_view_and_corrects_the_endpoint_guess() -> None:
    env = _pc_env()
    # The control plane's IPs listed the other way round: the guess is the node's own IP.
    env.vms[0].nics[0].ip_addresses = ["10.21.71.245", "10.21.67.241"]
    clusters, nodes = derive_from_vms(env)
    assert clusters[0].control_plane_endpoint == "10.21.67.241"
    apply_app(clusters[0], _app(), app_variables(_app()), nodes)
    k = clusters[0]
    assert k.control_plane_endpoint == "10.21.71.245"
    cp = next(n for n in nodes if n.role == "control-plane")
    assert cp.ip_address == "10.21.67.241"
    assert k.nkp_version == "2.18.0" and k.kubernetes_version == "1.35.2"
    assert k.deployed_by == "Marketplace (Self-Service)" and k.status == "provisioning"
    assert k.load_balancer == "MetalLB 10.21.71.246-10.21.71.249"


# ─── merge ─────────────────────────────────────────────────────────────


def test_merge_pc_and_kubernetes_into_one_cluster() -> None:
    env = _pc_env()
    clusters, nodes = derive_from_vms(env)
    env.kubernetes_clusters, env.kubernetes_nodes = clusters, nodes
    merge_cluster(env, map_kubernetes(_k8s(), "x"))
    assert len(env.kubernetes_clusters) == 1
    k = env.kubernetes_clusters[0]
    assert k.api_source == "pc+kubernetes" and k.nkp_version == "2.18.0"
    assert k.cni == "Cilium" and k.namespace_count == 33
    assert len(env.kubernetes_nodes) == 4
    assert {n.api_source for n in env.kubernetes_nodes} == {"pc+kubernetes"}
    cp = next(n for n in env.kubernetes_nodes if n.role == "control-plane")
    assert cp.kubelet_version == "v1.35.2" and cp.status == "Ready"
    assert cp.cluster_name == "PHX-PROD-02"
    assert {n.kubernetes_cluster for n in env.kubernetes_namespaces} == {"demo-nkp"}


def test_merge_by_vm_when_the_names_differ() -> None:
    env = _pc_env()
    for vm in env.vms:
        vm.categories = {"KubernetesClusterName": ["Prod-K8s"]} if vm.categories else {}
    clusters, nodes = derive_from_vms(env)
    env.kubernetes_clusters, env.kubernetes_nodes = clusters, nodes
    merge_cluster(env, map_kubernetes(_k8s(), "x"))
    # The control plane VM uuid is the node's providerID: same cluster.
    assert [c.name for c in env.kubernetes_clusters] == ["Prod-K8s"]
    assert env.kubernetes_clusters[0].api_source == "pc+kubernetes"


def test_kubernetes_only_cluster_is_added() -> None:
    env = Environment()
    merge_cluster(env, map_kubernetes(_k8s(), "x"))
    assert [c.api_source for c in env.kubernetes_clusters] == ["kubernetes"]
    assert len(env.kubernetes_nodes) == 4


# ─── read-only guard ───────────────────────────────────────────────────


def test_guard_allows_the_reads_and_refuses_writes() -> None:
    check_request("POST", "https://pc.example.lab:9440/api/nutanix/v3/apps/list", b"{}")
    check_request("GET", "https://pc.example.lab:9440/api/nutanix/v3/apps/abc")
    check_request("GET", "https://k8s.example.lab:6443/api/v1/nodes")
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(ReadOnlyViolation):
            check_request(method, "https://k8s.example.lab:6443/api/v1/namespaces", b"{}")


# ─── plumbing: sidecar, CLI helpers, connection test ───────────────────


def test_sidecar_reads_kubernetes_blocks() -> None:
    from ntnxvision_core.sidecar import _kube_from

    pin = "AB" * 32
    creds = _kube_from([{"name": "prod", "host": "k8s.example.lab", "token": "t",
                         "insecure": True, "pinned_sha256": pin},
                        {"host": "k8s2.example.lab", "port": 16443, "token": "u"},
                        {"host": "no-token.example.lab"}], insecure=False)
    assert creds is not None and len(creds) == 2
    a, b = creds
    assert (a.name, a.port, a.verify_ssl, a.pinned_sha256) == ("prod", 6443, False,
                                                               pin.lower())
    assert (b.name, b.port, b.verify_ssl) == (None, 16443, True)
    assert _kube_from(None, False) is None and _kube_from([], False) is None
    with pytest.raises(NutanixApiError):
        _kube_from([{"host": "k8s.example.lab", "token": "t", "insecure": True}], False)


def test_cli_pairs_the_nkp_options() -> None:
    import typer

    from ntnxvision_core.__main__ import _kube_creds, _nkp_paired

    hosts = ["a.example.lab", "b.example.lab"]
    creds = _kube_creds(hosts, _nkp_paired("--nkp-port", None, 2, "6443"),
                        _nkp_paired("--nkp-name", ["one", "two"], 2),
                        _nkp_paired("--nkp-token", ["tok"], 2),
                        _nkp_paired("--nkp-sha256", ["CD" * 32, ""], 2), insecure=False)
    assert creds is not None
    assert [(c.host, c.port, c.name, c.token) for c in creds] == [
        ("a.example.lab", 6443, "one", "tok"), ("b.example.lab", 6443, "two", "tok")]
    assert creds[0].verify_ssl is False and creds[0].pinned_sha256 == ("cd" * 32)
    assert creds[1].verify_ssl is True and creds[1].pinned_sha256 is None
    with pytest.raises(typer.BadParameter):
        _nkp_paired("--nkp-token", ["a", "b", "c"], 2)


def test_connection_test_trust_flow(demo: Any) -> None:
    from ntnxvision_core.connectivity import test_connection

    base = {"mode": "kubernetes", "host": "nkp01.demo.local", "token": "demo"}
    first = test_connection({**base, "insecure": True})
    assert first["status"] == "trust_required" and first["ok"] is False
    pin = first["certificate"]["fingerprint_sha256"]
    ok = test_connection({**base, "insecure": True, "pinned_sha256": pin})
    assert ok["status"] == "ok" and ok["ok"] is True
    assert ok["message"] == "Connected to Kubernetes v1.35.2 (4 nodes)"
    assert test_connection({**base, "token": "wrong"})["status"] == "auth_failed"
    assert test_connection({**base, "token": ""})["status"] == "auth_failed"
    assert test_connection({**base, "port": None})["name"] == "nkp01.demo.local"


# ─── workloads, ingresses and applications (live capture with the apps deployed) ──


def _workload_data() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = json.loads(
        (FIXTURES / "nkp_workloads.json").read_text("utf-8"))
    return out


def test_workload_fixture_carries_no_lab_data() -> None:
    text = (FIXTURES / "nkp_workloads.json").read_text("utf-8")
    for marker in ("10.50.", "nvd", "ntnxv", "NTNXV"):
        assert marker not in text


def test_system_namespace_rule() -> None:
    from ntnxvision_core.collectors.nkp import is_system_namespace

    for ns in ("kube-system", "kube-public", "kommander", "kommander-flux",
               "kommander-default-workspace", "ntnx-system", "metallb-system",
               "cilium-secrets", "capx-system", "caren-system", "node-feature-discovery",
               "registry-system", "cert-manager", "git-operator-system",
               "kube-federation-system", "external-secrets",
               "container-object-storage-system", "capi-stack-operator"):
        assert is_system_namespace(ns), ns
    for ns in ("default", "demo-blog", "demo-shop", "demo-apps", "payments"):
        assert not is_system_namespace(ns), ns


def test_workloads_from_the_live_payloads() -> None:
    from ntnxvision_core.collectors.nkp import map_workloads

    wl = {(w.namespace, w.name): w for w in map_workloads(_workload_data(), "demo-nkp")}
    wp = wl[("demo-blog", "blog-wordpress")]
    assert (wp.kind, wp.helm_release, wp.app_name) == ("Deployment", "blog", "wordpress")
    assert wp.images == ["wordpress:6.8-apache"] and wp.status == "Ready"
    assert wp.system is False and wp.owner_app is None
    assert wl[("demo-blog", "blog-mariadb")].kind == "StatefulSet"
    # Online Boutique labels only managed-by=Helm: the release comes from the annotation.
    cart = wl[("demo-shop", "cartservice")]
    assert cart.helm_release == "shop" and cart.app_name is None
    server = wl[("demo-monitoring", "prometheus-server")]
    assert server.helm_chart == "prometheus-29.44.0" and server.app_version == "v3.16.0"
    assert len(server.images) == 2
    fluent = wl[("demo-logging", "fluent-bit")]
    assert fluent.kind == "DaemonSet" and (fluent.replicas_desired,
                                           fluent.replicas_ready) == (4, 4)
    cron = wl[("demo-data", "nightly-report")]
    assert cron.kind == "CronJob" and cron.schedule == "0 2 * * *"
    assert cron.status == "Scheduled" and cron.images == ["busybox:1.36"]
    assert wl[("demo-cache", "valkey")].helm_release is None  # no managed-by label
    web = wl[("demo-apps", "web-frontend")]
    assert (web.replicas_desired, web.replicas_ready) == (3, 3)
    # Platform workloads: flagged, and tied to their Kommander AppDeployment.
    assert wl[("kommander", "dex")].system and wl[("kommander", "dex")].owner_app == "dex"
    ui = next(w for w in wl.values() if w.helm_release == "kommander-kommander-ui")
    assert ui.owner_app == "kommander-ui"
    assert all(w.owner_app == "git-operator" for w in wl.values()
               if w.namespace == "git-operator-system")
    assert wl[("kube-system", "cilium-operator")].system
    users = {w.namespace for w in wl.values() if not w.system}
    assert users == {"demo-apps", "demo-blog", "demo-cache", "demo-data", "demo-logging",
                     "demo-monitoring", "demo-shop"}


def test_workload_status() -> None:
    from ntnxvision_core.collectors.nkp import map_workloads

    data = {"deployments": [
        {"metadata": {"name": "a", "namespace": "apps"}, "spec": {"replicas": 3},
         "status": {"readyReplicas": 1}},
        {"metadata": {"name": "b", "namespace": "apps"}, "spec": {"replicas": 0},
         "status": {}},
        {"metadata": {"name": "c", "namespace": "apps"}, "spec": {}, "status": {}}],
        "cronjobs": [{"metadata": {"name": "d", "namespace": "apps"},
                      "spec": {"schedule": "@daily", "suspend": True}}]}
    st = {w.name: w.status for w in map_workloads(data, "k")}
    assert st == {"a": "Not ready", "b": "Scaled to zero", "c": "Not ready",
                  "d": "Suspended"}


def test_ingresses_and_applications() -> None:
    data = _workload_data()
    parsed = map_kubernetes(data, "demo-nkp")
    ings = {(i.namespace, i.name): i for i in parsed["ingresses"]}
    blog = ings[("demo-blog", "blog-wordpress")]
    assert blog.hosts == ["blog.demo.example.lab"] and blog.ingress_class == "kommander-traefik"
    assert blog.backends == ["blog.demo.example.lab/ -> blog-wordpress:http"]
    assert blog.helm_release == "blog" and not blog.system
    assert blog.addresses == ["10.21.71.246"]
    assert ings[("kommander", "dex")].system and ings[("kommander", "dex")].hosts == []
    apps = {(a.namespace, a.name): a for a in parsed["applications"]}
    assert set(apps) == {("demo-apps", "demo-apps"), ("demo-blog", "blog"),
                         ("demo-cache", "demo-cache"), ("demo-data", "demo-data"),
                         ("demo-logging", "fluent-bit"), ("demo-monitoring", "prometheus"),
                         ("demo-shop", "shop")}
    shop = apps[("demo-shop", "shop")]
    assert shop.kind == "Helm release" and shop.workloads == 11
    assert shop.workload_kinds == ["11 Deployments"] and shop.addresses == ["10.21.71.248"]
    assert len(shop.images) == 11 and shop.status == "Ready"
    assert apps[("demo-blog", "blog")].ingress_hosts == ["blog.demo.example.lab"]
    assert apps[("demo-apps", "demo-apps")].ingress_hosts == ["web.demo.example.lab"]
    assert apps[("demo-apps", "demo-apps")].kind == "Namespace"
    mon = apps[("demo-monitoring", "prometheus")]
    assert mon.workloads == 4 and "prometheus-29.44.0" in (mon.helm_chart or "")
    assert apps[("demo-data", "demo-data")].workload_kinds == ["1 CronJob", "1 StatefulSet"]


def test_namespaces_carry_the_system_flag() -> None:
    spaces = {n.name: n for n in map_kubernetes(_k8s(), "x")["namespaces"]}
    assert spaces["kube-system"].system and spaces["capx-system"].system
    assert not spaces["shop-data"].system and not spaces["default"].system
