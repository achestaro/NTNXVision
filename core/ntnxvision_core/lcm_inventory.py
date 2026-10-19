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

Below the v4 GA line (pc.2024.1 and pc.2024.2) the inventory is not attempted: the LCM
collector reads entities only through the v4 lifecycle namespace, so an inventory there
could not make any collected value fresher, and the pre-v4 LCM interfaces are internal
RPCs. The run records why.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ntnxvision_core.api.errors import AuthenticationError, NutanixApiError
from ntnxvision_core.api.transport import lcm_inventory_permit
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
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


Update = Callable[[LcmInventoryRun], None]


class LcmInventory:
    """Runs the LCM inventory on Prism Central and the selected clusters, in the
    background, while the other collectors run.

    `start()` launches one worker thread with its own Prism Central connection (the
    collectors keep theirs). It reads every target's LCM status, starts or joins the
    inventories, then polls their tasks together with backoff until each finishes, the
    timeout passes, or the run is cancelled. `on_update` receives a copy of the state
    after every change; `wait()` blocks the engine until the worker is done.
    """

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
        self._thread: threading.Thread | None = None
        #: Every registered cluster (selected or not): entities of none of them are
        #: Prism Central's own.
        self._cluster_ids = set(registered or ()) | {cid for cid, _ in clusters}
        self._t0 = time.monotonic()
        self._clock: dict[int, float] = {}
        self._errors: dict[int, int] = {}
        self._entities: list[dict[str, Any]] | None = None
        self.run = LcmInventoryRun(
            requested=True, api="v4", timeout_s=int(self.options.timeout_s),
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
            pause = min(interval, max(0.0, deadline - time.monotonic()))
            if self.cancel is not None:
                if self.cancel.wait(pause):
                    raise RunCancelled("Run cancelled")
            else:
                time.sleep(pause)
            interval = min(interval * self.options.poll_factor, self.options.poll_max_s)
            for i in active:
                self._check_cancel()
                self._poll(conn, i)
            self._publish()

    def _begin(self, conn: Any, i: int, target: LcmInventoryTarget) -> None:
        headers = _cluster_header(target)
        try:
            status = (conn.v4_get(STATUS_PATH, headers=headers).get("data") or {})
        except AuthenticationError:
            self._final(i, FORBIDDEN, "The account cannot read LCM status (needs the LCM "
                                      "View Status permission)")
            return
        except NutanixApiError as exc:
            self._final(i, UNAVAILABLE, f"LCM status could not be read: {_short(exc)}")
            return
        op = status.get("inProgressOperation") or {}
        kind = str(op.get("operationType") or "").upper()
        op_id = str(op.get("operationId") or "").strip()
        if kind in _INVENTORY_OPS:
            with self._lock:
                target.joined = True
                target.task_id = task_ref(op_id) if op_id else None
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
            task = conn.v4_get(TASK_PATH.format(target.task_id)).get("data") or {}
        except NutanixApiError as exc:
            self._errors[i] = self._errors.get(i, 0) + 1
            if self._errors[i] >= _MAX_READ_ERRORS:
                self._final(i, FAILED, f"The LCM task could not be read: {_short(exc)}")
            return
        self._errors[i] = 0
        state = str(task.get("status") or "").upper()
        pct = task.get("progressPercentage")
        with self._lock:
            if isinstance(pct, (int, float)):
                target.percent = max(0, min(100, int(pct)))
        if state == "SUCCEEDED":
            self._final(i, SUCCEEDED, None, percent=100)
        elif state in ("FAILED", "CANCELED", "CANCELLED"):
            reason = _task_error(task) or ("The LCM task was cancelled on Prism Central"
                                           if state.startswith("CANCEL") else "LCM failed")
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

    def _publish(self) -> None:
        if self.on_update is not None:
            self.on_update(self.result())


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
            return str(msg["message"]).strip().rstrip(".")[:300]
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
    return f"{head}: " + "; ".join(parts)


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


def report_sentence(run: LcmInventoryRun | None) -> str | None:
    """The Lifecycle chapter's sentence about the inventory, or None when not requested."""
    if run is None or not run.requested:
        return None
    if not run.targets:
        return (f"An LCM inventory was requested for this collection but not run: "
                f"{run.detail or 'not available'}.")
    ok = sum(1 for t in run.targets if t.status == SUCCEEDED)
    total = len(run.targets)
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
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
