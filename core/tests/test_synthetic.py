"""The synthetic estate generator and the mock Nutanix API it is served from."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from ntnxvision_core.synthetic.estate import (
    Features,
    build_ndb_estate,
    build_pc_estate,
    build_pe_estate,
)
from ntnxvision_core.synthetic.mockapi import MockApi, Target
from ntnxvision_core.synthetic.profiles import demo_targets, make_resolver

AUTH = "Basic " + base64.b64encode(b"admin:demo").decode()


def _api(**targets: Target) -> MockApi:
    return MockApi(make_resolver({t.host: t for t in targets.values()}))


def _pc(host: str, profile: str = "tiny", version: str = "pc.7.3",
        features: Features | None = None) -> Target:
    return Target(host=host, kind="pc",
                  build=lambda e: build_pc_estate(host, profile, version, epoch=e,
                                                  features=features or Features()))


# ─── generator ─────────────────────────────────────────────────────────


def test_generator_is_deterministic() -> None:
    a = build_pc_estate("pc.test", "medium")
    b = build_pc_estate("pc.test", "medium")
    assert [v["uuid"] for v in a.vms] == [v["uuid"] for v in b.vms]
    assert a.vms[17] == b.vms[17]


def test_medium_profile_size() -> None:
    est = build_pc_estate("pc.test", "medium")
    assert len(est.clusters) == 4
    user_vms = [v for v in est.vms if "PCVM" not in v["name"]]
    assert len(user_vms) == 1214
    assert len(est.hosts) == 28


def test_large_profile_makes_the_20k_estate_across_three_pcs() -> None:
    total = sum(len([v for v in build_pc_estate(f"large{i}", "large").vms
                     if "PCVM" not in v["name"]]) for i in (1, 2, 3))
    assert total >= 20_000


def test_cross_references_are_consistent() -> None:
    est = build_pc_estate("pc.test", "medium")
    hosts = {h["uuid"]: h for h in est.hosts}
    containers = {c["uuid"] for c in est.containers}
    subnets = {s["uuid"] for s in est.subnets}
    images = {i["uuid"] for i in est.images}
    for vm in est.vms:
        assert hosts[vm["host_uuid"]]["cluster_uuid"] == vm["cluster_uuid"]
        for d in vm["disks"]:
            # An ESXi disk on a local datastore has no container (by design).
            assert d["container_uuid"] in containers or (
                vm["hypervisor"] == "ESXi" and d["datastore"])
            assert d["image"] is None or d["image"] in images
        for n in vm["nics"]:
            assert n["subnet_uuid"] is None or n["subnet_uuid"] in subnets
            # ESXi NICs sit on a port group of their own cluster, never a subnet.
            if vm["hypervisor"] == "ESXi":
                assert n["subnet_uuid"] is None
                assert n["port_group_uuid"] in {p["uuid"] for p in est.port_groups
                                                if p["cluster_uuid"] == vm["cluster_uuid"]}
    category_pairs = {(c["key"], c["value"]) for c in est.categories}
    for p in est.protection_policies:
        for key, values in p["categories"].items():
            assert all((key, v) in category_pairs for v in values)


def test_epoch_drift_only_touches_what_it_says() -> None:
    a = build_pc_estate("pc.test", "medium", epoch=0)
    b = build_pc_estate("pc.test", "medium", epoch=1)
    va = {v["uuid"]: v for v in a.vms}
    vb = {v["uuid"]: v for v in b.vms}
    added = [vb[u]["name"] for u in vb.keys() - va.keys()]
    removed = [va[u]["name"] for u in va.keys() - vb.keys()]
    assert len(added) == 32 and all(n.startswith("k8s-worker-") for n in added)
    assert sorted(removed) == [f"legacy-app-0{i}" for i in range(1, 7)]
    changed = [u for u in va.keys() & vb.keys()
               if {k: v for k, v in va[u].items() if k not in ("host_uuid", "host_name")}
               != {k: v for k, v in vb[u].items() if k not in ("host_uuid", "host_name")}]
    assert [va[u]["name"] for u in changed] == ["app-sql-01"]
    assert b.clusters[0]["aos"] == "7.0" and a.clusters[0]["aos"] == "6.10.1"
    assert "Bronze-daily" not in {p["name"] for p in b.protection_policies}


def test_pe_and_ndb_estates() -> None:
    pe = build_pe_estate("pe.test", vms=50, nodes=4)
    assert pe.cluster["nodes"] == 4 and len(pe.vms) == 50 and len(pe.hosts) == 4
    ndb = build_ndb_estate("ndb.test")
    assert len(ndb.dbservers) == 12 and len(ndb.databases) == len(ndb.time_machines)


# ─── mock API ──────────────────────────────────────────────────────────


def _get(api: MockApi, host: str, path: str, auth: str | None = AUTH,
         **query: Any) -> tuple[int, Any, dict[str, str]]:
    return api.handle(host, "GET", path, {k: str(v) for k, v in query.items()}, None, auth)


def _post(api: MockApi, host: str, path: str, body: dict[str, Any]
          ) -> tuple[int, Any, dict[str, str]]:
    return api.handle(host, "POST", path, {}, body, AUTH)


def test_auth_is_enforced() -> None:
    api = _api(pc=_pc("pc.t"))
    assert _get(api, "pc.t", "/PrismGateway/services/rest/v1/cluster", auth=None)[0] == 401
    wrong = "Basic " + base64.b64encode(b"admin:wrong").decode()
    assert _get(api, "pc.t", "/PrismGateway/services/rest/v1/cluster", auth=wrong)[0] == 401
    status, body, _ = _get(api, "pc.t", "/PrismGateway/services/rest/v1/cluster")
    assert status == 200 and body["version"] == "pc.7.3"


def test_v3_paging() -> None:
    api = _api(pc=_pc("pc.t", "medium"))
    seen: list[str] = []
    offset = 0
    while True:
        status, page, _ = _post(api, "pc.t", "/api/nutanix/v3/vms/list",
                                {"offset": offset, "length": 250})
        assert status == 200
        seen += [e["metadata"]["uuid"] for e in page["entities"]]
        offset += page["metadata"]["length"]
        if offset >= page["metadata"]["total_matches"] or not page["entities"]:
            break
    assert len(seen) == len(set(seen)) == page["metadata"]["total_matches"]
    assert len(seen) > 1000


def test_v4_paging_and_limit_ceiling() -> None:
    api = _api(pc=_pc("pc.t", "medium"))
    status, first, _ = _get(api, "pc.t", "/api/vmm/v4.0/ahv/config/vms",
                            **{"$page": 0, "$limit": 100})
    assert status == 200 and len(first["data"]) == 100
    total = first["metadata"]["totalAvailableResults"]
    assert any(link["rel"] == "next" for link in first["metadata"]["links"])
    last_page = (total - 1) // 100
    _, last, _ = _get(api, "pc.t", "/api/vmm/v4.0/ahv/config/vms",
                      **{"$page": last_page, "$limit": 100})
    assert len(last["data"]) == total - last_page * 100
    assert _get(api, "pc.t", "/api/vmm/v4.0/ahv/config/vms", **{"$limit": 101})[0] == 400


def test_v4_is_absent_below_ga_and_namespaces_can_be_down() -> None:
    api = _api(old=_pc("old.t", version="pc.2024.1"),
               new=_pc("new.t", features=Features(v4_unavailable={"objects"},
                                                  v4_absent={"files"})))
    assert _get(api, "old.t", "/api/clustermgmt/v4.0/config/clusters")[0] == 404
    assert _get(api, "new.t", "/api/clustermgmt/v4.0/config/clusters")[0] == 200
    assert _get(api, "new.t", "/api/objects/v4.0/config/object-stores")[0] == 503
    assert _get(api, "new.t", "/api/files/v4.0/config/file-servers")[0] == 404


def test_products_not_deployed_404_on_v3() -> None:
    api = _api(pc=_pc("pc.t", features=Features(files=False, objects=False, fvn=False)))
    for kind in ("file_server", "objectstores", "vpcs"):
        assert _post(api, "pc.t", f"/api/nutanix/v3/{kind}/list", {"length": 1})[0] == 404


def test_esxi_page_with_a_bad_record_fails_but_single_records_salvage() -> None:
    api = _api(pc=_pc("pc.t", "medium", features=Features(esxi_bad_vms=2)))
    status, _, _ = _get(api, "pc.t", "/api/vmm/v4.0/esxi/config/vms",
                        **{"$page": 0, "$limit": 100})
    assert status == 500
    statuses = [_get(api, "pc.t", "/api/vmm/v4.0/esxi/config/vms",
                     **{"$page": i, "$limit": 1})[0] for i in range(8)]
    assert statuses.count(500) == 2 and statuses.count(200) == 6


def test_alerts_rate_limit_answers_429() -> None:
    api = _api(pc=_pc("pc.t"))
    codes = [_get(api, "pc.t", "/api/monitoring/v4.0/serviceability/alerts",
                  **{"$page": 0, "$limit": 50})[0] for _ in range(4)]
    assert codes[:2] == [200, 200] and 429 in codes[2:]
    _, _, headers = _get(api, "pc.t", "/api/monitoring/v4.0/serviceability/alerts",
                         **{"$page": 0, "$limit": 50})
    assert headers.get("X-Api-Ratelimit-Limit") == "2"


def test_groups_api_and_pe_and_era_shapes() -> None:
    targets = demo_targets()
    api = MockApi(make_resolver(targets))
    status, groups, _ = _post(api, "pc01.demo.local", "/api/nutanix/v3/groups", {
        "entity_type": "vm", "filter_criteria": "is_cvm==1",
        "group_member_attributes": [{"attribute": "vm_name"}],
        "group_member_count": 5, "group_member_offset": 0})
    assert status == 200
    rows = groups["group_results"][0]["entity_results"]
    assert len(rows) == 5 and rows[0]["data"][0]["values"][0]["values"][0].startswith("NTNX-")
    status, vms, _ = _get(api, "pe01.demo.local", "/PrismGateway/services/rest/v2.0/vms",
                          count=10, offset=0, include_vm_disk_config="true")
    assert status == 200 and vms["metadata"]["grand_total_entities"] == 212
    assert "vm_disk_info" in vms["entities"][0] and "vm_nics" not in vms["entities"][0]
    status, dbs, _ = _get(api, "ndb.demo.local", "/era/v0.9/databases")
    assert status == 200 and isinstance(dbs, list) and dbs[0]["timeMachine"]["sla"]["name"]


@pytest.mark.parametrize("path", [
    "/api/clustermgmt/v4.0/config/clusters", "/api/vmm/v4.0/content/templates",
    "/api/microseg/v4.0/config/policies", "/api/lifecycle/v4.0/resources/entities",
    "/api/iam/v4.0/authn/users", "/api/datapolicies/v4.0/config/protection-policies",
])
def test_v4_entities_carry_ext_id_and_object_type(path: str) -> None:
    api = MockApi(make_resolver(demo_targets()))
    status, body, _ = _get(api, "pc01.demo.local", path)
    assert status == 200 and body["data"]
    assert all("extId" in e and "$objectType" in e for e in body["data"])


def test_generated_demo_certificates(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import ssl

    from ntnxvision_core.synthetic import certgen

    monkeypatch.setenv("NTNXVISION_DEMO_CERT_DIR", str(tmp_path / "certs"))
    directory = certgen.ensure()
    assert sorted(p.name for p in directory.iterdir()) == sorted(certgen.FILES)
    # The CA's private key is never written.
    assert not any("ca" in p.name and p.suffix == ".key" for p in directory.iterdir())
    ctx = ssl.create_default_context(cafile=str(directory / "demo-ca.pem"))
    ctx.load_cert_chain(directory / "server.pem", directory / "server.key")
    decoded = ssl._ssl._test_decode_cert(str(directory / "server.pem"))  # type: ignore[attr-defined]
    assert ("DNS", "localhost") in decoded["subjectAltName"]
    assert ("IP Address", "127.0.0.1") in decoded["subjectAltName"]
    assert certgen.ensure() == directory  # cached, not regenerated
