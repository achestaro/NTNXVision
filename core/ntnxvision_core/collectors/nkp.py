"""NKP (Nutanix Kubernetes Platform) clusters, from two sources.

**Prism Central view** (`NkpCollector`, the ``nkp`` area, always on). Prism Central has no
Kubernetes API, but NKP leaves two traces in it:

* its node VMs carry the category ``KubernetesClusterName: <cluster>`` (already read by
  the VM collector, so this costs no request). Workers are named
  ``<cluster>-md-<n>-...`` (pool ``md-<n>``); the other VMs are control-plane. The
  control-plane VM also holds the API endpoint (a kube-vip virtual IP) as a second IP;
* a Self-Service (Marketplace) app "Nutanix Kubernetes Platform", when the cluster was
  deployed from the Marketplace (``POST apps/list`` then ``GET apps/<uuid>``). Its
  variables (``nkp_version``, ``kubernetes_version``, ``control_plane_endpoint_ip`` …)
  sit in the deployment's substrate (``status.resources.deployment_list[].
  substrate_configuration.variable_list`` and ``spec.resources.deployment_list[].
  substrate.variable_list``) and its services. A cluster made with the CLI has no app, so
  the app is enrichment only, read tolerantly (values can be empty mid-deploy).

**Kubernetes API** (`collect_kubernetes`, optional like NDB): one cluster's own API server,
read with a (read-only) bearer token. Every list is tolerant: a resource type that is not
served (a CRD that is not installed) or that the token may not read is skipped with a
note, never failing the run.

Both sources merge per cluster name (`merge_cluster`): one `KubernetesCluster` with
``api_source`` "pc", "kubernetes" or "pc+kubernetes", and nodes merged by name.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

from ntnxvision_core.api.errors import AuthenticationError, NutanixApiError
from ntnxvision_core.api.kube_client import KubeConnection, KubeForbidden, KubeNotFound
from ntnxvision_core.cancel import RunCancelled
from ntnxvision_core.collectors.base import Collector
from ntnxvision_core.models import (
    KubernetesApp,
    KubernetesApplication,
    KubernetesCluster,
    KubernetesIngress,
    KubernetesNamespace,
    KubernetesNode,
    KubernetesService,
    KubernetesStorageClass,
    KubernetesVolume,
    KubernetesWorkload,
)
from ntnxvision_core.numfmt import fmt_count

#: The category NKP puts on every node VM.
CLUSTER_CATEGORY = "KubernetesClusterName"
#: The Marketplace item an NKP cluster is deployed from.
NKP_MARKETPLACE_NAME = "Nutanix Kubernetes Platform"
MARKETPLACE = "Marketplace (Self-Service)"
#: At most this many Self-Service apps are read in detail (apps are large records).
MAX_APP_DETAILS = 25

#: The app variables read, by name.
_APP_VARIABLES = (
    "nkp_cluster_name", "nkp_version", "kubernetes_version", "control_plane_endpoint_ip",
    "control_plane_endpoint_port", "worker_replicas", "control_plane_replicas",
    "kubernetes_pod_network_cidr", "kubernetes_service_cidr",
    "kubernetes_service_load_balancer_ip_range", "nkp_control_plane_prism_element_name",
)

# ─── Prism Central view ────────────────────────────────────────────────


class NkpCollector(Collector):
    name = "nkp"
    label = "NKP clusters (Prism Central view)"

    def collect(self, env: Any) -> None:
        clusters, nodes = derive_from_vms(env)
        notes: list[str] = []
        try:
            apps = self._nkp_apps({c.name for c in clusters})
        except RunCancelled:
            raise
        except NutanixApiError as exc:
            apps = []
            # Self-Service not enabled answers 404: nothing to say about it.
            if exc.status_code != 404:
                notes.append(f"Self-Service apps not read ({exc})")
        by_name = {c.name.lower(): c for c in clusters}
        for app in apps:
            values = app_variables(app)
            name = values.get("nkp_cluster_name") or _app_name(app)
            if not name:
                continue
            cluster = by_name.get(name.lower())
            if cluster is None:
                # Deployed (or deploying) from the Marketplace, no node VM seen yet.
                cluster = KubernetesCluster(name=name)
                clusters.append(cluster)
                by_name[name.lower()] = cluster
            apply_app(cluster, app, values, nodes)
        env.kubernetes_clusters.extend(clusters)
        env.kubernetes_nodes.extend(nodes)
        if not clusters:
            env.collection_notes[self.name] = "skipped: no NKP clusters found"
            return
        env.collection_notes[self.name] = f"ok: {'; '.join(notes)}" if notes else "ok"

    def _nkp_apps(self, cluster_names: set[str]) -> list[dict[str, Any]]:
        """The Self-Service app records of NKP clusters (detail GETs, at most a few)."""
        entities = self.conn.v3_list("apps")
        wanted = {n.lower() for n in cluster_names}
        out: list[dict[str, Any]] = []
        for entity in entities:
            status = entity.get("status") or {}
            if str(status.get("state") or "").lower() == "deleted":
                continue
            uuid = (entity.get("metadata") or {}).get("uuid")
            if not uuid or not _maybe_nkp_app(entity, wanted):
                continue
            if len(out) >= MAX_APP_DETAILS:
                break
            detail = entity
            if not (_is_nkp_app(entity) and _has_variables(entity)):
                # A list entry is a summary: the variables are in the app's own record.
                detail = self.conn.get_json(f"{self.conn.creds.base}/api/nutanix/v3/apps/{uuid}")
            if _is_nkp_app(detail):
                out.append(detail)
        return out


def _app_name(app: dict[str, Any]) -> str:
    return str((app.get("status") or {}).get("name")
               or (app.get("metadata") or {}).get("name") or "")


def _marketplace_name(app: dict[str, Any]) -> str:
    res = (app.get("status") or {}).get("resources") or {}
    return str(res.get("source_marketplace_name") or "")


def _blueprint_name(app: dict[str, Any]) -> str:
    res = (app.get("status") or {}).get("resources") or {}
    return str((res.get("app_blueprint_reference") or {}).get("name") or "")


def _is_nkp_app(app: dict[str, Any]) -> bool:
    """The NKP cluster app (not the NKP bastion VM app, whose item is "NKP_VM")."""
    return (_marketplace_name(app) == NKP_MARKETPLACE_NAME
            or _blueprint_name(app).startswith(NKP_MARKETPLACE_NAME))


def _maybe_nkp_app(entity: dict[str, Any], cluster_names: set[str]) -> bool:
    """Whether a listed app may be an NKP cluster's: it says so, or (a list entry that
    carries no resources) it is named after a cluster the node VMs revealed."""
    if _is_nkp_app(entity):
        return True
    res = (entity.get("status") or {}).get("resources")
    if res and (res.get("source_marketplace_name") or res.get("app_blueprint_reference")):
        return False
    return _app_name(entity).lower() in cluster_names


def _has_variables(app: dict[str, Any]) -> bool:
    return bool(app_variables(app))


def app_variables(app: dict[str, Any]) -> dict[str, str]:
    """The NKP variables of a Self-Service app record, wherever they sit.

    The substrate's ``variable_list`` holds them in both ``status`` and ``spec``; the
    services repeat some with a "v" prefix. The first non-empty value of each wins,
    substrate first. Values captured mid-deploy can be empty and are skipped.
    """
    found: dict[str, str] = {}
    lists: list[list[Any]] = []
    for part in ("status", "spec"):
        res = (app.get(part) or {}).get("resources") or {}
        for dep in res.get("deployment_list") or []:
            if not isinstance(dep, dict):
                continue
            for key in ("substrate_configuration", "substrate"):
                sub = dep.get(key) or {}
                lists.append(sub.get("variable_list") or [])
                for element in sub.get("element_list") or []:
                    if isinstance(element, dict):
                        lists.append(element.get("variable_list") or [])
            for svc in dep.get("service_list") or []:
                if isinstance(svc, dict):
                    lists.append(svc.get("variable_list") or [])
        lists.append(res.get("variable_list") or [])
    for variables in lists:
        for var in variables:
            if not isinstance(var, dict):
                continue
            name, value = var.get("name"), var.get("value")
            if name in _APP_VARIABLES and name not in found and value not in (None, ""):
                found[str(name)] = str(value).strip()
    return found


def apply_app(cluster: KubernetesCluster, app: dict[str, Any], values: dict[str, str],
              nodes: list[KubernetesNode]) -> None:
    """Fill a PC-view cluster from its Self-Service app (the app never overrides what the
    node VMs said)."""
    cluster.deployed_by = MARKETPLACE
    cluster.nkp_version = cluster.nkp_version or _strip_v(values.get("nkp_version"))
    cluster.kubernetes_version = cluster.kubernetes_version or _strip_v(
        values.get("kubernetes_version"))
    endpoint = values.get("control_plane_endpoint_ip")
    if endpoint:
        if cluster.control_plane_endpoint and cluster.control_plane_endpoint != endpoint:
            # The VM-derived guess named the node's own address: the app knows better.
            for node in nodes:
                if node.kubernetes_cluster == cluster.name and node.ip_address == endpoint:
                    node.ip_address = cluster.control_plane_endpoint
        cluster.control_plane_endpoint = endpoint
    cluster.pod_cidr = cluster.pod_cidr or values.get("kubernetes_pod_network_cidr")
    cluster.service_cidr = cluster.service_cidr or values.get("kubernetes_service_cidr")
    lb = values.get("kubernetes_service_load_balancer_ip_range")
    if lb and not cluster.load_balancer:
        cluster.load_balancer = f"MetalLB {lb}"
    if cluster.cluster_name is None:
        cluster.cluster_name = values.get("nkp_control_plane_prism_element_name")
    if cluster.control_plane_nodes is None:
        cluster.control_plane_nodes = _int(values.get("control_plane_replicas"))
    if cluster.worker_nodes is None:
        cluster.worker_nodes = _int(values.get("worker_replicas"))
    state = (app.get("status") or {}).get("state")
    if state and not cluster.status:
        cluster.status = str(state)


def node_role(vm_name: str, cluster: str) -> tuple[str, str | None]:
    """(role, pool) from an NKP node VM name: ``<cluster>-md-<n>-...`` is a worker of
    pool ``md-<n>``; anything else is control-plane."""
    m = re.match(re.escape(cluster) + r"-(md-\d+)-", vm_name, re.IGNORECASE)
    if m:
        return "worker", m.group(1).lower()
    return "control-plane", None


def derive_from_vms(env: Any) -> tuple[list[KubernetesCluster], list[KubernetesNode]]:
    """Clusters and nodes from the VMs that carry the ``KubernetesClusterName`` category."""
    cluster_names = {c.uuid: c.name for c in env.clusters if c.uuid and c.name}
    grouped: dict[str, list[Any]] = {}
    display: dict[str, str] = {}
    for vm in [*env.vms, *env.system_vms]:
        values = vm.categories.get(CLUSTER_CATEGORY) or []
        name = next((str(v) for v in values if v), None)
        if not name:
            continue
        grouped.setdefault(name.lower(), []).append(vm)
        display.setdefault(name.lower(), name)
    clusters: list[KubernetesCluster] = []
    nodes: list[KubernetesNode] = []
    for key in sorted(grouped):
        name = display[key]
        endpoint: str | None = None
        members: list[KubernetesNode] = []
        for vm in sorted(grouped[key], key=lambda v: v.name or ""):
            role, pool = node_role(vm.name or "", name)
            ips = [ip for nic in vm.nics for ip in nic.ip_addresses if ip]
            if role == "control-plane" and len(ips) > 1 and endpoint is None:
                endpoint = ips[1]
            members.append(KubernetesNode(
                kubernetes_cluster=name, name=vm.name or vm.uuid, role=role, pool=pool,
                cluster_name=vm.cluster_name or cluster_names.get(vm.cluster_uuid or ""),
                ip_address=next((ip for ip in ips if ip != endpoint), ips[0] if ips else None),
                vcpus=vm.vcpu_total, memory_bytes=vm.memory_bytes,
                status=_power(vm.power_state), vm_uuid=vm.uuid or None, api_source="pc"))
        cluster = KubernetesCluster(name=name, control_plane_endpoint=endpoint,
                                    api_source="pc")
        summarize(cluster, members)
        clusters.append(cluster)
        nodes.extend(members)
    return clusters, nodes


def summarize(cluster: KubernetesCluster, nodes: Iterable[KubernetesNode]) -> None:
    """Node counts, size and Nutanix cluster of a cluster, from its nodes."""
    mine = [n for n in nodes if n.kubernetes_cluster == cluster.name]
    if not mine:
        return
    cluster.control_plane_nodes = sum(1 for n in mine if n.role == "control-plane")
    cluster.worker_nodes = sum(1 for n in mine if n.role != "control-plane")
    vcpus = [n.vcpus for n in mine if n.vcpus]
    memory = [n.memory_bytes for n in mine if n.memory_bytes]
    cluster.total_vcpus = sum(vcpus) if vcpus else cluster.total_vcpus
    cluster.total_memory_bytes = sum(memory) if memory else cluster.total_memory_bytes
    homes = Counter(n.cluster_name for n in mine if n.cluster_name)
    if homes:
        cluster.cluster_name = homes.most_common(1)[0][0]


def _power(state: str | None) -> str | None:
    if not state:
        return None
    up = state.upper()
    return "Powered on" if up == "ON" else "Powered off" if up == "OFF" else state


# ─── Kubernetes API ────────────────────────────────────────────────────


#: (key, path, fallback paths) of every list read from the Kubernetes API.
KUBE_LISTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("nodes", "/api/v1/nodes", ()),
    ("namespaces", "/api/v1/namespaces", ()),
    ("pods", "/api/v1/pods", ()),
    ("services", "/api/v1/services", ()),
    ("pvs", "/api/v1/persistentvolumes", ()),
    ("pvcs", "/api/v1/persistentvolumeclaims", ()),
    ("storageclasses", "/apis/storage.k8s.io/v1/storageclasses", ()),
    ("deployments", "/apis/apps/v1/deployments", ()),
    ("statefulsets", "/apis/apps/v1/statefulsets", ()),
    ("daemonsets", "/apis/apps/v1/daemonsets", ()),
    ("cronjobs", "/apis/batch/v1/cronjobs", ()),
    ("ingresses", "/apis/networking.k8s.io/v1/ingresses", ()),
    ("capi_clusters", "/apis/cluster.x-k8s.io/v1beta1/clusters",
     ("/apis/cluster.x-k8s.io/v1beta2/clusters",)),
    ("machines", "/apis/cluster.x-k8s.io/v1beta1/machines",
     ("/apis/cluster.x-k8s.io/v1beta2/machines",)),
    ("nutanixclusters", "/apis/infrastructure.cluster.x-k8s.io/v1beta1/nutanixclusters", ()),
    ("kommanderclusters", "/apis/kommander.mesosphere.io/v1beta1/kommanderclusters", ()),
    ("licenses", "/apis/kommander.mesosphere.io/v1beta1/licenses", ()),
    ("appdeployments", "/apis/apps.kommander.d2iq.io/v1alpha3/appdeployments", ()),
    ("helmreleases", "/apis/helm.toolkit.fluxcd.io/v2/helmreleases",
     ("/apis/helm.toolkit.fluxcd.io/v2beta2/helmreleases",)),
    ("ipaddresspools", "/apis/metallb.io/v1beta1/ipaddresspools", ()),
)

#: Lists whose absence is worth a note (the rest are NKP / add-on CRDs that a plain
#: Kubernetes cluster does not have).
_CORE_LISTS = {"nodes", "namespaces", "pods", "services", "pvs", "pvcs", "storageclasses",
               "deployments", "statefulsets", "daemonsets", "cronjobs", "ingresses"}


def read_kubernetes(kube: KubeConnection) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Every list of `KUBE_LISTS`, tolerantly: (items by key, notes)."""
    data: dict[str, list[dict[str, Any]]] = {}
    notes: list[str] = []
    for key, path, fallbacks in KUBE_LISTS:
        items: list[dict[str, Any]] = []
        for candidate in (path, *fallbacks):
            try:
                items = kube.list(candidate)
                break
            except KubeNotFound:
                continue
            except KubeForbidden:
                notes.append(f"{key}: not readable with this token (403)")
                break
            except AuthenticationError:
                raise
            except NutanixApiError as exc:
                notes.append(f"{key}: {exc}")
                break
        else:
            if key in _CORE_LISTS:
                notes.append(f"{key}: not served")
        data[key] = items
    return data, notes


