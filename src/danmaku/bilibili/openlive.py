"""Open Live HTTP session request/response models and WebSocket bootstrap.

This module defines the strict, deterministic models for the three Open Live
session endpoints (start, heartbeat, end), the shared response envelope, and
the WebSocket bootstrap/auth-body framing. It performs no network access: it
only validates parsed JSON against the documented schemas and frames/parses
auth packets through the existing :class:`~danmaku.bilibili.wire.BilibiliWireCodec`
wire codec without changing its protocol-v1 product-frame behaviour.

Every "exact" schema means no additional or missing object keys. Malformed or
unexpected responses are rejected with :class:`MalformedResponseError` rather
than silently coerced. The contract is documented in
``docs/bilibili-open-live-client-core.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .signing import IdentityCode, Secret, redact
from .wire import ControlFrame, Operation, ProtocolVersion, decode, encode_frame

__all__ = [
    "AuthError",
    "EndRequest",
    "EndResponse",
    "Envelope",
    "HeartbeatRequest",
    "HeartbeatResponse",
    "MalformedResponseError",
    "OpenLiveError",
    "StartRequest",
    "StartResponse",
    "WebSocketInfo",
    "decode_auth_reply",
    "encode_auth_frame",
    "parse_envelope",
]


class OpenLiveError(Exception):
    """Base class for Open Live transport errors."""


class MalformedResponseError(OpenLiveError):
    """The platform returned a response that violates the documented schema."""


class AuthError(OpenLiveError):
    """The WebSocket auth handshake was rejected by the platform."""


def _reject_constant(value: str) -> None:
    raise MalformedResponseError(f"non-finite numbers are not allowed: {value}")


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MalformedResponseError(f"{field} must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], field: str) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details = []
    if missing:
        details.append(f"missing {missing}")
    if unknown:
        details.append(f"unknown {unknown}")
    raise MalformedResponseError(
        f"{field} must have exactly keys {sorted(expected)}; {' and '.join(details)}"
    )


def _validate_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise MalformedResponseError(f"{field} must be a string")
    if any(ord(char) < 0x20 for char in value):
        raise MalformedResponseError(f"{field} must not contain control characters")
    return value


def _validate_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MalformedResponseError(f"{field} must be an integer")
    if value < 1:
        raise MalformedResponseError(f"{field} must be a positive integer")
    return value


def _validate_game_id(value: Any) -> str:
    value = _validate_string(value, "game_info.game_id")
    if not value:
        raise MalformedResponseError("game_info.game_id must not be empty")
    return value


def _validate_request_game_id(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("game_id must be a non-empty string")
    if any(ord(char) < 0x20 for char in value):
        raise ValueError("game_id must not contain control characters")
    return value


def _validate_auth_body(value: Any) -> Secret:
    text = _validate_string(value, "websocket_info.auth_body")
    try:
        parsed = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise MalformedResponseError(
            "websocket_info.auth_body must be valid JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise MalformedResponseError("websocket_info.auth_body must be a JSON object")
    return Secret(text)


def _validate_wss_link(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise MalformedResponseError("websocket_info.wss_link must be an array")
    if not value:
        raise MalformedResponseError("websocket_info.wss_link must not be empty")
    links = []
    for index, link in enumerate(value):
        link = _validate_string(link, f"websocket_info.wss_link[{index}]")
        if not link.startswith(("wss://", "ws://")):
            raise MalformedResponseError(
                f"websocket_info.wss_link[{index}] must start with ws:// or wss://"
            )
        links.append(link)
    return tuple(links)


@dataclass(frozen=True, slots=True)
class Envelope:
    """A parsed, validated Open Live HTTP response envelope.

    ``data`` is the raw, as-yet-untyped payload and may carry the WebSocket
    ``auth_body`` (a secret) on a Start response, so ``__repr__`` redacts it.
    """

    code: int
    message: str
    data: Any

    def __repr__(self) -> str:
        return (
            f"Envelope(code={self.code!r}, message={self.message!r}, "
            f"data={redact(self.data)!r})"
        )


_ENVELOPE_KEYS = frozenset({"code", "message", "data"})


def parse_envelope(body: bytes) -> Envelope:
    """Validate the shared Open Live HTTP response envelope.

    The body must be strict UTF-8, a single JSON object with exactly the keys
    ``code`` (integer), ``message`` (string), and ``data``. On success
    (``code == 0``) ``data`` must be an object. Non-finite JSON numbers are
    rejected. Raises :class:`MalformedResponseError` otherwise.
    """
    if not isinstance(body, bytes):
        raise TypeError("response body must be bytes")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MalformedResponseError("response body is not valid UTF-8") from exc
    try:
        value = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise MalformedResponseError("response body is not valid JSON") from exc
    if not isinstance(value, dict):
        raise MalformedResponseError("response must be a JSON object")
    _exact_keys(value, _ENVELOPE_KEYS, "response")

    code = value["code"]
    if isinstance(code, bool) or not isinstance(code, int):
        raise MalformedResponseError("code must be an integer")
    message = value["message"]
    if not isinstance(message, str):
        raise MalformedResponseError("message must be a string")
    data = value["data"]
    if code == 0 and not isinstance(data, dict):
        raise MalformedResponseError("data must be an object on success")
    return Envelope(code=code, message=message, data=data)


@dataclass(frozen=True, slots=True)
class StartRequest:
    """A validated Start request (identity code + application id)."""

    code: IdentityCode
    app_id: int

    def __post_init__(self) -> None:
        if not isinstance(self.code, IdentityCode):
            raise TypeError("code must be an IdentityCode")
        if (
            isinstance(self.app_id, bool)
            or not isinstance(self.app_id, int)
            or self.app_id < 1
        ):
            raise ValueError("app_id must be a positive integer")

    def body_json(self) -> str:
        """Return the exact, deterministically ordered request body JSON."""
        return json.dumps(
            {"code": self.code.value, "app_id": self.app_id},
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class HeartbeatRequest:
    """A validated Heartbeat request (session game id)."""

    game_id: str

    def __post_init__(self) -> None:
        _validate_request_game_id(self.game_id)

    def body_json(self) -> str:
        """Return the exact, deterministically ordered request body JSON."""
        return json.dumps(
            {"game_id": self.game_id}, ensure_ascii=False, separators=(",", ":")
        )


@dataclass(frozen=True, slots=True)
class EndRequest:
    """A validated End request (application id + session game id)."""

    app_id: int
    game_id: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.app_id, bool)
            or not isinstance(self.app_id, int)
            or self.app_id < 1
        ):
            raise ValueError("app_id must be a positive integer")
        _validate_request_game_id(self.game_id)

    def body_json(self) -> str:
        """Return the exact, deterministically ordered request body JSON."""
        return json.dumps(
            {"app_id": self.app_id, "game_id": self.game_id},
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class WebSocketInfo:
    """Validated WebSocket bootstrap information from the start endpoint.

    ``wss_link`` is a non-empty tuple of ``ws://``/``wss://`` URLs and
    ``auth_body`` is the exact JSON object text to send as the AUTH frame body.
    ``auth_body`` is wrapped in :class:`~danmaku.bilibili.signing.Secret` so it
    redacts itself in diagnostic output.
    """

    wss_link: tuple[str, ...]
    auth_body: Secret

    def __post_init__(self) -> None:
        if not isinstance(self.wss_link, tuple):
            raise TypeError("wss_link must be a tuple")
        if not isinstance(self.auth_body, Secret):
            raise TypeError("auth_body must be a Secret")


_WS_INFO_KEYS = frozenset({"wss_link", "auth_body"})
_GAME_INFO_KEYS = frozenset({"game_id"})
_START_DATA_KEYS = frozenset({"game_info", "websocket_info"})


@dataclass(frozen=True, slots=True)
class StartResponse:
    """A validated Start success payload."""

    game_id: str
    websocket_info: WebSocketInfo

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> "StartResponse":
        """Validate a Start ``data`` object and return the model."""
        value = _require_object(data, "data")
        _exact_keys(value, _START_DATA_KEYS, "data")

        game_info = _require_object(value["game_info"], "data.game_info")
        _exact_keys(game_info, _GAME_INFO_KEYS, "data.game_info")
        game_id = _validate_game_id(game_info["game_id"])

        ws_info = _require_object(value["websocket_info"], "data.websocket_info")
        _exact_keys(ws_info, _WS_INFO_KEYS, "data.websocket_info")
        wss_link = _validate_wss_link(ws_info["wss_link"])
        auth_body = _validate_auth_body(ws_info["auth_body"])

        return cls(game_id=game_id, websocket_info=WebSocketInfo(wss_link, auth_body))


_HEARTBEAT_DATA_KEYS = frozenset({"interval"})


@dataclass(frozen=True, slots=True)
class HeartbeatResponse:
    """A validated Heartbeat success payload."""

    interval: int | None

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> "HeartbeatResponse":
        """Validate a Heartbeat ``data`` object and return the model.

        The only documented optional field is ``interval`` (a positive integer
        heartbeat interval in seconds); any other key is rejected.
        """
        value = _require_object(data, "data")
        actual = set(value)
        unknown = sorted(actual - _HEARTBEAT_DATA_KEYS)
        if unknown:
            raise MalformedResponseError(
                f"data must not contain unknown keys {unknown}"
            )
        interval = value.get("interval")
        if interval is None:
            return cls(interval=None)
        if isinstance(interval, bool) or not isinstance(interval, int):
            raise MalformedResponseError("data.interval must be an integer")
        if interval < 1:
            raise MalformedResponseError("data.interval must be a positive integer")
        return cls(interval=interval)


@dataclass(frozen=True, slots=True)
class EndResponse:
    """A validated End success payload (no additional fields are modeled)."""

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> "EndResponse":
        """Validate an End ``data`` object and return the model."""
        _require_object(data, "data")
        return cls()


def encode_auth_frame(auth_body: str, *, sequence: int = 1) -> bytes:
    """Build the AUTH frame (operation 7, plain body) from an auth body.

    The auth body must be a valid JSON object string; it is carried verbatim as
    the frame body using the existing wire codec's ``encode_frame``.
    """
    if not isinstance(auth_body, str):
        raise TypeError("auth_body must be a string")
    try:
        parsed = json.loads(auth_body, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise MalformedResponseError("auth_body must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise MalformedResponseError("auth_body must be a JSON object")
    return encode_frame(
        protocol_version=ProtocolVersion.PLAIN,
        operation=Operation.AUTH,
        sequence=sequence,
        body=auth_body.encode("utf-8"),
    )


def decode_auth_reply(data: bytes) -> int:
    """Decode an AUTH_REPLY frame and return its business code (0 == success).

    The frame must be a single AUTH_REPLY control frame whose body is a JSON
    object with an integer ``code``. Malformed frames raise
    :class:`MalformedResponseError`; the returned non-zero code is interpreted
    by the caller as an :class:`AuthError`.
    """
    frames = decode(data)
    if len(frames) != 1:
        raise MalformedResponseError("auth reply must be a single frame")
    frame = frames[0]
    if not isinstance(frame, ControlFrame) or frame.operation is not Operation.AUTH_REPLY:
        raise MalformedResponseError("expected an auth-reply frame")
    try:
        text = frame.body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MalformedResponseError("auth reply body is not valid UTF-8") from exc
    try:
        value = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise MalformedResponseError("auth reply body is not valid JSON") from exc
    if not isinstance(value, dict):
        raise MalformedResponseError("auth reply body must be a JSON object")
    code = value.get("code")
    if isinstance(code, bool) or not isinstance(code, int):
        raise MalformedResponseError("auth reply code must be an integer")
    return code
