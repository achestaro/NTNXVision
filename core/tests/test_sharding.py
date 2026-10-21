"""CI shards (``pytest --shard I/N``): every test file runs in exactly one shard."""

from __future__ import annotations

from pathlib import Path

import pytest

from . import sharding

TEST_FILES = sorted(f"tests/{p.name}" for p in Path(__file__).parent.glob("test_*.py"))


@pytest.mark.parametrize("total", range(1, 9))
def test_every_file_lands_in_exactly_one_shard(total: int) -> None:
    plan = sharding.assign(TEST_FILES, total, sharding.load_weights())
    assert sorted(plan) == TEST_FILES
    assert set(plan.values()) <= set(range(1, total + 1))
    if total <= len(TEST_FILES):
        assert set(plan.values()) == set(range(1, total + 1))  # no empty shard


def test_assignment_ignores_input_order_and_counts_new_files() -> None:
    weights = {"tests/test_a.py": 10.0, "tests/test_b.py": 6.0, "tests/test_c.py": 4.0}
    files = [*weights, "tests/test_new.py"]
    plan = sharding.assign(files, 2, weights)
    assert plan == sharding.assign(list(reversed(files)), 2, weights)
    assert plan["tests/test_a.py"] != plan["tests/test_b.py"]  # balanced, not alphabetical
    assert "tests/test_new.py" in plan


def test_every_test_file_has_a_weight() -> None:
    """A missing weight still runs (as the median) but skews the balance: refresh with
    ``python -m tests.sharding`` (see tests/sharding.py)."""
    assert set(TEST_FILES) <= set(sharding.load_weights())


@pytest.mark.parametrize("value", ["0/4", "5/4", "1/0", "x", "1-4"])
def test_bad_shard_values_are_refused(value: str) -> None:
    with pytest.raises(ValueError):
        sharding.parse_shard(value)


def test_weights_are_read_from_a_durations_table() -> None:
    table = (
        "1.50s call     tests/test_a.py::test_one[x]\n"
        "0.50s setup    tests/test_a.py::test_one[x]\n"
        "2.00s call     tests/test_b.py::test_two\n"
        "1375 passed in 696.14s\n"
    )
    weights = sharding.weights_from_durations(table)
    assert weights == {"tests/test_a.py": 2.0, "tests/test_b.py": 2.0}