def collect_kubernetes(kube: KubeConnection, env: Any) -> str:
    """Read one cluster's Kubernetes API into `env`, merged with its PC view.
    Returns a short note (the cluster's name and what was skipped)."""
    data, notes = read_kubernetes(kube)
    version = str(kube.version().get("gitVersion") or "") or None
    parsed = map_kubernetes(data, kube.creds.name or kube.creds.host, version)
    merge_cluster(env, parsed)
    cluster = parsed["cluster"]
    text = f"{cluster.name}: {fmt_count(len(parsed['nodes']))} nodes"
    return f"{text} ({'; '.join(notes)})" if notes else text


def map_kubernetes(data: dict[str, list[dict[str, Any]]], fallback_name: str,
                   version: str | None = None) -> dict[str, Any]:
    """Map the raw lists to the model (no Environment involved)."""
    nodes_raw = data.get("nodes") or []
    capi = data.get("capi_clusters") or []
    own = _own_capi_cluster(capi, nodes_raw, fallback_name)
    name = _meta(own, "name") if own else None
    if not name:
        name = next((a.get("cluster.x-k8s.io/cluster-name") for n in nodes_raw
                     if (a := _meta(n, "annotations") or {}).get("cluster.x-k8s.io/cluster-name")),
                    None) or fallback_name
    cluster = KubernetesCluster(name=name, api_source="kubernetes")
    config = _cluster_config(own)

    # ── identity and versions ──
    topology = ((own or {}).get("spec") or {}).get("topology") or {}
    cluster.kubernetes_version = _strip_v(topology.get("version") or version)
    cluster.nkp_version = _nkp_version(data, topology)
    endpoint = _nutanix_endpoint(data, own)
    cluster.control_plane_endpoint = endpoint
    network = ((own or {}).get("spec") or {}).get("clusterNetwork") or {}
    cluster.pod_cidr = ", ".join(((network.get("pods") or {}).get("cidrBlocks")) or []) or None
    cluster.service_cidr = ", ".join(
        ((network.get("services") or {}).get("cidrBlocks")) or []) or None
    cluster.cni = ((config.get("addons") or {}).get("cni") or {}).get("provider") \
        or _cni_from_daemonsets(data.get("daemonsets") or [])
    cluster.load_balancer = _load_balancer(data, config)
    cluster.status = ((own or {}).get("status") or {}).get("phase")
    cluster.license = _license(data.get("licenses") or [])
    labels = _meta(own, "labels") or {} if own else {}
    cluster.management_cluster = bool(
        labels.get("kommander.d2iq.io/host") == "true"
        or data.get("kommanderclusters")
        or any(_meta(h, "name") == "kommander" for h in data.get("helmreleases") or []))
    cluster.managed_clusters = sorted(
        str(_meta(c, "name")) for c in capi if c is not own and _meta(c, "name"))
    # ── nodes ──
    pools = _pools(data.get("machines") or [])
    home = _machine_cluster(config)
    nodes = [_node(n, name, pools, home) for n in nodes_raw]
    summarize(cluster, nodes)
    # ── namespaces ──
    namespaces = _namespaces(data, name)
    cluster.namespace_count = len(namespaces) if data.get("namespaces") else None
    cluster.pod_count = len(data.get("pods") or [])
    cluster.workload_count = sum(len(data.get(k) or [])
                                 for k in ("deployments", "statefulsets", "daemonsets"))
    # ── storage ──
    classes = [_storage_class(s, name) for s in data.get("storageclasses") or []]
    cluster.default_storage_class = next((c.name for c in classes if c.default), None)
    volumes = [_volume(p, name) for p in data.get("pvs") or []]
    services = [_service(s, name) for s in data.get("services") or []
                if (s.get("spec") or {}).get("type") == "LoadBalancer"]
    apps = _apps(data, name)
    cluster.platform_apps = len(apps)
    workloads = map_workloads(data, name)
    ingresses = [_ingress(i, name) for i in data.get("ingresses") or []]
    return {"cluster": cluster, "nodes": nodes, "namespaces": namespaces,
            "storage_classes": classes, "volumes": volumes, "services": services,
            "apps": apps, "workloads": workloads, "ingresses": ingresses,
            "applications": applications(workloads, ingresses, data.get("services") or [],
                                         name)}


