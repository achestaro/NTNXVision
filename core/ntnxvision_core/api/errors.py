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


class CertificateNotTrusted(TlsVerificationFailed):
    """TLS verification is off for an endpoint, but no certificate has been trusted for it
    yet. Nothing is sent to the server: the user must test the connection and trust the
    certificate it presents (trust on first use)."""


class CertificateChanged(TlsVerificationFailed):
    """TLS verification is off and a certificate was trusted for the endpoint, but the
    server now presents a different one. Re-test the connection and trust it again."""


class InvalidHost(NutanixApiError):
    """The host is not a plain hostname or IP address."""


class RedirectBlocked(NutanixApiError):
    """The server answered with an HTTP redirect. Redirects are never followed: a
    cross-host redirect would carry the credentials or session header along."""


class AuthenticationError(NutanixApiError):
    """Credentials were rejected (401 / 403)."""


class ApiVersionUnavailable(NutanixApiError):
    """A required API version/namespace is not available on this Prism Central."""


class CollectionError(NutanixApiError):
    """A collector failed while gathering a specific object type."""


class ReadOnlyViolation(RuntimeError):
    """A request that could change a system was about to leave the process and was
    refused before it was sent (`api.transport.check_request`).

    Not a `NutanixApiError`: it is a bug in NTNXVision, never a server answer.
    """
