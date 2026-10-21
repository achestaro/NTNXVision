"""The optional LCM inventory before a collection: the one write NTNXVision can make.

Everything else NTNXVision sends is a read. This module is the single exception, and
the only code that issues it: when the user switches "Run LCM inventory first" on for a
run, it asks LCM on Prism Central itself and on every cluster in the run's scope to
refresh its inventory, so the LCM entities collected afterwards are current. An LCM
inventory upgrades nothing; it re-reads what is installed and what LCM could install.

How it stays narrow:

* **Off by default, per run.** The engine creates an `LcmInventory` only when the
  request carries ``lcm_inventory: true``; nothing inherits it (schedules never send it).
* **One request, one place.** `_start_inventory` is the only function that sends the
  POST, and it does so inside `transport.lcm_inventory_permit()`. Without the permit the
  transport's read-only guard refuses the request before it leaves the process, and
  `tests/test_lcm_inventory.py` asserts that no other module enters the permit.
* **Never on top of another LCM operation.** Each target's LCM status is read first.
  An inventory already running is *joined* (monitored to completion, its results used).
  An upgrade, prechecks or upload in progress means no inventory on that target: what is
  running is reported (operation, components with from and to versions, progress, start
  time) and the current LCM values are collected as they are.
* **Isolated failures.** A 403 (the account lacks the LCM "Perform Inventory" permission:
  Super Admin, Prism Admin or Cluster Admin) or any other error is recorded on that
  target only; the collection itself never fails because of the inventory.

API (verified read-only against a live pc.7.5.1.1; the POST shape from Nutanix's
lifecycle v4 OpenAPI specification and SDKs, see docs/ARCHITECTURE.md):

* ``GET  /api/lifecycle/v4.0/resources/status`` with ``X-Cluster-Id: <cluster extId>``
  (omitted for Prism Central's own LCM): ``inProgressOperation {operationType,
  operationId}``. ``$clusterExtId`` as a query parameter is ignored by the PC; the header
  is what routes the call.
* ``POST /api/lifecycle/v4.0/operations/$actions/inventory`` with the same header and a
  fresh ``NTNX-Request-Id`` (idempotency key), no body: ``202`` and a task reference.
* ``GET  /api/prism/v4.0/config/tasks/ZXJnb24=:<uuid>``: ``status``, ``progressPercentage``,
  ``errorMessages``. A bare uuid is a 400, so LCM's operation ids get the ``ZXJnb24=:``
  (base64 "ergon") prefix.
* ``GET  /api/lifecycle/v4.0/resources/entities``: entities whose ``targetVersion``
  differs from ``entityVersion`` are the components an upgrade is moving.

Below the v4 GA line (pc.2024.1 and pc.2024.2) `LegacyLcmInventory` drives the same
run through the interface the Prism UI uses there, Genesis RPC
(``POST /PrismGateway/services/rest/v1/genesis``; the exact bodies and the guard's body
check are in `api.transport`, next to `genesis_body`):

* ``is_lcm_operation_in_progress`` (read): the root task of a running LCM operation;
  its v3 task (``GET /api/nutanix/v3/tasks/<uuid>``) says whether it is an inventory
  (joined) or something else (skipped as busy);
* ``get_config`` (read): the LCM catalogue URL the inventory is given;
* ``perform_inventory`` (the write, inside the permit, sent once: Genesis has no
  idempotency key, so a 429 is not retried);
* the returned root task is polled with ``GET /api/nutanix/v3/tasks/<uuid>``.

Clusters are addressed through Prism Central's Prism Element proxy
(``?proxyClusterUuid=<uuid>``), Prism Central's own LCM without it. These payloads are
from general Nutanix knowledge and not yet verified on a live pc.2024.x. Below
pc.2024.3 the LCM collector reads no entities (it is v4 only), so the legacy inventory
refreshes what LCM shows in Prism, not a collected value; the run says so.
Other versions without the v4 lifecycle API are not attempted; the run records why.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ntnxvision_core.api.errors import AuthenticationError, NutanixApiError, ReadOnlyViolation
from ntnxvision_core.api.transport import (
    GENESIS_INVENTORY_METHOD,
    GENESIS_PATH,
    UUID_RE,
    genesis_body,
    lcm_inventory_permit,
)
from ntnxvision_core.areas import LCM_INVENTORY_SECONDS
from ntnxvision_core.cancel import CancelToken, RunCancelled
from ntnxvision_core.models import (
    LcmBusyComponent,
    LcmBusyOperation,
    LcmInventoryRun,
    LcmInventoryTarget,
)
from ntnxvision_core.numfmt import fmt_count

STATUS_PATH = "lifecycle/v4.0/resources/status"
INVENTORY_PATH = "lifecycle/v4.0/operations/$actions/inventory"
ENTITIES_PATH = "lifecycle/v4.0/resources/entities"
TASK_PATH = "prism/v4.0/config/tasks/{}"
#: Task ids are "<base64 'ergon'>:<uuid>"; LCM's status reports the bare uuid.
TASK_PREFIX = "ZXJnb24=:"
#: The legacy interface's task reads (v3 tasks API on Prism Central).
V3_TASK_PATH = "/api/nutanix/v3/tasks/{}"
#: Prism Central versions without the v4 lifecycle API that the legacy driver serves.
LEGACY_PC_VERSIONS = ("pc.2024.1", "pc.2024.2")

DEFAULT_TIMEOUT_S = 1800
MIN_TIMEOUT_S = 60
MAX_TIMEOUT_S = 6 * 3600
#: What an inventory typically adds to a run, for the estimate shown before it starts.
TYPICAL_SECONDS = LCM_INVENTORY_SECONDS

#: Final per-target statuses.
SUCCEEDED, FAILED, TIMED_OUT, CANCELLED = "succeeded", "failed", "timed_out", "cancelled"
SKIPPED_BUSY, FORBIDDEN, UNAVAILABLE = "skipped_busy", "forbidden", "unavailable"
#: Transient ones.
PENDING, RUNNING = "pending", "running"
FINAL = {SUCCEEDED, FAILED, TIMED_OUT, CANCELLED, SKIPPED_BUSY, FORBIDDEN, UNAVAILABLE}

_INVENTORY_OPS = {"INVENTORY", "TARGETED_INVENTORY"}
_IDLE_OPS = {"", "NONE"}
_OP_LABEL = {
    "UPGRADE": "upgrade in progress",
    "PRECHECKS": "LCM prechecks in progress",
    "UPLOAD": "LCM bundle upload in progress",
    "CENTRALIZED_UPLOAD": "LCM bundle upload in progress",
}
_CANT_CANCEL = ("NTNXVision stopped waiting; the LCM task keeps running on Prism "
                "Central and cannot be cancelled from NTNXVision")

#: Consecutive failed task reads before a target is given up as failed.
_MAX_READ_ERRORS = 5


class _Stopped(Exception):
    """The engine stopped the inventory (the collection ended without waiting for it)."""


_STOPPED = ("The collection ended before the inventory finished: " + _CANT_CANCEL)


def legacy_supported(version: str | None) -> bool:
    """True for pc.2024.1 / pc.2024.2 (any build): the legacy Genesis driver's range."""
    v = (version or "").strip().lower()
    if not v.startswith("pc."):
        v = f"pc.{v}"
    return any(v == base or v.startswith(base + ".") for base in LEGACY_PC_VERSIONS)


