"""A synthetic NKP cluster: its traces in Prism Central and its own Kubernetes API.

The shapes are copied from a live NKP 2.18 management cluster (read with a read-only
token), trimmed and with example names and 10.21.x addresses:

* in Prism Central, the node VMs carrying ``KubernetesClusterName`` and the Self-Service
  app the Marketplace "Nutanix Kubernetes Platform" item leaves behind (plus the NKP
  bastion VM's app, which is *not* a cluster);
* the cluster's API at ``nkp01.demo.local:6443`` (`handle_kube`): nodes, namespaces,
  workloads, storage, LoadBalancer services, Cluster API, Kommander and Flux objects.

Both halves derive the node names, VM uuids and addresses from `nkp_nodes`, so a demo run
with both sources merges into one "pc+kubernetes" cluster.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ntnxvision_core.synthetic.estate import GIB, uid

NKP_VERSION = "2.18.0"
K8S_VERSION = "1.35.2"
#: The Nutanix cluster (index into the medium profile) the demo NKP cluster runs on.
NKP_CLUSTER_INDEX = 1
ENDPOINT = "10.21.40.10"
LB_RANGE = "10.21.40.20-10.21.40.29"
POD_CIDR = "192.168.0.0/16"
SERVICE_CIDR = "10.96.0.0/12"
STORAGE_CONTAINER = "default-container-21004"


@dataclass(frozen=True)
class NkpNode:
    name: str
    vm_uuid: str
    ip: str
    role: str  # control-plane | worker
    vcpus: int
    memory_gib: int


def nkp_nodes(seed: str, cluster: str) -> list[NkpNode]:
    """The node VMs of demo NKP cluster `cluster`: one control plane, three workers."""
    nodes = [NkpNode(f"{cluster}-x7k2p-q9vtl", uid(seed, "vm", cluster, "cp", 0),
                     "10.21.40.101", "control-plane", 4, 16)]
    for i, suffix in enumerate(("4j4p4", "5b5g9", "wdg9q")):
        nodes.append(NkpNode(f"{cluster}-md-0-c62sk-7dqrp-{suffix}",
                             uid(seed, "vm", cluster, "md-0", i), f"10.21.40.{102 + i}",
                             "worker", 8, 32))
    return nodes


# ─── Prism Central side ────────────────────────────────────────────────


def add_nkp_vms(est: Any, cluster: str) -> None:
    """Append `cluster`'s node VMs to a PC estate (they reuse a workload VM as template)."""
    host_cluster = est.clusters[min(NKP_CLUSTER_INDEX, len(est.clusters) - 1)]
    template = next(v for v in est.vms if v["cluster_uuid"] == host_cluster["uuid"]
                    and v["hypervisor"] == "AHV" and v["nics"][0]["subnet_uuid"])
    est.categories.append({"uuid": uid(est.seed, "category", "KubernetesClusterName", cluster),
                           "key": "KubernetesClusterName", "value": cluster,
                           "description": f"NKP cluster {cluster}"})
    for node in nkp_nodes(est.seed, cluster):
        disk = {**template["disks"][0], "uuid": uid(node.vm_uuid, "disk"), "size": 80 * GIB,
                "image": None, "index": 0}
        nic = {**template["nics"][0], "uuid": uid(node.vm_uuid, "nic"),
               "mac": "50:6b:8d:" + node.vm_uuid[:6][0:2] + ":" + node.vm_uuid[2:4] + ":"
               + node.vm_uuid[4:6], "ip": node.ip, "connected": True,
               # kube-vip holds the API endpoint on the control plane as a second address.
               "extra_ips": [ENDPOINT] if node.role == "control-plane" else []}
        est.vms.append({
            **template, "uuid": node.vm_uuid, "name": node.name, "power": "ON",
            "sockets": node.vcpus, "cores": 1, "threads": 1, "vnuma": 0,
            "memory": node.memory_gib * GIB, "guest_os": "Rocky Linux 9",
            "guest_os_id": "rhel9_64Guest", "description": "",
            "categories": {"KubernetesClusterName": [cluster]},
            "protection_type": "UNPROTECTED", "disks": [disk], "cdroms": 0, "nics": [nic],
            "boot": "UEFI", "secure_boot": False, "gpus": [], "esxi_bad": False})
    est.nkp_apps = nkp_apps(est.seed, cluster, host_cluster["name"])


