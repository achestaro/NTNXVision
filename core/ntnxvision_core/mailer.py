"""Outgoing email over SMTP: test messages and report delivery (stdlib only).

The only outbound network use besides the Nutanix APIs, and only when the user has
configured a mail server (Settings > Email). Every outcome is classified into the
`EmailStatus` vocabulary of the IPC contract (``app/src/shared/ipc.ts``) and returned as a
result dict, never raised to the caller, so the UI can say exactly what went wrong.

Security modes: ``none`` (plain SMTP), ``starttls`` (upgrade on 587) and ``ssl``
(implicit TLS on 465). Certificates are verified unless the user allows an untrusted one.
The password is used for AUTH only and never appears in any message or detail.

Demo mode never sends real email: any server other than the local loopback is refused
(``ntnxvision smtp-sink`` runs a local test server that keeps what it receives).
"""

from __future__ import annotations

import ipaddress
import mimetypes
import re
import shutil
import smtplib
import socket
import ssl
import tempfile
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Any

from ntnxvision_core.paths import demo_enabled

SECURITY_MODES = ("none", "starttls", "ssl")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
DEFAULT_TIMEOUT = 20.0
DEFAULT_MAX_MB = 20.0

_MIME = {
    ".xlsx": ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".docx": ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".pdf": ("application", "pdf"),
    ".zip": ("application", "zip"),
    ".csv": ("text", "csv"),
}

_ADDRESS = re.compile(r"^[^\s@<>()\[\]\\,;:\"]+@[^\s@<>()\[\]\\,;:\"]+$")
_NOT_CONFIGURED = "No mail server is set up. Add one in Settings > Email."
_PLACEHOLDER = re.compile(r"\{(target|date|snapshot|summary)\}")