def merge_cluster(env: Any, parsed: dict[str, Any]) -> KubernetesCluster:
    """Merge a Kubernetes-API cluster into `env`: with its PC-view twin when there is one
    (same name, or a node whose VM the PC view already listed), nodes merged by name."""
    kube: KubernetesCluster = parsed["cluster"]
    kube_nodes: list[KubernetesNode] = parsed["nodes"]
    vm_uuids = {n.vm_uuid for n in kube_nodes if n.vm_uuid}
    twin = next((c for c in env.kubernetes_clusters if c.name.lower() == kube.name.lower()),
                None)
    if twin is None and vm_uuids:
        owner = next((n.kubernetes_cluster for n in env.kubernetes_nodes
                      if n.vm_uuid in vm_uuids), None)
        twin = next((c for c in env.kubernetes_clusters if c.name == owner), None)
    vms = {v.uuid: v for v in [*env.vms, *env.system_vms] if v.uuid}
    for node in kube_nodes:
        vm = vms.get(node.vm_uuid or "")
        if vm is not None and vm.cluster_name:
            node.cluster_name = vm.cluster_name  # the Nutanix cluster PC places it on
    if twin is None:
        cluster = kube
        env.kubernetes_clusters.append(cluster)
        env.kubernetes_nodes.extend(kube_nodes)
    else:
        cluster = twin
        renamed = twin.name
        for field in ("nkp_version", "kubernetes_version", "control_plane_endpoint",
                      "cni", "default_storage_class", "load_balancer", "pod_cidr",
                      "service_cidr", "license", "status"):
            value = getattr(kube, field)
            if value:  # the cluster's own API is the authority on itself
                setattr(twin, field, value)
        for field in ("management_cluster", "namespace_count", "pod_count",
                      "workload_count", "platform_apps"):
            setattr(twin, field, getattr(kube, field))
        twin.managed_clusters = kube.managed_clusters
        twin.api_source = "pc+kubernetes"
        existing = {n.name.lower(): n for n in env.kubernetes_nodes
                    if n.kubernetes_cluster == renamed}
        by_vm = {n.vm_uuid: n for n in existing.values() if n.vm_uuid}
        for node in kube_nodes:
            node.kubernetes_cluster = renamed
            match = existing.get(node.name.lower()) or by_vm.get(node.vm_uuid or "")
            if match is None:
                env.kubernetes_nodes.append(node)
                continue
            for field in ("kubelet_version", "os_image", "kernel", "container_runtime",
                          "status", "role", "ip_address"):
                value = getattr(node, field)
                if value:
                    setattr(match, field, value)
            match.pool = node.pool or match.pool
            match.vcpus = match.vcpus or node.vcpus
            match.memory_bytes = match.memory_bytes or node.memory_bytes
            match.cluster_name = match.cluster_name or node.cluster_name
            match.vm_uuid = match.vm_uuid or node.vm_uuid
            match.api_source = "pc+kubernetes"
        summarize(twin, env.kubernetes_nodes)
    for key, target in (("namespaces", "kubernetes_namespaces"),
                        ("storage_classes", "kubernetes_storage_classes"),
                        ("volumes", "kubernetes_volumes"),
                        ("services", "kubernetes_services"), ("apps", "kubernetes_apps"),
                        ("workloads", "kubernetes_workloads"),
                        ("ingresses", "kubernetes_ingresses"),
                        ("applications", "kubernetes_applications")):
        for item in parsed[key]:
            item.kubernetes_cluster = cluster.name
            getattr(env, target).append(item)
    return cluster