def _var(name: str, value: str) -> dict[str, Any]:
    return {"type": "LOCAL", "name": name, "value": value, "val_type": "STRING",
            "is_hidden": False, "data_type": "BASE"}


def nkp_apps(seed: str, cluster: str, pe_name: str) -> list[dict[str, Any]]:
    """The Self-Service app records: the NKP cluster's, then its bastion VM's."""
    substrate = [
        _var("nkp_control_plane_prism_element_name", pe_name),
        _var("nkp_cluster_name", cluster),
        _var("kubernetes_service_load_balancer_ip_range", LB_RANGE),
        _var("control_plane_endpoint_ip", ENDPOINT),
        _var("control_plane_endpoint_port", "6443"),
        _var("kubernetes_pod_network_cidr", POD_CIDR),
        _var("kubernetes_service_cidr", SERVICE_CIDR),
        _var("control_plane_replicas", "1"),
        _var("control_plane_node_vcpus", "4"),
        _var("control_plane_node_memory", "16"),
        _var("worker_replicas", "3"),
        _var("worker_node_vcpus", "8"),
        _var("worker_node_memory", "32"),
        _var("nkp_version", NKP_VERSION),
        _var("kubernetes_version", K8S_VERSION),
    ]
    services = [_var("nkp_version", f"v{NKP_VERSION}"),
                _var("kubernetes_version", f"v{K8S_VERSION}")]
    nkp_uuid = uid(seed, "app", cluster)
    blueprint = {"kind": "blueprint", "name": "Nutanix Kubernetes Platform 3f1a9c2e",
                 "uuid": uid(seed, "blueprint", "nkp")}

    def deployment(substrate_key: str) -> dict[str, Any]:
        return {"type": "GREENFIELD", "name": "NkpDeployment",
                "uuid": uid(seed, "app-deployment", cluster),
                substrate_key: {"name": "NkpSubstrate", "variable_list": substrate},
                "service_list": [{"name": "NkpService", "variable_list": services}]}

    nkp = {
        "api_version": "3.0",
        "metadata": {"uuid": nkp_uuid, "name": cluster, "kind": "app",
                     "categories": {"AppFamily": "Nutanix"}},
        "status": {"name": cluster, "state": "running", "uuid": nkp_uuid,
                   "resources": {"source_marketplace_name": "Nutanix Kubernetes Platform",
                                 "source_marketplace_version": "1.2.0",
                                 "app_blueprint_reference": blueprint,
                                 "variable_list": [], "action_list": [],
                                 "deployment_list": [deployment("substrate_configuration")]}},
        "spec": {"name": cluster, "resources": {
            "deployment_list": [deployment("substrate")]}},
    }
    bastion_uuid = uid(seed, "app", cluster, "bastion")
    bastion = {
        "api_version": "3.0",
        "metadata": {"uuid": bastion_uuid, "name": f"{cluster}-bastion-host", "kind": "app"},
        "status": {"name": f"{cluster}-bastion-host", "state": "running",
                   "uuid": bastion_uuid,
                   "resources": {"source_marketplace_name": "NKP_VM",
                                 "app_blueprint_reference": {
                                     "kind": "blueprint", "name": "Mpi-Bp-NKP_VM-8d21e04b7c",
                                     "uuid": uid(seed, "blueprint", "bastion")},
                                 "variable_list": [
                                     _var("image_name", f"nkp-bastion-v{NKP_VERSION}")],
                                 "deployment_list": []}},
        "spec": {"name": f"{cluster}-bastion-host", "resources": {}},
    }
    return [nkp, bastion]


def app_list_entry(app: dict[str, Any]) -> dict[str, Any]:
    """An ``apps/list`` entity: a summary, without the deployments and their variables."""
    res = app["status"]["resources"]
    return {"metadata": app["metadata"],
            "status": {"name": app["status"]["name"], "state": app["status"]["state"],
                       "uuid": app["status"]["uuid"],
                       "resources": {"app_blueprint_reference": res["app_blueprint_reference"],
                                     "source_marketplace_name":
                                         res.get("source_marketplace_name")}}}


# ─── Kubernetes API side ───────────────────────────────────────────────


@dataclass
class KubeEstate:
    host: str
    cluster: str
    lists: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: Paths that answer 403 (the read-only role does not cover them).
    forbidden: set[str] = field(default_factory=set)


_TS = "2026-10-01T09:00:00Z"