class MailError(Exception):
    """A classified failure: `status` is an EmailStatus value."""

    def __init__(self, status: str, message: str, detail: str | None = None,
                 rejected: list[str] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail
        self.rejected = rejected or []


@dataclass
class SmtpConfig:
    host: str
    port: int = 587
    security: str = "starttls"
    username: str = ""
    password: str = field(default="", repr=False)
    from_address: str = ""
    from_name: str = "NTNXVision"
    allow_untrusted: bool = False
    timeout: float = DEFAULT_TIMEOUT
    max_attachment_mb: float = DEFAULT_MAX_MB
    zip_attachments: bool = False
    #: Extra CA bundle to trust (tests and demo only; never set from the UI).
    cafile: str | None = None

    @classmethod
    def from_params(cls, raw: Any) -> SmtpConfig:
        """Build from the sidecar's snake_case `smtp` block, or raise MailError('invalid')."""
        if not isinstance(raw, dict):
            raise MailError("not_configured", _NOT_CONFIGURED)
        host = str(raw.get("host") or "").strip()
        if not host:
            raise MailError("not_configured", _NOT_CONFIGURED)
        security = str(raw.get("security") or "starttls")
        if security not in SECURITY_MODES:
            raise MailError("invalid", f"Unknown security mode '{security}'.")
        try:
            port = int(raw.get("port") or {"none": 25, "starttls": 587, "ssl": 465}[security])
        except (TypeError, ValueError):
            raise MailError("invalid", "The port must be a number from 1 to 65535.") from None
        if not 1 <= port <= 65535:
            raise MailError("invalid", "The port must be a number from 1 to 65535.")
        from_address = str(raw.get("from_address") or "").strip()
        if not is_valid_address(from_address):
            raise MailError("invalid", "Enter a valid From address in Settings > Email.")
        try:
            max_mb = float(raw.get("max_attachment_mb") or DEFAULT_MAX_MB)
        except (TypeError, ValueError):
            max_mb = DEFAULT_MAX_MB
        if max_mb <= 0:
            max_mb = DEFAULT_MAX_MB
        timeout = raw.get("timeout")
        return cls(
            host=host,
            port=port,
            security=security,
            username=str(raw.get("username") or "").strip(),
            password=str(raw.get("password") or ""),
            from_address=from_address,
            from_name=str(raw.get("from_name") or "").strip(),
            allow_untrusted=bool(raw.get("allow_untrusted", False)),
            timeout=float(timeout) if isinstance(timeout, (int, float)) and timeout > 0
            else DEFAULT_TIMEOUT,
            max_attachment_mb=max_mb,
            zip_attachments=bool(raw.get("zip_attachments", False)),
        )

    @property
    def max_bytes(self) -> int:
        return int(self.max_attachment_mb * 1024 * 1024)


# ─── addresses and templates ───────────────────────────────────────────


def is_valid_address(address: str) -> bool:
    """A plain address ("ops@example.com"): one @, a dotted domain or localhost."""
    a = (address or "").strip()
    if len(a) > 254 or not _ADDRESS.match(a):
        return False
    domain = a.rsplit("@", 1)[1]
    return domain == "localhost" or (
        "." in domain and not domain.startswith(".") and not domain.endswith(".")
        and ".." not in domain)


def clean_addresses(raw: Any) -> list[str]:
    """A list of addresses from a list or a comma/semicolon separated string (deduplicated)."""
    items: Iterable[Any]
    if isinstance(raw, str):
        items = re.split(r"[,;\s]+", raw)
    elif isinstance(raw, (list, tuple)):
        items = raw
    else:
        items = []
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        a = str(item or "").strip()
        if a and a.casefold() not in seen:
            seen.add(a.casefold())
            out.append(a)
    return out


def render_template(text: str, ctx: dict[str, Any]) -> str:
    """Fill {target}, {date}, {snapshot} and {summary}; any other braces stay as typed."""
    def sub(m: re.Match[str]) -> str:
        value = ctx.get(m.group(1))
        return m.group(0) if value is None else str(value)

    return _PLACEHOLDER.sub(sub, text or "")


MAX_SUBJECT = 200


def render_subject(text: str, ctx: dict[str, Any]) -> str:
    """A rendered subject on one line: whitespace runs (newlines included) become single
    spaces and the result is capped, so a multi-snapshot {summary} cannot break the header."""
    one = " ".join(render_template(text, ctx).split())
    return one[:MAX_SUBJECT].rstrip()


def fmt_size(n: int) -> str:
    """Binary size with comma grouping: 34.2 MB, 1,024 KB."""
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value):,} B"
            text = f"{value:,.1f}" if value < 10 else f"{value:,.1f}".rstrip("0").rstrip(".")
            return f"{text} {unit}"
        value /= 1024
    return f"{n:,} B"  # pragma: no cover


def _fmt_mb(mb: float) -> str:
    return f"{mb:,.0f} MB" if float(mb).is_integer() else f"{mb:,.1f} MB"


# ─── message ───────────────────────────────────────────────────────────


def build_message(cfg: SmtpConfig, to: list[str], cc: list[str], subject: str, body: str,
                  attachments: list[Path] | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((cfg.from_name, cfg.from_address)) if cfg.from_name \
        else cfg.from_address
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = " ".join(subject.split())
    msg["Date"] = formatdate(localtime=True)
    domain = cfg.from_address.rsplit("@", 1)[-1] if "@" in cfg.from_address else None
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["X-Mailer"] = "NTNXVision"
    msg.set_content(body or "")
    for path in attachments or []:
        maintype, subtype = _MIME.get(path.suffix.lower(), (None, None))
        if maintype is None:
            guessed = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            maintype, subtype = guessed.split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype,
                           filename=path.name)
    return msg


