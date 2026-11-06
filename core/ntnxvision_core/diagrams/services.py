"""Platform service designs (S1 to S4): Kubernetes (NKP), Database Service (NDB),
Objects and Files. One model shape and one layout (`layout.services`) for all four, so
they read alike: the management plane on top (Prism Central, or the NDB appliance), a
"manages" line to each Nutanix cluster, and inside the cluster the product instances
with their members.

* NKP (`nkp`): Prism Central, the Nutanix cluster the NKP cluster runs on, the NKP
  cluster with its control plane VIP and load balancer, its control plane and worker
  nodes; a management cluster's line to the clusters it manages.
* NDB (`ndb`): the NDB appliance, the clusters registered in it, per cluster one box per
  database engine with its databases (each naming its DB server); a clone sits under the
  database it was cloned from, joined by a dashed line.
* Objects (`objects`): Prism Central, the cluster, the object store with its S3
  endpoint and its buckets.
* Files (`files`): Prism Central, the cluster, the file server with its FSVMs and shares.

The figures carry relationships only: names, the one address a reader looks for and
the share protocol. Sizes, counts and settings are in the Excel, Word and PDF tables.
Every model is sorted and its ids come from object identity, so a snapshot always gives
the same figure. A diagram exists only when the snapshot has that product's data.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ntnxvision_core.models import Environment

from . import fmt
from .facts import short_name
from .model import known as _known
from .netcommon import default_host, natural, src_of


@dataclass
class SvcItem:
    """A member of a product instance: a node, an endpoint, a bucket, a share, a
    database."""

    id: str
    name: str
    #: cp | worker | vip | lb | endpoint | bucket | fsvm | share | db | clone | note
    kind: str = "item"
    #: One short second line (an address, a protocol, the DB server).
    sub: str | None = None
    #: A clone: the id of the database it was cloned from.
    of: str | None = None
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class SvcLane:
    """A titled group of items inside a product box ("CONTROL PLANE", "BUCKETS")."""

    id: str
    title: str
    items: list[SvcItem] = field(default_factory=list)
    #: Items per row.
    cols: int = 1
    #: What the items are, for the "+N more" card ("buckets").
    noun: str = "items"


@dataclass
class SvcBox:
    """A product instance on a cluster: an NKP cluster, an object store, a file server,
    or (NDB) the databases of one engine."""

    id: str
    name: str
    kind: str
    sub: str | None = None
    lanes: list[SvcLane] = field(default_factory=list)
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class SvcCluster:
    """A Nutanix cluster and the product instances it runs."""

    id: str
    name: str
    #: The anchor (Prism Central or NDB appliance) that manages it.
    anchor: str | None = None
    boxes: list[SvcBox] = field(default_factory=list)
    #: Shown when the cluster runs nothing of the product ("No DB servers").
    empty: str | None = None
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class SvcAnchor:
    """The management plane: a Prism Central, or the NDB appliance."""

    id: str
    name: str
    sub: str | None = None
    data: dict[str, str] = field(default_factory=dict)


@dataclass
class SvcLink:
    """A relationship drawn between two boxes (an NKP management cluster to a cluster
    it manages)."""

    id: str
    a: str
    b: str
    kind: str = "manages"


@dataclass
class ServiceModel:
    type: str
    title: str
    anchors: list[SvcAnchor] = field(default_factory=list)
    clusters: list[SvcCluster] = field(default_factory=list)
    links: list[SvcLink] = field(default_factory=list)
    edited: bool = False

    def anchor(self, ident: str | None) -> SvcAnchor | None:
        return next((a for a in self.anchors if a.id == ident), None)

    def boxes(self) -> list[SvcBox]:
        return [b for c in self.clusters for b in c.boxes]


TITLES = {"nkp": "Kubernetes platform design", "ndb": "Database service design",
          "objects": "Objects design", "files": "Files design"}


# ─── documents ─────────────────────────────────────────────────────────


def to_doc(m: ServiceModel) -> dict[str, Any]:
    return asdict(m)


def from_doc(d: dict[str, Any]) -> ServiceModel:
    def item(x: dict[str, Any]) -> SvcItem:
        return SvcItem(**_known(SvcItem, {**x, "data": dict(x.get("data") or {})}))

    def lane(x: dict[str, Any]) -> SvcLane:
        return SvcLane(**_known(SvcLane, {**x, "items": [item(i) for i in x.get("items", [])]}))

    def box(x: dict[str, Any]) -> SvcBox:
        return SvcBox(**_known(SvcBox, {**x, "lanes": [lane(ln) for ln in x.get("lanes", [])],
                                        "data": dict(x.get("data") or {})}))

    def cluster(x: dict[str, Any]) -> SvcCluster:
        return SvcCluster(**_known(SvcCluster, {
            **x, "boxes": [box(b) for b in x.get("boxes", [])],
            "data": dict(x.get("data") or {})}))

    kind = str(d.get("type") or "nkp")
    return ServiceModel(
        type=kind, title=str(d.get("title") or TITLES.get(kind, "Service design")),
        anchors=[SvcAnchor(**_known(SvcAnchor, a)) for a in d.get("anchors", [])],
        clusters=[cluster(c) for c in d.get("clusters", [])],
        links=[SvcLink(**_known(SvcLink, lk)) for lk in d.get("links", [])],
        edited=bool(d.get("edited")))


# ─── shared facts ──────────────────────────────────────────────────────


class _Clusters:
    """Nutanix clusters by (PC host, name), for ids that carry the cluster uuid (the NCI
    badge and the editor match on it) and for the cluster's anchor."""

    def __init__(self, env: Environment) -> None:
        self.env = env
        self.default = default_host(env)
        self.by_name: dict[tuple[str, str], Any] = {}
        self.by_uuid: dict[str, Any] = {}
        for c in env.clusters:
            self.by_uuid[c.uuid] = c
            if c.name:
                self.by_name.setdefault((self.src(c), c.name.lower()), c)
        self.out: dict[str, SvcCluster] = {}

    def src(self, obj: Any) -> str:
        return src_of(self.env, obj, self.default)

    def get(self, obj: Any, name: str | None, uuid: str | None = None,
            anchor: str | None = None) -> SvcCluster:
        host = self.src(obj)
        c = self.by_uuid.get(uuid or "") or self.by_name.get((host, (name or "").lower()))
        if c is not None:
            ident, label = f"cluster:{c.uuid}", c.name or c.uuid[:8]
            data = {"cluster_uuid": c.uuid, "name": label}
            host = self.src(c)
        elif name:
            ident, label, data = f"cluster:name:{name.lower()}", name, {"name": name}
        else:
            ident, label, data = "cluster:unknown", "Cluster not reported", {}
        if self.env.is_combined:
            data["prism_central"] = host
        if ident not in self.out:
            self.out[ident] = SvcCluster(id=ident, name=label,
                                         anchor=anchor or f"site:{host}:pc", data=data)
        return self.out[ident]

    def sorted(self) -> list[SvcCluster]:
        out = list(self.out.values())
        for c in out:
            c.boxes.sort(key=lambda b: (natural(b.name), b.id))
        return sorted(out, key=lambda c: (c.anchor or "", c.id == "cluster:unknown",
                                          natural(c.name), c.id))