@dataclass
class InventoryOptions:
    timeout_s: float = DEFAULT_TIMEOUT_S
    #: Poll backoff: the first wait, the growth factor and the longest wait.
    poll_initial_s: float = 5.0
    poll_factor: float = 1.5
    poll_max_s: float = 30.0


def valid_timeout(value: Any) -> int | None:
    """A timeout in seconds within the allowed range, else None (bools are refused)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    seconds = int(value)
    return seconds if MIN_TIMEOUT_S <= seconds <= MAX_TIMEOUT_S else None


def task_ref(raw: str) -> str:
    """The id the prism tasks API accepts: LCM reports bare uuids."""
    return raw if ":" in raw else f"{TASK_PREFIX}{raw}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


Update = Callable[[LcmInventoryRun], None]


class LcmInventory:
    """Runs the LCM inventory on Prism Central and the selected clusters, in the
    background, while the other collectors run.

    `start()` launches one worker thread with its own Prism Central connection (the
    collectors keep theirs). It reads every target's LCM status, starts or joins the
    inventories, then polls their tasks together with backoff until each finishes, the
    timeout passes, or the run is cancelled. `on_update` receives a copy of the state
    after every change; `wait()` blocks the engine until the worker is done. `stop()`
    ends the polling early (the engine calls it when the run ends for any reason).

    This class talks to the v4 lifecycle API; `LegacyLcmInventory` overrides the four
    request hooks (`_read_status`, `_start_inventory`, `_read_task`, `_describe_busy`)
    for the Genesis interface of pc.2024.1 / pc.2024.2.
    """

    API = "v4"

    def __init__(
        self,
        connect: Callable[[], Any],
        clusters: list[tuple[str, str | None]],
        *,
        options: InventoryOptions | None = None,
        cancel: CancelToken | None = None,
        on_update: Update | None = None,
        registered: set[str] | None = None,
    ) -> None:
        self._connect = connect
        self.options = options or InventoryOptions()
        self.cancel = cancel
        self.on_update = on_update
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        #: Every registered cluster (selected or not): entities of none of them are
        #: Prism Central's own.
        self._cluster_ids = set(registered or ()) | {cid for cid, _ in clusters}
        self._t0 = time.monotonic()
        self._clock: dict[int, float] = {}
        self._errors: dict[int, int] = {}
        self._entities: list[dict[str, Any]] | None = None
        self.run = LcmInventoryRun(
            requested=True, api=self.API, timeout_s=int(self.options.timeout_s),
            targets=[LcmInventoryTarget(kind="prism_central", name="Prism Central"),
                     *(LcmInventoryTarget(kind="cluster", target_id=cid, name=name or cid)
                       for cid, name in clusters)])

    # ─── lifecycle ─────────────────────────────────────────────────

    def start(self) -> None:
        self.run.started_at = _now()
        self._t0 = time.monotonic()
        self._publish()
        self._thread = threading.Thread(target=self._work, name="lcm-inventory", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop polling: open targets end as cancelled and the worker exits promptly.

        A no-op once the worker is done. The LCM tasks themselves keep running."""
        self._stop.set()

    def wait(self) -> LcmInventoryRun:
        """Block until every target is final; raises `RunCancelled` on cancel."""
        thread = self._thread
        while thread is not None and thread.is_alive():
            if self.cancel is not None and self.cancel.cancelled:
                thread.join(2.0)  # the worker marks its targets and exits promptly
                raise RunCancelled("Run cancelled")
            thread.join(0.2)
        return self.result()

    def result(self) -> LcmInventoryRun:
        with self._lock:
            return self.run.model_copy(deep=True)

    def _work(self) -> None:
        try:
            with self._connect() as conn:
                self._drive(conn)
        except RunCancelled:
            self._finish_open(CANCELLED, f"Run cancelled: {_CANT_CANCEL}")
        except _Stopped:
            self._finish_open(CANCELLED, _STOPPED)
        except Exception as exc:  # noqa: BLE001 — never fail the collection over this
            self._finish_open(UNAVAILABLE, f"LCM could not be reached: {exc}")
        finally:
            with self._lock:
                self.run.finished_at = _now()
                self.run.duration_ms = int((time.monotonic() - self._t0) * 1000)
            self._publish()

    # ─── the run ───────────────────────────────────────────────────

    def _drive(self, conn: Any) -> None:
        # 1. Read every target's LCM status; start the idle ones' inventories right away,
        #    so they run in parallel on the Nutanix side.
        for i, target in enumerate(self.run.targets):
            self._check_cancel()
            self._begin(conn, i, target)
            self._publish()
        # 2. Poll the running tasks together until all are final.
        interval = self.options.poll_initial_s
        deadline = self._t0 + self.options.timeout_s
        while True:
            active = [i for i, t in enumerate(self.run.targets) if t.status == RUNNING]
            if not active:
                return
            if time.monotonic() >= deadline:
                minutes = max(1, round(self.options.timeout_s / 60))
                for i in active:
                    self._final(i, TIMED_OUT, f"Still running after {fmt_count(minutes)} "
                                              f"min: {_CANT_CANCEL}")
                return
            self._pause(min(interval, max(0.0, deadline - time.monotonic())))
            interval = min(interval * self.options.poll_factor, self.options.poll_max_s)
            for i in active:
                self._check_cancel()
                self._poll(conn, i)
            self._publish()

    def _pause(self, seconds: float) -> None:
        """Sleep, waking early (and raising) on cancel or `stop()`."""
        end = time.monotonic() + seconds
        while True:
            self._check_cancel()
            left = end - time.monotonic()
            if left <= 0:
                return
            if self._stop.wait(min(left, 0.1)):
                raise _Stopped()

    # ─── requests (v4 lifecycle; `LegacyLcmInventory` overrides these) ──

    def _read_status(self, conn: Any, target: LcmInventoryTarget) -> tuple[str, str]:
        """(operation type in upper case, its id) of the LCM operation in progress."""
        status = (conn.v4_get(STATUS_PATH, headers=_cluster_header(target)).get("data")
                  or {})
        op = status.get("inProgressOperation") or {}
        return (str(op.get("operationType") or "").upper(),
                str(op.get("operationId") or "").strip())

    def _task_ref(self, raw: str) -> str:
        return task_ref(raw)

    def _read_task(self, conn: Any, task_id: str) -> tuple[str, Any, str | None]:
        """(status in upper case, progress percent, error text) of a task."""
        task = conn.v4_get(TASK_PATH.format(task_id)).get("data") or {}
        return (str(task.get("status") or "").upper(), task.get("progressPercentage"),
                _task_error(task))

    def _begin(self, conn: Any, i: int, target: LcmInventoryTarget) -> None:
        try:
            kind, op_id = self._read_status(conn, target)
        except AuthenticationError:
            self._final(i, FORBIDDEN, "The account cannot read LCM status (needs the LCM "
                                      "View Status permission)")
            return
        except NutanixApiError as exc:
            self._final(i, UNAVAILABLE, f"LCM status could not be read: {_short(exc)}")
            return
        if kind in _INVENTORY_OPS:
            with self._lock:
                target.joined = True
                target.task_id = self._task_ref(op_id) if op_id else None
                target.status = RUNNING
                target.started_at = _now()
                self._clock[i] = time.monotonic()
            if not op_id:
                self._final(i, UNAVAILABLE, "An inventory is running but LCM did not report "
                                            "its task, so it cannot be followed")
            return
        if kind not in _IDLE_OPS or op_id:
            busy = self._describe_busy(conn, target, kind, op_id)
            self._final(i, SKIPPED_BUSY, busy_sentence(busy), busy=busy)
            return
        self._start_inventory(conn, i, target)

    def _start_inventory(self, conn: Any, i: int, target: LcmInventoryTarget) -> None:
        """Send the LCM inventory request: the only non-read request NTNXVision makes."""
        url = f"{conn.creds.base}/api/{INVENTORY_PATH}"
        headers = {**_cluster_header(target), "NTNX-Request-Id": str(uuid.uuid4())}
        with lcm_inventory_permit():
            # `_send` retries a 429 with the same NTNX-Request-Id, which LCM treats as the
            # same request (idempotent), so a retry never starts a second inventory.
            resp = conn._send("POST", url, headers=headers)
        if resp.status_code in (401, 403):
            self._final(i, FORBIDDEN, "The account lacks the LCM Perform Inventory "
                                      "permission (Super Admin, Prism Admin or Cluster "
                                      "Admin role)")
            return
        if resp.status_code >= 400:
            self._final(i, FAILED, f"LCM refused the inventory: HTTP {resp.status_code}"
                                   f"{_error_text(resp)}")
            return
        try:
            data = resp.json().get("data") or {}
        except ValueError:
            data = {}
        task = str(data.get("extId") or "").strip()
        with self._lock:
            target.status = RUNNING
            target.task_id = task_ref(task) if task else None
            target.started_at = _now()
            target.percent = 0
            target.detail = None
            self._clock[i] = time.monotonic()
        if not task:
            self._final(i, FAILED, "LCM accepted the inventory but returned no task id")

    def _poll(self, conn: Any, i: int) -> None:
        target = self.run.targets[i]
        assert target.task_id is not None
        try:
            state, pct, error = self._read_task(conn, target.task_id)
        except NutanixApiError as exc:
            self._errors[i] = self._errors.get(i, 0) + 1
            if self._errors[i] >= _MAX_READ_ERRORS:
                self._final(i, FAILED, f"The LCM task could not be read: {_short(exc)}")
            return
        self._errors[i] = 0
        with self._lock:
            if isinstance(pct, (int, float)) and not isinstance(pct, bool):
                target.percent = max(0, min(100, int(pct)))
        if state == "SUCCEEDED":
            self._final(i, SUCCEEDED, None, percent=100)
        elif state in ("FAILED", "CANCELED", "CANCELLED", "ABORTED"):
            reason = error or ("The LCM task was cancelled on Prism Central"
                               if state.startswith("CANCEL") or state == "ABORTED"
                               else "LCM failed")
            self._final(i, FAILED, reason)

    # ─── busy targets ──────────────────────────────────────────────

    def _describe_busy(self, conn: Any, target: LcmInventoryTarget, kind: str,
                       op_id: str) -> LcmBusyOperation:
        busy = LcmBusyOperation(operation=kind or None,
                                task_id=task_ref(op_id) if op_id else None)
        if busy.task_id:
            try:
                task = conn.v4_get(TASK_PATH.format(busy.task_id)).get("data") or {}
                busy.description = _text(task.get("operationDescription"))
                pct = task.get("progressPercentage")
                busy.percent = int(pct) if isinstance(pct, (int, float)) else None
                busy.started_at = _text(task.get("startedTime")) or \
                    _text(task.get("createdTime"))
            except NutanixApiError:
                pass
        for entity in self._target_entities(conn, target):
            current = _text(entity.get("entityVersion"))
            goal = _text(entity.get("targetVersion"))
            if goal and goal != current:
                busy.components.append(LcmBusyComponent(
                    name=_text(entity.get("entityModel")), from_version=current,
                    to_version=goal))
        return busy

    def _target_entities(self, conn: Any, target: LcmInventoryTarget) -> list[dict[str, Any]]:
        if self._entities is None:
            try:
                self._entities = list(conn.v4_list(ENTITIES_PATH))
            except NutanixApiError:
                self._entities = []
        if target.kind == "cluster":
            return [e for e in self._entities if e.get("clusterExtId") == target.target_id]
        return [e for e in self._entities if e.get("clusterExtId") not in self._cluster_ids]

    # ─── state ─────────────────────────────────────────────────────

    def _final(self, i: int, status: str, detail: str | None, *,
               busy: LcmBusyOperation | None = None, percent: int | None = None) -> None:
        with self._lock:
            target = self.run.targets[i]
            target.status = status
            target.detail = detail
            target.finished_at = _now()
            if busy is not None:
                target.busy = busy
            if percent is not None:
                target.percent = percent
            if i in self._clock:
                target.duration_ms = int((time.monotonic() - self._clock[i]) * 1000)

    def _finish_open(self, status: str, detail: str) -> None:
        for i, target in enumerate(self.run.targets):
            if target.status not in FINAL:
                self._final(i, status, detail)

    def _check_cancel(self) -> None:
        if self.cancel is not None:
            self.cancel.check()
        if self._stop.is_set():
            raise _Stopped()

    def _publish(self) -> None:
        if self.on_update is not None:
            self.on_update(self.result())