# ─── mapping helpers ───────────────────────────────────────────────────


def _meta(obj: dict[str, Any] | None, key: str) -> Any:
    return ((obj or {}).get("metadata") or {}).get(key)


def _own_capi_cluster(capi: list[dict[str, Any]], nodes: list[dict[str, Any]],
                      hint: str) -> dict[str, Any] | None:
    """The Cluster API object describing *this* cluster (a management cluster lists the
    clusters it manages too): the one its nodes name, else the self-managed host, else
    the one named like the hint, else the only one."""
    if not capi:
        return None
    named = {str(_meta(n, "annotations") and _meta(n, "annotations").get(
        "cluster.x-k8s.io/cluster-name") or "") for n in nodes} - {""}
    for c in capi:
        if _meta(c, "name") in named:
            return c
    for c in capi:
        if (_meta(c, "labels") or {}).get("kommander.d2iq.io/host") == "true":
            return c
    for c in capi:
        if str(_meta(c, "name") or "").lower() == hint.lower():
            return c
    return capi[0] if len(capi) == 1 else None


def _cluster_config(capi: dict[str, Any] | None) -> dict[str, Any]:
    topology = ((capi or {}).get("spec") or {}).get("topology") or {}
    for var in topology.get("variables") or []:
        if isinstance(var, dict) and var.get("name") == "clusterConfig":
            value = var.get("value")
            return value if isinstance(value, dict) else {}
    return {}


