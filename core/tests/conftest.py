"""Shared fixtures: the synthetic demo estate and an isolated data directory."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--shard", default=None, metavar="I/N",
        help="run only shard I of N (whole test files, balanced by tests/shard-weights.json)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """``--shard I/N`` keeps the tests of the files assigned to shard I (tests/sharding.py).
    Every xdist worker computes the same assignment, so their collections agree."""
    value = config.getoption("--shard")
    if not value:
        return
    from . import sharding

    index, total = sharding.parse_shard(value)
    tests_dir = Path(__file__).parent

    def key(item: pytest.Item) -> str:
        return "tests/" + Path(item.path).relative_to(tests_dir).as_posix()

    plan = sharding.assign(sorted({key(item) for item in items}), total, sharding.load_weights())
    keep = [item for item in items if plan[key(item)] == index]
    dropped = [item for item in items if plan[key(item)] != index]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = keep


@pytest.fixture(scope="session", autouse=True)
def _demo_cert_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Generated demo TLS material goes to a session temp dir, not the user's cache
    (subprocess sidecars inherit the variable)."""
    import os

    previous = os.environ.get("NTNXVISION_DEMO_CERT_DIR")
    os.environ["NTNXVISION_DEMO_CERT_DIR"] = str(tmp_path_factory.mktemp("demo-certs") / "c")
    yield
    if previous is None:
        os.environ.pop("NTNXVISION_DEMO_CERT_DIR", None)
    else:
        os.environ["NTNXVISION_DEMO_CERT_DIR"] = previous


@pytest.fixture(scope="session", autouse=True)
def _never_the_real_data_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Every test runs with a session data directory, so nothing (a snapshot, a report
    written to the default exports folder) ever lands in the user's real NTNXVision
    folder. Tests that need their own use the `data_dir` fixture, which overrides it."""
    import os

    previous = os.environ.get("NTNXVISION_DATA_DIR")
    os.environ["NTNXVISION_DATA_DIR"] = str(tmp_path_factory.mktemp("session-data"))
    yield
    if previous is None:
        os.environ.pop("NTNXVISION_DATA_DIR", None)
    else:
        os.environ["NTNXVISION_DATA_DIR"] = previous


@pytest.fixture(scope="session")
def _demo_state() -> Iterator[Any]:
    from ntnxvision_core.synthetic import demo

    state = demo.enable(latency_ms=0)
    yield state
    demo.disable()


#: Estates the architecture-diagram tests share: the scenario estates (small, medium,
#: enterprise, xlarge) and demo PCs carrying the design data (blocks, CVMs, virtual
#: switches with LACP, LLDP or not, ESXi, a FlashArray, a PowerFlex, Sync and NearSync).
DESIGN_DEMO_HOSTS = ("pc01", "pc02", "pc03", "large1", "large2", "large3")


@pytest.fixture(scope="session")
def design_envs(_demo_state: Any) -> dict[str, Any]:
    """Every design estate, collected once per session through the mock API with the
    real collectors: scenario names, demo short names, and ``multi3`` (pc01 to pc03
    combined)."""
    from ntnxvision_core.api import PrismCredentials
    from ntnxvision_core.engine import collect_environment, combine_environments
    from ntnxvision_core.synthetic import demo as demo_mode
    from ntnxvision_core.synthetic.scenarios import SCENARIOS, collect_scenario

    demo_mode.reset()
    out: dict[str, Any] = {name: collect_scenario(name) for name in SCENARIOS}
    demo_mode.reset()
    for host in DESIGN_DEMO_HOSTS:
        out[host] = collect_environment(PrismCredentials(
            host=f"{host}.demo.local", username="admin", password="demo"))
    out["multi3"] = combine_environments([out["pc01"], out["pc02"], out["pc03"]])
    demo_mode.reset()
    return out


@pytest.fixture
def demo(_demo_state: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Demo routing on, every synthetic system at epoch 0, alert pacing shortened."""
    from ntnxvision_core.collectors import alerts
    from ntnxvision_core.synthetic import demo as demo_mode

    demo_mode.reset()
    monkeypatch.setenv("NTNXVISION_DEMO", "1")
    monkeypatch.setattr(alerts, "_V4_BACKOFF", 0.05)
    _demo_state.api.lcm.seconds = 0.2  # mock LCM inventories finish fast in tests
    return _demo_state


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Snapshots and exports under a temp dir; sidecar caches emptied."""
    import ntnxvision_core.sidecar as sc

    data = tmp_path / "data"
    monkeypatch.setenv("NTNXVISION_DATA_DIR", str(data))
    # In-process handler calls would otherwise print every progress notification.
    monkeypatch.setattr(sc, "_send", lambda message: None)
    sc._cache.invalidate()
    sc._inventory_service().clear()
    return data
