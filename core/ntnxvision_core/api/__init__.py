"""API access layer.

Prefers the official Nutanix **v4 SDKs**, falling back to **v3 / v2 REST** for features
not yet covered by v4 (recovery plans, X-Play, some Flow rules). All collectors depend
only on the `PrismConnection` facade, not on any specific API version.
"""

from ntnxvision_core.api.connection import PrismConnection, PrismCredentials
from ntnxvision_core.api.errors import (
    ApiVersionUnavailable,
    AuthenticationError,
    ConnectionFailed,
    NutanixApiError,
)

__all__ = [
    "PrismConnection",
    "PrismCredentials",
    "NutanixApiError",
    "AuthenticationError",
    "ConnectionFailed",
    "ApiVersionUnavailable",
]
