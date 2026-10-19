"""Shared fixtures: the synthetic demo estate and an isolated data directory."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


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
