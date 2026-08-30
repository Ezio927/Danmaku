"""Frozen protocol v1 frame serialization and client-frame classification."""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from danmaku.core.model import Message

__all__ = [
    "ERROR_MESSAGES",
    "MAX_TEXT_FRAME_BYTES",
    "PROTOCOL_VERSION",
    "classify_client_frame",
    "error_frame",
    "message_created_frame",
    "snapshot_frame",
]

PROTOCOL_VERSION = 1
MAX_TEXT_FRAME_BYTES = 65_536

ERROR_MESSAGES: dict[str, str] = {
    "INVALID_JSON": "Client frame is not valid JSON.",
    "INVALID_FRAME": "Client frame does not match the protocol.",
    "UNSUPPORTED_VERSION": "Only protocolVersion 1 is supported.",
    "UNSUPPORTED_TYPE": "Only the hello client frame is supported.",
}

_CLIENT_KEYS = frozenset({"protocolVersion", "type", "payload"})


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite numbers are not allowed: {value}")


def _dump(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def snapshot_frame(messages: Iterable[Message]) -> str:
    return _dump(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "type": "snapshot",
            "payload": {"messages": [message.to_dict() for message in messages]},
        }
    )


def message_created_frame(message: Message) -> str:
    return _dump(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "type": "message.created",
            "payload": {"message": message.to_dict()},
        }
    )


def error_frame(code: str) -> str:
    return _dump(
        {
            "protocolVersion": PROTOCOL_VERSION,
            "type": "error",
            "payload": {"code": code, "message": ERROR_MESSAGES[code]},
        }
    )


def classify_client_frame(text: str) -> str | None:
    """Return ``None`` for the exact v1 hello, else a fixed error code."""
    if len(text.encode("utf-8")) > MAX_TEXT_FRAME_BYTES:
        return "INVALID_FRAME"
    try:
        value = json.loads(text, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError):
        return "INVALID_JSON"
    if not isinstance(value, dict):
        return "INVALID_FRAME"
    if set(value) != _CLIENT_KEYS:
        return "INVALID_FRAME"
    version = value["protocolVersion"]
    if isinstance(version, bool) or not isinstance(version, int):
        return "INVALID_FRAME"
    if version != PROTOCOL_VERSION:
        return "UNSUPPORTED_VERSION"
    frame_type = value["type"]
    if not isinstance(frame_type, str):
        return "INVALID_FRAME"
    if frame_type != "hello":
        return "UNSUPPORTED_TYPE"
    payload = value["payload"]
    if not isinstance(payload, dict) or payload != {}:
        return "INVALID_FRAME"
    return None