@contextmanager
def prepared_attachments(cfg: SmtpConfig, files: list[Path], zip_name: str
                         ) -> Iterator[tuple[list[Path], bool]]:
    """The files to attach (zipped into one archive when configured), size-checked.

    Raises MailError('too_large') before anything connects when they exceed the limit.
    """
    tmp: Path | None = None
    try:
        out = list(files)
        zipped = False
        if cfg.zip_attachments and files:
            tmp = Path(tempfile.mkdtemp(prefix="ntnxvision-mail-"))
            archive = tmp / (zip_name if zip_name.endswith(".zip") else f"{zip_name}.zip")
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for f in files:
                    zf.write(f, arcname=f.name)
            out = [archive]
            zipped = True
        total = sum(p.stat().st_size for p in out)
        if total > cfg.max_bytes:
            hint = ("Choose fewer formats or raise the limit in Settings > Email."
                    if cfg.zip_attachments else
                    "Turn on Zip attachments, choose fewer formats or raise the limit "
                    "in Settings > Email.")
            raise MailError(
                "too_large",
                f"Attachments total {fmt_size(total)}, over the "
                f"{_fmt_mb(cfg.max_attachment_mb)} limit. {hint}",
                detail=", ".join(f"{p.name} {fmt_size(p.stat().st_size)}" for p in out))
        yield out, zipped
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


# ─── transport ─────────────────────────────────────────────────────────


def _context(cfg: SmtpConfig) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if cfg.cafile:
        ctx.load_verify_locations(cafile=cfg.cafile)
    if cfg.allow_untrusted:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _guard_demo(cfg: SmtpConfig) -> None:
    if demo_enabled() and cfg.host.strip("[]").lower() not in LOOPBACK:
        raise MailError("invalid", "Demo mode only sends to a local test server on 127.0.0.1 "
                                   "(run: ntnxvision smtp-sink).", detail=cfg.host)


def _reply(code: int, raw: Any) -> str:
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")
    return f"{code} {text}".strip()


def is_loopback_host(host: str) -> bool:
    """True for localhost and any 127.0.0.0/8 or ::1 address."""
    h = (host or "").strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _guard_cleartext_auth(cfg: SmtpConfig) -> None:
    """Never send a sign-in over an unencrypted connection to a non-local server."""
    if cfg.username and cfg.security == "none" and not is_loopback_host(cfg.host):
        raise MailError(
            "auth_failed",
            "This server needs STARTTLS or SSL/TLS to sign in safely. Choose one of them, or "
            "clear the username to send without signing in.",
            detail=f"{cfg.host}:{cfg.port} · Security: None")


def _connect(cfg: SmtpConfig) -> smtplib.SMTP:
    _guard_cleartext_auth(cfg)
    ctx = _context(cfg)
    server: smtplib.SMTP
    if cfg.security == "ssl":
        server = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=cfg.timeout, context=ctx)
    else:
        server = smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout)
    try:
        server.ehlo()
        if cfg.security == "starttls":
            if not server.has_extn("starttls"):
                raise MailError("tls_error", "The server does not offer STARTTLS. Choose None or "
                                             "SSL/TLS, or another port.")
            server.starttls(context=ctx)
            server.ehlo()
        if cfg.username:
            if not server.has_extn("auth"):
                raise MailError("auth_failed", "The server does not accept a sign-in on this "
                                               "connection. Clear the username or turn on TLS.")
            server.login(cfg.username, cfg.password)
    except BaseException:
        _close(server)
        raise
    return server


def _close(server: smtplib.SMTP) -> None:
    try:
        server.quit()
    except Exception:  # noqa: BLE001
        with suppress(Exception):
            server.close()


def _scrub(text: str, cfg: SmtpConfig) -> str:
    if cfg.password and cfg.password in text:
        text = text.replace(cfg.password, "***")
    return text


