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
    assert [s.prism_central.host for s in combined.source_environments] == [
        "pc1.local", "pc2.local",
    ]
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


# ─── combined exports ──────────────────────────────────────────────────
# A combined snapshot exported fine before these tests existed — it just described
# itself wrongly (one PC's sizing presented as the whole estate's, sources never
# named, identically-named clusters indistinguishable). These assert the report, not
# just the absence of an exception.


def _sizable_env(host: str, version: str, nodes: int, cluster_name: str) -> Environment:
    """A PC with its own sizing and a cluster named the same on every PC — the
    collision that makes source attribution necessary.
    """
    from ntnxvision_core.models import Host as HostModel
    from ntnxvision_core.models import PrismCentralConfig

    env = Environment(
        collected_at="2026-09-05T00:00:00Z",
        prism_central=PrismCentral(host=host, version=version),
    )
    env.pc_config = PrismCentralConfig(
        version=version, node_count=nodes, is_scale_out=nodes > 1,
        registered_cluster_count=1,
    )
    env.clusters = [Cluster(uuid=f"c-{host}", name=cluster_name, num_nodes=3,
                            hypervisor_types=["AHV"])]
    env.hosts = [HostModel(uuid=f"h-{host}", name=f"node-{host}", cluster_name=cluster_name)]
    env.vms = [VirtualMachine(uuid=f"v-{host}", name=f"vm-{host}", cluster_name=cluster_name)]
    return env


def _combined_pair() -> Environment:
    return combine_environments([
        _sizable_env("pc-emea.local", "pc.2024.3.1.8", 1, "Cluster01"),
        _sizable_env("pc-amer.local", "pc.2024.3.2", 3, "Cluster01"),
    ])


def test_combine_stamps_every_merged_object_with_its_source_pc() -> None:
    combined = _combined_pair()
    assert [c.source_pc for c in combined.clusters] == ["pc-emea.local", "pc-amer.local"]
    assert [h.source_pc for h in combined.hosts] == ["pc-emea.local", "pc-amer.local"]
    assert [v.source_pc for v in combined.vms] == ["pc-emea.local", "pc-amer.local"]
    assert combined.is_combined


def test_combined_keeps_each_pcs_sizing_instead_of_the_first_ones() -> None:
    """The combined environment must not adopt one PC's management-plane config —
    reports rendered it as the whole estate's sizing.
    """
    combined = _combined_pair()
    assert combined.pc_config.node_count is None
    sizing = [(s.prism_central.host, s.pc_config.node_count)
              for s in combined.source_environments]
    assert sizing == [("pc-emea.local", 1), ("pc-amer.local", 3)]
    assert combined.source_environments[0].summary["clusters"] == 1


def test_single_pc_environment_is_not_treated_as_combined() -> None:
    env = _sizable_env("pc1.local", "pc.2024.3", 1, "Cluster01")
    assert not env.is_combined
    assert env.clusters[0].source_pc is None


def test_combined_excel_names_the_sources_and_attributes_every_row(tmp_path) -> None:
    from openpyxl import load_workbook

    from ntnxvision_core.exporters import ExcelExporter

    path = ExcelExporter().export(_combined_pair(), tmp_path / "combined.xlsx")
    wb = load_workbook(path)

    overview = {r[0]: r[1] for r in wb["Overview"].iter_rows(values_only=True) if r[0]}
    assert overview["Prism Centrals"] == "2 (combined)"
    assert overview["Sources"] == "pc-emea.local, pc-amer.local"
    # The single-PC property sheet — which would have to pick one PC's sizing — is
    # replaced by a row per source.
    assert "Prism Central" not in wb.sheetnames
    pcs = list(wb["Prism Centrals"].iter_rows(values_only=True))
    assert pcs[0][:4] == ("Prism Central", "Version", "Deployment", "PC Nodes")
    assert [(r[0], r[1], r[3]) for r in pcs[1:]] == [
        ("pc-emea.local", "pc.2024.3.1.8", 1),
        ("pc-amer.local", "pc.2024.3.2", 3),
    ]

    for sheet, expected in (("Clusters", "Cluster01"), ("Hosts", "node-pc-emea.local")):
        rows = list(wb[sheet].iter_rows(values_only=True))
        assert rows[0][0] == "Prism Central", f"{sheet} lacks the attribution column"
        assert rows[1][0] == "pc-emea.local"
        assert rows[2][0] == "pc-amer.local"
        assert rows[1][1] == expected
    assert list(wb["VMs"].iter_rows(values_only=True))[0][0] == "Prism Central"


