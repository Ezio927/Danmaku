"""Offline, credential-free Bilibili WebSocket wire codec.

This module decodes the documented Bilibili live-platform WebSocket binary
framing (a 16-byte big-endian header followed by a body) into two explicit
result classes:

* :class:`ControlFrame` for the connection/keep-alive operations (heartbeat,
  heartbeat-reply, auth, and auth-reply). These are classified explicitly and
  never manufacture product messages.
* :class:`MessageEnvelope` for operation ``5`` (message). Each extracted
  envelope is validated UTF-8 JSON text that is handed, unmodified, to the
  existing :class:`~danmaku.bilibili.adapter.BilibiliAdapter` boundary.

The codec is deterministic and offline-only: it reads recorded bytes, performs
no network access, no credential handling, and no live transport. Truncated,
malformed, unsupported, and oversized packets are rejected explicitly rather
than silently coerced. Framing, decompression bounds, and rejection rules are
documented in ``docs/bilibili-wire-contract.md``.
"""

from __future__ import annotations

import json
import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from danmaku.bilibili.adapter import BilibiliAdapter
from danmaku.core.model import Message

__all__ = [
    "BilibiliWireCodec",
    "ControlFrame",
    "FrameHeader",
    "HEADER_LENGTH",
    "MAX_DECOMPRESSED_LENGTH",
    "MAX_NESTING_DEPTH",
    "MAX_PACKET_LENGTH",
    "MalformedFrameError",
    "MessageEnvelope",
    "Operation",
    "OversizedPacketError",
    "PayloadDecodeError",
    "ProtocolVersion",
    "TruncatedPacketError",
    "UnsupportedOperationError",
    "UnsupportedProtocolVersionError",
    "WireError",
    "decode",
    "encode_frame",
    "encode_heartbeat",
    "encode_heartbeat_reply",
    "encode_message_plain",
    "encode_message_zlib",
    "parse_header",
]

# The Bilibili packet header is always 16 bytes.
HEADER_LENGTH = 16

# A single recorded frame (header + body) must not exceed 1 MiB.
MAX_PACKET_LENGTH = 1_048_576

# A decompressed message body must not exceed 4 MiB.
MAX_DECOMPRESSED_LENGTH = 4_194_304

# Nested zlib message bodies are expanded at most this many levels.
MAX_NESTING_DEPTH = 4

_KNOWN_VERSIONS = frozenset({0, 1, 2, 3})


class Operation(IntEnum):
    """The four Bilibili WebSocket operations this codec classifies.

    ``MESSAGE`` carries the event envelopes; the other three are control
    operations that never become product messages.
    """

    HEARTBEAT = 2
    HEARTBEAT_REPLY = 3
    MESSAGE = 5
    AUTH = 7
    AUTH_REPLY = 8


class ProtocolVersion(IntEnum):
    """The Bilibili body encoding versions.

    ``PLAIN`` bodies are uncompressed UTF-8 JSON, ``HEARTBEAT`` is the opaque
    keep-alive body, ``ZLIB`` is zlib-compressed message payload, and
    ``BROTLI`` is not supported by this offline codec.
    """

    PLAIN = 0
    HEARTBEAT = 1
    ZLIB = 2
    BROTLI = 3


class WireError(ValueError):
    """Base class for offline Bilibili wire codec errors."""


class TruncatedPacketError(WireError):
    """Raised when a recorded frame is shorter than its declared size."""


class OversizedPacketError(WireError):
    """Raised when a recorded frame or decompressed body exceeds its bound."""


class MalformedFrameError(WireError):
    """Raised when header or payload framing violates the wire grammar."""


class UnsupportedProtocolVersionError(WireError):
    """Raised when a body uses an unsupported encoding version."""


class UnsupportedOperationError(WireError):
    """Raised when a frame declares an unknown operation."""


class PayloadDecodeError(WireError):
    """Raised when a zlib message body cannot be decompressed."""


def _reject_constant(value: str) -> None:
    raise MalformedFrameError(f"non-finite numbers are not allowed: {value}")


def _require_bytes(data: Any) -> bytes:
    if isinstance(data, memoryview):
        data = data.tobytes()
    elif isinstance(data, bytearray):
        data = bytes(data)
    if not isinstance(data, bytes):
        raise TypeError("wire data must be bytes")
    return data


@dataclass(frozen=True, slots=True)
class FrameHeader:
    """The validated 16-byte Bilibili packet header fields."""

    packet_length: int
    header_length: int
    protocol_version: int
    operation: int
    sequence: int


@dataclass(frozen=True, slots=True)
class ControlFrame:
    """A classified control packet that never becomes a product message."""

    operation: Operation
    protocol_version: int
    sequence: int
    body: bytes


@dataclass(frozen=True, slots=True)
class MessageEnvelope:
    """A single extracted event envelope destined for the adapter boundary."""

    sequence: int
    text: str


