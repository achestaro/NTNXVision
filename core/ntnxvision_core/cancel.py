"""Cooperative cancellation for collection runs.

A run is cancelled by setting its `CancelToken`. Nothing is interrupted mid-request:
the connection layers check the token before every HTTP call (so a paged walk stops
between pages) and the engine checks it between collectors. The check raises
`RunCancelled`, which derives from `BaseException` on purpose — collectors isolate their
own failures with broad ``except Exception`` blocks, and a cancellation must pass
straight through those instead of being recorded as one more collector error.
"""

from __future__ import annotations

import threading


class RunCancelled(BaseException):
    """The run was cancelled via `cancel_run`. Never caught by `except Exception`."""


class CancelToken:
    """A thread-safe, one-way cancel flag shared by everything working on one run."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, seconds: float) -> bool:
        """Sleep up to `seconds`, waking early on cancel. True when cancelled."""
        return self._event.wait(max(0.0, seconds))

    def check(self) -> None:
        """Raise `RunCancelled` if the run has been cancelled."""
        if self._event.is_set():
            raise RunCancelled("Run cancelled")