def test_single_pc_excel_keeps_its_original_shape(tmp_path) -> None:
    """The attribution column and the source sheet are combined-only — a normal run's
    workbook must be byte-for-byte the same shape it has always been.
    """
    from openpyxl import load_workbook

    from ntnxvision_core.exporters import ExcelExporter

    env = _sizable_env("pc1.local", "pc.2024.3", 1, "Cluster01")
    wb = load_workbook(ExcelExporter().export(env, tmp_path / "solo.xlsx"))

    assert "Prism Centrals" not in wb.sheetnames
    assert "Prism Central" in wb.sheetnames          # the single-PC property sheet
    overview = {r[0]: r[1] for r in wb["Overview"].iter_rows(values_only=True) if r[0]}
    assert overview["Prism Central"] == "pc1.local"
    assert overview["PC Version"] == "pc.2024.3"
    assert list(wb["Clusters"].iter_rows(values_only=True))[0][0] == "Name"


def test_combined_word_names_the_sources_and_attributes_clusters(tmp_path) -> None:
    from docx import Document

    from ntnxvision_core.exporters.word import WordExporter

    path = WordExporter().export(_combined_pair(), tmp_path / "combined.docx")
    doc = Document(str(path))
    text = "\n".join(p.text for p in doc.paragraphs)

    assert "spanning 2 Prism Centrals (pc-emea.local, pc-amer.local)" in text
    assert "Prism Centrals — Management & Sizing" in text
    # The misleading single-PC sizing table must be gone, not merely supplemented.
    assert "Prism Central management-plane configuration" not in text

    tables = [[[c.text for c in row.cells] for row in t.rows] for t in doc.tables]
    source_table = next(t for t in tables if t[0][:3] == ["Prism Central", "Version", "Deployment"])
    assert [r[0] for r in source_table[1:]] == ["pc-emea.local", "pc-amer.local"]
    assert [r[3] for r in source_table[1:]] == ["1", "3"]        # per-PC node counts

    cluster_table = next(t for t in tables if t[0] == ["Prism Central", "Cluster", "AOS",
                                                       "Hypervisor", "Nodes", "RF", "Encryption"])
    assert [r[0] for r in cluster_table[1:]] == ["pc-emea.local", "pc-amer.local"]
    assert {r[1] for r in cluster_table[1:]} == {"Cluster01"}    # same name, distinguishable


def test_combined_pdf_exports_with_the_extra_column(tmp_path) -> None:
    """The PDF's column widths are tuned to the landscape page, so the attribution
    column has to fit rather than overflow it.
    """
    from ntnxvision_core.exporters.pdf import PdfExporter

    path = PdfExporter().export(_combined_pair(), tmp_path / "combined.pdf")
    assert path.exists() and path.stat().st_size > 10_000


def test_legacy_combined_snapshot_still_names_its_sources(tmp_path) -> None:
    """`source_environments` used to be a bare list of PrismCentral. Snapshots in that
    shape must still read back with their PCs named, not as "unknown".
    """
    import json

    from ntnxvision_core.store import SnapshotStore

    (tmp_path / "old.ntnxsnap.json").write_text(json.dumps({
        "collected_at": "2026-08-25T00:00:00Z",
        "prism_central": {"host": "2 Prism Centrals (combined)"},
        "source_environments": [
            {"host": "pc1.local", "version": "pc.2024.3"},
            {"host": "pc2.local", "version": "pc.2024.3"},
        ],
        "clusters": [{"uuid": "c1", "name": "Cluster01"}],
    }))
    env = SnapshotStore(tmp_path).load(tmp_path / "old.ntnxsnap.json")

    assert env.is_combined
    assert [s.prism_central.host for s in env.source_environments] == ["pc1.local", "pc2.local"]
    assert [s.prism_central.version for s in env.source_environments] == ["pc.2024.3"] * 2