def _nkp_version(data: dict[str, list[dict[str, Any]]], topology: dict[str, Any]
                 ) -> str | None:
    for hr in data.get("helmreleases") or []:
        if _meta(hr, "name") == "kommander":
            rev = (hr.get("status") or {}).get("lastAttemptedRevision")
            if rev:
                return _strip_v(str(rev))
    for kc in data.get("kommanderclusters") or []:
        version = ((kc.get("spec") or {}).get("platform") or {}).get("version")
        if version:
            return _strip_v(str(version))
    klass = topology.get("class") or (topology.get("classRef") or {}).get("name") or ""
    m = re.search(r"nkp-[a-z0-9]+-v?(\d+\.\d+\.\d+)", str(klass))
    return m.group(1) if m else None


def _nutanix_endpoint(data: dict[str, list[dict[str, Any]]], capi: dict[str, Any] | None
                      ) -> str | None:
    ref = (((capi or {}).get("spec") or {}).get("infrastructureRef") or {}).get("name")
    ncs = data.get("nutanixclusters") or []
    for nc in sorted(ncs, key=lambda n: _meta(n, "name") != ref):
        host = ((nc.get("spec") or {}).get("controlPlaneEndpoint") or {}).get("host")
        if host:
            return str(host)
    host = (((capi or {}).get("spec") or {}).get("controlPlaneEndpoint") or {}).get("host")
    return str(host) if host else None


def _load_balancer(data: dict[str, list[dict[str, Any]]], config: dict[str, Any]
                   ) -> str | None:
    ranges = [str(a) for pool in data.get("ipaddresspools") or []
              for a in (pool.get("spec") or {}).get("addresses") or []]
    if ranges:
        return "MetalLB " + ", ".join(ranges)
    lb = (config.get("addons") or {}).get("serviceLoadBalancer") or {}
    spans = [f"{r.get('start')}-{r.get('end')}"
             for r in (lb.get("configuration") or {}).get("addressRanges") or []
             if isinstance(r, dict)]
    if lb.get("provider"):
        return f"{lb['provider']} {', '.join(spans)}".strip()
    return None


def _license(licenses: list[dict[str, Any]]) -> str | None:
    for lic in licenses:
        status = lic.get("status") or {}
        level = status.get("dkpLevel")
        if not level:
            continue
        product = status.get("productName") or "NKP"
        valid = "" if status.get("valid", True) else " (not valid)"
        return f"{product} {level}{valid}"
    return None


def _cni_from_daemonsets(daemonsets: list[dict[str, Any]]) -> str | None:
    names = {str(_meta(d, "name") or "") for d in daemonsets}
    for marker, cni in (("cilium", "Cilium"), ("calico", "Calico"), ("flannel", "Flannel")):
        if any(marker in n for n in names):
            return cni
    return None


def _machine_cluster(config: dict[str, Any]) -> str | None:
    details = ((config.get("controlPlane") or {}).get("nutanix") or {}).get(
        "machineDetails") or {}
    return (details.get("cluster") or {}).get("name")


def _pools(machines: list[dict[str, Any]]) -> dict[str, str]:
    """node name -> machine deployment (pool) name, from the Cluster API machines."""
    out: dict[str, str] = {}
    for m in machines:
        labels = _meta(m, "labels") or {}
        pool = labels.get("topology.cluster.x-k8s.io/deployment-name")
        node = ((m.get("status") or {}).get("nodeRef") or {}).get("name") or _meta(m, "name")
        if pool and node:
            out[str(node)] = str(pool)
    return out


