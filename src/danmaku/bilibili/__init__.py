"""Offline Bilibili boundaries: recorded-event normalization and lifecycle.

This package adapts a documented, recorded Bilibili input envelope into the
canonical Danmaku message model and owns the credential-free, transport-
independent connection lifecycle state machine. It is deterministic and
offline-only: it validates a frozen fixture contract, maps supported danmaku,
gift, guard, and super-chat events into ``source="bilibili"`` messages, and
decides connection state, failure classification, and bounded reconnect
scheduling. There is no network access, credential handling, or live transport
here.
"""

from .adapter import BilibiliAdapter, BilibiliAdapterError, DuplicateEventError
from .lifecycle import (
    BackoffPolicy,
    ConnectionLifecycle,
    ConnectionLifecycleError,
    ConnectionState,
    FailureClass,
    IllegalTransitionError,
    InvalidIdentityCodeError,
)
from .wire import (
    BilibiliWireCodec,
    ControlFrame,
    FrameHeader,
    HEADER_LENGTH,
    MAX_DECOMPRESSED_LENGTH,
    MAX_NESTING_DEPTH,
    MAX_PACKET_LENGTH,
    MalformedFrameError,
    MessageEnvelope,
    Operation,
    OversizedPacketError,
    PayloadDecodeError,
    ProtocolVersion,
    TruncatedPacketError,
    UnsupportedOperationError,
    UnsupportedProtocolVersionError,
    WireError,
    decode,
    encode_frame,
    encode_heartbeat,
    encode_heartbeat_reply,
    encode_message_plain,
    encode_message_zlib,
    parse_header,
)

__all__ = [
    "BackoffPolicy",
    "BilibiliAdapter",
    "BilibiliAdapterError",
    "BilibiliWireCodec",
    "ConnectionLifecycle",
    "ConnectionLifecycleError",
    "ConnectionState",
    "ControlFrame",
    "DuplicateEventError",
    "FailureClass",
    "FrameHeader",
    "HEADER_LENGTH",
    "IllegalTransitionError",
    "InvalidIdentityCodeError",
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