class LegacyLcmInventory(LcmInventory):
    """The same run through LCM's Genesis RPC, for pc.2024.1 / pc.2024.2.

    Statuses, busy detection, polling, timeout, cancel and notes are the base class's;
    only the requests differ (see the module docstring and `api.transport`)."""

    API = "genesis"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: Each target's v3 root task as read during the status check (busy details).
        self._busy_tasks: dict[str, dict[str, Any]] = {}

    def _task_ref(self, raw: str) -> str:
        return raw  # the v3 tasks API takes the bare uuid

    def _genesis(self, conn: Any, target: LcmInventoryTarget, method: str,
                 args: list[str] | None = None) -> Any:
        """A read-only LCM framework RPC; returns its decoded ``.return`` value."""
        resp = conn._send("POST", f"{conn.creds.base}{GENESIS_PATH}",
                          params=_proxy_params(target), json=genesis_body(method, args))
        return _genesis_return(conn._json_or_raise(resp))

    def _v3_task(self, conn: Any, task_id: str) -> dict[str, Any]:
        """A v3 task; an id that is not a plain uuid is never put into a URL."""
        if not UUID_RE.fullmatch(task_id):
            raise NutanixApiError(f"Not a task uuid: {task_id[:80]!r}")
        return conn.get_json(f"{conn.creds.base}{V3_TASK_PATH.format(task_id)}")

    def _read_status(self, conn: Any, target: LcmInventoryTarget) -> tuple[str, str]:
        running = self._genesis(conn, target, "is_lcm_operation_in_progress")
        op_id = running.strip() if isinstance(running, str) else ""
        if not op_id:
            return "", ""
        if not UUID_RE.fullmatch(op_id):
            # Something runs but its task cannot be read safely: never start on top of it.
            return "OPERATION", op_id[:80]
        try:
            task = self._v3_task(conn, op_id)
        except NutanixApiError:
            # Something runs but cannot be identified: never start on top of it.
            return "OPERATION", op_id
        self._busy_tasks[op_id] = task
        words = " ".join(str(task.get(k) or "") for k in
                         ("operation_type", "progress_message", "display_name")).lower()
        if "inventory" in words:
            return "INVENTORY", op_id
        if "upgrade" in words or "update" in words:
            return "UPGRADE", op_id
        if "precheck" in words:
            return "PRECHECKS", op_id
        return "OPERATION", op_id

    def _read_task(self, conn: Any, task_id: str) -> tuple[str, Any, str | None]:
        task = self._v3_task(conn, task_id)
        state = str(task.get("status") or "").upper()
        error = _text(task.get("error_detail"))
        if error:
            error = " ".join(error.split()).rstrip(". ")[:300]
        return state, task.get("percentage_complete"), error

    def _describe_busy(self, conn: Any, target: LcmInventoryTarget, kind: str,
                       op_id: str) -> LcmBusyOperation:
        busy = LcmBusyOperation(operation=None if kind == "OPERATION" else kind or None,
                                task_id=op_id or None)
        task = self._busy_tasks.get(op_id) or {}
        busy.description = _text(task.get("progress_message"))
        pct = task.get("percentage_complete")
        busy.percent = int(pct) if isinstance(pct, (int, float)) else None
        busy.started_at = _text(task.get("start_time")) or _text(task.get("creation_time"))
        return busy  # component versions come from the v4 entities, not served here

    def _start_inventory(self, conn: Any, i: int, target: LcmInventoryTarget) -> None:
        """Send the legacy LCM inventory RPC: the only non-read request on pc.2024.1/2."""
        try:
            config = self._genesis(conn, target, "get_config")
        except AuthenticationError:
            self._final(i, FORBIDDEN, "The account cannot read the LCM configuration")
            return
        except NutanixApiError as exc:
            self._final(i, UNAVAILABLE, f"The LCM configuration could not be read: "
                                        f"{_short(exc)}")
            return
        catalogue = _text(config.get("url")) if isinstance(config, dict) else None
        if not catalogue:
            self._final(i, UNAVAILABLE, "LCM did not report its catalogue URL, so no "
                                        "inventory was started")
            return
        url = f"{conn.creds.base}{GENESIS_PATH}"
        body = genesis_body(GENESIS_INVENTORY_METHOD, [catalogue])
        try:
            with lcm_inventory_permit():
                # Sent once: Genesis has no idempotency key, so a retried 429 could start
                # a second inventory.
                resp = conn._send("POST", url, params=_proxy_params(target), json=body,
                                  retry_429=False)
        except ReadOnlyViolation:
            # The guard binds the inventory to the catalogue URL LCM's own get_config
            # reply gave; anything else is refused before it is sent, for this target.
            self._final(i, FAILED, "The inventory request was refused before it was sent: "
                                   "LCM's catalogue URL could not be confirmed")
            return
        if resp.status_code in (401, 403):
            self._final(i, FORBIDDEN, "The account lacks the LCM Perform Inventory "
                                      "permission (Super Admin, Prism Admin or Cluster "
                                      "Admin role)")
            return
        if resp.status_code >= 400:
            self._final(i, FAILED, f"LCM refused the inventory: HTTP {resp.status_code}"
                                   f"{_error_text(resp)}")
            return
        try:
            task = _genesis_return(resp.json())
        except (ValueError, NutanixApiError) as exc:
            self._final(i, FAILED, f"LCM did not accept the inventory: {_short(exc)}")
            return
        task_id = task.strip() if isinstance(task, str) else ""
        if task_id and not UUID_RE.fullmatch(task_id):
            self._final(i, FAILED, "LCM accepted the inventory but returned a task id that "
                                   "is not a uuid, so it cannot be followed")
            return
        with self._lock:
            target.status = RUNNING
            target.task_id = task_id or None
            target.started_at = _now()
            target.percent = 0
            target.detail = None
            self._clock[i] = time.monotonic()
        if not task_id:
            self._final(i, FAILED, "LCM accepted the inventory but returned no task id")