def _node(raw: dict[str, Any], cluster: str, pools: dict[str, str],
          home: str | None) -> KubernetesNode:
    name = str(_meta(raw, "name") or "")
    labels = _meta(raw, "labels") or {}
    spec = raw.get("spec") or {}
    status = raw.get("status") or {}
    info = status.get("nodeInfo") or {}
    control = "node-role.kubernetes.io/control-plane" in labels \
        or "node-role.kubernetes.io/master" in labels
    pool = pools.get(name)
    if pool is None and not control:
        pool = node_role(name, cluster)[1]
    ready = next((c.get("status") for c in status.get("conditions") or []
                  if c.get("type") == "Ready"), None)
    provider = str(spec.get("providerID") or "")
    capacity = status.get("capacity") or {}
    return KubernetesNode(
        kubernetes_cluster=cluster, name=name,
        role="control-plane" if control else "worker", pool=pool,
        cluster_name=labels.get("topology.kubernetes.io/zone") or home,
        ip_address=next((a.get("address") for a in status.get("addresses") or []
                         if a.get("type") == "InternalIP"), None),
        vcpus=_cpu(capacity.get("cpu")), memory_bytes=quantity_bytes(capacity.get("memory")),
        kubelet_version=info.get("kubeletVersion"), os_image=info.get("osImage"),
        kernel=info.get("kernelVersion"), container_runtime=info.get("containerRuntimeVersion"),
        status="Ready" if ready == "True" else "NotReady" if ready else None,
        vm_uuid=provider.split("://", 1)[1] if provider.startswith("nutanix://") else None,
        api_source="kubernetes")


def _namespaces(data: dict[str, list[dict[str, Any]]], cluster: str
                ) -> list[KubernetesNamespace]:
    counts: dict[str, Counter[str]] = {}
    for key, kind in (("pods", "pods"), ("deployments", "deployments"),
                      ("statefulsets", "statefulsets"), ("daemonsets", "daemonsets"),
                      ("services", "services"), ("pvcs", "volume_claims")):
        for item in data.get(key) or []:
            ns = str(_meta(item, "namespace") or "")
            counts.setdefault(ns, Counter())[kind] += 1
    out: list[KubernetesNamespace] = []
    for raw in data.get("namespaces") or []:
        name = str(_meta(raw, "name") or "")
        c = counts.get(name, Counter())
        out.append(KubernetesNamespace(
            kubernetes_cluster=cluster, name=name,
            status=(raw.get("status") or {}).get("phase"),
            pods=c["pods"], deployments=c["deployments"], statefulsets=c["statefulsets"],
            daemonsets=c["daemonsets"], services=c["services"],
            volume_claims=c["volume_claims"], system=is_system_namespace(name)))
    return sorted(out, key=lambda n: n.name)


def _storage_class(raw: dict[str, Any], cluster: str) -> KubernetesStorageClass:
    annotations = _meta(raw, "annotations") or {}
    default = any(str(annotations.get(k, "")).lower() == "true" for k in (
        "storageclass.kubernetes.io/is-default-class",
        "storageclass.beta.kubernetes.io/is-default-class"))
    params = raw.get("parameters") or {}
    return KubernetesStorageClass(
        kubernetes_cluster=cluster, name=str(_meta(raw, "name") or ""),
        provisioner=raw.get("provisioner"), default=default,
        reclaim_policy=raw.get("reclaimPolicy"), binding_mode=raw.get("volumeBindingMode"),
        expansion=raw.get("allowVolumeExpansion"),
        storage_container=params.get("storageContainer"))


def _volume(raw: dict[str, Any], cluster: str) -> KubernetesVolume:
    spec = raw.get("spec") or {}
    claim = spec.get("claimRef") or {}
    return KubernetesVolume(
        kubernetes_cluster=cluster, name=str(_meta(raw, "name") or ""),
        capacity_bytes=quantity_bytes((spec.get("capacity") or {}).get("storage")),
        storage_class=spec.get("storageClassName"),
        claim=f"{claim.get('namespace')}/{claim.get('name')}" if claim.get("name") else None,
        status=(raw.get("status") or {}).get("phase"),
        reclaim_policy=spec.get("persistentVolumeReclaimPolicy"),
        access_modes=list(spec.get("accessModes") or []))


def _service(raw: dict[str, Any], cluster: str) -> KubernetesService:
    ingress = ((raw.get("status") or {}).get("loadBalancer") or {}).get("ingress") or []
    ips = [str(i.get("ip") or i.get("hostname")) for i in ingress
           if i.get("ip") or i.get("hostname")]
    ports = [f"{p.get('port')}/{p.get('protocol') or 'TCP'}"
             for p in (raw.get("spec") or {}).get("ports") or []]
    return KubernetesService(
        kubernetes_cluster=cluster, namespace=str(_meta(raw, "namespace") or ""),
        name=str(_meta(raw, "name") or ""), external_ips=ips, ports=ports)


_APP_REF = re.compile(r"^(.+)-v?(\d+(?:\.\d+)+.*)$")


def _apps(data: dict[str, list[dict[str, Any]]], cluster: str) -> list[KubernetesApp]:
    """Kommander AppDeployments (the NKP platform apps), else the Flux HelmReleases."""
    releases = {(str(_meta(h, "namespace")), str(_meta(h, "name"))): h
                for h in data.get("helmreleases") or []}
    out: list[KubernetesApp] = []
    deployments = data.get("appdeployments") or []
    for ad in deployments:
        ns, name = str(_meta(ad, "namespace") or ""), str(_meta(ad, "name") or "")
        ref = str(((ad.get("spec") or {}).get("appRef") or {}).get("name") or "")
        m = _APP_REF.match(ref)
        status = _ready(releases.get((ns, name)))
        if status is None:
            synced = [c.get("status") for cl in (ad.get("status") or {}).get("clusters") or []
                      for c in cl.get("conditions") or []
                      if c.get("type") == "AppDeploymentInstanceSynced"]
            status = ("Synced" if all(s == "True" for s in synced) else "Not synced") \
                if synced else None
        out.append(KubernetesApp(kubernetes_cluster=cluster, namespace=ns, name=name,
                                 version=m.group(2) if m else None, status=status,
                                 kind="AppDeployment"))
    if not deployments:
        for (ns, name), hr in releases.items():
            rev = (hr.get("status") or {}).get("lastAttemptedRevision")
            out.append(KubernetesApp(kubernetes_cluster=cluster, namespace=ns, name=name,
                                     version=_strip_v(str(rev)) if rev else None,
                                     status=_ready(hr), kind="HelmRelease"))
    return sorted(out, key=lambda a: (a.namespace or "", a.name))


