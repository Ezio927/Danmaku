"""Synthetic credential handling and deterministic Open Live request signing.

This module is the credential-free signing layer for the future Bilibili Open
Live HTTP transport. It models application credentials (access key id + secret)
and the room identity code structurally, but never requests, persists, or logs
real values: every secret is wrapped in a redacting value object, and signing is
a pure function of its inputs, so tests inject synthetic fixtures and never
touch a live account or the network.

Request signing follows the documented Open Live header scheme and is
deterministic: for a fixed access key, secret, method, body, timestamp, and
nonce the signature is always identical. The canonical signature string contains
no secret material (the secret is only the HMAC key), so it is safe to record in
fixtures and diagnostic output.

Behaviour is documented in ``docs/bilibili-open-live-client-core.md``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Mapping

__all__ = [
    "AppCredentials",
    "IdentityCode",
    "IdentityCodeError",
    "MAX_IDENTITY_CODE_LENGTH",
    "REDACTED",
    "SIGNATURE_METHOD",
    "SIGNATURE_VERSION",
    "Secret",
    "SignedRequest",
    "canonical_string",
    "content_md5",
    "redact",
    "sign_request",
]

REDACTED = "<redacted>"
SIGNATURE_METHOD = "HMAC-SHA256"
SIGNATURE_VERSION = "1.0"

MAX_IDENTITY_CODE_LENGTH = 256


class IdentityCodeError(ValueError):
    """Raised when a submitted identity code is malformed."""


def redact(_value: object) -> str:
    """Return the fixed redaction placeholder for a sensitive value."""
    return REDACTED


@dataclass(frozen=True, slots=True)
class Secret:
    """A sensitive string that redacts itself in diagnostic output.

    The raw value is reachable only through :attr:`value`, which signing code
    uses internally. Both ``repr`` and ``str`` return redaction placeholders so
    secrets never leak into exceptions, logs, audit summaries, or request
    debugging output.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError("secret value must be a string")

    def __repr__(self) -> str:
        return f"Secret({REDACTED!r})"

    def __str__(self) -> str:
        return REDACTED


@dataclass(frozen=True, slots=True)
class AppCredentials:
    """A synthetic Open Live application credential pair.

    ``access_key_id`` is public and non-secret. ``access_key_secret`` is a
    :class:`Secret` and redacts itself wherever it is rendered.
    """

    access_key_id: str
    access_key_secret: Secret

    def __post_init__(self) -> None:
        if not isinstance(self.access_key_id, str) or not self.access_key_id:
            raise TypeError("access_key_id must be a non-empty string")
        if any(ord(char) < 0x20 for char in self.access_key_id):
            raise TypeError("access_key_id must not contain control characters")
        if not isinstance(self.access_key_secret, Secret):
            raise TypeError("access_key_secret must be a Secret")
        if not self.access_key_secret.value:
            raise ValueError("access_key_secret must not be empty")


def _validate_identity_code(code: object) -> str:
    if not isinstance(code, str):
        raise IdentityCodeError("identity code must be a string")
    if not code:
        raise IdentityCodeError("identity code must not be empty")
    if len(code) > MAX_IDENTITY_CODE_LENGTH:
        raise IdentityCodeError(
            f"identity code must be at most {MAX_IDENTITY_CODE_LENGTH} characters"
        )
    if any(ord(char) < 0x20 for char in code):
        raise IdentityCodeError(
            "identity code must not contain control characters"
        )
    return code


@dataclass(frozen=True, slots=True)
class IdentityCode:
    """A validated, redacting room identity code.

    The value is validated but never persisted or logged: ``repr`` and ``str``
    both redact it, and the raw value is reachable only through :attr:`value`.
    """

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _validate_identity_code(self.value))

    def __repr__(self) -> str:
        return f"IdentityCode({REDACTED!r})"

    def __str__(self) -> str:
        return REDACTED


def content_md5(body: bytes) -> str:
    """Return the lowercase 32-hex MD5 digest of ``body``."""
    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    return hashlib.md5(body).hexdigest()