def _proxy_params(target: LcmInventoryTarget) -> dict[str, str]:
    """Prism Central's proxy to a cluster's Prism Element; none for PC's own LCM."""
    return {"proxyClusterUuid": target.target_id} if target.target_id else {}


def _genesis_return(body: Any) -> Any:
    """The ``.return`` of a Genesis answer ``{"value": "<JSON text>"}``.

    Raises `NutanixApiError` for an ``.error`` answer or a body of another shape."""
    value = body.get("value") if isinstance(body, dict) else None
    if not isinstance(value, str):
        raise NutanixApiError("Genesis answered without a value")
    try:
        decoded = json.loads(value)
    except ValueError as exc:
        raise NutanixApiError("Genesis answered with a value that is not JSON") from exc
    if not isinstance(decoded, dict):
        raise NutanixApiError("Genesis answered with an unexpected value")
    if decoded.get(".error"):
        raise NutanixApiError(f"LCM error: {str(decoded['.error'])[:200]}")
    return decoded.get(".return")


def _cluster_header(target: LcmInventoryTarget) -> dict[str, str]:
    """``X-Cluster-Id`` addresses one cluster's LCM; without it LCM is Prism Central's."""
    return {"X-Cluster-Id": target.target_id} if target.target_id else {}


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _short(exc: Exception) -> str:
    text = str(exc).strip()
    return text[:200] if text else type(exc).__name__