def _pc_anchors(env: Environment, clusters: list[SvcCluster]) -> list[SvcAnchor]:
    used = {c.anchor for c in clusters}
    out = []
    for host in sorted({(c.anchor or "").removeprefix("site:").removesuffix(":pc")
                        for c in clusters if c.anchor}):
        ident = f"site:{host}:pc"
        if ident in used:
            out.append(SvcAnchor(id=ident, name="Prism Central", sub=host,
                                 data={"prism_central": host, "name": "Prism Central"}))
    return out


def _ip(values: list[str]) -> str | None:
    return ", ".join(values[:2]) + (" …" if len(values) > 2 else "") if values else None


# ─── NKP ───────────────────────────────────────────────────────────────


def nkp_applies(env: Environment) -> bool:
    return bool(env.kubernetes_clusters)


def _role(role: str | None) -> str:
    r = (role or "").lower()
    return "cp" if "control" in r or "master" in r else "worker"


def _node_name(name: str, cluster: str) -> str:
    short = name.removeprefix(f"{cluster}-") if cluster else name
    return short or name


def nkp_model(env: Environment) -> ServiceModel:
    cl = _Clusters(env)
    nodes: dict[tuple[str, str], list[Any]] = {}
    for n in env.kubernetes_nodes:
        nodes.setdefault((cl.src(n), n.kubernetes_cluster), []).append(n)
    ids: dict[tuple[str, str], str] = {}
    for k in env.kubernetes_clusters:
        src = cl.src(k)
        mine = nodes.get((src, k.name), [])
        home = k.cluster_name or next((n.cluster_name for n in mine if n.cluster_name), None)
        cluster = cl.get(k, home)
        bid = f"nkp:{src}:{k.name}"
        ids[(src, k.name)] = bid
        lanes: list[SvcLane] = []
        ends = []
        if k.control_plane_endpoint:
            ends.append(SvcItem(id=f"{bid}:vip", name="Control plane VIP", kind="vip",
                                sub=k.control_plane_endpoint))
        if k.load_balancer:
            ends.append(SvcItem(id=f"{bid}:lb", name="Load balancer", kind="lb",
                                sub=k.load_balancer))
        if ends:
            lanes.append(SvcLane(id=f"{bid}:endpoints", title="ENDPOINTS", items=ends,
                                 cols=2, noun="endpoints"))
        for role, title, noun in (("cp", "CONTROL PLANE", "control plane nodes"),
                                  ("worker", "WORKERS", "workers")):
            # Node names repeat the NKP cluster's name (the box title): drawn without it.
            items = [SvcItem(id=f"{bid}:node:{n.name}", name=_node_name(n.name, k.name),
                             kind=role,
                             sub=n.ip_address,
                             data={"node": n.name, "kubernetes_cluster": k.name})
                     for n in sorted(mine, key=lambda n: (natural(n.pool), natural(n.name)))
                     if _role(n.role) == role]
            if items:
                lanes.append(SvcLane(id=f"{bid}:{role}", title=title, items=items, cols=2,
                                     noun=noun))
        versions = [f"NKP {k.nkp_version}" if k.nkp_version else None,
                    f"Kubernetes {k.kubernetes_version}" if k.kubernetes_version else None]
        cluster.boxes.append(SvcBox(
            id=bid, name=k.name, kind="nkp",
            sub=" · ".join(v for v in versions if v) or None, lanes=lanes,
            data={"kubernetes_cluster": k.name, "badge": "nkp",
                  **({"role": "management cluster"} if k.management_cluster else {})}))
    links = []
    for k in env.kubernetes_clusters:
        a = ids.get((cl.src(k), k.name))
        for other in sorted(k.managed_clusters, key=natural):
            b = ids.get((cl.src(k), other))
            if a and b and a != b:
                links.append(SvcLink(id=f"link:{a}->{b}", a=a, b=b))
    # A managed cluster names its management cluster (a line, not a connector: the two
    # may sit on different Nutanix clusters).
    boxes = {b.id: b for c in cl.out.values() for b in c.boxes}
    for lk in links:
        managed, by = boxes[lk.b], boxes[lk.a]
        managed.sub = " · ".join(x for x in (managed.sub, f"managed by {by.name}") if x)
    clusters = cl.sorted()
    return ServiceModel(type="nkp", title=TITLES["nkp"], anchors=_pc_anchors(env, clusters),
                        clusters=clusters, links=links)


