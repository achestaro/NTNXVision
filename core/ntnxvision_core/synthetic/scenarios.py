"""Collect a scenario estate (`profiles.SCENARIOS`) through the mock API.

Runs the real connection layer and collectors against a scenario's synthetic Prism
Centrals and combines them, as a multi-PC run in the app does. Demo mode must be on
(`demo.enable()`); the tests use the session fixture.
"""

from __future__ import annotations

from ntnxvision_core.api import PrismCredentials
from ntnxvision_core.engine import collect_environment, combine_environments
from ntnxvision_core.models import Environment
from ntnxvision_core.synthetic.profiles import SCENARIOS


def collect_scenario(name: str) -> Environment:
    envs = [collect_environment(PrismCredentials(host=h, username="admin", password="demo"))
            for h in SCENARIOS[name]]
    return combine_environments(envs)


__all__ = ["SCENARIOS", "collect_scenario"]
