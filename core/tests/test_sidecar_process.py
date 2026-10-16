"""The sidecar as the Electron main process sees it: a child process on stdio.

These tests start ``python -m ntnxvision_core --demo sidecar`` and speak JSON-RPC to it,
so they cover what in-process tests cannot: requests answered *while* a collection runs
on its worker thread, cancellation from the reader thread, the -32002 / -32800 codes, and
interleaved progress notifications from several parallel targets on one stdout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

CORE = Path(__file__).resolve().parents[1]


class Sidecar:
    def __init__(self, data_dir: Path, latency_ms: int = 20) -> None:
        env = dict(os.environ, NTNXVISION_DATA_DIR=str(data_dir), NTNXVISION_DEMO="1",
                   NTNXVISION_DEMO_LATENCY_MS=str(latency_ms), PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "ntnxvision_core", "--demo", "sidecar"], cwd=CORE,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
            env=env)
        self.responses: dict[int, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.orphan_errors: list[dict[str, Any]] = []
        self.cond = threading.Condition()
        self._id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            msg = json.loads(line)  # every line must be one complete JSON message
            with self.cond:
                if msg.get("id") is not None:
                    self.responses[msg["id"]] = msg
                elif "error" in msg:
                    self.orphan_errors.append(msg["error"])
                else:
                    assert msg["method"] == "progress"
                    self.events.append(msg["params"])
                self.cond.notify_all()

    def send(self, method: str, params: dict[str, Any] | None = None) -> int:
        assert self.proc.stdin
        self._id += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method,
                                          "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        return self._id

    def wait(self, req_id: int, timeout: float = 90) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self.cond:
            while req_id not in self.responses:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f"no answer to request {req_id}")
                self.cond.wait(left)
            return self.responses[req_id]

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.wait(self.send(method, params))

    def wait_event(self, predicate: Any, timeout: float = 60) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                for e in self.events:
                    if predicate(e):
                        return e
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError("event never arrived")
                self.cond.wait(left)

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture
def sidecar(tmp_path: Path) -> Iterator[Sidecar]:
    sc = Sidecar(tmp_path / "data")
    yield sc
    sc.close()


def _target(host: str, **extra: Any) -> dict[str, Any]:
    return {"host": host, "username": "admin", "password": "demo", "port": 9440,
            "insecure": False, **extra}


def test_stays_responsive_and_cancels_cleanly(sidecar: Sidecar, tmp_path: Path) -> None:
    run = sidecar.send("inventory", {**_target("pc01.demo.local"), "run_id": "run-a",
                                     "name": "never-saved", "formats": ["excel"],
                                     "export_dir": str(tmp_path / "exports")})
    started = sidecar.wait_event(lambda e: e["type"] == "started")
    assert started["run_id"] == "run-a" and len(started["areas"]) == 21
    assert {a["status"] for a in started["areas"]} == {"pending"}
    sidecar.wait_event(lambda e: e["type"] == "area" and e["area"]["status"] != "running")

    t0 = time.monotonic()
    assert sidecar.call("ping")["result"]["pong"] is True
    assert sidecar.call("list_snapshots")["result"]["snapshots"] == []
    tc = sidecar.call("test_connection", {**_target("pc02.demo.local"),
                                          "mode": "prism_central"})["result"]
    assert tc["status"] == "ok"
    assert time.monotonic() - t0 < 10  # answered while the run kept going

    second = sidecar.call("inventory", {**_target("tiny.demo.local"), "run_id": "run-b"})
    assert second["error"]["code"] == -32002
    assert sidecar.call("clear_snapshots")["error"]["code"] == -32002

    assert sidecar.call("cancel_run", {"run_id": "run-a"})["result"] == {"cancelled": True}
    result = sidecar.wait(run)
    assert result["error"] == {"code": -32800, "message": "Run cancelled"}
    # Nothing half-written: no snapshot, no report.
    assert sidecar.call("list_snapshots")["result"]["snapshots"] == []
    assert not (tmp_path / "exports").exists() or not any((tmp_path / "exports").iterdir())
    # The slot is free again.
    assert sidecar.call("cancel_run", {"run_id": "run-a"})["result"] == {"cancelled": False}


def test_event_stream_for_a_selected_subset(sidecar: Sidecar) -> None:
    res = sidecar.call("inventory", {**_target("tiny.demo.local"), "run_id": "run-c",
                                     "name": "tiny", "formats": [],
                                     "areas": ["clusters", "hosts", "vms", "lcm"]})["result"]
    events = [e for e in sidecar.events if e["run_id"] == "run-c"]
    started = [e for e in events if e["type"] == "started"]
    assert len(started) == 1
    assert [a["id"] for a in started[0]["areas"]] == ["clusters", "hosts", "vms", "lcm"]
    assert started[0]["typical_seconds"] == 240 and started[0]["version"] == "pc.7.3"
    finals = {e["area"]["id"]: e["area"] for e in events
              if e["type"] == "area" and e["area"]["status"] != "running"}
    assert set(finals) == {"clusters", "hosts", "vms", "lcm"}
    for area in finals.values():
        assert area["status"] == "collected" and area["api"] == "v4"
        assert isinstance(area["objects"], int) and area["duration_ms"] >= 0
    running = [e for e in events if e["type"] == "area" and e["area"]["status"] == "running"]
    assert len(running) == 4
    totals = [e["objects_total"] for e in events if e["type"] == "area"]
    assert totals == sorted(totals)  # running totals only grow
    assert any(e["type"] == "phase" and e["phase"] == "snapshot" for e in events)
    assert [a["id"] for a in res["areas"]] == ["clusters", "hosts", "vms", "lcm"]


def test_parallel_multi_pc_with_a_failing_target(sidecar: Sidecar) -> None:
    res = sidecar.call("inventory_multi", {
        "run_id": "run-m",
        "targets": [_target("tiny.demo.local"), _target("down.demo.local"),
                    _target("pc03.demo.local")],
        "combine": True, "combined_name": "estate", "formats": [],
    })["result"]
    assert [t["success"] for t in res["targets"]] == [True, False, True]
    assert "resolve" in res["targets"][1]["error"]
    assert res["combined"]["summary"]["source_environments"] == 2
    events = [e for e in sidecar.events if e["run_id"] == "run-m"]
    started = {e["target_index"]: e for e in events if e["type"] == "started"}
    assert set(started) == {0, 2}  # the unreachable PC never got as far as starting
    assert all(e["target_total"] == 3 for e in events if "target_total" in e)
    assert {e["target_host"] for e in events if e["type"] == "area"} == {
        "tiny.demo.local", "pc03.demo.local"}
    assert any(e["type"] == "phase" and e["phase"] == "combine" for e in events)
    kinds = sorted(s["kind"] for s in sidecar.call("list_snapshots")["result"]["snapshots"])
    assert kinds == ["combined", "pc", "pc"]


def test_parse_errors_and_unknown_methods(sidecar: Sidecar) -> None:
    assert sidecar.proc.stdin
    sidecar.proc.stdin.write("this is not json\n")
    sidecar.proc.stdin.flush()
    assert sidecar.call("no_such_method")["error"]["code"] == -32601
    assert sidecar.call("ping")["result"]["demo"] is True
    assert sidecar.orphan_errors == [{"code": -32700, "message": "Parse error"}]
