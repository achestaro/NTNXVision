"""Typed exceptions for the API layer."""

from __future__ import annotations


class NutanixApiError(Exception):
    """Base class for all API-layer errors."""


class ConnectionFailed(NutanixApiError):
    """Could not reach Prism Central (network / TLS / DNS)."""


class AuthenticationError(NutanixApiError):
    """Credentials were rejected (401 / 403)."""


class ApiVersionUnavailable(NutanixApiError):
    """A required API version/namespace is not available on this Prism Central."""


class CollectionError(NutanixApiError):
    """A collector failed while gathering a specific object type."""
