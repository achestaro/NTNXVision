"""Microsegmentation Design (N3) and the Flow rule peers (F0): facts, layout, collapse
of identical AZs, determinism, golden, editing, report sections, collectors."""

from __future__ import annotations

from typing import Any

import pytest

from ntnxvision_core.diagrams import flow as N3
from ntnxvision_core.diagrams.layout.flow import layout_site
from ntnxvision_core.models import Environment, SecurityPolicy, SecurityRule

from . import net_diagram_helpers as H


def test_medium_facts_bullets_and_collapse(design_envs: dict[str, Any]) -> None:
    m = N3.flow_model(design_envs["medium"])
    [s] = m.sites  # md-pc01 and md-pc02 have the same policies: one figure
    assert s.applies_to == ["md-pc01.demo.local", "md-pc02.demo.local"]
    assert [x.type for x in s.lanes] == ["APPLICATION", "ISOLATION", "QUARANTINE"]
    app = s.lanes[0].policies
    assert [p.state for p in app] == sorted((p.state for p in app),
                                            key=lambda st: N3.STATE_ORDER[st or ""])
    billing = next(p for p in app if p.name == "app-billing")
    assert billing.peers_in == ["All sources", "App:Web", "corp-networks"]
    assert billing.peers_out == ["App:SQL", "monitoring-hosts"]
    assert N3.split_groups("Env:Dev,Prod; App:Web") == ["Env:Dev", "Env:Prod", "App:Web"]
    b = N3.bullets(s)
    assert b[0] == "6 Flow policies: 4 application, 1 isolation, 1 quarantine."
    assert "5 enforced; app-web is in monitor mode." in b
    assert "Env:Dev is isolated from Env:Prod." in b
    assert "k8s-cluster has the most rules: 5 inbound, 3 outbound." in b


def test_none_counts_read_not_reported() -> None:
    env = Environment()
    env.prism_central.host = "pc.example.lab"
    env.security_policies.append(SecurityPolicy(uuid="p", name="app", policy_type="APPLICATION",
                                                state="ENFORCE", secured_group="App:X"))
    scs = H.scenes("flow", env)
    H.assert_clean(scs)
    texts = H.texts(scs[0])
    assert "inbound rules: not reported" in texts and "no inbound rules" not in texts
    assert "rule peers were not collected" in scs[0].caption


@pytest.mark.parametrize("name", H.ESTATES)
def test_every_estate_lays_out_cleanly(design_envs: dict[str, Any], name: str) -> None:
    H.assert_clean(H.scenes("flow", design_envs[name]))


@pytest.mark.parametrize("name", ["medium", "enterprise"])
def test_deterministic(design_envs: dict[str, Any], name: str) -> None:
    H.assert_deterministic("flow", design_envs[name])


@pytest.mark.parametrize("name", ["medium", "enterprise"])
def test_golden(design_envs: dict[str, Any], name: str) -> None:
    H.assert_golden("flow", design_envs[name], name)


def test_round_trip(design_envs: dict[str, Any]) -> None:
    H.assert_round_trip("flow", design_envs["enterprise"])


def test_stress_50_policies() -> None:
    env = Environment()
    env.prism_central.host = "pc.example.lab"
    states = ["ENFORCE", "MONITOR", "SAVE", None]
    for i in range(50):
        t = ["APPLICATION", "APPLICATION", "ISOLATION", "QUARANTINE", "VDI"][i % 5]
        env.security_policies.append(SecurityPolicy(
            uuid=f"p{i:02d}", name=f"policy-{i:02d}" + "-long" * (i % 4), policy_type=t,
            state=states[i % 4], secured_group="Env:A,B,C,D,E" if t == "ISOLATION" else "App:X",
            inbound_rule_count=None if i % 7 == 0 else i % 5, outbound_rule_count=i % 3,
            rules=[SecurityRule(direction="IN", peer_kind="ALL", peer="All sources")]))
    s = N3.flow_model(env).sites[0]
    assert [x.type for x in s.lanes][-1] == "OTHER"
    scs = layout_site(s)
    H.assert_clean(scs)
    drawn = [i for sc in scs for i in sc.meta["drawn"]]
    summarised = [i for sc in scs for i in sc.meta["summarised"]]
    assert len(drawn) + len(summarised) == 50
    texts = [t for sc in scs for t in H.texts(sc)]
    assert any(t.startswith("+") and "more application policies" in t for t in texts)


def test_edit_planned_policy(design_envs: dict[str, Any]) -> None:
    env = design_envs["enterprise"]
    s = N3.flow_model(env).sites[0]
    lane = s.lanes[0]
    ops = [{"op": "set", "id": lane.policies[0].id, "field": "sublabel", "value": "PCI scope"},
           {"op": "move", "id": lane.policies[0].id, "index": 3},
           {"op": "add", "parent": lane.id, "field": "policies", "index": 0,
            "element": {"id": "user:p", "palette": "policy", "name": "Planned web tier"}}]
    payload, problems = H.edit("flow", env, ops)
    assert problems == []
    scs = H.laid_out("flow", payload)
    H.assert_clean(scs)
    texts = [t for sc in scs for t in H.texts(sc)]
    assert "Planned web tier" in texts and "PCI scope" in texts
    assert "USER-ADDED" not in texts
    # Lanes reorder but are never removed.
    _, bad = H.edit("flow", env, [{"op": "delete", "id": lane.id}])
    assert bad


def test_report_sections(design_envs: dict[str, Any]) -> None:
    heads, blocks = H.report_titles(design_envs["enterprise"])
    flow = [h for h in heads if "Microsegmentation Design" in h]
    assert flow and heads.index(flow[0]) < next(i for i, h in enumerate(heads)
                                               if h.endswith("Categories"))
    tables = [b for b in blocks if getattr(b, "caption", "").startswith(
        "Flow network security policies")]
    assert tables and "Inbound peers" in tables[0].headers


def test_v3_rules_are_read_from_allow_lists() -> None:
    from ntnxvision_core.collectors.flow import _v3_rule

    r = _v3_rule("IN", {"peer_specification_type": "FILTER",
                        "filter": {"params": {"App": ["Web"]}},
                        "service_group_list": [{"name": "HTTPS"}]})
    assert (r.peer_kind, r.peer, r.services) == ("CATEGORY", "App:Web", ["HTTPS"])
    r = _v3_rule("OUT", {"address_group_inclusion_list": [{"name": "corp"}]})
    assert (r.peer_kind, r.peer) == ("ADDRESS_GROUP", "corp")
    assert _v3_rule("IN", {"peer_specification_type": "ALL"}).peer == "All sources"