def _task_error(task: dict[str, Any]) -> str | None:
    for msg in task.get("errorMessages") or []:
        if isinstance(msg, dict) and _text(msg.get("message")):
            # LCM messages carry newlines and a doubled full stop ("...14607.\n.").
            return " ".join(str(msg["message"]).split()).rstrip(". ")[:300]
    return _text(task.get("legacyErrorMessage"))


def _error_text(resp: Any) -> str:
    try:
        body = resp.json()
    except ValueError:
        return ""
    data = body.get("data") if isinstance(body, dict) else None
    errors = (data or {}).get("error") if isinstance(data, dict) else None
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        message = _text(errors[0].get("message"))
        if message:
            return f" ({message[:200]})"
    message = _text(body.get("message")) if isinstance(body, dict) else None
    return f" ({message[:200]})" if message else ""


# ─── wording shared by the run notes, Results and the reports ──────────

STATUS_LABEL = {
    SUCCEEDED: "Succeeded",
    FAILED: "Failed",
    TIMED_OUT: "Timed out",
    CANCELLED: "Cancelled",
    SKIPPED_BUSY: "Skipped",
    FORBIDDEN: "No permission",
    UNAVAILABLE: "Not available",
    PENDING: "Waiting",
    RUNNING: "Running",
}


def busy_sentence(busy: LcmBusyOperation) -> str:
    """'upgrade in progress (AOS 7.0 to 7.3; 35%, started 2026-10-19 03:25 UTC)'."""
    label = _OP_LABEL.get(busy.operation or "", "LCM operation in progress"
                          + (f" ({busy.operation.lower()})" if busy.operation else ""))
    parts: list[str] = []
    if busy.components:
        parts.append(", ".join(
            f"{c.name or 'component'} {c.from_version or 'N/A'} to {c.to_version or 'N/A'}"
            for c in busy.components[:6])
            + (f" + {fmt_count(len(busy.components) - 6)} more"
               if len(busy.components) > 6 else ""))
    elif busy.description:
        parts.append(busy.description)
    when: list[str] = []
    if busy.percent is not None:
        when.append(f"{busy.percent}%")
    if busy.started_at:
        when.append(f"started {_utc_label(busy.started_at)}")
    if when:
        parts.append(", ".join(when))
    return f"{label} ({'; '.join(parts)})" if parts else label