def _ready(obj: dict[str, Any] | None) -> str | None:
    if obj is None:
        return None
    ready = next((c.get("status") for c in (obj.get("status") or {}).get("conditions") or []
                  if c.get("type") == "Ready"), None)
    return None if ready is None else "Ready" if ready == "True" else "Not ready"


# ─── workloads, ingresses, applications ────────────────────────────────

#: Namespaces whose workloads are platform plumbing, not user applications.
_SYSTEM_NAMESPACES = frozenset({
    "default-system", "ntnx-system", "metallb-system", "node-feature-discovery",
    "registry-system", "cert-manager", "git-operator-system", "kube-federation-system",
    "external-secrets", "container-object-storage-system", "caren-system",
    "capi-stack-operator", "capi-stack-operator-installer",
})
_SYSTEM_PREFIXES = ("kube-", "kommander", "cilium", "capi-")


def is_system_namespace(namespace: str) -> bool:
    """kube-*, kommander*, cilium*, *-system and the named NKP add-on namespaces."""
    ns = namespace.lower()
    return (ns in _SYSTEM_NAMESPACES or ns.startswith(_SYSTEM_PREFIXES)
            or ns.endswith("-system"))


#: (list key, kind) of the workload lists.
_WORKLOAD_KINDS = (("deployments", "Deployment"), ("statefulsets", "StatefulSet"),
                   ("daemonsets", "DaemonSet"), ("cronjobs", "CronJob"))


def helm_release(obj: dict[str, Any]) -> str | None:
    """The Helm release an object belongs to: Helm's own annotation, else the instance
    label of a Helm-managed object."""
    annotations = _meta(obj, "annotations") or {}
    labels = _meta(obj, "labels") or {}
    release = annotations.get("meta.helm.sh/release-name")
    if not release and labels.get("app.kubernetes.io/managed-by") == "Helm":
        release = labels.get("app.kubernetes.io/instance")
    return str(release) if release else None


def _owner_index(data: dict[str, list[dict[str, Any]]]
                 ) -> tuple[dict[str, str], list[str]]:
    """(Helm release name -> Kommander AppDeployment, AppDeployment names).

    Flux names a release ``spec.releaseName``, else ``<targetNamespace>-<name>`` (the
    HelmRelease's own namespace when it has no target); a release whose HelmRelease an
    AppDeployment manages (or is named after) belongs to that app."""
    apps = sorted({str(_meta(a, "name")) for a in data.get("appdeployments") or []
                   if _meta(a, "name")}, key=len, reverse=True)
    by_release: dict[str, str] = {}
    for hr in data.get("helmreleases") or []:
        name = str(_meta(hr, "name") or "")
        labels = _meta(hr, "labels") or {}
        managed = labels.get("kommander.d2iq.io/managed-by-kind") == "AppDeployment"
        if not name or not (managed or name in apps):
            continue
        spec = hr.get("spec") or {}
        target = spec.get("targetNamespace") or _meta(hr, "namespace")
        for release in {spec.get("releaseName"), f"{target}-{name}", name} - {None}:
            by_release[str(release)] = name
    return by_release, apps


def _owner(release: str | None, namespace: str, by_release: dict[str, str],
           apps: list[str]) -> str | None:
    if release and release in by_release:
        return by_release[release]
    if release and release in apps:
        return release
    for app in apps:  # "git-operator" owns "git-operator-system"
        if namespace in (app, f"{app}-system"):
            return app
    return None


def _containers(raw: dict[str, Any], kind: str) -> list[str]:
    spec = raw.get("spec") or {}
    if kind == "CronJob":
        spec = ((spec.get("jobTemplate") or {}).get("spec") or {})
    pod = ((spec.get("template") or {}).get("spec") or {})
    images: list[str] = []
    for c in pod.get("containers") or []:
        image = c.get("image") if isinstance(c, dict) else None
        if image and image not in images:
            images.append(str(image))
    return images


def map_workloads(data: dict[str, list[dict[str, Any]]], cluster: str
                  ) -> list[KubernetesWorkload]:
    by_release, apps = _owner_index(data)
    out: list[KubernetesWorkload] = []
    for key, kind in _WORKLOAD_KINDS:
        for raw in data.get(key) or []:
            ns = str(_meta(raw, "namespace") or "")
            labels = _meta(raw, "labels") or {}
            spec = raw.get("spec") or {}
            status = raw.get("status") or {}
            desired: int | None
            ready: int | None
            schedule = None
            if kind == "DaemonSet":
                desired = _int(status.get("desiredNumberScheduled"))
                ready = _int(status.get("numberReady")) or 0
            elif kind == "CronJob":
                desired = ready = None
                schedule = spec.get("schedule")
            else:
                desired = _int(spec.get("replicas"))
                desired = 1 if desired is None else desired
                ready = _int(status.get("readyReplicas")) or 0
            release = helm_release(raw)
            out.append(KubernetesWorkload(
                kubernetes_cluster=cluster, namespace=ns, name=str(_meta(raw, "name") or ""),
                kind=kind, replicas_desired=desired, replicas_ready=ready,
                images=_containers(raw, kind), helm_release=release,
                helm_chart=labels.get("helm.sh/chart"),
                app_name=labels.get("app.kubernetes.io/name"),
                app_version=labels.get("app.kubernetes.io/version"),
                owner_app=_owner(release, ns, by_release, apps), schedule=schedule,
                system=is_system_namespace(ns),
                status=_workload_status(kind, desired, ready, spec)))
    return sorted(out, key=lambda w: (w.system, w.namespace, w.kind, w.name))


