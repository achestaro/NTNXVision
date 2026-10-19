"""The mock LCM: status, the inventory action and the tasks that follow it.

Each Prism Central has LCM targets: the PC itself (key ``"pc"``) and every registered
cluster (its uuid, addressed with ``X-Cluster-Id`` as on a real PC). Operations run in
wall-clock time so a poller sees progress: an inventory takes `seconds` (a few seconds in
demo mode, a fraction of a second in tests), an upgrade far longer.

What a PC does is set by `Features.lcm_running` / `Features.lcm_outcome` (the demo
estates) and can be replaced per host with `MockLcm.script(host, ...)` (the tests):

* ``running``: ``{target key: "inventory" | "upgrade"}``, operations already in
  progress when the PC is first asked at each estate epoch (a joined inventory; an
  upgrade of AOS and AHV);
* ``outcome``: ``{target key or "*": outcome}`` for inventories started through the API:
  ``succeeded``, ``failed`` (the task fails with an LCM precheck error), ``forbidden``
  (the POST answers 403), ``status_forbidden`` (even the status read answers 403) or
  ``stuck`` (the task never finishes, for timeouts).

The action requires ``NTNX-Request-Id`` and is idempotent on it, as Nutanix documents.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from ntnxvision_core.synthetic import render as r

TASK_PREFIX = "ZXJnb24=:"
DEFAULT_SECONDS = 6.0
#: An upgrade found running lasts this many inventory durations (never ends in a run).
_UPGRADE_FACTOR = 50


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


class MockLcm:
    def __init__(self, seconds: float = DEFAULT_SECONDS) -> None:
        self.seconds = seconds
        self._lock = threading.Lock()
        self._scripts: dict[str, dict[str, Any]] = {}
        self._seeded: set[str] = set()
        #: task uuid → task record.
        self._tasks: dict[str, dict[str, Any]] = {}
        #: (host, target key) → task uuid of the operation in progress (or last one).
        self._ops: dict[tuple[str, str], str] = {}
        #: NTNX-Request-Id → task uuid (idempotent retries).
        self._requests: dict[str, str] = {}
        #: (host, target key) → when its last inventory finished.
        self.inventoried: dict[tuple[str, str], str] = {}
        #: Every inventory POST accepted: (host, target key).
        self.started: list[tuple[str, str]] = []

    def reset(self) -> None:
        with self._lock:
            self._scripts.clear()
            self._seeded.clear()
            self._tasks.clear()
            self._ops.clear()
            self._requests.clear()
            self.inventoried.clear()
            self.started.clear()

    def script(self, host: str, *, running: dict[str, str] | None = None,
               outcome: dict[str, str] | None = None, seconds: float | None = None) -> None:
        """Replace what `host`'s LCM does (tests)."""
        with self._lock:
            self._scripts[host] = {"running": dict(running or {}),
                                   "outcome": dict(outcome or {}), "seconds": seconds}
            self._seeded = {k for k in self._seeded if not k.startswith(f"{host}#")}

    # ─── config ────────────────────────────────────────────────────

    def _conf(self, host: str, est: Any) -> dict[str, Any]:
        scripted = self._scripts.get(host)
        if scripted is not None:
            return scripted
        f = est.features
        keys = ["pc", *(c["uuid"] for c in est.clusters)]
        running = {}
        for index, kind in f.lcm_running:
            key = "pc" if index < 0 else (keys[index + 1] if index + 1 < len(keys) else None)
            if key:
                running[key] = kind
        return {"running": running, "outcome": {"*": f.lcm_outcome}, "seconds": None}

    def _seconds(self, conf: dict[str, Any]) -> float:
        return float(conf.get("seconds") or self.seconds)

    def _outcome(self, conf: dict[str, Any], key: str) -> str:
        outcome = conf["outcome"]
        return str(outcome.get(key) or outcome.get("*") or "succeeded")

    def _seed(self, host: str, est: Any) -> dict[str, Any]:
        """Start the operations a PC has running when first asked (once per reset)."""
        conf = self._conf(host, est)
        # Once per estate epoch: each demo collection advances the epoch, so the next one
        # finds the scenario's operations running again.
        seed_key = f"{host}#{getattr(est, 'epoch', 0)}"
        if seed_key not in self._seeded:
            self._seeded.add(seed_key)
            for key, kind in conf["running"].items():
                duration = self._seconds(conf) * (_UPGRADE_FACTOR if kind == "upgrade" else 1)
                # A joined inventory has been running a little while already.
                # Both have been running a while: the inventory a third of its time.
                self._new_task(host, key, kind, duration, "succeeded",
                               started=time.time() - duration * (0.3 if kind != "upgrade"
                                                                 else 0.35))
        return conf

    def _new_task(self, host: str, key: str, kind: str, duration: float, outcome: str,
                  started: float | None = None) -> str:
        task_id = str(uuid.uuid4())
        self._tasks[task_id] = {"host": host, "key": key, "kind": kind,
                                "started": started or time.time(), "duration": duration,
                                "outcome": outcome, "recorded": False}
        self._ops[(host, key)] = task_id
        return task_id

    # ─── task state ────────────────────────────────────────────────

    def _state(self, task: dict[str, Any]) -> tuple[str, int]:
        elapsed = time.time() - task["started"]
        if task["outcome"] == "stuck":
            return "RUNNING", min(95, int(elapsed / max(task["duration"], 0.001) * 100))
        if elapsed >= task["duration"]:
            state = "FAILED" if task["outcome"] == "failed" else "SUCCEEDED"
            if state == "SUCCEEDED" and task["kind"] == "inventory" and not task["recorded"]:
                task["recorded"] = True
                self.inventoried[(task["host"], task["key"])] = _iso(
                    task["started"] + task["duration"])
            return state, 100
        return "RUNNING", int(elapsed / task["duration"] * 100)

    def _running(self, host: str, key: str) -> tuple[str, dict[str, Any]] | None:
        task_id = self._ops.get((host, key))
        task = self._tasks.get(task_id or "")
        if task is None or self._state(task)[0] != "RUNNING":
            return None
        return task_id or "", task

    def upgrading(self, host: str, est: Any) -> set[str]:
        """Target keys with an upgrade in progress (their entities show a target version)."""
        with self._lock:
            self._seed(host, est)
            return {key for (h, key), _ in list(self._ops.items()) if h == host
                    and (run := self._running(host, key)) and run[1]["kind"] == "upgrade"}

    # ─── endpoints ─────────────────────────────────────────────────

    def status(self, host: str, est: Any, cluster: str | None) -> tuple[int, Any]:
        key = cluster or "pc"
        with self._lock:
            conf = self._seed(host, est)
            if self._outcome(conf, key) == "status_forbidden":
                return 403, _forbidden()
            run = self._running(host, key)
        op: dict[str, Any] = {}
        if run is not None:
            op = {"operationType": "INVENTORY" if run[1]["kind"] == "inventory" else "UPGRADE",
                  "operationId": run[0]}
        return 200, r.v4_single(r.lcm_status_v4(est.features.lcm_framework_stale, op))

    def inventory(self, host: str, est: Any, cluster: str | None,
                  request_id: str | None) -> tuple[int, Any]:
        key = cluster or "pc"
        if cluster and cluster not in {c["uuid"] for c in est.clusters}:
            return 400, _error("LIF-10001", f"Unknown cluster {cluster}")
        if not request_id:
            return 400, _error("VALIDATION_ERROR", "Missing NTNX-Request-Id header")
        with self._lock:
            conf = self._seed(host, est)
            outcome = self._outcome(conf, key)
            if outcome in ("forbidden", "status_forbidden"):
                return 403, _forbidden()
            if request_id in self._requests:
                task_id = self._requests[request_id]
            else:
                if self._running(host, key) is not None:
                    return 409, _error("LIF-20009", "Another LCM operation is in progress")
                task_id = self._new_task(host, key, "inventory", self._seconds(conf), outcome)
                self._requests[request_id] = task_id
                self.started.append((host, key))
        return 202, {"$objectType": "lifecycle.v4.operations.InventoryApiResponse",
                     "data": {"$objectType": "prism.v4.config.TaskReference",
                              "extId": f"{TASK_PREFIX}{task_id}"}}

    def task(self, raw_id: str, est: Any) -> tuple[int, Any]:
        if not raw_id.startswith(TASK_PREFIX):
            return 400, _error("VALIDATION_ERROR", f"Invalid task id {raw_id}")
        with self._lock:
            task = self._tasks.get(raw_id[len(TASK_PREFIX):])
            if task is None:
                return 404, _error("NOT_FOUND", f"Task {raw_id} not found")
            state, pct = self._state(task)
        names = {c["uuid"]: c["name"] for c in est.clusters}
        cluster = None if task["key"] == "pc" else task["key"]
        data: dict[str, Any] = {
            "$objectType": "prism.v4.config.Task", "extId": raw_id,
            "operation": "kLcmRootTask",
            "operationDescription": ("Inventory root task" if task["kind"] == "inventory"
                                     else "Upgrade root task"),
            "status": state, "progressPercentage": pct,
            "createdTime": _iso(task["started"]), "startedTime": _iso(task["started"]),
            "clusterExtIds": [cluster or est.pc_uuid],
            "entitiesAffected": [{"extId": cluster or est.pc_uuid,
                                  "name": names.get(cluster or "", "Prism Central"),
                                  "rel": "clustermgmt:config:cluster"}],
            "errorMessages": None,
        }
        if state != "RUNNING":
            data["completedTime"] = _iso(task["started"] + task["duration"])
        if state == "FAILED":
            data["errorMessages"] = [{
                "message": "Failed to perform the precheck test_example_check: the "
                           "synthetic cluster failed an LCM precheck.",
                "severity": "ERROR", "code": "LIF-20002"}]
        return 200, r.v4_single(data)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"$objectType": "lifecycle.v4.error.ErrorResponse",
            "data": {"error": [{"code": code, "message": message, "severity": "ERROR"}]}}


def _forbidden() -> dict[str, Any]:
    return _error("LIF-40301", "The user is not authorized to perform this operation.")