def target_outcome(t: LcmInventoryTarget) -> str:
    """One target's result in words: 'joined running inventory, succeeded'."""
    if t.status == SKIPPED_BUSY:
        return f"skipped: {t.detail}" if t.detail else "skipped: LCM busy"
    words = STATUS_LABEL.get(t.status, t.status).lower()
    if t.joined:
        words = f"joined running inventory, {words}"
    return words


def note_line(run: LcmInventoryRun) -> str:
    """The run's `collection_notes["lcm_inventory"]` line."""
    if run.detail and not run.targets:
        return f"skipped: {run.detail}"
    parts = []
    for t in run.targets:
        text = f"{t.name or t.target_id or 'target'}: {target_outcome(t)}"
        if t.duration_ms and t.status in (SUCCEEDED, FAILED, TIMED_OUT):
            text += f" in {duration_label(t.duration_ms)}"
        if t.detail and t.status in (FAILED, FORBIDDEN, UNAVAILABLE, TIMED_OUT):
            text += f" ({t.detail})"
        parts.append(text)
    head = "ok" if all(t.status == SUCCEEDED for t in run.targets) else "notes"
    tail = f"; {LEGACY_NOTE}" if run.api == LegacyLcmInventory.API else ""
    return f"{head}: " + "; ".join(parts) + tail