def classify(exc: BaseException, cfg: SmtpConfig) -> MailError:
    """Map a smtplib / socket / ssl exception to an EmailStatus."""
    where = f"{cfg.host}:{cfg.port}"
    if isinstance(exc, MailError):
        return exc
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return MailError("auth_failed", "The server rejected the username or password.",
                         _reply(exc.smtp_code, exc.smtp_error))
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        rejected = sorted(exc.recipients)
        detail = "; ".join(f"{a}: {_reply(c, m)}" for a, (c, m) in exc.recipients.items())
        who = "the recipient" if len(rejected) == 1 else "every recipient"
        return MailError("recipient_rejected",
                         f"The server refused {who}: {', '.join(rejected)}.", detail, rejected)
    if isinstance(exc, smtplib.SMTPSenderRefused):
        if exc.smtp_code == 530 or b"auth" in (exc.smtp_error or b"").lower():
            return MailError("auth_required", "The server requires a sign-in. Enter a username "
                                              "and password in Settings > Email.",
                             _reply(exc.smtp_code, exc.smtp_error))
        if exc.smtp_code == 552:
            return MailError("too_large", "The server refused the message as too large.",
                             _reply(exc.smtp_code, exc.smtp_error))
        return MailError("sender_rejected", f"The server refused the From address {exc.sender}.",
                         _reply(exc.smtp_code, exc.smtp_error))
    if isinstance(exc, smtplib.SMTPDataError):
        if exc.smtp_code == 552 or b"size" in (exc.smtp_error or b"").lower():
            return MailError("too_large", "The server refused the message as too large. Turn on "
                                          "Zip attachments or choose fewer formats.",
                             _reply(exc.smtp_code, exc.smtp_error))
        return MailError("error", "The server refused the message.",
                         _reply(exc.smtp_code, exc.smtp_error))
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return MailError("tls_error", "The server does not support this connection setting. "
                                      "Check the Security setting.", str(exc))
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        if "timed out" in str(exc).lower():
            return MailError("timeout", f"No answer from {where} in time.", "Timed out")
        return MailError("error", "The server closed the connection. Check that the Security "
                                  "setting matches the port (STARTTLS on 587, SSL/TLS on 465).",
                         str(exc) or where)
    if isinstance(exc, smtplib.SMTPResponseException):
        if exc.smtp_code == 530:
            return MailError("auth_required", "The server requires a sign-in. Enter a username "
                                              "and password in Settings > Email.",
                             _reply(exc.smtp_code, exc.smtp_error))
        return MailError("error", "The server refused the request.",
                         _reply(exc.smtp_code, exc.smtp_error))
    if isinstance(exc, ssl.SSLCertVerificationError):
        return MailError("untrusted_cert", "The server's certificate is not trusted. Turn on "
                                           "Allow untrusted certificate if you trust this server.",
                         exc.verify_message or str(exc))
    if isinstance(exc, ssl.SSLError):
        return MailError("tls_error", "The TLS handshake failed. Check that the Security setting "
                                      "matches the port (STARTTLS on 587, SSL/TLS on 465).",
                         exc.reason or str(exc))
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return MailError("timeout", f"No answer from {where} in time.", "Timed out")
    if isinstance(exc, ConnectionRefusedError):
        return MailError("unreachable", f"Could not connect to {where}.", "Connection refused")
    if isinstance(exc, socket.gaierror):
        return MailError("unreachable", f"Could not find the server {cfg.host}.",
                         f"DNS lookup failed: {exc.strerror or exc}")
    if isinstance(exc, smtplib.SMTPException):
        return MailError("error", "Sending failed.", str(exc))
    if isinstance(exc, OSError):
        return MailError("unreachable", f"Could not connect to {where}.",
                         exc.strerror or str(exc))
    return MailError("error", "Sending failed.", f"{type(exc).__name__}: {exc}")


def _failure(err: MailError, cfg: SmtpConfig | None, **extra: Any) -> dict[str, Any]:
    detail = err.detail
    if cfg is not None and detail:
        detail = _scrub(detail, cfg)
    message = _scrub(err.message, cfg) if cfg is not None else err.message
    return result(False, err.status, message, detail=detail, rejected=err.rejected, **extra)


def result(ok: bool, status: str, message: str, **fields: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": ok, "status": status, "message": message, "detail": None, "recipients": [],
        "rejected": [], "attachments": [], "total_bytes": None, "zipped": False, "files": [],
        "subject": None,
    }
    out.update({k: v for k, v in fields.items() if v is not None or k == "detail"})
    return out


