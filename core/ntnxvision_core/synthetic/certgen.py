"""Demo-mode TLS material, generated on this machine — never shipped, never committed.

The mock Nutanix server needs certificates for 127.0.0.1, and the repository (rightly)
refuses ``*.pem`` / ``*.key`` files. So the first time demo mode starts, this module mints:

* a throwaway **NTNXVision Demo CA** — its private key exists only in memory for the
  duration of signing and is then discarded, so the CA can never vouch for anything else;
* a **server** leaf for 127.0.0.1 / localhost signed by that CA (what the trusted demo
  hostnames present — demo mode trusts the CA for them only);
* a genuinely **self-signed** leaf, what a lab Prism Central presents out of the box.

It is written with the standard library alone (RSA key generation, DER encoding and
PKCS#1 v1.5 signing are a few dozen lines each), so demo mode adds no dependency to the
frozen sidecar. The files are cached under the user's cache directory and regenerated
when missing or close to expiry.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import os
import secrets
import shutil
import ssl
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SMALL_PRIMES = [p for p in range(3, 2000) if all(p % d for d in range(2, int(p**0.5) + 1))]

FILES = ("demo-ca.pem", "server.pem", "server.key", "selfsigned.pem", "selfsigned.key")


def cert_dir() -> Path:
    """Where the generated material lives (``NTNXVISION_DEMO_CERT_DIR`` overrides)."""
    override = os.environ.get("NTNXVISION_DEMO_CERT_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "NTNXVision" / "demo-certs"
    if os.uname().sysname == "Darwin":
        return Path.home() / "Library" / "Caches" / "NTNXVision" / "demo-certs"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / \
        "ntnxvision" / "demo-certs"


def ensure() -> Path:
    """Return a directory holding valid demo certificates, generating them if needed."""
    target = cert_dir()
    if _valid(target):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".demo-certs-", dir=target.parent))
    try:
        _generate(work)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        try:
            os.replace(work, target)
        except OSError:
            # Another process won the race; its set is just as good.
            if not _valid(target):
                raise
    finally:
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
    return target


def _valid(directory: Path) -> bool:
    if not all((directory / f).is_file() for f in FILES):
        return False
    try:
        info = ssl._ssl._test_decode_cert(str(directory / "server.pem"))  # type: ignore[attr-defined]
        expires = ssl.cert_time_to_seconds(info["notAfter"])
    except Exception:  # noqa: BLE001 — unreadable means regenerate
        return False
    return expires - time.time() > 30 * 86400


# ─── RSA ───────────────────────────────────────────────────────────────


def _is_probable_prime(n: int, rounds: int = 24) -> bool:
    for p in _SMALL_PRIMES:
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        a = secrets.randbelow(n - 3) + 2
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits: int, e: int) -> int:
    while True:
        n = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if _is_probable_prime(n) and (n - 1) % e:
            return n


class RsaKey:
    def __init__(self, bits: int = 2048) -> None:
        self.e = 65537
        while True:
            p, q = _prime(bits // 2, self.e), _prime(bits // 2, self.e)
            if p != q and (p * q).bit_length() == bits:
                break
        self.p, self.q = max(p, q), min(p, q)
        self.n = self.p * self.q
        self.d = pow(self.e, -1, (self.p - 1) * (self.q - 1))
        self.k = (self.n.bit_length() + 7) // 8

    def sign_sha256(self, data: bytes) -> bytes:
        digest_info = _seq(_seq(_oid("2.16.840.1.101.3.4.2.1"), _NULL),
                           _tlv(0x04, hashlib.sha256(data).digest()))
        em = b"\x00\x01" + b"\xff" * (self.k - len(digest_info) - 3) + b"\x00" + digest_info
        sig = pow(int.from_bytes(em, "big"), self.d, self.n)
        return sig.to_bytes(self.k, "big")

    def public_der(self) -> bytes:
        return _seq(_int(self.n), _int(self.e))

    def private_pem(self) -> str:
        der = _seq(_int(0), _int(self.n), _int(self.e), _int(self.d), _int(self.p),
                   _int(self.q), _int(self.d % (self.p - 1)), _int(self.d % (self.q - 1)),
                   _int(pow(self.q, -1, self.p)))
        return _pem("RSA PRIVATE KEY", der)


# ─── DER ───────────────────────────────────────────────────────────────


def _tlv(tag: int, content: bytes) -> bytes:
    n = len(content)
    if n < 0x80:
        length = bytes([n])
    else:
        raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
        length = bytes([0x80 | len(raw)]) + raw
    return bytes([tag]) + length + content


def _seq(*parts: bytes) -> bytes:
    return _tlv(0x30, b"".join(parts))


def _int(value: int) -> bytes:
    raw = value.to_bytes(max(1, (value.bit_length() + 8) // 8), "big")
    return _tlv(0x02, raw)


def _oid(dotted: str) -> bytes:
    arcs = [int(a) for a in dotted.split(".")]
    body = bytearray([40 * arcs[0] + arcs[1]])
    for arc in arcs[2:]:
        chunk = [arc & 0x7F]
        arc >>= 7
        while arc:
            chunk.append(0x80 | (arc & 0x7F))
            arc >>= 7
        body += bytes(reversed(chunk))
    return _tlv(0x06, bytes(body))


_NULL = b"\x05\x00"
_SHA256_RSA = _seq(_oid("1.2.840.113549.1.1.11"), _NULL)


def _name(cn: str, org: str) -> bytes:
    def rdn(oid: str, value: str) -> bytes:
        return _tlv(0x31, _seq(_oid(oid), _tlv(0x0C, value.encode())))
    return _seq(rdn("2.5.4.10", org), rdn("2.5.4.3", cn))


def _time(dt: datetime) -> bytes:
    return _tlv(0x17, dt.strftime("%y%m%d%H%M%SZ").encode())


def _ext(oid: str, value: bytes, critical: bool = False) -> bytes:
    parts = [_oid(oid)]
    if critical:
        parts.append(b"\x01\x01\xff")
    parts.append(_tlv(0x04, value))
    return _seq(*parts)


def _pem(label: str, der: bytes) -> str:
    body = base64.encodebytes(der).decode().replace("\n", "")
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    return f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n"


def _certificate(subject: tuple[str, str], issuer: tuple[str, str], key: RsaKey,
                 signer: RsaKey, *, ca: bool, days: int) -> bytes:
    now = datetime.now(UTC).replace(microsecond=0)
    spki = _seq(_seq(_oid("1.2.840.113549.1.1.1"), _NULL), _tlv(0x03, b"\x00" + key.public_der()))
    ski = hashlib.sha1(key.public_der()).digest()  # noqa: S324 — RFC 5280 key identifier
    aki = hashlib.sha1(signer.public_der()).digest()  # noqa: S324
    if ca:
        exts = [
            _ext("2.5.29.19", _seq(b"\x01\x01\xff"), critical=True),
            _ext("2.5.29.15", _tlv(0x03, b"\x01\x06"), critical=True),
            _ext("2.5.29.14", _tlv(0x04, ski)),
        ]
    else:
        san = _seq(_tlv(0x82, b"localhost"), _tlv(0x87, bytes([127, 0, 0, 1])))
        exts = [
            _ext("2.5.29.19", _seq(), critical=True),
            _ext("2.5.29.15", _tlv(0x03, b"\x05\xa0"), critical=True),
            _ext("2.5.29.37", _seq(_oid("1.3.6.1.5.5.7.3.1"))),
            _ext("2.5.29.17", san),
            _ext("2.5.29.14", _tlv(0x04, ski)),
            _ext("2.5.29.35", _seq(_tlv(0x80, aki))),
        ]
    tbs = _seq(
        _tlv(0xA0, _int(2)),
        _int(secrets.randbits(63) | 1),
        _SHA256_RSA,
        _name(*issuer),
        _seq(_time(now - timedelta(days=1)), _time(now + timedelta(days=days))),
        _name(*subject),
        spki,
        _tlv(0xA3, _seq(*exts)),
    )
    return _seq(tbs, _SHA256_RSA, _tlv(0x03, b"\x00" + signer.sign_sha256(tbs)))


def _generate(directory: Path) -> None:
    ca_name = ("NTNXVision Demo CA", "NTNXVision synthetic data")
    ca_key = RsaKey()
    ca = _certificate(ca_name, ca_name, ca_key, ca_key, ca=True, days=800)
    leaf_key = RsaKey()
    leaf = _certificate(("demo.ntnxvision.local", "NTNXVision synthetic data"), ca_name,
                        leaf_key, ca_key, ca=False, days=790)
    del ca_key  # the CA key never touches the disk
    self_key = RsaKey()
    self_name = ("selfsigned.demo.local", "Lab Prism Central (self-signed)")
    self_cert = _certificate(self_name, self_name, self_key, self_key, ca=False, days=790)
    (directory / "demo-ca.pem").write_text(_pem("CERTIFICATE", ca))
    (directory / "server.pem").write_text(_pem("CERTIFICATE", leaf))
    (directory / "selfsigned.pem").write_text(_pem("CERTIFICATE", self_cert))
    for name, key in (("server.key", leaf_key), ("selfsigned.key", self_key)):
        path = directory / name
        path.write_text(key.private_pem())
        with contextlib.suppress(OSError):
            path.chmod(0o600)