def _read_header(data: bytes, start: int) -> tuple[int, int, int, int, int]:
    """Read and validate the header at ``start`` and return its five fields."""
    end = start + HEADER_LENGTH
    if end > len(data):
        raise TruncatedPacketError("packet header is truncated")
    packet_length = struct.unpack(">I", data[start : start + 4])[0]
    header_length = struct.unpack(">H", data[start + 4 : start + 6])[0]
    protocol_version = struct.unpack(">H", data[start + 6 : start + 8])[0]
    operation = struct.unpack(">I", data[start + 8 : start + 12])[0]
    sequence = struct.unpack(">I", data[start + 12 : start + 16])[0]
    if header_length != HEADER_LENGTH:
        raise MalformedFrameError(
            f"header length must be {HEADER_LENGTH}, got {header_length}"
        )
    if packet_length < HEADER_LENGTH:
        raise MalformedFrameError(
            f"packet length must be at least {HEADER_LENGTH}, got {packet_length}"
        )
    if packet_length > MAX_PACKET_LENGTH:
        raise OversizedPacketError(
            f"packet length {packet_length} exceeds {MAX_PACKET_LENGTH}"
        )
    if protocol_version not in _KNOWN_VERSIONS:
        raise UnsupportedProtocolVersionError(
            f"protocol version {protocol_version} is not supported"
        )
    return packet_length, header_length, protocol_version, operation, sequence


def _classify_operation(operation: int) -> Operation:
    try:
        return Operation(operation)
    except ValueError:
        raise UnsupportedOperationError(
            f"operation {operation} is not supported"
        ) from None


def parse_header(data: Any) -> FrameHeader:
    """Validate the 16-byte framing of ``data`` and return its header fields.

    Raises :class:`WireError` subclasses for truncated, malformed, oversized,
    unsupported-version, or unknown-operation headers.
    """
    data = _require_bytes(data)
    packet_length, header_length, protocol_version, operation, sequence = (
        _read_header(data, 0)
    )
    _classify_operation(operation)
    return FrameHeader(
        packet_length=packet_length,
        header_length=header_length,
        protocol_version=protocol_version,
        operation=operation,
        sequence=sequence,
    )


def _decode_envelope_text(body: bytes) -> str:
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MalformedFrameError("message body is not valid UTF-8") from exc
    try:
        value = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise MalformedFrameError("message body is not valid JSON") from exc
    if not isinstance(value, dict):
        raise MalformedFrameError("message body must be a JSON object")
    return text


def _zlib_decompress(body: bytes) -> bytes:
    decompressor = zlib.decompressobj()
    try:
        out = decompressor.decompress(body, MAX_DECOMPRESSED_LENGTH + 1)
    except zlib.error as exc:
        raise PayloadDecodeError("message body is not a valid zlib stream") from exc
    if len(out) > MAX_DECOMPRESSED_LENGTH:
        raise OversizedPacketError(
            f"decompressed body exceeds {MAX_DECOMPRESSED_LENGTH} bytes"
        )
    if not decompressor.eof:
        raise PayloadDecodeError("message body is an incomplete zlib stream")
    return out


def _dispatch(
    operation: Operation,
    protocol_version: int,
    sequence: int,
    body: bytes,
    results: list[ControlFrame | MessageEnvelope],
    depth: int,
) -> None:
    if operation is Operation.MESSAGE:
        _decode_message_body(protocol_version, sequence, body, results, depth)
    else:
        results.append(
            ControlFrame(
                operation=operation,
                protocol_version=protocol_version,
                sequence=sequence,
                body=body,
            )
        )


def _decode_message_body(
    protocol_version: int,
    sequence: int,
    body: bytes,
    results: list[ControlFrame | MessageEnvelope],
    depth: int,
) -> None:
    if protocol_version == ProtocolVersion.PLAIN:
        results.append(
            MessageEnvelope(sequence=sequence, text=_decode_envelope_text(body))
        )
        return
    if protocol_version == ProtocolVersion.ZLIB:
        _decode_zlib_body(body, results, depth)
        return
    if protocol_version == ProtocolVersion.BROTLI:
        raise UnsupportedProtocolVersionError(
            "brotli-compressed messages are not supported"
        )
    raise MalformedFrameError(
        "message packets cannot use protocol version 1 (heartbeat)"
    )


def _decode_zlib_body(
    body: bytes, results: list[ControlFrame | MessageEnvelope], depth: int
) -> None:
    if depth >= MAX_NESTING_DEPTH:
        raise MalformedFrameError(
            f"zlib message nesting exceeds {MAX_NESTING_DEPTH} levels"
        )
    decompressed = _zlib_decompress(body)
    if not decompressed:
        raise MalformedFrameError("message payload is empty")
    offset = 0
    while offset < len(decompressed):
        packet_length, _header_length, protocol_version, operation, sequence = (
            _read_header(decompressed, offset)
        )
        if offset + packet_length > len(decompressed):
            raise TruncatedPacketError("inner packet is truncated")
        classified = _classify_operation(operation)
        inner_body = decompressed[offset + HEADER_LENGTH : offset + packet_length]
        _dispatch(classified, protocol_version, sequence, inner_body, results, depth + 1)
        offset += packet_length


