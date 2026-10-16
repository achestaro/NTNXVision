"""Typed exceptions for the API layer."""

from __future__ import annotations


class NutanixApiError(Exception):
    """Base class for all API-layer errors.

    Carries the HTTP status when one is known. v4 capability negotiation needs to tell
    "this namespace does not exist on this PC" (404) apart from "the service exists but
    is not running" (503) — the first is a permanent routing fact, the second is a
    transient condition that should not be cached as an absent namespace.
    """

    def __init__(self, *args: object, status_code: int | None = None) -> None:
        super().__init__(*args)
        self.status_code = status_code


class ConnectionFailed(NutanixApiError):
    """Could not reach Prism Central (network / TLS / DNS)."""


class TlsVerificationFailed(ConnectionFailed):
    """The endpoint answered, but its certificate did not verify (self-signed, unknown
    CA, name mismatch). Kept distinct from a network failure so a connection test can
    tell "fix your trust settings" apart from "the host is not there".
    """


class AuthenticationError(NutanixApiError):
    """Credentials were rejected (401 / 403)."""


class ApiVersionUnavailable(NutanixApiError):
    """A required API version/namespace is not available on this Prism Central."""


class CollectionError(NutanixApiError):
    """A collector failed while gathering a specific object type."""
