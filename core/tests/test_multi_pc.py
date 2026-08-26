"""Multi-Prism-Central collection: combine_environments() and the sidecar's
inventory_multi orchestration (per-target isolation + optional combine).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from ntnxvision_core.engine import combine_environments
from ntnxvision_core.models import Cluster, Environment, PrismCentral, VirtualMachine


def _env(host: str, cluster_uuid: str) -> Environment:
    env = Environment(prism_central=PrismCentral(host=host))
    env.clusters = [Cluster(uuid=cluster_uuid, name=f"clu-{host}")]
    env.vms = [VirtualMachine(uuid=f"vm-{host}", name=f"vm-{host}", cluster_name=f"clu-{host}")]
    env.warnings = [f"warning from {host}"]
    env.collection_notes = {"clusters": "ok"}
    env.flow.security_policy_count = 1
    return env


def test_combine_environments_merges_lists_and_sums_summaries() -> None:
    e1, e2 = _env("pc1.local", "c1"), _env("pc2.local", "c2")
    combined = combine_environments([e1, e2])

    assert [c.name for c in combined.clusters] == ["clu-pc1.local", "clu-pc2.local"]
    assert [v.name for v in combined.vms] == ["vm-pc1.local", "vm-pc2.local"]
    assert combined.warnings == ["warning from pc1.local", "warning from pc2.local"]
    assert combined.flow.security_policy_count == 2
    assert combined.collection_notes == {
        "pc1.local: clusters": "ok",
        "pc2.local: clusters": "ok",
    }
    assert [pc.host for pc in combined.source_environments] == ["pc1.local", "pc2.local"]
    assert combined.summary()["source_environments"] == 2


def test_combine_environments_single_is_identity() -> None:
    e1 = _env("pc1.local", "c1")
    assert combine_environments([e1]) is e1


def test_combine_environments_empty_raises() -> None:
    with pytest.raises(ValueError, match="at least one"):
        combine_environments([])


def test_inventory_multi_isolates_a_failing_target() -> None:
    """One target raising (bad creds, unreachable host, ...) must not prevent the
    other targets from collecting and being finalized normally.
    """
    from ntnxvision_core import sidecar

    def fake_collect(creds: Any, progress: Any = None, ndb_creds: Any = None) -> Environment:
        if creds.host == "bad.local":
            raise RuntimeError("connection refused")
        return _env(creds.host, f"c-{creds.host}")

    with patch.object(sidecar, "collect_environment", side_effect=fake_collect), \
         patch.object(sidecar, "_finalize", side_effect=lambda env, name, params: {
             "summary": env.summary(), "source_type": env.source_type,
             "prism_central": env.prism_central.model_dump(), "warnings": [],
             "collection_notes": {}, "snapshot_path": None, "exports": {}, "export_errors": {},
         }):
        result = sidecar._m_inventory_multi(1, {
            "targets": [
                {"host": "good1.local", "username": "admin", "password": "x"},
                {"host": "bad.local", "username": "admin", "password": "x"},
                {"host": "good2.local", "username": "admin", "password": "x"},
            ],
            "combine": False,
        })

    outcomes = {t["host"]: t["success"] for t in result["targets"]}
    assert outcomes == {"good1.local": True, "bad.local": False, "good2.local": True}
    bad = next(t for t in result["targets"] if t["host"] == "bad.local")
    assert "connection refused" in bad["error"]
    assert result["combined"] is None


def test_inventory_multi_combines_when_requested() -> None:
    from ntnxvision_core import sidecar

    def fake_collect(creds: Any, progress: Any = None, ndb_creds: Any = None) -> Environment:
        return _env(creds.host, f"c-{creds.host}")

    finalize_calls: list[str] = []

    def fake_finalize(env: Environment, name: str, params: Any) -> dict[str, Any]:
        finalize_calls.append(name)
        return {
            "summary": env.summary(), "source_type": env.source_type,
            "prism_central": env.prism_central.model_dump(), "warnings": [],
            "collection_notes": {}, "snapshot_path": None, "exports": {}, "export_errors": {},
        }

    with patch.object(sidecar, "collect_environment", side_effect=fake_collect), \
         patch.object(sidecar, "_finalize", side_effect=fake_finalize):
        result = sidecar._m_inventory_multi(1, {
            "targets": [
                {"host": "pc1.local", "username": "admin", "password": "x"},
                {"host": "pc2.local", "username": "admin", "password": "x"},
            ],
            "combine": True,
            "combined_name": "acme-combined",
        })

    assert all(t["success"] for t in result["targets"])
    assert result["combined"] is not None
    assert result["combined"]["summary"]["source_environments"] == 2
    assert "acme-combined" in finalize_calls


def test_inventory_multi_skips_combine_with_fewer_than_two_successes() -> None:
    from ntnxvision_core import sidecar

    def fake_collect(creds: Any, progress: Any = None, ndb_creds: Any = None) -> Environment:
        if creds.host == "bad.local":
            raise RuntimeError("nope")
        return _env(creds.host, "c1")

    with patch.object(sidecar, "collect_environment", side_effect=fake_collect), \
         patch.object(sidecar, "_finalize", side_effect=lambda env, name, params: {
             "summary": env.summary(), "source_type": env.source_type,
             "prism_central": env.prism_central.model_dump(), "warnings": [],
             "collection_notes": {}, "snapshot_path": None, "exports": {}, "export_errors": {},
         }):
        result = sidecar._m_inventory_multi(1, {
            "targets": [
                {"host": "good.local", "username": "admin", "password": "x"},
                {"host": "bad.local", "username": "admin", "password": "x"},
            ],
            "combine": True,
        })

    assert result["combined"] is None


def test_inventory_multi_requires_at_least_one_target() -> None:
    from ntnxvision_core import sidecar
    from ntnxvision_core.api.errors import NutanixApiError

    with pytest.raises(NutanixApiError):
        sidecar._m_inventory_multi(1, {"targets": []})
