"""Host network configuration (A4): model, facts, design summary and table rows.

AHV hosts are grouped into host network *profiles*: hosts whose virtual switches are
configured alike (per switch: name, default flag, bond mode, LACP, MTU, uplink count and
link speeds) share a profile. Profiles are lettered A, B, C by host count (most first).
One figure per profile draws its switches, bonds, uplinks and the LLDP neighbours one
representative host reports (the first by cluster and host name).

Only collected data is drawn. ESXi host networking (vSwitch, dvSwitch, teaming, LACP,
MTU) is not reported by the Nutanix APIs (decision 8): it is stated in a note, never
drawn. LLDP neighbours appear only when a NIC reports one; a switch port only when
``switch_port_id`` is set.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from ntnxvision_core.models import Environment

from . import fmt
from .facts import hypervisor_label

#: Profiles drawn as figures; the profiles table lists every one.
MAX_DRAWN = 3

_BOND = {"ACTIVE_BACKUP": "Active-backup", "BALANCE_SLB": "Balance-slb",
         "BALANCE_TCP": "Balance-tcp", "NONE": "No bond"}


# ─── model ─────────────────────────────────────────────────────────────


@dataclass
class Traffic:
    """Something that uses a virtual switch, as the data shows it."""

    id: str
    label: str  # "AHV host", "CVM", "Guest VMs"
    sub: str  # "management", "external", "5 subnets"


@dataclass
class Uplink:
    id: str
    name: str  # eth0
    speed_mbps: int | None = None
    up: bool | None = None
    #: The LLDP neighbour key ("A") this NIC reports, when it reports one.
    switch: str | None = None


@dataclass
class VSwitch:
    id: str
    name: str
    default: bool = False
    bridge: str | None = None
    bond: str | None = None  # br0-up
    bond_mode: str | None = None  # ACTIVE_BACKUP / BALANCE_SLB / BALANCE_TCP / NONE
    lacp: bool = False
    mtu: int | None = None
    uplinks: list[Uplink] = field(default_factory=list)
    traffic: list[Traffic] = field(default_factory=list)


@dataclass
class Neighbour:
    """An upstream switch as LLDP / CDP reports it (never inferred)."""

    id: str
    key: str  # "A"
    model: str | None = None
    chassis: str | None = None
    port: str | None = None
    seen_on: list[str] = field(default_factory=list)


@dataclass
class Profile:
    id: str
    letter: str
    hosts: int
    clusters: list[str] = field(default_factory=list)
    switches: list[VSwitch] = field(default_factory=list)
    neighbours: list[Neighbour] = field(default_factory=list)
    #: The host the switches, NICs and LLDP data are drawn from.
    host: str | None = None
    nic_model: str | None = None
    nic_driver: str | None = None
    nic_firmware: str | None = None


@dataclass
class EsxiNote:
    clusters: list[str] = field(default_factory=list)
    hosts: int = 0
    #: Physical NIC names per host ("vmnic0" ...) and their link speeds (Mb/s).
    nics: list[str] = field(default_factory=list)
    speeds: list[int] = field(default_factory=list)
    port_groups: int = 0
    vcenters: list[str] = field(default_factory=list)


@dataclass
class NetworkModel:
    profiles: list[Profile] = field(default_factory=list)
    esxi: EsxiNote | None = None
    #: AHV clusters with collected host networking.
    ahv_clusters: int = 0
    title: str = "Host network configuration"




def _ident_hash(ident: str) -> str:
    """A neighbour's id part from its identity (chassis MAC, device id or model), so
    the id survives a change in the order neighbours are seen."""
    return hashlib.sha256(ident.encode()).hexdigest()[:10]

_DIGITS = re.compile(r"(\d+)")


def natural(name: str) -> tuple[Any, ...]:
    """Natural sort key: eth2 before eth10, vmnic9 before vmnic10."""
    return tuple(int(p) if p.isdigit() else p.lower() for p in _DIGITS.split(name))

def to_doc(m: NetworkModel) -> dict[str, Any]:
    return asdict(m)


def from_doc(d: dict[str, Any]) -> NetworkModel:
    def vswitch(v: dict[str, Any]) -> VSwitch:
        return VSwitch(**{**v, "uplinks": [Uplink(**u) for u in v.get("uplinks", [])],
                          "traffic": [Traffic(**t) for t in v.get("traffic", [])]})

    def profile(p: dict[str, Any]) -> Profile:
        return Profile(**{**p, "switches": [vswitch(v) for v in p.get("switches", [])],
                          "neighbours": [Neighbour(**n) for n in p.get("neighbours", [])]})

    esxi = d.get("esxi")
    return NetworkModel(profiles=[profile(p) for p in d.get("profiles", [])],
                        esxi=EsxiNote(**esxi) if esxi else None,
                        ahv_clusters=int(d.get("ahv_clusters", 0)),
                        title=d.get("title", "Host network configuration"))


# ─── text helpers ──────────────────────────────────────────────────────


def speed_text(mbps: int | None) -> str | None:
    if not mbps:
        return None
    if mbps >= 1000:
        return f"{mbps / 1000:,.10g} GbE"
    return f"{mbps:,} Mb/s"


def bond_text(mode: str | None, lacp: bool) -> str:
    if not mode:
        return "Bond mode N/A"
    label = _BOND.get(mode.upper(), mode.replace("_", " ").capitalize())
    return f"{label} with LACP" if lacp and mode.upper() != "NONE" else label


def uplinks_text(v: VSwitch) -> str:
    """"2 x 25 GbE: eth0, eth1"; mixed speeds list each, unknown ones count uplinks."""
    names = ", ".join(u.name for u in v.uplinks)
    if not v.uplinks:
        return "No uplinks reported"
    speeds = Counter(u.speed_mbps for u in v.uplinks)
    if None in speeds:
        return f"{fmt.plural(len(v.uplinks), 'uplink')}: {names}"
    parts = [f"{n:,} x {speed_text(s)}" for s, n in sorted(speeds.items(),
                                                          key=lambda kv: kv[0] or 0)]
    return f"{', '.join(parts)}: {names}"


def mtu_text(mtu: int | None) -> str:
    return f"MTU {mtu:,}" if mtu else "MTU N/A"


def switch_model(vendor: str | None) -> str | None:
    """The switch model from an LLDP system description: "... running on an Arista
    DCS-7050SX3-48YC8" gives "Arista DCS-7050SX3-48YC8"; anything else as reported."""
    if not vendor:
        return None
    m = re.search(r"running on (?:an? )?(.+)$", vendor.strip(), re.IGNORECASE)
    return (m.group(1) if m else vendor).strip() or None


