"""The demo estate: which synthetic system answers at which demo hostname.

Every host is under ``.demo.local`` (a reserved-looking name nothing real resolves) and
exercises a different path through the app:

==========================  ===============================================================
``pc01.demo.local``         Prism Central pc.2024.3, ``medium`` (1 214 VMs, 4 clusters).
                            Paired with pc02; Metro (Sync) and NearSync policies.
                            v4 for most areas; Objects v4 service down (503, v3 used);
                            Files not deployed; 3 ESXi VMs fail on v4 (VMs "with notes");
                            alerts throttled once (429 → retried).
``pc02.demo.local``         pc.7.3, ``small`` (~1 590 VMs). All v4; Files deployed; Objects
                            and Flow virtual networking not deployed. Scale-out PC (3
                            PC VMs, Large); a Pure FlashArray on SEA-PROD-02 (v4.3).
``pc03.demo.local``         pc.2024.1 — below the v4 GA line, so every area falls back to
                            v3 and the v4-only areas (licensing, LCM) are skipped.
``pc04.demo.local``         A tiny PC reporting the numeric version 7.5.1.1 (no ``pc.``
                            prefix): reports must show ``pc.7.5.1.1`` in full.
``legacy-pc.demo.local``    pc.2023.4 — outside the supported range (warn, may proceed).
``pe01.demo.local``         Prism Element, AOS 7.0, 6 hosts / 212 VMs (v2.0 API).
``ndb.demo.local``          NDB (Era API) with 12 database servers.
``selfsigned.demo.local``   A tiny PC presenting a self-signed certificate.
``tiny.demo.local``         The ``tiny`` profile (1 cluster, 40 VMs).
``down.demo.local``         Never resolves — the unreachable case.
``large1..3.demo.local``    Three Prism Centrals of ~6 700 VMs each: the 20 000-VM estate.
                            large1 has 2U4N blocks (four nodes per chassis); large2
                            a Dell PowerFlex (groups fallback); large3 no LLDP.
``vcenter.demo.local``      VMware vCenter (vSphere REST) of pc01's ESXi cluster AMS-EDGE-02,
                            also at 198.51.100.20, the address Prism reports for it.
==========================  ===============================================================

Scenario estates for the architecture diagrams (`SCENARIOS`; `scenarios.collect_scenario`
collects a scenario's hosts and combines them):

* ``small``: ``sm-pc01`` (1 PC, 2 AHV clusters, one remote DR AZ);
* ``medium``: ``md-pc01`` / ``md-pc02`` (2 paired PCs replicating both ways, 6 clusters,
  AHV and ESXi, Nutanix / Dell / HPE / Lenovo / Cisco hardware);
* ``enterprise``: ``en-pc01..04`` (4 PCs on 4 sites, 16 clusters, a scale-out PC,
  NearSync and Async, Xi and a remote PC outside the inventory);
* ``xlarge``: ``xl-pc01..08`` (8 PCs, 104 clusters with long names, a chain of paired
  PCs, every PC replicating to a remote vault AZ).

Any username works; any non-empty password except ``wrong`` is accepted.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ntnxvision_core.synthetic.estate import (
    PROFILES,
    XL_SITES,
    AzSpec,
    Features,
    PolicySpec,
    build_ndb_estate,
    build_pc_estate,
    build_pe_estate,
)
from ntnxvision_core.synthetic.mockapi import Target

DEMO_DOMAIN = ".demo.local"

#: The address pc01's ESXi cluster reports as its vCenter (a documentation range, so it
#: never reaches a real host); the mock vCenter answers there and at vcenter.demo.local.
VCENTER_ADDRESS = "198.51.100.20"
VCENTER_HOST = "vcenter.demo.local"


def _pc(host: str, profile: str, version: str, features: Features | None = None,
        tls: str = "ca") -> Target:
    def build(epoch: int) -> Any:
        return build_pc_estate(host, profile, version, epoch=epoch,
                               features=features or Features())
    return Target(host=host, kind="pc", build=build, tls=tls)


def demo_targets() -> dict[str, Target]:
    targets: list[Target] = [
        _pc("pc01.demo.local", "medium", "pc.2024.3", Features(
            v4_unavailable={"objects"}, v4_absent={"files"}, files=False,
            alerts_rate_limited=True, esxi_bad_vms=3, lcm_framework_stale=True,
            vcenter_address=VCENTER_ADDRESS,
            # Paired with pc02; a Metro (Sync) policy inside the AZ and a NearSync one
            # to pc02's SEA-PROD-01.
            az_peers=("pc02.demo.local",),
            extra_policies=(("Metro-PHX-Sync", 0, "PHX-PROD-01", "", "PHX-DR-01"),
                            ("NearSync-PHX-SEA", 60, "PHX-PROD-01", "pc02.demo.local",
                             "SEA-PROD-01")))),
        # Scale-out (3 PC VMs, LARGE) and a Pure FlashArray on SEA-PROD-02, listed by the
        # clustermgmt v4.3 external storage endpoint this PC serves.
        _pc("pc02.demo.local", "small", "pc.7.3", Features(
            objects=False, fvn=False, v4_absent={"objects"}, pc_vm_count=3, pc_size="LARGE",
            az_peers=("pc01.demo.local",),
            external_storage=((1, "EVERPURE_FLASHARRAY"),),
            v4_minor={"clustermgmt": 3, "networking": 3, "prism": 3, "datapolicies": 2,
                      "dataprotection": 3})),
        _pc("pc03.demo.local", "legacy", "pc.2024.1", Features(files=False, ncm=False)),
        _pc("pc04.demo.local", "tiny", "7.5.1.1", Features(files=False, objects=False)),
        _pc("legacy-pc.demo.local", "tiny", "pc.2023.4", Features(files=False, objects=False)),
        _pc("selfsigned.demo.local", "tiny", "pc.7.3", tls="self"),
        _pc("tiny.demo.local", "tiny", "pc.7.3", Features(files=False, objects=False)),
        Target(host="pe01.demo.local", kind="pe",
               build=lambda epoch: build_pe_estate("pe01.demo.local", version="7.0",
                                                   epoch=epoch)),
        Target(host="ndb.demo.local", kind="ndb",
               build=lambda _epoch: build_ndb_estate("ndb.demo.local")),
        Target(host="down.demo.local", kind="pc", build=lambda _e: None, reachable=False),
    ]
    for i in (1, 2, 3):
        # large2 has a Dell PowerFlex on PROD-A, found through the v3 groups fallback
        # (its PC serves clustermgmt up to v4.2); large3 hosts report no LLDP neighbours.
        targets.append(_pc(f"large{i}.demo.local", "large", "pc.7.3",
                           Features(files=i == 1, objects=i == 2, lldp=i != 3,
                                    nodes_per_block=4 if i == 1 else 1,
                                    external_storage=((0, "DELL_POWERFLEX"),) if i == 2
                                    else ())))
    targets.extend(scenario_targets())
    # pc01's ESXi cluster is registered to a vCenter, reachable by name or by the address
    # Prism reports for it; it serves pc01's estate at pc01's current epoch.
    pc01 = targets[0]
    for vc_host in (VCENTER_HOST, VCENTER_ADDRESS):
        targets.append(Target(host=vc_host, kind="vcenter", build=lambda _e: None,
                              source=pc01))
    return {t.host: t for t in targets}


def make_resolver(targets: dict[str, Target]) -> Callable[[str], Target | None]:
    def resolve(host: str) -> Target | None:
        return targets.get(host.lower().strip("."))
    return resolve


# ─── scenario estates ──────────────────────────────────────────────────

_SYNC = 0
_NEAR = 60
_HOUR = 3600
_DAY = 86400


def _p(name: str, rpo: int, target: str) -> PolicySpec:
    return PolicySpec(name=name, rpo=rpo, target=target)


def _xl_host(i: int) -> str:
    # The last site has a long PC name, as some estates do.
    return ("xl-pc08-apac-sydney-management-plane.demo.local" if i == 8
            else f"xl-pc{i:02d}.demo.local")


def _xl_cluster(site: int, index: int) -> str:
    """The name of cluster `index` of very-large site `site` (1-based)."""
    return PROFILES[f"xl-{site}"][index].name


#: Scenario name -> the demo hosts it combines.
SCENARIOS: dict[str, list[str]] = {
    "small": ["sm-pc01.demo.local"],
    "medium": ["md-pc01.demo.local", "md-pc02.demo.local"],
    "enterprise": [f"en-pc0{i}.demo.local" for i in range(1, 5)],
    "xlarge": [_xl_host(i) for i in range(1, 9)],
}

_V43 = {"clustermgmt": 3, "networking": 3, "prism": 3, "datapolicies": 2,
        "dataprotection": 3}


def scenario_targets() -> list[Target]:
    """The scenario estates. Paired demo PCs use `az_peers` and per-direction
    `extra_policies` (so one-way and both-way replication, Sync, NearSync and Async all
    occur); AZs outside the inventory use `azs` / `policies`."""
    dr_colo = AzSpec("dr-colo", "https://dr-colo.example.lab:9440", region="us-east")
    xi_eu = AzSpec("Xi-EU-West", "https://xi.nutanix.com", type="XI", region="eu-west")
    dr_apac = AzSpec("dr-apac", "https://dr-apac.example.lab:9440", region="ap-southeast")
    vault = AzSpec("dr-vault", "https://dr-vault.example.lab:9440", region="us-central")
    md1, md2 = SCENARIOS["medium"]
    en1, en2, en3, en4 = SCENARIOS["enterprise"]
    out = [
        _pc("sm-pc01.demo.local", "small-branch", "pc.7.5.1.1", Features(
            objects=False, fvn=False, files=False, azs=(dr_colo,),
            policies=(_p("Branch-4h", 4 * _HOUR, "dr-colo"),))),
        # md-pc01 to md-pc02: NearSync 1 min and Async 1 h; back: Async 1 h only.
        _pc(md1, "med-east", "pc.7.5.1.1", Features(
            objects=False, vcenter_address="198.51.100.31", azs=(), az_peers=(md2,),
            extra_policies=(("SQL-NearSync", _NEAR, "PROD-E01", md2, "PROD-W01"),
                            ("Prod-1h", _HOUR, "PROD-E02", md2, "DR-W01")))),
        _pc(md2, "med-west", "pc.7.5.1.1", Features(
            files=False, vcenter_address="198.51.100.32", azs=(), az_peers=(md1,),
            extra_policies=(("West-Prod-1h", _HOUR, "PROD-W01", md1, "PROD-E01"),))),
        # A scale-out PC on its management cluster, Sync / NearSync / Async to Chicago,
        # a Pure FlashArray on the database cluster.
        _pc(en1, "ent-nyc", "pc.7.5.1.1", Features(
            pc_vm_count=3, pc_size="LARGE", pc_cluster=4, v4_minor=dict(_V43),
            vcenter_address="198.51.100.41", azs=(), az_peers=(en2,),
            external_storage=((2, "EVERPURE_FLASHARRAY"),),
            extra_policies=(("Tier0-Metro", _SYNC, "NYC-PROD-01", en2, "CHI-PROD-01"),
                            ("Tier1-NearSync", _NEAR, "NYC-DB-01", en2, "CHI-DR-01"),
                            ("Tier2-1h", _HOUR, "NYC-PROD-02", en2, "CHI-DR-01")))),
        _pc(en2, "ent-chi", "pc.7.5.1.1", Features(
            files=False, vcenter_address="198.51.100.42", azs=(), az_peers=(en1,),
            extra_policies=(("CHI-Prod-1h", _HOUR, "CHI-PROD-01", en1, "NYC-PROD-02"),))),
        # London and Singapore replicate to each other with different RPOs, and each
        # to an AZ outside the inventory; a Dell PowerStore on LON-PROD-01.
        _pc(en3, "ent-lon", "pc.7.3", Features(
            objects=False, vcenter_address="198.51.100.43", az_peers=(en4,),
            external_storage=((0, "DELL_POWERSTORE"),), v4_minor=dict(_V43),
            azs=(xi_eu,), policies=(_p("LON-Cloud-Daily", _DAY, "Xi-EU-West"),),
            extra_policies=(("LON-4h", 4 * _HOUR, "LON-PROD-01", en4, "SIN-PROD-01"),))),
        _pc(en4, "ent-sin", "pc.7.3", Features(
            files=False, objects=False, az_peers=(en3,), azs=(dr_apac,),
            policies=(_p("SIN-Vault-Daily", _DAY, "dr-apac"),),
            extra_policies=(("SIN-Daily", _DAY, "SIN-PROD-01", en3, "LON-PROD-02"),))),
    ]
    xl = SCENARIOS["xlarge"]
    # Chain 1-2-3-4 (one linked group larger than a part), pairs 5-6 and 7-8. 1 <-> 2
    # and 3 <-> 4 Async both ways, 2 -> 3 NearSync one way, 5 <-> 6 Sync both ways,
    # 7 -> 8 Async 4 h one way; 8 is also paired with Xi (no policy).
    peers = {1: (2,), 2: (1, 3), 3: (2, 4), 4: (3,), 5: (6,), 6: (5,), 7: (8,), 8: (7,)}
    flows = {1: [(2, _HOUR)], 2: [(1, _HOUR), (3, 15 * 60)], 3: [(4, _HOUR)],
             4: [(3, _HOUR)], 5: [(6, _SYNC)], 6: [(5, _SYNC)], 7: [(8, 4 * _HOUR)], 8: []}
    arrays = {1: ((0, "EVERPURE_FLASHARRAY"),), 5: ((1, "DELL_POWERSTORE"),)}
    for i in range(1, 9):
        azs = [vault]
        if i == 8:
            azs.append(AzSpec("Xi-AP-Southeast", "https://xi.nutanix.com", type="XI",
                              region="ap-southeast"))
        big = i in (1, 5)
        out.append(_pc(xl[i - 1], f"xl-{i}", "pc.7.5.1.1", Features(
            files=i % 3 == 0, objects=i % 4 == 0, fvn=i % 2 == 0,
            pc_vm_count=3 if big else 1, pc_size="LARGE" if big else "SMALL",
            external_storage=arrays.get(i, ()), v4_minor=dict(_V43),
            az_peers=tuple(xl[j - 1] for j in peers[i]), azs=tuple(azs),
            policies=(_p(f"{XL_SITES[i - 1]}-VAULT-DAILY", _DAY, "dr-vault"),),
            extra_policies=tuple(
                (f"{XL_SITES[i - 1]}-TO-{XL_SITES[j - 1]}", rpo, _xl_cluster(i, 0),
                 xl[j - 1], _xl_cluster(j, 0)) for j, rpo in flows[i]))))
    return out
