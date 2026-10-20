"""Helpers shared by the architecture-diagram tests."""

from __future__ import annotations

import random
from typing import Any

from pydantic import BaseModel


def _shuffle(value: Any, r: random.Random) -> None:
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            _shuffle(getattr(value, name), r)
    elif isinstance(value, list):
        r.shuffle(value)
        for item in value:
            _shuffle(item, r)
    elif isinstance(value, dict):
        for item in value.values():
            _shuffle(item, r)


def shuffled(env: Any, seed: int) -> Any:
    """A deep copy of `env` with EVERY list shuffled, nested ones too (a host's uplinks,
    a policy's locations and their clusters, a category's values): diagrams must not
    depend on the order any API returns anything in."""
    out = env.model_copy(deep=True)
    _shuffle(out, random.Random(seed))
    return out