def canonical_string(
    *,
    access_key_id: str,
    content_md5: str,
    nonce: str,
    timestamp: int,
    method: str = SIGNATURE_METHOD,
    version: str = SIGNATURE_VERSION,
) -> str:
    """Build the canonical signature string (which contains no secret material)."""
    return (
        f"x-bili-accesskeyid:{access_key_id}\n"
        f"x-bili-content-md5:{content_md5}\n"
        f"x-bili-signature-method:{method}\n"
        f"x-bili-signature-nonce:{nonce}\n"
        f"x-bili-signature-version:{version}\n"
        f"x-bili-timestamp:{timestamp}"
    )


@dataclass(frozen=True, slots=True)
class SignedRequest:
    """A deterministic, fully-specified signed HTTP request.

    The headers include every signing header plus the ``Authorization`` value.
    The ``canonical_string`` and ``signature`` contain no secret material, so
    they are safe to record in fixtures and diagnostics. ``__repr__`` redacts
    the request body, which may carry the identity code for a start request.
    """

    method: str
    path: str
    body: str
    headers: Mapping[str, str]
    signature: str
    canonical_string: str

    @property
    def authorization(self) -> str:
        """The ``Authorization`` header value (access key id + signature)."""
        return self.headers["Authorization"]

    def __repr__(self) -> str:
        return (
            f"SignedRequest(method={self.method!r}, path={self.path!r}, "
            f"body={redact(self.body)!r}, signature={self.signature!r})"
        )

    def summary(self) -> str:
        """Return a secret-safe one-line audit summary of this signed request."""
        return (
            f"{self.method} {self.path} "
            f"(md5={self.headers['x-bili-content-md5']}, "
            f"ts={self.headers['x-bili-timestamp']}, "
            f"nonce={self.headers['x-bili-signature-nonce']})"
        )


def _validate_method(method: object) -> None:
    if not isinstance(method, str) or not method:
        raise TypeError("method must be a non-empty string")
    if any(ord(char) < 0x20 for char in method):
        raise ValueError("method must not contain control characters")


def _validate_path(path: object) -> None:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("path must be a string starting with '/'")
    if any(ord(char) < 0x20 for char in path):
        raise ValueError("path must not contain control characters")


def _validate_timestamp(timestamp: object) -> None:
    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
        raise TypeError("timestamp must be an integer")
    if timestamp < 0:
        raise ValueError("timestamp must be >= 0")


def _validate_nonce(nonce: object) -> None:
    if not isinstance(nonce, str) or not nonce:
        raise TypeError("nonce must be a non-empty string")
    if any(ord(char) < 0x20 for char in nonce):
        raise ValueError("nonce must not contain control characters")


def sign_request(
    *,
    credentials: AppCredentials,
    method: str,
    path: str,
    body: str,
    timestamp: int,
    nonce: str,
) -> SignedRequest:
    """Canonicalize and sign one Open Live HTTP request.

    The result is deterministic: identical inputs always produce the identical
    headers, canonical string, and signature. No secret material is ever placed
    into the canonical string or the signature itself.
    """
    if not isinstance(credentials, AppCredentials):
        raise TypeError("credentials must be AppCredentials")
    _validate_method(method)
    _validate_path(path)
    _validate_timestamp(timestamp)
    _validate_nonce(nonce)
    if not isinstance(body, str):
        raise TypeError("body must be a string")

    body_bytes = body.encode("utf-8")
    digest = content_md5(body_bytes)
    canonical = canonical_string(
        access_key_id=credentials.access_key_id,
        content_md5=digest,
        nonce=nonce,
        timestamp=timestamp,
    )
    signature = base64.b64encode(
        hmac.new(
            credentials.access_key_secret.value.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    ).decode("ascii")

    headers = {
        "Content-Type": "application/json",
        "x-bili-accesskeyid": credentials.access_key_id,
        "x-bili-content-md5": digest,
        "x-bili-signature-method": SIGNATURE_METHOD,
        "x-bili-signature-nonce": nonce,
        "x-bili-signature-version": SIGNATURE_VERSION,
        "x-bili-timestamp": str(timestamp),
        "Authorization": f"{credentials.access_key_id}:{signature}",
    }
    return SignedRequest(
        method=method,
        path=path,
        body=body,
        headers=headers,
        signature=signature,
        canonical_string=canonical,
    )