def _workload_status(kind: str, desired: int | None, ready: int | None,
                     spec: dict[str, Any]) -> str:
    if kind == "CronJob":
        return "Suspended" if spec.get("suspend") else "Scheduled"
    if desired == 0:
        return "Scaled to zero"
    return "Ready" if ready == desired else "Not ready"


def _ingress(raw: dict[str, Any], cluster: str) -> KubernetesIngress:
    spec = raw.get("spec") or {}
    annotations = _meta(raw, "annotations") or {}
    hosts: list[str] = []
    backends: list[str] = []
    for rule in spec.get("rules") or []:
        host = rule.get("host")
        if host and host not in hosts:
            hosts.append(str(host))
        for path in ((rule.get("http") or {}).get("paths") or []):
            svc = ((path.get("backend") or {}).get("service") or {})
            port = svc.get("port") or {}
            target = f"{svc.get('name')}:{port.get('number') or port.get('name') or ''}"
            backends.append(f"{host or '*'}{path.get('path') or '/'} -> {target.rstrip(':')}")
    ingress = ((raw.get("status") or {}).get("loadBalancer") or {}).get("ingress") or []
    ns = str(_meta(raw, "namespace") or "")
    return KubernetesIngress(
        kubernetes_cluster=cluster, namespace=ns, name=str(_meta(raw, "name") or ""),
        ingress_class=spec.get("ingressClassName") or annotations.get(
            "kubernetes.io/ingress.class"),
        hosts=hosts, backends=backends,
        addresses=[str(i.get("ip") or i.get("hostname")) for i in ingress
                   if i.get("ip") or i.get("hostname")],
        helm_release=helm_release(raw), system=is_system_namespace(ns))


def applications(workloads: list[KubernetesWorkload], ingresses: list[KubernetesIngress],
                 services: list[dict[str, Any]], cluster: str
                 ) -> list[KubernetesApplication]:
    """The user applications: each Helm release of a user namespace, and the rest of each
    user namespace's workloads, with their images, ingress hosts and LB addresses."""
    groups: dict[tuple[str, str | None], list[KubernetesWorkload]] = {}
    for w in workloads:
        if not w.system:
            groups.setdefault((w.namespace, w.helm_release), []).append(w)
    per_ns: dict[str, list[tuple[str, str | None]]] = {}
    for group in groups:
        per_ns.setdefault(group[0], []).append(group)

    def home(namespace: str, release: str | None) -> tuple[str, str | None] | None:
        if (namespace, release) in groups:
            return (namespace, release)
        if (namespace, None) in groups:
            return (namespace, None)
        keys = per_ns.get(namespace) or []
        return keys[0] if len(keys) == 1 else None

    hosts: dict[tuple[str, str | None], list[str]] = {}
    addresses: dict[tuple[str, str | None], list[str]] = {}
    for ing in ingresses:
        key = home(ing.namespace, ing.helm_release)
        if key is not None:
            hosts.setdefault(key, []).extend(h for h in ing.hosts
                                             if h not in hosts.get(key, []))
    for svc in services:
        if (svc.get("spec") or {}).get("type") != "LoadBalancer":
            continue
        key = home(str(_meta(svc, "namespace") or ""), helm_release(svc))
        if key is None:
            continue
        for i in ((svc.get("status") or {}).get("loadBalancer") or {}).get("ingress") or []:
            ip = i.get("ip") or i.get("hostname")
            if ip and ip not in addresses.get(key, []):
                addresses.setdefault(key, []).append(str(ip))
    out: list[KubernetesApplication] = []
    for (ns, release), members in groups.items():
        kinds = Counter(w.kind for w in members)
        images: list[str] = []
        for w in members:
            images.extend(i for i in w.images if i not in images)
        charts = {w.helm_chart for w in members if w.helm_chart}
        running = [w for w in members if w.kind != "CronJob"]
        out.append(KubernetesApplication(
            kubernetes_cluster=cluster, namespace=ns, name=release or ns,
            kind="Helm release" if release else "Namespace",
            helm_chart=", ".join(sorted(charts)) or None, workloads=len(members),
            workload_kinds=[f"{n} {k}{'s' if n != 1 else ''}"
                            for k, n in sorted(kinds.items())],
            images=images, ingress_hosts=hosts.get((ns, release), []),
            addresses=addresses.get((ns, release), []),
            status="Ready" if all(w.status in ("Ready", "Scaled to zero") for w in running)
            else "Not ready"))
    return sorted(out, key=lambda a: (a.namespace, a.kind != "Helm release", a.name))


# ─── units ─────────────────────────────────────────────────────────────


_SUFFIX = {"Ki": 1024, "Mi": 1024 ** 2, "Gi": 1024 ** 3, "Ti": 1024 ** 4, "Pi": 1024 ** 5,
           "k": 1000, "K": 1000, "M": 1000 ** 2, "G": 1000 ** 3, "T": 1000 ** 4,
           "P": 1000 ** 5}


def quantity_bytes(value: Any) -> int | None:
    """A Kubernetes quantity ("16033620Ki", "100Gi", "5G", "1024") in bytes."""
    if value is None:
        return None
    text = str(value).strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([KMGTP]i?|k)?", text)
    if not m:
        return None
    return int(float(m.group(1)) * _SUFFIX.get(m.group(2) or "", 1))


def _cpu(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value)
    try:
        return int(text[:-1]) // 1000 if text.endswith("m") else int(float(text))
    except ValueError:
        return None


def _strip_v(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text[1:] if text[:1] in ("v", "V") and text[1:2].isdigit() else text


def _int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


__all__ = [
    "CLUSTER_CATEGORY",
    "NkpCollector",
    "app_variables",
    "collect_kubernetes",
    "applications",
    "derive_from_vms",
    "helm_release",
    "is_system_namespace",
    "map_kubernetes",
    "map_workloads",
    "merge_cluster",
    "node_role",
    "quantity_bytes",
    "read_kubernetes",
]
