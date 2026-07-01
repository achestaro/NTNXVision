"""Base collector contract."""

from __future__ import annotations

import abc
from typing import Any

from ntnxvision_core.api import PrismConnection


class Collector(abc.ABC):
    """Collects one object type / feature area and writes it into the Environment.

    Subclasses set `name` and implement `collect`, appending to / setting fields on the
    given `env` (an `Environment`). Collectors must be read-only.
    """

    #: Short identifier used in progress reporting and collection_notes.
    name: str = "collector"
    #: Human-friendly label for UI/progress.
    label: str = "Collector"

    def __init__(self, conn: PrismConnection) -> None:
        self.conn = conn

    @abc.abstractmethod
    def collect(self, env: Any) -> None:  # env: Environment (avoid import cycle)
        """Gather objects and populate `env`. May raise; the engine records the note."""

    # ─── shared helpers ────────────────────────────────────────────

    @staticmethod
    def _name_of(reference: dict[str, Any] | None) -> str | None:
        """Extract a display name from a v3 reference dict."""
        if not reference:
            return None
        return reference.get("name")

    @staticmethod
    def _uuid_of(reference: dict[str, Any] | None) -> str | None:
        if not reference:
            return None
        return reference.get("uuid")