def nic_short(model: str | None) -> str | None:
    """A short NIC model: "Mellanox ConnectX-6 Lx", "Intel X710"."""
    if not model:
        return None
    vendor = model.split()[0]
    bracket = re.search(r"\[([^\]]+)\]", model)
    if bracket:
        return f"{vendor} {bracket.group(1)}"
    part = re.search(r"\b([A-Z]{1,4}\d{2,4}[A-Z0-9-]*)\b", model[len(vendor):])
    return f"{vendor} {part.group(1)}" if part else model


def name_list(names: list[str], limit: int = 4) -> str:
    """Up to `limit` names joined; more read "A, B, C and 12 more clusters"."""
    if len(names) <= limit:
        return fmt.join_names(names)
    rest = len(names) - (limit - 1)
    return f"{', '.join(names[:limit - 1])} and {fmt.plural(rest, 'more cluster')}"


def _range(names: list[str]) -> str:
    """"vmnic0 to vmnic3" for a consecutive numbered run, else the names joined."""
    m = [re.fullmatch(r"(.*?)(\d+)", n) for n in names]
    if len(names) > 2 and all(m):
        stems = {x.group(1) for x in m if x}
        nums = [int(x.group(2)) for x in m if x]
        if len(stems) == 1 and nums == list(range(nums[0], nums[0] + len(nums))):
            return f"{names[0]} to {names[-1]}"
    return fmt.join_names(names)


# ─── facts ─────────────────────────────────────────────────────────────


def _hid(prefix: str, *parts: object) -> str:
    return f"{prefix}:{hashlib.sha256(repr(parts).encode()).hexdigest()[:12]}"


def _is_esx(types: list[str]) -> bool:
    return any((hypervisor_label(t) or "") == "ESXi" for t in types)


def applies(env: Environment) -> bool:
    """Decision 1: drawn only when host networking was collected (AHV switches with
    their hosts)."""
    return any(vs.hosts for vs in env.virtual_switches)