# ─── NDB ───────────────────────────────────────────────────────────────

NDB_ANCHOR = "ndb:appliance"

#: Engine names as NDB reports them -> the label and the order the figure uses.
_ENGINES = {"oracle": "Oracle", "postgres": "PostgreSQL", "postgresql": "PostgreSQL",
            "sqlserver": "SQL Server", "sql server": "SQL Server", "mssql": "SQL Server",
            "mysql": "MySQL", "mariadb": "MariaDB", "mongodb": "MongoDB"}


def engine_label(value: str | None) -> str:
    if not value:
        return "Engine not reported"
    key = value.strip().lower().removesuffix("_database").replace("_", " ")
    return _ENGINES.get(key, _ENGINES.get(key.replace(" ", ""), value.strip()))


def ndb_applies(env: Environment) -> bool:
    return bool(env.databases or env.database_servers or env.ndb_clusters)


def ndb_model(env: Environment) -> ServiceModel:
    cl = _Clusters(env)
    for nc in sorted(env.ndb_clusters, key=lambda x: natural(x.name)):
        cl.get(nc, nc.name, anchor=NDB_ANCHOR)
    servers = {s.name: s for s in env.database_servers if s.name}
    tm_db = {t.name: t.database_name for t in env.time_machines if t.name and t.database_name}
    by_name = {d.name: d for d in env.databases if d.name and not d.clone}
    boxes: dict[tuple[str, str], SvcBox] = {}

    def engine_box(cluster: SvcCluster, engine: str) -> SvcBox:
        key = (cluster.id, engine)
        if key not in boxes:
            bid = f"{cluster.id}:engine:{engine.lower().replace(' ', '-')}"
            boxes[key] = SvcBox(id=bid, name=engine, kind="engine",
                                lanes=[SvcLane(id=f"{bid}:databases", title="",
                                               noun="databases")],
                                data={"engine": engine, "cluster": cluster.name})
            cluster.boxes.append(boxes[key])
        return boxes[key]

    def cluster_of(obj: Any) -> SvcCluster:
        name = getattr(obj, "cluster_name", None)
        server = servers.get(getattr(obj, "db_server_name", None) or "")
        return cl.get(obj, name or (server.cluster_name if server else None),
                      anchor=NDB_ANCHOR)

    def source_of(d: Any) -> Any | None:
        parent = d.parent_time_machine or ""
        name = tm_db.get(parent) or (parent[:-3] if parent.endswith("_TM") else parent)
        return by_name.get(name)

    hosted: set[str] = set()
    for d in sorted((d for d in env.databases if not d.clone),
                    key=lambda d: (natural(d.name), d.id)):
        c = cluster_of(d)
        lane = engine_box(c, engine_label(d.db_engine)).lanes[0]
        lane.items.append(SvcItem(id=f"db:{d.id}", name=d.name or d.id, kind="db",
                                  sub=d.db_server_name, data={"database": d.name or d.id}))
        if d.db_server_name:
            hosted.add(d.db_server_name)
    for d in sorted((d for d in env.databases if d.clone),
                    key=lambda d: (natural(d.name), d.id)):
        src = source_of(d)
        c = cluster_of(d)
        lane = engine_box(c, engine_label(d.db_engine)).lanes[0]
        item = SvcItem(id=f"db:{d.id}", name=d.name or d.id, kind="clone",
                       of=f"db:{src.id}" if src else None,
                       sub=d.db_server_name, data={"database": d.name or d.id, "clone": "yes"})
        at = next((i for i, x in enumerate(lane.items) if x.id == item.of), None)
        if at is None:
            item.sub = f"clone of {src.name}" if src else "clone"
            lane.items.append(item)
        else:
            # Under its source, after the source's earlier clones.
            while at + 1 < len(lane.items) and lane.items[at + 1].of == item.of:
                at += 1
            lane.items.insert(at + 1, item)
    # DB servers that host no database the snapshot lists (a server provisioned alone).
    for s in sorted(env.database_servers, key=lambda s: (natural(s.name), s.id)):
        if s.name in hosted:
            continue
        c = cl.get(s, s.cluster_name, anchor=NDB_ANCHOR)
        lane = engine_box(c, engine_label(s.db_engine)).lanes[0]
        lane.items.append(SvcItem(id=f"dbserver:{s.id}", name=s.name or s.id, kind="db",
                                  sub="DB server, no database", data={"db_server": s.name or s.id}))
    clusters = cl.sorted()
    for c in clusters:
        c.boxes.sort(key=lambda b: (b.name == "Engine not reported", b.name.lower()))
        if not c.boxes:
            c.empty = "No DB servers"
    anchor = SvcAnchor(id=NDB_ANCHOR, name="NDB", sub="Nutanix Database Service",
                       data={"name": "NDB", "badge": "ndb"})
    return ServiceModel(type="ndb", title=TITLES["ndb"], anchors=[anchor], clusters=clusters)


