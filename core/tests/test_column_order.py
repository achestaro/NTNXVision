"""The Cluster column comes first in every table: grid, workbook, PDF and Word.

The rule lives in `exporters.tables.TableSpec` (grid + Excel) and `content.make_table`
(PDF + Word); these tests hold every spec and every report table to it, so a column added
later cannot quietly break it. The VM tables (Virtual machines, System VMs) are the one
exception: they keep Name, Power, Cluster in that order.
"""

from __future__ import annotations

from typing import Any

import pytest

from ntnxvision_core.exporters import tables as T
from ntnxvision_core.exporters.content import Table, build_report, make_table
from ntnxvision_core.exporters.sourcing import SOURCE_HEADER, with_sources

#: Specs exempt from the rule, and why: VM tables lead with the VM's name and power.
VM_SPECS = {"vms", "system_vms"}


@pytest.mark.parametrize("spec", list(T.SPECS.values()), ids=lambda s: s.type)
def test_every_spec_puts_cluster_first(spec: T.TableSpec) -> None:
    col = spec.cluster_col
    if col is None:
        return  # no per-row cluster (policies, users, ...)
    if spec.type in VM_SPECS:
        assert spec.cluster_first is False
        assert spec.cols[0].key == "name", "VM tables keep Name first"
        return
    assert spec.cluster_first
    assert spec.cols[0] is col, f"{spec.type}: Cluster must be the first column"
    if col.header is not None:
        assert spec.excel_cols[0] is col
        assert col.header == "Cluster"
    if col.label is not None:
        assert spec.ui_cols[0] is col
        assert col.label == "Cluster"


def test_only_vm_specs_opt_out() -> None:
    assert {s.type for s in T.SPECS.values() if not s.cluster_first} == VM_SPECS


def test_appended_column_still_lands_after_cluster() -> None:
    """A spec built with Cluster in the middle (or new columns appended after it, as the
    capacity columns will be) is reordered when the spec is created."""
    getter: Any = lambda o, _c: None  # noqa: E731
    spec = T.TableSpec(
        "x", "X", "X", "X", "x", lambda e: [],
        (T.Col("name", "Name", getter, label="Name", kind="name"),
         T.Col("a", "A", getter), T.Col("cluster", "Cluster", getter, label="Cluster"),
         T.Col("ratio", "vCPU:pCPU", getter, label="vCPU:pCPU")),
        id=lambda o: "", name=lambda o: "")
    assert [c.key for c in spec.cols] == ["cluster", "name", "a", "ratio"]


def test_source_pc_column_follows_cluster() -> None:
    headers, rows = with_sources(["Cluster", "Name"], [["c1", "n1"]], ["pc1"])
    assert headers == ["Cluster", SOURCE_HEADER, "Name"] and rows == [["c1", "pc1", "n1"]]
    headers, rows = with_sources(["Name", "Power", "Cluster"], [["v", "On", "c"]], ["pc"])
    assert headers[0] == SOURCE_HEADER  # VM tables: attribution leads
    assert with_sources(["A"], [[1]], None) == (["A"], [[1]])


def test_make_table_moves_cluster_first_unless_vm() -> None:
    t = make_table(["Host", "Cluster", "Cores"], [["h1", "c1", 4]], "Hosts")
    assert t is not None and t.headers == ["Cluster", "Host", "Cores"]
    assert t.rows[0][:2] == ["c1", "h1"] and t.align == ["L", "L", "R"]
    vm = make_table(["Name", "Power", "Cluster"], [["v", "On", "c"]], "VMs", keep_order=True)
    assert vm is not None and vm.headers == ["Name", "Power", "Cluster"]
    combined = make_table(["Host", "Cluster"], [["h", "c"]], "Hosts", srcs=["pc1"])
    assert combined is not None and combined.headers == ["Cluster", SOURCE_HEADER, "Host"]


def _tables(report: Any) -> list[Table]:
    out: list[Table] = []
    for ch in report.chapters:
        for blk in [*ch.blocks, *(b for s in ch.sections for b in s.blocks)]:
            if isinstance(blk, Table):
                out.append(blk)
    return out


def _check_report(env: Any) -> int:
    from ntnxvision_core.exporters import ExportOptions

    tables = _tables(build_report(env, ExportOptions(), eyebrow="x"))
    checked = 0
    for t in tables:
        if "Cluster" not in t.headers or t.keep_order:
            continue
        checked += 1
        assert t.headers[0] == "Cluster", f"{t.caption}: {t.headers}"
        if env.is_combined:
            assert t.headers[1] == SOURCE_HEADER, f"{t.caption}: {t.headers}"
    return checked


def test_every_report_table_puts_cluster_first(demo: Any) -> None:
    from ntnxvision_core.api import PrismCredentials
    from ntnxvision_core.engine import collect_environment, combine_environments

    def pc(host: str) -> Any:
        return collect_environment(PrismCredentials(host=host, username="a", password="p"))

    assert _check_report(pc("pc01.demo.local")) >= 8
    assert _check_report(combine_environments([pc("pc02.demo.local"),
                                               pc("pc03.demo.local")])) >= 5