def network_model(env: Environment) -> NetworkModel:
    clusters = {c.uuid: c for c in env.clusters}

    def cname(uuid: str | None, fallback: str | None) -> str:
        c = clusters.get(uuid or "")
        return (c.name if c and c.name else fallback) or (uuid or "N/A")

    nics: dict[tuple[str, str], Any] = {}
    for n in sorted(env.host_nics,
                    key=lambda n: (n.host_uuid or "", natural(n.name or ""), n.uuid)):
        if n.host_uuid and n.name:
            nics.setdefault((n.host_uuid, n.name), n)

    # Each AHV host's switches: [(switch, membership)] keyed by host uuid.
    per_host: dict[str, list[tuple[Any, Any]]] = {}
    host_cluster: dict[str, tuple[str, str, str]] = {}  # uuid -> (cluster, host name, cl uuid)
    for vs in sorted(env.virtual_switches,
                     key=lambda v: (v.cluster_uuid or "", v.name or "", v.uuid)):
        for h in vs.hosts:
            if not h.host_uuid:
                continue
            per_host.setdefault(h.host_uuid, []).append((vs, h))
            host_cluster[h.host_uuid] = (cname(vs.cluster_uuid, vs.cluster_name),
                                         h.host_name or h.host_uuid, vs.cluster_uuid or "")

    def speeds(host: str, names: list[str]) -> tuple[int | None, ...]:
        return tuple(sorted((getattr(nics.get((host, n)), "speed_mbps", None) or 0)
                            for n in names))

    def signature(host: str) -> tuple[Any, ...]:
        rows = []
        for vs, h in per_host[host]:
            rows.append((vs.name or "", bool(vs.is_default), (vs.bond_mode or "").upper(),
                         vs.lacp, vs.mtu or 0, len(h.uplinks), speeds(host, h.uplinks)))
        return tuple(sorted(rows, key=lambda r: (not r[1], r[0])))

    groups: dict[tuple[Any, ...], list[str]] = {}
    for host in sorted(per_host, key=lambda u: (host_cluster[u][0].lower(),
                                                host_cluster[u][1].lower(), u)):
        groups.setdefault(signature(host), []).append(host)

    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), host_cluster[kv[1][0]][0]
                                                    .lower(), repr(kv[0])))
    profiles = [_profile(env, sig, hosts, i, per_host, host_cluster, nics)
                for i, (sig, hosts) in enumerate(ordered)]
    ahv = {host_cluster[h][2] for h in per_host}
    return NetworkModel(profiles=profiles, esxi=_esxi(env), ahv_clusters=len(ahv))


def _letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(ord("A") + r) + s
    return s


def _profile(env: Environment, sig: tuple[Any, ...], hosts: list[str], i: int,
             per_host: dict[str, list[tuple[Any, Any]]],
             host_cluster: dict[str, tuple[str, str, str]],
             nics: dict[tuple[str, str], Any]) -> Profile:
    pid = _hid("profile", sig)
    rep = hosts[0]
    cl_uuids = {host_cluster[h][2] for h in hosts}
    # Guest subnets per switch name and cluster.
    subnets: dict[str, dict[str, set[str]]] = {}
    for s in env.subnets:
        if s.virtual_switch and s.cluster_uuid in cl_uuids:
            subnets.setdefault(s.virtual_switch, {}).setdefault(
                s.cluster_uuid or "", set()).add(s.name or s.uuid)

    # LLDP neighbours on the representative host, keyed A, B, ... by first NIC name.
    rep_nics = sorted((n for (h, _), n in nics.items() if h == rep and n.has_neighbour),
                      key=lambda n: (natural(n.name or ""), n.uuid))
    keys: dict[str, str] = {}
    neighbours: list[Neighbour] = []
    for n in rep_nics:
        ident = (n.switch_mac or n.switch_device_id or n.switch_vendor or "").lower()
        if ident not in keys:
            keys[ident] = _letter(len(keys))
            neighbours.append(Neighbour(
                id=f"{pid}:lldp:{_ident_hash(ident)}", key=keys[ident],
                model=switch_model(n.switch_vendor) or n.switch_device_id,
                chassis=n.switch_mac.upper() if n.switch_mac else None,
                port=None))
        nb = neighbours[[x.key for x in neighbours].index(keys[ident])]
        nb.seen_on.append(n.name or "N/A")
        if n.switch_port_id:  # "Ethernet1/1 (eth0), Ethernet1/2 (eth2)"
            entry = f"{n.switch_port_id} ({n.name})"
            nb.port = f"{nb.port}, {entry}" if nb.port else entry

    switches = []
    for vs, h in sorted(per_host[rep], key=lambda vh: (not vh[0].is_default,
                                                      vh[0].name or "")):
        vid = f"{pid}:vs:{vs.name}"
        ups = []
        for name in sorted(h.uplinks, key=natural):
            n = nics.get((rep, name))
            sw = None
            if n is not None and n.has_neighbour:
                sw = keys.get((n.switch_mac or n.switch_device_id or n.switch_vendor
                               or "").lower())
            ups.append(Uplink(id=f"{vid}:nic:{name}", name=name,
                              speed_mbps=n.speed_mbps if n is not None else None,
                              up=n.link_up if n is not None else None, switch=sw))
        traffic = []
        # Only what the data shows: host management where the switch carries the host's
        # address (the CVM's external network is not reported per switch).
        if vs.is_default and h.ip_address:
            traffic.append(Traffic(f"{vid}:t:host", "AHV host", "management"))
        if vs.name in subnets:
            traffic.append(Traffic(f"{vid}:t:guest", "Guest VMs",
                                   _subnets_text(subnets[vs.name], len(cl_uuids))))
        switches.append(VSwitch(
            id=vid, name=vs.name or "N/A", default=bool(vs.is_default), bridge=h.bridge,
            bond=h.bond_name, bond_mode=(h.bond_type or vs.bond_mode),
            lacp=vs.lacp or (h.lacp_status or "NIL").upper() not in ("NIL", ""),
            mtu=vs.mtu, uplinks=ups, traffic=traffic))
    rep_all = sorted((n for (hh, _), n in nics.items() if hh == rep),
                     key=lambda n: (natural(n.name or ""), n.uuid))
    models = Counter(n.model for n in rep_all if n.model)
    drivers = Counter((n.driver_version or "").split(":")[0] for n in rep_all
                      if n.driver_version)
    fw = Counter((n.firmware_version or "").split()[0] for n in rep_all
                 if n.firmware_version and n.firmware_version.split())

    def top(c: Counter[str]) -> str | None:
        return sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if c else None

    return Profile(id=pid, letter=_letter(i), hosts=len(hosts),
                   clusters=sorted({host_cluster[h][0] for h in hosts}, key=str.lower),
                   switches=switches, neighbours=neighbours, host=host_cluster[rep][1],
                   nic_model=top(models), nic_driver=top(drivers), nic_firmware=top(fw))