# ─── Objects ───────────────────────────────────────────────────────────


def objects_applies(env: Environment) -> bool:
    return bool(env.object_stores)


def objects_model(env: Environment) -> ServiceModel:
    cl = _Clusters(env)
    buckets: dict[tuple[str, str], list[Any]] = {}
    for b in env.buckets:
        buckets.setdefault((cl.src(b), b.object_store_name or ""), []).append(b)
    for o in env.object_stores:
        cluster = cl.get(o, o.cluster_name, o.cluster_uuid)
        bid = f"objectstore:{o.uuid}"
        lanes = []
        if o.client_ips:
            lanes.append(SvcLane(id=f"{bid}:endpoints", title="CLIENT ACCESS", items=[
                SvcItem(id=f"{bid}:s3", name="S3 endpoint", kind="endpoint",
                        sub=_ip(sorted(o.client_ips)))], cols=2, noun="endpoints"))
        items = [SvcItem(id=f"bucket:{o.uuid}:{b.name}", name=b.name, kind="bucket",
                         data={"bucket": b.name})
                 for b in sorted(buckets.get((cl.src(o), o.name or ""), []),
                                 key=lambda b: natural(b.name))]
        if items:
            lanes.append(SvcLane(id=f"{bid}:buckets", title="BUCKETS", items=items, cols=3,
                                 noun="buckets"))
        cluster.boxes.append(SvcBox(
            id=bid, name=o.name or o.uuid[:8], kind="objectstore",
            sub=f"Objects {o.deployment_version}" if o.deployment_version else "Objects",
            lanes=lanes, data={"object_store": o.name or o.uuid, "badge": "nus"}))
    clusters = cl.sorted()
    return ServiceModel(type="objects", title=TITLES["objects"],
                        anchors=_pc_anchors(env, clusters), clusters=clusters)