def _obj(api: str, kind: str, name: str, namespace: str | None = None,
         labels: dict[str, str] | None = None, annotations: dict[str, str] | None = None,
         **body: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {"name": name, "uid": uid("k8s", kind, namespace or "", name),
                            "creationTimestamp": _TS, "resourceVersion": "1000"}
    if namespace is not None:
        meta["namespace"] = namespace
    if labels:
        meta["labels"] = labels
    if annotations:
        meta["annotations"] = annotations
    return {"apiVersion": api, "kind": kind, "metadata": meta, **body}


#: namespace -> (pods, deployments) of the demo cluster's platform and apps.
_NAMESPACES: dict[str, tuple[int, int]] = {
    "caaph-system": (1, 1), "capi-kubeadm-bootstrap-system": (1, 1),
    "capi-kubeadm-control-plane-system": (1, 1), "capi-system": (1, 1),
    "capx-system": (1, 1), "caren-system": (2, 2), "cert-manager": (3, 3),
    "default": (0, 0), "external-secrets": (2, 2), "git-operator-system": (2, 1),
    "kommander": (35, 31), "kommander-default-workspace": (2, 2), "kommander-flux": (7, 7),
    "kube-federation-system": (3, 2), "kube-node-lease": (0, 0), "kube-public": (0, 0),
    "kube-system": (25, 5), "metallb-system": (5, 1), "node-feature-discovery": (6, 2),
    "ntnx-system": (7, 2), "registry-system": (2, 0),
    # User namespaces: their workloads are `_USER_WORKLOADS` (pods follow from them).
    "shop-apps": (0, 0), "shop-data": (0, 0), "demo-blog": (0, 0), "demo-shop": (0, 0),
    "demo-cache": (0, 0), "demo-monitoring": (0, 0), "demo-logging": (0, 0),
}

_BOUTIQUE = "us-central1-docker.pkg.dev/online-boutique-ci/microservices-demo"
#: The user applications: (namespace, kind, name, Helm release, chart, app, version,
#: images, replicas). Shapes as on the live cluster: Online Boutique labels only
#: ``managed-by``, the blog chart sets no ``helm.sh/chart``.
_USER_WORKLOADS: list[tuple[str, str, str, str | None, str | None, str | None,
                            str | None, tuple[str, ...], int]] = [
    ("shop-apps", "Deployment", "web-frontend", None, None, None, None,
     ("nginx:1.27-alpine",), 3),
    ("shop-data", "StatefulSet", "orders-db", None, None, None, None,
     ("postgres:16-alpine",), 1),
    ("shop-data", "CronJob", "nightly-report", None, None, None, None, ("busybox:1.36",), 0),
    ("demo-blog", "Deployment", "blog-wordpress", "blog", None, "wordpress", None,
     ("wordpress:6.8-apache",), 1),
    ("demo-blog", "StatefulSet", "blog-mariadb", "blog", None, "mariadb", None,
     ("mariadb:11.4",), 1),
    *(("demo-shop", "Deployment", svc, "shop", None, None, None,
       (f"{_BOUTIQUE}/{svc}:v0.10.7",), 1)
      for svc in ("adservice", "cartservice", "checkoutservice", "currencyservice",
                  "emailservice", "frontend", "paymentservice", "productcatalogservice",
                  "recommendationservice", "shippingservice")),
    ("demo-shop", "Deployment", "redis-cart", "shop", None, None, None,
     ("redis:alpine",), 1),
    ("demo-cache", "StatefulSet", "valkey", None, None, "valkey", None,
     ("valkey/valkey:8.1-alpine",), 1),
    ("demo-monitoring", "Deployment", "prometheus-server", "prometheus",
     "prometheus-29.44.0", "prometheus", "v3.16.0",
     ("quay.io/prometheus-operator/prometheus-config-reloader:v0.95.1",
      "quay.io/prometheus/prometheus:v3.16.0"), 1),
    ("demo-monitoring", "Deployment", "prometheus-kube-state-metrics", "prometheus",
     "kube-state-metrics-9.0.0", "kube-state-metrics", "2.20.0",
     ("registry.k8s.io/kube-state-metrics/kube-state-metrics:v2.20.0",), 1),
    ("demo-monitoring", "StatefulSet", "prometheus-alertmanager", "prometheus",
     "alertmanager-2.3.0", "alertmanager", "v0.34.2",
     ("quay.io/prometheus/alertmanager:v0.34.2",), 1),
    ("demo-monitoring", "DaemonSet", "prometheus-prometheus-node-exporter", "prometheus",
     "prometheus-node-exporter-4.60.1", "prometheus-node-exporter", "1.12.1",
     ("quay.io/prometheus/node-exporter:v1.12.1",), 4),
    ("demo-logging", "DaemonSet", "fluent-bit", "fluent-bit", "fluent-bit-0.58.3",
     "fluent-bit", "5.1.3", ("cr.fluentbit.io/fluent/fluent-bit:5.1.3",), 4),
]
#: (namespace, name, host, backend service, Helm release)
_INGRESSES = [("shop-apps", "web-frontend", "web.shop.example.lab", "web-frontend", None),
              ("demo-blog", "blog-wordpress", "blog.shop.example.lab", "blog-wordpress",
               "blog"),
              ("kommander", "kommander-kommander-ui", None, "kommander", "kommander-kommander-ui"),
              ("kommander", "dex", None, "dex", "dex")]
_DAEMONSETS = [("kube-system", "cilium"), ("kube-system", "cilium-envoy"),
               ("kube-system", "multus"), ("metallb-system", "metallb-speaker"),
               ("node-feature-discovery", "node-feature-discovery-worker"),
               ("ntnx-system", "nutanix-csi-node")]
_STATEFULSETS = [("git-operator-system", "git-operator-git"),
                 ("registry-system", "cncf-distribution-registry-docker-registry")]
#: (namespace, claim, size GiB, storage class)
_CLAIMS = [("caren-system", "helm-charts-pvc", 2, "nutanix-volume"),
           ("git-operator-system", "git-operator-admin-volume", 2, "nutanix-volume"),
           ("git-operator-system", "git-operator-git-volume", 10, "nutanix-volume"),
           ("shop-data", "data-orders-db-0", 5, "nutanix-volume"),
           ("shop-data", "reports-archive", 2, "nutanix-volume-retain"),
           ("registry-system", "data-cncf-distribution-registry-docker-registry-0", 100,
            "nutanix-volume"),
           ("registry-system", "data-cncf-distribution-registry-docker-registry-1", 100,
            "nutanix-volume")]
#: Kommander AppDeployments in the "kommander" workspace: (name, ClusterApp version).
_APPS = [("cert-manager", "1.19.3"), ("dex", "2.14.5"), ("external-secrets", "2.3.0"),
         ("gatekeeper", "3.22.0"), ("git-operator", "0.13.18"), ("kommander", "0.18.0"),
         ("kommander-appmanagement", "0.18.0"), ("kommander-flux", "2.8.5"),
         ("kommander-ui", "17.234.30"), ("kube-oidc-proxy", "0.3.8"),
         ("reloader", "2.2.11"), ("traefik", "39.0.7")]


def build_kube_estate(host: str, seed: str, cluster: str, pe_name: str) -> KubeEstate:
    """The demo NKP management cluster as its Kubernetes API serves it."""
    est = KubeEstate(host=host, cluster=cluster)
    nodes = nkp_nodes(seed, cluster)
    lists = est.lists
    lists["nodes"] = [_node(n, cluster, pe_name) for n in nodes]
    lists["namespaces"] = [_obj("v1", "Namespace", ns, status={"phase": "Active"})
                           for ns in _NAMESPACES]
    lists["pods"] = [_obj("v1", "Pod", f"{ns}-pod-{i}", ns, status={"phase": "Running"})
                     for ns, (pods, _) in _NAMESPACES.items() for i in range(pods)]
    lists["deployments"] = [_obj("apps/v1", "Deployment", f"{ns}-deploy-{i}", ns,
                                 spec={"replicas": 1, "template": _pod(
                                     f"docker.io/mesosphere/{ns}-controller:v{NKP_VERSION}")},
                                 status={"readyReplicas": 1})
                            for ns, (_, deps) in _NAMESPACES.items() for i in range(deps)]
    lists["statefulsets"] = [_obj("apps/v1", "StatefulSet", n, ns, spec={
        "replicas": 1, "template": _pod(f"docker.io/mesosphere/{n}:v{NKP_VERSION}")},
        status={"readyReplicas": 1}) for ns, n in _STATEFULSETS]
    lists["daemonsets"] = [_obj("apps/v1", "DaemonSet", n, ns,
                                spec={"template": _pod(f"quay.io/{ns}/{n}:v1.18.4")},
                                status={"desiredNumberScheduled": 4, "numberReady": 4})
                           for ns, n in _DAEMONSETS]
    lists["cronjobs"] = [_obj("batch/v1", "CronJob", "hubble-generate-certs", "kube-system",
                              spec={"schedule": "0 0 1 * *", "suspend": False,
                                    "jobTemplate": {"spec": {"template": _pod(
                                        "quay.io/cilium/certgen:v0.2.4")}}})]
    _user_workloads(lists)
    services = [_obj("v1", "Service", f"{ns}-svc", ns,
                     spec={"type": "ClusterIP", "ports": [{"port": 443, "protocol": "TCP"}]})
                for ns in _NAMESPACES if ns not in ("kube-node-lease", "kube-public")]
    services += [
        _obj("v1", "Service", "kommander-traefik", "kommander",
             spec={"type": "LoadBalancer", "ports": [
                 {"name": "web", "port": 80, "protocol": "TCP"},
                 {"name": "websecure", "port": 443, "protocol": "TCP"}]},
             status={"loadBalancer": {"ingress": [{"ip": "10.21.40.20", "ipMode": "VIP"}]}}),
        _obj("v1", "Service", "web-frontend", "shop-apps",
             spec={"type": "LoadBalancer", "loadBalancerIP": "10.21.40.21",
                   "ports": [{"name": "http", "port": 80, "protocol": "TCP"}]},
             status={"loadBalancer": {"ingress": [{"ip": "10.21.40.21", "ipMode": "VIP"}]}}),
    ]
    services.append(_obj(
        "v1", "Service", "frontend-external", "demo-shop",
        annotations={"meta.helm.sh/release-name": "shop"},
        labels={"app.kubernetes.io/managed-by": "Helm"},
        spec={"type": "LoadBalancer", "ports": [{"name": "http", "port": 80,
                                                 "protocol": "TCP"}]},
        status={"loadBalancer": {"ingress": [{"ip": "10.21.40.22", "ipMode": "VIP"}]}}))
    lists["services"] = services
    lists["ingresses"] = [_obj(
        "networking.k8s.io/v1", "Ingress", name, ns,
        annotations={"meta.helm.sh/release-name": release} if release else None,
        spec={"ingressClassName": "kommander-traefik", "rules": [{
            **({"host": host} if host else {}),
            "http": {"paths": [{"path": "/", "pathType": "Prefix", "backend": {
                "service": {"name": svc, "port": {"number": 80}}}}]}}]},
        status={"loadBalancer": {"ingress": [{"ip": "10.21.40.20"}]}})
        for ns, name, host, svc, release in _INGRESSES]
    lists["pvcs"], lists["pvs"] = [], []
    for ns, claim, size, sc in _CLAIMS:
        pv = f"pvc-{uid(seed, 'pv', ns, claim)}"
        lists["pvcs"].append(_obj("v1", "PersistentVolumeClaim", claim, ns,
                                  spec={"volumeName": pv, "storageClassName": sc},
                                  status={"phase": "Bound",
                                          "capacity": {"storage": f"{size}Gi"}}))
        lists["pvs"].append(_obj("v1", "PersistentVolume", pv, spec={
            "capacity": {"storage": f"{size}Gi"}, "storageClassName": sc,
            "accessModes": ["ReadWriteOnce"],
            "persistentVolumeReclaimPolicy": "Retain" if sc.endswith("retain") else "Delete",
            "claimRef": {"kind": "PersistentVolumeClaim", "namespace": ns, "name": claim},
            "csi": {"driver": "csi.nutanix.com"}}, status={"phase": "Bound"}))
    lists["storageclasses"] = [
        _storage_class("nutanix-volume", "Delete", "ext4", default=True),
        _storage_class("nutanix-volume-retain", "Retain", "xfs", default=False)]
    lists["capi_clusters"] = [_capi_cluster(cluster, pe_name)]
    lists["machines"] = [_machine(n, cluster) for n in nodes]
    lists["nutanixclusters"] = [_obj(
        "infrastructure.cluster.x-k8s.io/v1beta1", "NutanixCluster", f"{cluster}-bntf7",
        "kommander", spec={"controlPlaneEndpoint": {"host": ENDPOINT, "port": 6443},
                           "prismCentral": {"address": "pc01.demo.local", "port": 9440,
                                            "insecure": False}})]
    lists["kommanderclusters"] = [_obj(
        "kommander.mesosphere.io/v1beta1", "KommanderCluster", cluster, "kommander",
        labels={"kommander.d2iq.io/host": "true", "kommander.d2iq.io/cluster-name": cluster},
        spec={"clusterRef": {"capiCluster": {"name": cluster, "namespace": "kommander"}},
              "platform": {"version": f"v{NKP_VERSION}"}},
        status={"ingress": {"address": "10.21.40.20"}})]
    lists["licenses"] = [_obj(
        "kommander.mesosphere.io/v1beta1", "License", "nutanix-license", "kommander",
        spec={}, status={"dkpLevel": "Starter", "productName": "NKP", "valid": True,
                         "clusterCapacity": 0, "coreCapacity": 0})]
    lists["appdeployments"] = [_obj(
        "apps.kommander.d2iq.io/v1alpha3", "AppDeployment", name, "kommander",
        spec={"appRef": {"kind": "ClusterApp", "name": f"{name}-{version}"}},
        status={"clusters": [{"name": cluster, "conditions": [
            {"type": "AppDeploymentInstanceSynced", "status": "True",
             "reason": "Succeeded"}]}]}) for name, version in _APPS]
    releases = [_helm_release(name, version) for name, version in _APPS]
    releases.append(_helm_release("kommander-operator", "0.4.0", managed=False))
    for hr in releases:
        if hr["metadata"]["name"] == "kommander":
            hr["status"]["lastAttemptedRevision"] = f"v{NKP_VERSION}"
    lists["helmreleases"] = releases
    lists["ipaddresspools"] = [_obj("metallb.io/v1beta1", "IPAddressPool", "metallb",
                                    "metallb-system",
                                    spec={"addresses": [LB_RANGE], "autoAssign": True})]
    return est


def _pod(*images: str) -> dict[str, Any]:
    return {"spec": {"containers": [{"name": image.rsplit("/", 1)[-1].split(":")[0],
                                     "image": image} for image in images]}}


def _user_workloads(lists: dict[str, list[dict[str, Any]]]) -> None:
    """Append `_USER_WORKLOADS` (and their pods) in the live cluster's shapes."""
    key = {"Deployment": "deployments", "StatefulSet": "statefulsets",
           "DaemonSet": "daemonsets", "CronJob": "cronjobs"}
    for ns, kind, name, release, chart, app, version, images, replicas in _USER_WORKLOADS:
        labels: dict[str, str] = {}
        if release:
            labels["app.kubernetes.io/managed-by"] = "Helm"
            if app:
                labels["app.kubernetes.io/instance"] = release
        if app:
            labels["app.kubernetes.io/name"] = app
        if version:
            labels["app.kubernetes.io/version"] = version
        if chart:
            labels["helm.sh/chart"] = chart
        annotations = {"meta.helm.sh/release-name": release,
                       "meta.helm.sh/release-namespace": ns} if release else None
        pod = _pod(*images)
        api = "batch/v1" if kind == "CronJob" else "apps/v1"
        if kind == "CronJob":
            spec: dict[str, Any] = {"schedule": "0 2 * * *", "suspend": False,
                                    "jobTemplate": {"spec": {"template": pod}}}
            status: dict[str, Any] = {}
        elif kind == "DaemonSet":
            spec = {"template": pod}
            status = {"desiredNumberScheduled": replicas, "numberReady": replicas}
        else:
            spec = {"replicas": replicas, "template": pod}
            status = {"replicas": replicas, "readyReplicas": replicas}
        lists[key[kind]].append(_obj(api, kind, name, ns, labels=labels or None,
                                     annotations=annotations, spec=spec, status=status))
        lists["pods"].extend(_obj("v1", "Pod", f"{name}-{i}", ns, status={"phase": "Running"})
                             for i in range(max(replicas, 0)))


def _node(n: NkpNode, cluster: str, pe_name: str) -> dict[str, Any]:
    labels = {"kubernetes.io/hostname": n.name, "kubernetes.io/os": "linux",
              "node.kubernetes.io/instance-type": "ahv-vm",
              "topology.kubernetes.io/zone": pe_name}
    if n.role == "control-plane":
        labels["node-role.kubernetes.io/control-plane"] = ""
    owner = ("KubeadmControlPlane", f"{cluster}-x7k2p") if n.role == "control-plane" \
        else ("MachineSet", f"{cluster}-md-0-c62sk-7dqrp")
    kib = n.memory_gib * 1024 * 1024 - 400_000
    return _obj("v1", "Node", n.name, labels=labels, annotations={
        "cluster.x-k8s.io/cluster-name": cluster,
        "cluster.x-k8s.io/cluster-namespace": "kommander",
        "cluster.x-k8s.io/machine": n.name,
        "cluster.x-k8s.io/owner-kind": owner[0], "cluster.x-k8s.io/owner-name": owner[1]},
        spec={"providerID": f"nutanix://{n.vm_uuid}", "podCIDR": "192.168.0.0/24",
              **({"taints": [{"key": "node-role.kubernetes.io/control-plane",
                              "effect": "NoSchedule"}]} if n.role == "control-plane" else {})},
        status={"capacity": {"cpu": str(n.vcpus), "memory": f"{kib}Ki", "pods": "110",
                             "ephemeral-storage": "82691052Ki"},
                "addresses": [{"type": "InternalIP", "address": n.ip},
                              {"type": "Hostname", "address": n.name}],
                "nodeInfo": {"kernelVersion": "5.14.0-611.55.1.el9_7.x86_64",
                             "osImage": "Rocky Linux 9.7 (Blue Onyx)",
                             "containerRuntimeVersion": "containerd://2.1.6-d2iq.1",
                             "kubeletVersion": f"v{K8S_VERSION}", "kubeProxyVersion": "",
                             "operatingSystem": "linux", "architecture": "amd64",
                             "systemUUID": n.vm_uuid},
                "conditions": [{"type": "MemoryPressure", "status": "False"},
                               {"type": "DiskPressure", "status": "False"},
                               {"type": "Ready", "status": "True"}]})


def _storage_class(name: str, reclaim: str, fstype: str, default: bool) -> dict[str, Any]:
    return _obj("storage.k8s.io/v1", "StorageClass", name, annotations={
        "storageclass.kubernetes.io/is-default-class": "true"} if default else None,
        provisioner="csi.nutanix.com", reclaimPolicy=reclaim,
        volumeBindingMode="WaitForFirstConsumer", allowVolumeExpansion=True,
        parameters={"csi.storage.k8s.io/fstype": fstype, "storageType": "NutanixVolumes",
                    "storageContainer": STORAGE_CONTAINER, "flashMode": "DISABLED",
                    "hypervisorAttached": "ENABLED"})


def _capi_cluster(cluster: str, pe_name: str) -> dict[str, Any]:
    details = {"bootType": "uefi", "cluster": {"name": pe_name, "type": "name"},
               "image": {"name": f"nkp-rocky-9.7-release-cis-{K8S_VERSION}", "type": "name"},
               "memorySize": "16Gi", "subnets": [{"name": "nkp-nodes", "type": "name"}],
               "systemDiskSize": "80Gi", "vcpuSockets": 4, "vcpusPerSocket": 1}
    config = {
        "addons": {
            "cni": {"provider": "Cilium", "strategy": "HelmAddon"},
            "csi": {"defaultStorage": {"provider": "nutanix", "storageClassConfig": "volume"}},
            "serviceLoadBalancer": {"provider": "MetalLB", "configuration": {
                "addressRanges": [{"start": LB_RANGE.split("-")[0],
                                   "end": LB_RANGE.split("-")[1]}]}},
        },
        "controlPlane": {"nutanix": {"machineDetails": details}},
        "nutanix": {"controlPlaneEndpoint": {"host": ENDPOINT, "port": 6443,
                                             "virtualIP": {"provider": "KubeVIP"}}},
    }
    return _obj(
        "cluster.x-k8s.io/v1beta1", "Cluster", cluster, "kommander",
        labels={"cluster.x-k8s.io/cluster-name": cluster, "cluster.x-k8s.io/provider": "nutanix",
                "kommander.d2iq.io/host": "true"},
        spec={"clusterNetwork": {"pods": {"cidrBlocks": [POD_CIDR]},
                                 "services": {"cidrBlocks": [SERVICE_CIDR]}},
              "controlPlaneEndpoint": {"host": ENDPOINT, "port": 6443},
              "infrastructureRef": {"kind": "NutanixCluster", "name": f"{cluster}-bntf7",
                                    "namespace": "kommander"},
              "topology": {"class": f"nkp-nutanix-v{NKP_VERSION}", "version": f"v{K8S_VERSION}",
                           "controlPlane": {"replicas": 1},
                           "workers": {"machineDeployments": [
                               {"class": "default-worker", "name": "md-0", "replicas": 3}]},
                           "variables": [{"name": "clusterConfig", "value": config}]}},
        status={"phase": "Provisioned", "controlPlaneReady": True,
                "infrastructureReady": True})


def _machine(n: NkpNode, cluster: str) -> dict[str, Any]:
    labels = {"cluster.x-k8s.io/cluster-name": cluster}
    if n.role == "control-plane":
        labels["cluster.x-k8s.io/control-plane"] = ""
    else:
        labels["topology.cluster.x-k8s.io/deployment-name"] = "md-0"
    return _obj("cluster.x-k8s.io/v1beta1", "Machine", n.name, "kommander", labels=labels,
                spec={"clusterName": cluster, "providerID": f"nutanix://{n.vm_uuid}",
                      "version": f"v{K8S_VERSION}"},
                status={"phase": "Running", "nodeRef": {"kind": "Node", "name": n.name}})


def _helm_release(name: str, version: str, managed: bool = True) -> dict[str, Any]:
    return _obj("helm.toolkit.fluxcd.io/v2", "HelmRelease", name, "kommander",
                labels={"kommander.d2iq.io/managed-by-kind": "AppDeployment"} if managed
                else None,
                spec={"chartRef": {"kind": "OCIRepository", "name": f"{name}-{version}-chart"},
                      "interval": "15s"},
                status={"lastAttemptedRevision": version,
                        "conditions": [{"type": "Ready", "status": "True"},
                                       {"type": "Released", "status": "True"}]})


#: API path -> list key (`KubeEstate.lists`).
PATHS: dict[str, str] = {
    "/api/v1/nodes": "nodes",
    "/api/v1/namespaces": "namespaces",
    "/api/v1/pods": "pods",
    "/api/v1/services": "services",
    "/api/v1/persistentvolumes": "pvs",
    "/api/v1/persistentvolumeclaims": "pvcs",
    "/apis/storage.k8s.io/v1/storageclasses": "storageclasses",
    "/apis/apps/v1/deployments": "deployments",
    "/apis/apps/v1/statefulsets": "statefulsets",
    "/apis/apps/v1/daemonsets": "daemonsets",
    "/apis/batch/v1/cronjobs": "cronjobs",
    "/apis/networking.k8s.io/v1/ingresses": "ingresses",
    "/apis/cluster.x-k8s.io/v1beta1/clusters": "capi_clusters",
    "/apis/cluster.x-k8s.io/v1beta1/machines": "machines",
    "/apis/infrastructure.cluster.x-k8s.io/v1beta1/nutanixclusters": "nutanixclusters",
    "/apis/kommander.mesosphere.io/v1beta1/kommanderclusters": "kommanderclusters",
    "/apis/kommander.mesosphere.io/v1beta1/licenses": "licenses",
    "/apis/apps.kommander.d2iq.io/v1alpha3/appdeployments": "appdeployments",
    "/apis/helm.toolkit.fluxcd.io/v2/helmreleases": "helmreleases",
    "/apis/metallb.io/v1beta1/ipaddresspools": "ipaddresspools",
}

Response = tuple[int, Any, dict[str, str]]


def _status(code: int, reason: str, message: str) -> Response:
    return code, {"kind": "Status", "apiVersion": "v1", "metadata": {}, "status": "Failure",
                  "message": message, "reason": reason, "code": code}, {}


def handle_kube(est: KubeEstate, method: str, path: str, query: dict[str, str],
                authorization: str | None) -> Response:
    """The Kubernetes API of the demo cluster: GET only, bearer token (any but "wrong")."""
    if path == "/version":
        return 200, {"major": "1", "minor": "35", "gitVersion": f"v{K8S_VERSION}",
                     "platform": "linux/amd64"}, {}
    token = (authorization or "")[7:] if (authorization or "").lower().startswith(
        "bearer ") else ""
    if not token or token == "wrong":
        return _status(401, "Unauthorized", "Unauthorized")
    if method != "GET":
        return _status(405, "MethodNotAllowed", "the server does not allow this method")
    if path in est.forbidden:
        return _status(403, "Forbidden", f"cannot list resource at {path}")
    key = PATHS.get(path)
    if key is None:
        return _status(404, "NotFound", "the server could not find the requested resource")
    items = est.lists.get(key, [])
    limit = int(query.get("limit") or 0) or len(items) or 1
    start = int(query.get("continue") or 0)
    page = items[start:start + limit]
    meta: dict[str, Any] = {"resourceVersion": "1000"}
    if start + limit < len(items):
        meta["continue"] = str(start + limit)
    kind = (page[0]["kind"] if page else "Item") + "List"
    return 200, {"kind": kind, "apiVersion": "v1", "metadata": meta, "items": page}, {}