def _subnets_text(per_cluster: dict[str, set[str]], clusters: int) -> str:
    """"5 subnets" (one cluster), "5 subnets per cluster", "4 to 6 subnets per cluster";
    clusters of the profile without a subnet on the switch count as 0."""
    counts = [len(v) for v in per_cluster.values()] + [0] * (clusters - len(per_cluster))
    lo, hi = min(counts), max(counts)
    if clusters == 1:
        return fmt.plural(hi, "subnet")
    if lo == hi:
        return f"{fmt.plural(hi, 'subnet')} per cluster"
    return f"{lo:,} to {hi:,} subnets per cluster"


def _esxi(env: Environment) -> EsxiNote | None:
    esx = sorted((c for c in env.clusters if _is_esx(c.hypervisor_types)),
                 key=lambda c: ((c.name or "").lower(), c.uuid))
    if not esx:
        return None
    ids = {c.uuid for c in esx}
    hosts = sorted((h for h in env.hosts if h.cluster_uuid in ids),
                   key=lambda h: ((h.name or "").lower(), h.uuid))
    note = EsxiNote(clusters=list(dict.fromkeys(c.name or c.uuid for c in esx)),
                    hosts=len(hosts) or sum(c.num_nodes or 0 for c in esx),
                    port_groups=sum(1 for p in env.port_groups if p.cluster_uuid in ids),
                    vcenters=sorted({c.vcenter_address for c in esx if c.vcenter_address}))
    if hosts:
        first = sorted((n for n in env.host_nics if n.host_uuid == hosts[0].uuid and n.name),
                       key=lambda n: (natural(n.name or ""), n.uuid))
        note.nics = [n.name or "" for n in first]
        note.speeds = [n.speed_mbps or 0 for n in first]
    return note


# ─── design summary and table ──────────────────────────────────────────


def esxi_lines(e: EsxiNote) -> list[str]:
    """The note's lines: what is not drawn, what was collected, where to look."""
    who = f"{name_list(e.clusters, 3)}, {fmt.plural(e.hosts, 'host')}"
    out = [f"ESXi hosts ({who}): vSwitch and dvSwitch, teaming, LACP and MTU are not "
           "drawn."]
    got = []
    if e.nics:
        sp = Counter(s for s in e.speeds if s)
        speed = ", ".join(f"{n:,} x {speed_text(s)}" for s, n in sorted(sp.items()))
        got.append(f"{_range(e.nics)} per host" + (f" ({speed} link speed)" if speed else ""))
    if e.port_groups:
        got.append(fmt.plural(e.port_groups, "port group"))
    if got:
        out.append(f"Collected: {' and '.join(got)}.")
    if e.vcenters:
        out.append(f"Source of truth for this network: vCenter {fmt.join_names(e.vcenters)}.")
    return out