def progress_line(run: LcmInventoryRun) -> str:
    """The LCM area's live detail: 'Inventory running on PHX-01 45%, Prism Central 20%'."""
    running = [t for t in run.targets if t.status == RUNNING]
    done = sum(1 for t in run.targets if t.status in FINAL)
    if running:
        shown = ", ".join(f"{t.name} {t.percent or 0}%" for t in running[:3])
        more = f" + {fmt_count(len(running) - 3)} more" if len(running) > 3 else ""
        return (f"LCM inventory running on {shown}{more} "
                f"({fmt_count(done)} of {fmt_count(len(run.targets))} done)")
    if done < len(run.targets):
        return "Checking LCM status before the inventory"
    return "LCM inventory finished"


def report_rows(run: LcmInventoryRun) -> list[list[Any]]:
    """[Cluster, Result, Duration, Task, Detail] per target, for the reports."""
    rows = []
    for t in run.targets:
        result = STATUS_LABEL.get(t.status, t.status)
        if t.joined:
            result = f"Joined running inventory, {result.lower()}"
        rows.append([t.name if t.kind == "cluster" else "Prism Central", result,
                     duration_label(t.duration_ms) if t.duration_ms else None,
                     t.task_id, t.detail])
    return rows


def started_count(*runs: LcmInventoryRun | None) -> int:
    """Targets on which these runs started an LCM inventory themselves."""
    return sum(r.started_count for r in runs if r is not None and r.requested)