# ─── Files ─────────────────────────────────────────────────────────────


def files_applies(env: Environment) -> bool:
    return bool(env.file_servers)


def _protocol(s: Any) -> str | None:
    p = (s.protocol or "").upper()
    if p in ("SMB", "NFS"):
        return p
    if p:
        return s.protocol
    t = (s.share_type or "").upper()
    return t if t in ("SMB", "NFS") else None


def files_model(env: Environment) -> ServiceModel:
    cl = _Clusters(env)
    shares: dict[tuple[str, str], list[Any]] = {}
    for s in env.file_shares:
        shares.setdefault((cl.src(s), s.file_server_name or ""), []).append(s)
    for f in env.file_servers:
        cluster = cl.get(f, f.cluster_name)
        bid = f"fileserver:{f.uuid}"
        lanes = []
        if f.num_fsvm:
            lanes.append(SvcLane(id=f"{bid}:fsvms", title="FILE SERVER VMS", items=[
                SvcItem(id=f"{bid}:fsvm:{i}", name=f"FSVM {i}", kind="fsvm")
                for i in range(1, f.num_fsvm + 1)], cols=3, noun="FSVMs"))
        items = [SvcItem(id=f"share:{s.uuid}", name=s.name or s.uuid[:8], kind="share",
                         sub=_protocol(s), data={"share": s.name or s.uuid})
                 for s in sorted(shares.get((cl.src(f), f.name or ""), []),
                                 key=lambda s: (natural(s.name), s.uuid))]
        if items:
            lanes.append(SvcLane(id=f"{bid}:shares", title="SHARES", items=items, cols=3,
                                 noun="shares"))
        cluster.boxes.append(SvcBox(
            id=bid, name=f.name or f.uuid[:8], kind="fileserver",
            sub=f"Files {f.version}" if f.version else "Files", lanes=lanes,
            data={"file_server": f.name or f.uuid, "badge": "nus"}))
    clusters = cl.sorted()
    return ServiceModel(type="files", title=TITLES["files"],
                        anchors=_pc_anchors(env, clusters), clusters=clusters)