def _role(v: VSwitch) -> str:
    parts = []
    labels = [t.label for t in v.traffic]
    if "AHV host" in labels:
        parts.append("host management")
    if "CVM" in labels:
        parts.append("CVM")
    guest = next((t.sub for t in v.traffic if t.label == "Guest VMs"), None)
    if guest:
        parts.append(f"{guest.replace('subnet', 'guest subnet')}")
    return fmt.join_names(parts)


def _switch_phrase(v: VSwitch) -> str:
    sp = Counter(u.speed_mbps for u in v.uplinks)
    if v.uplinks and None not in sp and len(sp) == 1:
        over = f"{len(v.uplinks):,} x {speed_text(next(iter(sp)))}"
    else:
        over = fmt.plural(len(v.uplinks), "uplink")
    return f"{_lower_first(bond_text(v.bond_mode, v.lacp))} over {over}, {mtu_text(v.mtu)}"


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def bullets(m: NetworkModel) -> list[str]:
    out: list[str] = []
    if not m.profiles:
        return out
    a = m.profiles[0]
    n = len(m.profiles)
    if n == 1:
        out.append(f"One host network profile across {fmt.plural(m.ahv_clusters, 'AHV cluster')}"
                   f" ({fmt.plural(a.hosts, 'host')}).")
    else:
        drawn = ""
        if n > MAX_DRAWN:
            last = m.profiles[MAX_DRAWN - 1].letter
            drawn = ("; only profile A is drawn" if MAX_DRAWN == 1
                     else f"; profiles A to {last} are drawn")
        out.append(f"{n:,} host network profiles across "
                   f"{fmt.plural(m.ahv_clusters, 'AHV cluster')}; profile A applies to "
                   f"{fmt.plural(a.hosts, 'host')}{drawn}.")
    if len(a.switches) <= 2:
        for v in a.switches:
            role = _role(v)
            lead = f"{v.name} carries {role}: " if role else f"{v.name}: "
            out.append(f"{lead}{_switch_phrase(v)}.")
    else:
        out.append("Profile A switches: " + "; ".join(
            f"{v.name} ({_lower_first(bond_text(v.bond_mode, v.lacp))}, {mtu_text(v.mtu)})"
            for v in a.switches) + ".")
    if a.neighbours:
        models = sorted({x.model for x in a.neighbours if x.model})
        ports = all(x.port for x in a.neighbours)
        count = fmt.plural(len(a.neighbours), "upstream switch", "upstream switches")
        out.append(f"LLDP reports {count}"
                   + (f" ({fmt.join_names(models)})" if models else "")
                   + ("." if ports else "; switch ports are not reported."))
    else:
        out.append("No LLDP neighbour is reported on profile A's uplinks.")
    if m.esxi:
        tail = "; see vCenter." if m.esxi.vcenters else "."
        out.append(f"ESXi host networking ({name_list(m.esxi.clusters, 3)}) is not "
                   f"available from Nutanix APIs{tail}")
    return out[:5]


def table(m: NetworkModel) -> tuple[list[str], list[list[object]]]:
    """Profile | Clusters | Hosts | "<switch>: bond, uplinks, MTU" per switch name |
    NIC model | LLDP switch."""
    names: list[str] = []
    for p in m.profiles:
        for v in p.switches:
            if v.name not in names:
                names.append(v.name)
    default = [v.name for p in m.profiles for v in p.switches if v.default]
    names.sort(key=lambda s: (s not in default, s))
    headers = ["Profile", "Clusters", "Hosts", *[f"{s}: bond, uplinks, MTU" for s in names],
               "NIC model", "LLDP switch"]
    rows: list[list[object]] = []
    for p in m.profiles:
        by = {v.name: v for v in p.switches}
        cells: list[object] = []
        for s in names:
            sw = by.get(s)
            if sw is None:
                cells.append(None)
                continue
            up = uplinks_text(sw).split(":")[0]
            cells.append(f"{bond_text(sw.bond_mode, sw.lacp)}, {up}, "
                         f"{f'{sw.mtu:,}' if sw.mtu else 'N/A'}")
        models = sorted({x.model for x in p.neighbours if x.model})
        rows.append([p.letter, ", ".join(p.clusters), p.hosts, *cells, nic_short(p.nic_model),
                     ", ".join(models) if models else "Not reported"])
    if m.esxi:
        rows.append(["ESXi", ", ".join(m.esxi.clusters), m.esxi.hosts,
                     *(["Not reported (see vCenter)" if m.esxi.vcenters else "Not reported"]
                       + ["Not reported"] * (len(names) - 1)),
                     "N/A (link speed only)", "Not reported"])
    return headers, rows
