"""The demo estate: which synthetic system answers at which demo hostname.

Every host is under ``.demo.local`` (a reserved-looking name nothing real resolves) and
exercises a different path through the app:

==========================  ===============================================================
``pc01.demo.local``         Prism Central pc.2024.3, ``medium`` (1 214 VMs, 4 clusters).
                            v4 for most areas; Objects v4 service down (503, v3 used);
                            Files not deployed; 3 ESXi VMs fail on v4 (VMs "with notes");
                            alerts throttled once (429 → retried).
``pc02.demo.local``         pc.7.3, ``small`` (~1 590 VMs). All v4; Files deployed; Objects
                            and Flow virtual networking not deployed.
``pc03.demo.local``         pc.2024.1 — below the v4 GA line, so every area falls back to
                            v3 and the v4-only areas (licensing, LCM) are skipped.
``legacy-pc.demo.local``    pc.2023.4 — outside the supported range (warn, may proceed).
``pe01.demo.local``         Prism Element, AOS 7.0, 6 hosts / 212 VMs (v2.0 API).
``ndb.demo.local``          NDB (Era API) with 12 database servers.
``selfsigned.demo.local``   A tiny PC presenting a self-signed certificate.
``tiny.demo.local``         The ``tiny`` profile (1 cluster, 40 VMs).
``down.demo.local``         Never resolves — the unreachable case.
``large1..3.demo.local``    Three Prism Centrals of ~6 700 VMs each: the 20 000-VM estate.
==========================  ===============================================================

Any username works; any non-empty password except ``wrong`` is accepted.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ntnxvision_core.synthetic.estate import (
    Features,
    build_ndb_estate,
    build_pc_estate,
    build_pe_estate,
)
from ntnxvision_core.synthetic.mockapi import Target

DEMO_DOMAIN = ".demo.local"


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
            alerts_rate_limited=True, esxi_bad_vms=3, lcm_framework_stale=True)),
        _pc("pc02.demo.local", "small", "pc.7.3", Features(
            objects=False, fvn=False, v4_absent={"objects"})),
        _pc("pc03.demo.local", "legacy", "pc.2024.1", Features(files=False, ncm=False)),
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
        targets.append(_pc(f"large{i}.demo.local", "large", "pc.7.3",
                           Features(files=i == 1, objects=i == 2)))
    return {t.host: t for t in targets}


def make_resolver(targets: dict[str, Target]) -> Callable[[str], Target | None]:
    def resolve(host: str) -> Target | None:
        return targets.get(host.lower().strip("."))
    return resolve