def decode(data: Any) -> list[ControlFrame | MessageEnvelope]:
    """Decode one recorded Bilibili WebSocket frame.

    Returns the classified control frames and extracted message envelopes in
    order. Control frames (heartbeat, heartbeat-reply, auth, auth-reply)
    produce no product messages; only operation ``5`` bodies become
    :class:`MessageEnvelope` instances.
    """
    data = _require_bytes(data)
    if len(data) > MAX_PACKET_LENGTH:
        raise OversizedPacketError(
            f"frame length {len(data)} exceeds {MAX_PACKET_LENGTH}"
        )
    packet_length, _header_length, protocol_version, operation, sequence = (
        _read_header(data, 0)
    )
    classified = _classify_operation(operation)
    if packet_length != len(data):
        if packet_length > len(data):
            raise TruncatedPacketError(
                f"frame declares {packet_length} bytes but only {len(data)} present"
            )
        raise MalformedFrameError("frame contains trailing bytes")
    body = data[HEADER_LENGTH:packet_length]
    results: list[ControlFrame | MessageEnvelope] = []
    _dispatch(classified, protocol_version, sequence, body, results, depth=0)
    return results


class BilibiliWireCodec:
    """Decode recorded wire frames and normalize event envelopes to messages.

    The codec composes the existing :class:`BilibiliAdapter` boundary: every
    extracted :class:`MessageEnvelope` is normalized through the adapter into a
    canonical ``source="bilibili"`` message, preserving event identity and
    payload semantics. Control packets are classified but produce no messages.
    """

    def __init__(self, *, adapter: BilibiliAdapter | None = None) -> None:
        self._adapter = adapter if adapter is not None else BilibiliAdapter()

    @property
    def adapter(self) -> BilibiliAdapter:
        """The composed recorded-event adapter."""
        return self._adapter

    def decode_messages(self, data: Any) -> list[Message]:
        """Decode ``data`` and normalize every extracted event envelope.

        Raises the codec's :class:`WireError` subclasses for invalid frames and
        the adapter's :class:`~danmaku.bilibili.adapter.BilibiliAdapterError`
        for invalid envelopes.
        """
        messages: list[Message] = []
        for frame in decode(data):
            if isinstance(frame, MessageEnvelope):
                messages.append(self._adapter.normalize_json(frame.text))
        return messages


def encode_frame(
    *,
    protocol_version: int,
    operation: int,
    sequence: int = 0,
    body: bytes = b"",
) -> bytes:
    """Build a single Bilibili frame (used for fixtures and round-trip tests)."""
    packet_length = HEADER_LENGTH + len(body)
    if packet_length > MAX_PACKET_LENGTH:
        raise OversizedPacketError(
            f"packet length {packet_length} exceeds {MAX_PACKET_LENGTH}"
        )
    header = struct.pack(
        ">IHHII", packet_length, HEADER_LENGTH, protocol_version, operation, sequence
    )
    return header + body


def encode_heartbeat(*, sequence: int = 1) -> bytes:
    """Build a heartbeat frame (operation 2, empty heartbeat body)."""
    return encode_frame(
        protocol_version=ProtocolVersion.HEARTBEAT,
        operation=Operation.HEARTBEAT,
        sequence=sequence,
    )


def encode_heartbeat_reply(*, sequence: int = 1, popularity: int = 0) -> bytes:
    """Build a heartbeat-reply frame (operation 3, 4-byte popularity body)."""
    return encode_frame(
        protocol_version=ProtocolVersion.HEARTBEAT,
        operation=Operation.HEARTBEAT_REPLY,
        sequence=sequence,
        body=struct.pack(">I", popularity),
    )


def _encode_inner_packet(text: str, *, sequence: int) -> bytes:
    return encode_frame(
        protocol_version=ProtocolVersion.PLAIN,
        operation=Operation.MESSAGE,
        sequence=sequence,
        body=text.encode("utf-8"),
    )


def encode_message_plain(text: str, *, sequence: int = 1) -> bytes:
    """Build an uncompressed message frame (operation 5, plain JSON body)."""
    return encode_frame(
        protocol_version=ProtocolVersion.PLAIN,
        operation=Operation.MESSAGE,
        sequence=sequence,
        body=text.encode("utf-8"),
    )


def encode_message_zlib(
    envelopes: str | list[str], *, sequence: int = 1
) -> bytes:
    """Build a zlib-compressed message frame holding one or more envelopes."""
    if isinstance(envelopes, str):
        envelopes = [envelopes]
    payload = b"".join(
        _encode_inner_packet(text, sequence=sequence) for text in envelopes
    )
    return encode_frame(
        protocol_version=ProtocolVersion.ZLIB,
        operation=Operation.MESSAGE,
        sequence=sequence,
        body=zlib.compress(payload),
    )