# ─── design summary ────────────────────────────────────────────────────


def _on(model: ServiceModel) -> str:
    names = [c.name for c in model.clusters if c.boxes]
    return fmt.join_names(names[:4]) + (f" and {len(names) - 4:,} more" if len(names) > 4
                                        else "")


def bullets(model: ServiceModel) -> list[str]:
    """The design summary under the figure: what runs where, in a few lines."""
    out: list[str] = []
    boxes = model.boxes()
    pcs = [short_name(a.sub or a.name) for a in model.anchors if a.id != NDB_ANCHOR]
    if model.type == "nkp":
        out.append(f"{fmt.plural(len(boxes), 'NKP cluster')} on {_on(model)}"
                   + (f", managed from Prism Central {fmt.join_names(pcs)}." if pcs else "."))
        for b in boxes[:3]:
            lanes = {ln.id.rsplit(':', 1)[-1]: ln for ln in b.lanes}
            cp = len(lanes["cp"].items) if "cp" in lanes else 0
            wk = len(lanes["worker"].items) if "worker" in lanes else 0
            parts = [fmt.plural(cp, "control plane node") if cp else None,
                     fmt.plural(wk, "worker") if wk else None]
            text = f"{b.name}: " + (", ".join(p for p in parts if p) or "nodes not reported")
            if b.data.get("role"):
                text += "; the NKP management cluster"
            out.append(text + ".")
        if model.links:
            out.append(f"{fmt.plural(len(model.links), 'cluster')} managed by an NKP "
                       "management cluster.")
    elif model.type == "ndb":
        engines = sorted({b.name for b in boxes})
        dbs = [i for b in boxes for ln in b.lanes for i in ln.items if i.kind == "db"]
        clones = [i for b in boxes for ln in b.lanes for i in ln.items if i.kind == "clone"]
        out.append(f"NDB manages {fmt.plural(len(model.clusters), 'cluster')}: "
                   f"{fmt.join_names([c.name for c in model.clusters][:4])}"
                   + (f" and {len(model.clusters) - 4:,} more" if len(model.clusters) > 4
                      else "") + ".")
        if dbs:
            out.append(f"{fmt.plural(len(dbs), 'database')} on "
                       f"{fmt.plural(len(engines), 'engine')}: {fmt.join_names(engines)}.")
        if clones:
            out.append(f"{fmt.plural(len(clones), 'clone')}, each drawn under the database "
                       "it was cloned from.")
    else:
        noun = "object store" if model.type == "objects" else "file server"
        member = "bucket" if model.type == "objects" else "share"
        out.append(f"{fmt.plural(len(boxes), noun)} on {_on(model)}"
                   + (f", managed from Prism Central {fmt.join_names(pcs)}." if pcs else "."))
        for b in boxes[:3]:
            items = [i for ln in b.lanes for i in ln.items if i.kind == member]
            fsvms = [i for ln in b.lanes for i in ln.items if i.kind == "fsvm"]
            parts = [fmt.plural(len(fsvms), "FSVM") if fsvms else None,
                     fmt.plural(len(items), member)]
            out.append(f"{b.name}: {', '.join(p for p in parts if p)}.")
    out.append("Sizes, versions and settings are listed in the tables of this chapter.")
    return out[:6]


__all__ = ["NDB_ANCHOR", "TITLES", "ServiceModel", "SvcAnchor", "SvcBox", "SvcCluster",
           "SvcItem", "SvcLane", "SvcLink", "bullets", "engine_label", "files_applies",
           "files_model", "from_doc", "ndb_applies", "ndb_model", "nkp_applies",
           "nkp_model", "objects_applies", "objects_model", "to_doc"]
