"""VM compute optimization: does each VM's CPU and memory layout fit its hosts' NUMA layout?

A pure function over a snapshot `Environment` (no API calls). It reviews the CPU topology a
VM presents to its guest (sockets x cores per socket x threads per core, AHV's virtual NUMA
nodes) and its memory against the physical layout of the host it is checked against. It does
**not** judge whether a VM has the right number of vCPUs for its workload: that needs
performance data a snapshot does not hold.

How AHV presents topology (what the rules rely on)
--------------------------------------------------
* The guest sees exactly the sockets, cores per socket and threads per core configured on
  the VM. With vNUMA off (``num_vnuma_nodes`` 0, the default) the guest sees **one** NUMA
  node, however many sockets it has, while AHV may spread its vCPUs and memory over
  several physical nodes. vNUMA is opt-in on AHV and is what makes a wide VM's guest see
  the physical NUMA boundaries.
* For a VM that fits inside one physical NUMA node the socket / core split makes no
  performance difference on AHV (it matters for guest licensing only), so it is not a
  finding here. Two exceptions are kept: vNUMA switched on for a VM that does not need it,
  and a Windows desktop edition shown more sockets than it will use.
* Nutanix APIs give no NUMA node count for a host. The review assumes one NUMA node per
  CPU socket (sub-NUMA clustering off, the platform default), so a node has
  ``cores / sockets`` physical cores and ``memory / sockets`` of memory.

Which host a VM is checked against
----------------------------------
* A powered-on VM: the host it runs on. When the cluster's hosts differ, the smallest host
  is checked too, since HA or ADS can move the VM there.
* A powered-off VM (no host) or one whose host is not in the snapshot: the cluster's
  smallest host, where it may start next. Powered-off VMs are reviewed (their layout
  applies as soon as they start) and are marked so the page can tell them apart.
* A VM whose cluster has no host with a CPU socket and core count, or that has no socket
  count itself, is listed as not checked, with the reason, never guessed.

ESXi clusters: the v4 ESXi schema has sockets and cores but neither threads per core nor a
vNUMA setting, and ESXi sizes the guest's NUMA layout itself. Only the rules that need no
more than that apply there (more vCPUs than cores, an uneven split over the nodes, a desktop
Windows socket limit, the smallest host of a mixed cluster).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

HIGH, MEDIUM, LOW = "high", "medium", "low"
_RANK = {HIGH: 3, MEDIUM: 2, LOW: 1}
GIB = 1024**3

#: Windows desktop editions use at most two CPU sockets (Home one); servers use far more.
_DESKTOP_SOCKET_LIMIT = 2


@dataclass(frozen=True)
class HostShape:
    """The physical layout one VM is checked against (one NUMA node per socket)."""

    uuid: str
    name: str
    sockets: int
    cores: int
    threads: int | None
    memory_bytes: int | None

    @property
    def cores_per_node(self) -> int:
        return max(1, self.cores // self.sockets)

    @property
    def memory_per_node(self) -> int | None:
        return self.memory_bytes // self.sockets if self.memory_bytes else None

    @property
    def has_smt(self) -> bool | None:
        return None if not self.threads else self.threads > self.cores

    def nodes_for(self, vcpus: int, memory: int | None) -> int:
        """Physical NUMA nodes a VM of this size needs on this host (at most all of them)."""
        need = math.ceil(vcpus / self.cores_per_node)
        per = self.memory_per_node
        if memory and per:
            need = max(need, math.ceil(memory / per))
        return max(1, min(need, self.sockets))

    def as_dict(self, basis: str) -> dict[str, Any]:
        return {
            "uuid": self.uuid, "name": self.name, "sockets": self.sockets, "cores": self.cores,
            "cores_per_node": self.cores_per_node,
            "threads_per_core": (self.threads // self.cores) if self.threads else None,
            "memory_bytes": self.memory_bytes, "memory_per_node_bytes": self.memory_per_node,
            "numa_nodes": self.sockets, "basis": basis,
        }


def _shape(host: Any) -> HostShape | None:
    sockets, cores = host.num_cpu_sockets, host.num_cpu_cores
    if not sockets or not cores or sockets <= 0 or cores < sockets:
        return None
    return HostShape(uuid=host.uuid, name=host.name or host.uuid, sockets=sockets, cores=cores,
                     threads=host.num_cpu_threads or None, memory_bytes=host.memory_bytes or None)


def _smallest(shapes: list[HostShape]) -> HostShape:
    return min(shapes, key=lambda h: (h.cores_per_node, h.memory_per_node or 0, h.cores,
                                      h.name))


def _is_ahv(vm: Any) -> bool:
    return (vm.hypervisor or "AHV").upper() in ("AHV", "KVM")


def _is_desktop_windows(guest_os: str | None) -> bool:
    text = (guest_os or "").lower()
    return "windows" in text and "server" not in text and any(
        v in text for v in ("windows 7", "windows 8", "windows 10", "windows 11", "windows10",
                            "windows11"))


def _layout(sockets: int, cores: int, threads: int, vcpus: int, memory: int | None,
            vnuma: int | None) -> dict[str, Any]:
    return {"sockets": sockets, "cores_per_socket": cores, "threads_per_core": threads,
            "vcpus": vcpus, "memory_bytes": memory, "vnuma_nodes": vnuma}


def _fmt_gib(value: int | None) -> str:
    if not value:
        return "?"
    gib = value / GIB
    return f"{gib:.0f} GiB" if gib >= 10 or gib == int(gib) else f"{gib:.1f} GiB"


def _even_vcpus(vcpus: int, nodes: int, host: HostShape) -> int:
    """`vcpus` adjusted to a multiple of `nodes`: up when the nodes have room, else down."""
    if nodes <= 1 or vcpus % nodes == 0:
        return vcpus
    up = vcpus + (nodes - vcpus % nodes)
    if up <= nodes * host.cores_per_node and up <= host.cores:
        return up
    return max(nodes, vcpus - vcpus % nodes)


class _Review:
    """The findings for one VM against one host."""

    def __init__(self) -> None:
        self.reasons: list[dict[str, str]] = []

    def add(self, rule: str, severity: str, title: str, text: str) -> None:
        self.reasons.append({"rule": rule, "severity": severity, "title": title, "text": text})

    @property
    def severity(self) -> str | None:
        if not self.reasons:
            return None
        return max(self.reasons, key=lambda r: _RANK[r["severity"]])["severity"]


def review_vm(vm: Any, host: HostShape, smallest: HostShape | None = None) -> dict[str, Any]:
    """Review one VM against `host` (and the cluster's `smallest` host when it differs).

    Returns ``{"reasons": [...], "severity": ..., "current": ..., "recommended": ...}``;
    no reasons means the VM's layout fits the host.
    """
    ahv = _is_ahv(vm)
    sockets = int(vm.num_vcpus)
    cores = int(vm.num_cores_per_vcpu or 1)
    threads = int(vm.num_threads_per_core or 1)
    vcpus = sockets * cores * threads
    memory = vm.memory_bytes or None
    vnuma = vm.num_numa_nodes if ahv else None
    vnuma_on = (vnuma or 0) > 1
    current = _layout(sockets, cores, threads, vcpus, memory, vnuma)
    r = _Review()

    # 1. More vCPUs than physical cores: the VM can never get a core for each vCPU.
    target_vcpus = vcpus
    if vcpus > host.cores:
        target_vcpus = host.cores
        r.add("vcpus_exceed_cores", HIGH, "More vCPUs than physical cores",
              f"{vcpus} vCPUs on a host with {host.cores} physical cores. The vCPUs wait for "
              f"each other to be scheduled; keep it at {host.cores} or fewer.")

    nodes = host.nodes_for(target_vcpus, memory)
    cpu_spans = target_vcpus > host.cores_per_node
    per_node_mem = host.memory_per_node
    target_vcpus = _even_vcpus(target_vcpus, nodes, host)

    # 2. A VM wider than one NUMA node whose guest is not told (AHV, vNUMA off).
    if ahv and nodes > 1 and not vnuma_on:
        known = vnuma is not None
        where = (f"{host.cores_per_node} cores" if cpu_spans
                 else f"{_fmt_gib(per_node_mem)} of memory")
        what = f"{vcpus} vCPUs" if cpu_spans else f"{_fmt_gib(memory)} of memory"
        tail = (f"Turn on vNUMA with {nodes} nodes so the guest places its work and memory "
                f"on the right node." if known else
                f"This snapshot does not record its vNUMA setting: check it is set to {nodes} "
                f"nodes.")
        if cpu_spans:
            r.add("numa_span_cpu", HIGH if known else MEDIUM,
                  "Spans NUMA nodes, guest sees one",
                  f"{what} do not fit in one NUMA node ({where}), so AHV spreads them over "
                  f"{nodes} nodes while the guest sees a single node. {tail}")
        else:
            r.add("numa_span_memory", MEDIUM, "Memory spans NUMA nodes",
                  f"{what} is more than one NUMA node holds ({where}), so part of it is "
                  f"remote to the vCPUs. {tail}")

    # 3. vNUMA switched on but not matching what the VM needs (AHV).
    if ahv and vnuma_on and vnuma is not None:
        if nodes == 1:
            r.add("vnuma_not_needed", LOW, "vNUMA not needed",
                  f"The VM fits in one NUMA node of this host, yet vNUMA shows the guest "
                  f"{vnuma} nodes and splits its memory between them. Turn vNUMA off.")
        elif vnuma != nodes:
            r.add("vnuma_mismatch", MEDIUM, "vNUMA nodes don't match the host",
                  f"vNUMA is set to {vnuma} nodes but the VM needs {nodes} of this host's "
                  f"{host.sockets} NUMA nodes. Set it to {nodes}.")
        elif sockets % vnuma:
            r.add("socket_straddles_vnuma", MEDIUM, "A socket straddles vNUMA nodes",
                  f"{sockets} sockets cannot be shared evenly over {vnuma} vNUMA nodes, so "
                  f"the guest sees a socket split across nodes. Use {vnuma} sockets.")

    # 4. An uneven split: a wide VM whose vCPUs cannot be divided evenly over its nodes.
    # ESXi sizes the guest's NUMA nodes from its vCPUs, so there only a CPU span counts.
    if nodes > 1 and (ahv or cpu_spans) and vcpus <= host.cores and vcpus % nodes:
        r.add("uneven_split", MEDIUM, "vCPUs don't split evenly",
              f"{vcpus} vCPUs over {nodes} NUMA nodes leaves the nodes unequal. "
              f"{target_vcpus} vCPUs split evenly ({target_vcpus // nodes} per node).")
    elif not ahv and cpu_spans and sockets % nodes and not r.reasons:
        r.add("socket_layout", LOW, "Socket layout doesn't match the host",
              f"{sockets} sockets cannot be shared evenly over the {nodes} NUMA nodes the "
              f"VM spans. {nodes} sockets of {vcpus // nodes} cores match the host.")

    # 5. Guest threads with no hyperthreads underneath (AHV).
    if ahv and threads > 1 and host.has_smt is False:
        r.add("threads_without_smt", MEDIUM, "Guest sees hyperthreads the host lacks",
              f"The guest sees {threads} threads per core, but the host has hyperthreading "
              f"off, so each 'thread' is a full core. Use 1 thread per core.")

    # 6. Desktop Windows uses at most two sockets: the rest of the vCPUs sit idle.
    if sockets > _DESKTOP_SOCKET_LIMIT and _is_desktop_windows(vm.guest_os):
        r.add("guest_socket_limit", HIGH, "Guest can't use all its sockets",
              f"{vm.guest_os} uses at most {_DESKTOP_SOCKET_LIMIT} CPU sockets, so the vCPUs "
              f"on the other {sockets - _DESKTOP_SOCKET_LIMIT} sit unused. Give it fewer "
              f"sockets with more cores each.")

    # 7. Mixed cluster: fits the host it is on, not the smallest one it can move to. The
    # recommendation is then sized for that smallest host.
    rec_host, rec_vcpus, rec_nodes = host, target_vcpus, nodes
    if smallest is not None and smallest.uuid != host.uuid:
        small_vcpus = min(target_vcpus, smallest.cores)
        small_nodes = smallest.nodes_for(small_vcpus, memory)
        # (A VM already wider than its own host is told so by rule 1.)
        if vcpus <= host.cores and (small_nodes > nodes or vcpus > smallest.cores):
            rec_host, rec_nodes = smallest, small_nodes
            rec_vcpus = _even_vcpus(small_vcpus, small_nodes, smallest)
            # Sized for the smaller host, vNUMA is needed after all.
            if small_nodes > 1:
                r.reasons = [x for x in r.reasons if x["rule"] != "vnuma_not_needed"]
            there = (f"have more vCPUs than its {smallest.cores} cores"
                     if vcpus > smallest.cores else f"span {small_nodes} NUMA nodes")
            here = "one NUMA node" if nodes == 1 else f"{nodes} NUMA nodes"
            r.add("smaller_host", MEDIUM, "Doesn't fit the cluster's smallest host",
                  f"It takes {here} on {host.name}, but the cluster also has smaller hosts "
                  f"such as {smallest.name} ({smallest.cores_per_node} cores, "
                  f"{_fmt_gib(smallest.memory_per_node)} per NUMA node). Moved there by HA or "
                  f"ADS, it would {there}.")

    return {"reasons": r.reasons, "severity": r.severity, "current": current,
            "recommended": _recommend(r, current, rec_vcpus, rec_nodes, ahv),
            "sized_for": rec_host.name}


def _recommend(r: _Review, current: dict[str, Any], vcpus: int, nodes: int, ahv: bool
               ) -> dict[str, Any]:
    if not r.reasons:
        return dict(current)
    rules = {x["rule"] for x in r.reasons}
    vnuma = (nodes if nodes > 1 else 0) if ahv else None
    if nodes > 1:
        # Spans nodes: one virtual socket per NUMA node, as on the hardware.
        sockets = nodes
    elif rules & {"vcpus_exceed_cores", "guest_socket_limit"} or vcpus != current["vcpus"]:
        # Fits one node: one socket is what the hardware gives it.
        sockets = 1
    else:
        sockets = current["sockets"]
    if vcpus % sockets:
        sockets = 1
    threads = current["threads_per_core"]
    if "threads_without_smt" in rules or vcpus % (sockets * threads):
        threads = 1
    cores = max(1, vcpus // threads // sockets)
    return _layout(sockets, cores, threads, sockets * cores * threads, current["memory_bytes"],
                   vnuma)


def analyze_compute(env: Any) -> dict[str, Any]:
    """The VM compute review of a snapshot: summary counts, findings and unchecked VMs."""
    by_cluster: dict[str, list[HostShape]] = {}
    by_uuid: dict[str, HostShape] = {}
    for h in env.hosts:
        shape = _shape(h)
        if shape is None:
            continue
        by_uuid[h.uuid] = shape
        for key in filter(None, (h.cluster_uuid, h.cluster_name)):
            by_cluster.setdefault(key, []).append(shape)

    findings: list[dict[str, Any]] = []
    unchecked: list[dict[str, Any]] = []
    reviewed = clean = powered_off = 0
    counts = {HIGH: 0, MEDIUM: 0, LOW: 0}
    for vm in env.vms:
        base = {"id": vm.uuid, "vm": vm.name, "cluster": vm.cluster_name or "",
                "cluster_uuid": vm.cluster_uuid, "power_state": vm.power_state,
                "hypervisor": vm.hypervisor, "guest_os": vm.guest_os,
                "source_pc": vm.source_pc}
        if not vm.num_vcpus:
            unchecked.append({**base, "reason": "The snapshot has no vCPU count for it."})
            continue
        shapes = by_cluster.get(vm.cluster_uuid or "") or by_cluster.get(vm.cluster_name or "")
        if not shapes:
            unchecked.append({**base, "reason": "No host in its cluster has a CPU socket and "
                                                 "core count in this snapshot."})
            continue
        smallest = _smallest(shapes)
        mixed = len({(s.cores_per_node, s.memory_per_node) for s in shapes}) > 1
        on = (vm.power_state or "").upper() == "ON"
        current_host = by_uuid.get(vm.host_uuid or "") if on else None
        host, basis = (current_host, "current") if current_host else (smallest, "smallest")
        result = review_vm(vm, host, smallest if mixed and basis == "current" else None)
        reviewed += 1
        if not on:
            powered_off += 1
        if not result["severity"]:
            clean += 1
            continue
        counts[result["severity"]] += 1
        findings.append({**base, **result, "host": host.as_dict(basis), "mixed_cluster": mixed,
                         "finding": max(result["reasons"],
                                        key=lambda x: _RANK[x["severity"]])["title"]})

    findings.sort(key=lambda f: (-_RANK[f["severity"]], f["cluster"].lower(), f["vm"].lower()))
    return {
        "summary": {"reviewed": reviewed, "to_optimize": len(findings), "clean": clean,
                    "high": counts[HIGH], "medium": counts[MEDIUM], "low": counts[LOW],
                    "not_checked": len(unchecked), "powered_off": powered_off},
        "findings": findings,
        "not_checked": unchecked,
        "assumptions": [
            "One NUMA node per CPU socket (Nutanix APIs report no NUMA node count).",
            "Powered-off VMs are checked against the smallest host in their cluster.",
        ],
    }


def optimization_reviews(env: Any) -> dict[str, Any]:
    """Every optimization review of a snapshot (one for now: VM compute)."""
    compute = analyze_compute(env)
    s = compute["summary"]
    n = s["to_optimize"]
    return {
        "reviews": [{
            "id": "vm_compute",
            "title": "VM compute",
            "description": "vCPU and memory layouts that don't fit their hosts' NUMA layout.",
            "headline": f"{n} VM{'s' if n != 1 else ''} to review" if n else "Nothing to review",
            "count": n,
            "severity": HIGH if s["high"] else MEDIUM if s["medium"] else LOW if s["low"]
            else None,
            "reviewed": s["reviewed"],
        }],
        "vm_compute": compute,
    }