def targets_phrase(count: int) -> str:
    return f"{fmt_count(count)} target{'s' if count != 1 else ''}"


def provenance(*runs: LcmInventoryRun | None, snapshots: int = 1) -> str:
    """The provenance sentence every report prints (cover note, workbook footers).

    Read-only, unless the run started an LCM inventory, which is then stated."""
    head = ("Generated from read-only inventory snapshots" if snapshots > 1
            else "Generated from a read-only inventory snapshot")
    started = started_count(*runs)
    if not started:
        return f"{head}; no configuration was changed."
    return (f"{head}; an LCM inventory was run on {targets_phrase(started)} before "
            "collection, no configuration was changed.")


#: What a legacy (Genesis) inventory does and does not refresh, for notes and reports.
LEGACY_NOTE = ("run through the legacy LCM interface of this Prism Central version; it "
               "refreshes what LCM shows in Prism, LCM values are collected from "
               "pc.2024.3 on")


def report_sentence(run: LcmInventoryRun | None) -> str | None:
    """The Lifecycle chapter's sentence about the inventory, or None when not requested."""
    if run is None or not run.requested:
        return None
    if not run.targets:
        return (f"An LCM inventory was requested for this collection but not run: "
                f"{run.detail or 'not available'}.")
    ok = sum(1 for t in run.targets if t.status == SUCCEEDED)
    total = len(run.targets)
    if run.api == LegacyLcmInventory.API:
        return (f"An LCM inventory was requested before this collection on Prism Central "
                f"and {fmt_count(total - 1)} cluster{'s' if total - 1 != 1 else ''}; it "
                f"completed on {fmt_count(ok)} of {fmt_count(total)} targets. It was "
                f"{LEGACY_NOTE}. The inventory refreshes what LCM knows; it upgrades "
                "nothing.")
    text = (f"An LCM inventory was requested before this collection on Prism Central and "
            f"{fmt_count(total - 1)} cluster{'s' if total - 1 != 1 else ''}; it completed on "
            f"{fmt_count(ok)} of {fmt_count(total)} targets, so the values below are fresh "
            "for those targets")
    others = [t for t in run.targets if t.status != SUCCEEDED]
    if others:
        text += ("; the others show the values LCM held from its last inventory ("
                 + "; ".join(f"{t.name}: {target_outcome(t)}" for t in others) + ")")
    return text + ". The inventory refreshes what LCM knows; it upgrades nothing."


def duration_label(ms: int) -> str:
    seconds = max(0, round(ms / 1000))
    if seconds < 60:
        return f"{seconds} s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {sec} s" if sec else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


def _utc_label(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