def deliver(cfg: SmtpConfig, msg: EmailMessage, recipients: list[str]) -> None:
    """Connect, sign in and send; raises MailError on any failure (partial refusals too)."""
    _guard_demo(cfg)
    try:
        server = _connect(cfg)
    except BaseException as exc:  # noqa: BLE001
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise classify(exc, cfg) from None
    try:
        refused = server.send_message(msg, from_addr=cfg.from_address, to_addrs=recipients)
    except Exception as exc:  # noqa: BLE001
        _close(server)
        raise classify(exc, cfg) from None
    _close(server)
    if refused:
        rejected = sorted(refused)
        detail = "; ".join(f"{a}: {_reply(c, m)}" for a, (c, m) in refused.items())
        raise MailError("recipient_rejected",
                        f"Sent, but the server refused {len(rejected):,} of "
                        f"{len(recipients):,} recipients: {', '.join(rejected)}.",
                        detail, rejected)


def send_test(cfg: SmtpConfig, to: str) -> dict[str, Any]:
    """Send a short test message to `to` and classify the outcome."""
    to = (to or "").strip()
    if not is_valid_address(to):
        return result(False, "invalid", "Enter the address the test email goes to.")
    security = {"none": "None", "starttls": "STARTTLS", "ssl": "SSL/TLS"}[cfg.security]
    body = (
        "This is a test email from NTNXVision, sent from Settings > Email.\n\n"
        f"Server: {cfg.host}:{cfg.port}\n"
        f"Security: {security}{' (untrusted certificate allowed)' if cfg.allow_untrusted else ''}\n"
        f"Sign-in: {'as ' + cfg.username if cfg.username else 'none'}\n\n"
        "Scheduled and manual report emails will be sent the same way.\n"
    )
    subject = "NTNXVision test email"
    try:
        msg = build_message(cfg, [to], [], subject, body)
        deliver(cfg, msg, [to])
    except MailError as err:
        return _failure(err, cfg, recipients=[to], subject=subject)
    return result(True, "ok", f"Test email sent to {to}.",
                  detail=f"{cfg.host}:{cfg.port} · {security}", recipients=[to], subject=subject)


def send_files(cfg: SmtpConfig, to: list[str], cc: list[str], subject: str, body: str,
               files: list[Path], zip_name: str = "reports") -> dict[str, Any]:
    """Email `files` (zipped and size-checked as configured)."""
    to = clean_addresses(to)
    cc = clean_addresses(cc)
    if not to:
        return result(False, "invalid", "Add at least one recipient.", subject=subject)
    bad = [a for a in to + cc if not is_valid_address(a)]
    if bad:
        return result(False, "invalid", f"Not an email address: {', '.join(bad)}.",
                      subject=subject, rejected=bad)
    recipients = to + [a for a in cc if a.casefold() not in {t.casefold() for t in to}]
    try:
        with prepared_attachments(cfg, files, zip_name) as (attach, zipped):
            sizes: list[dict[str, Any]] = [
                {"name": p.name, "size_bytes": p.stat().st_size} for p in attach]
            total = sum(p.stat().st_size for p in attach)
            msg = build_message(cfg, to, cc, subject, body, attach)
            try:
                deliver(cfg, msg, recipients)
            except MailError as err:
                return _failure(err, cfg, recipients=recipients, attachments=sizes,
                                total_bytes=total, zipped=zipped, subject=subject)
    except MailError as err:
        return _failure(err, cfg, recipients=recipients, subject=subject)
    n = len(sizes)
    files_word = "file" if n == 1 else "files"
    people = "recipient" if len(recipients) == 1 else "recipients"
    return result(True, "ok", f"Sent {n:,} {files_word} to {len(recipients):,} {people}.",
                  detail=f"{fmt_size(total)}{' zipped' if zipped else ''} · "
                         f"{cfg.host}:{cfg.port}",
                  recipients=recipients, attachments=sizes, total_bytes=total,
                  zipped=zipped, subject=subject)
