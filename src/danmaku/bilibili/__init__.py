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

__all__ = [
    "BackoffPolicy",
    "BilibiliAdapter",
    "BilibiliAdapterError",
    "ConnectionLifecycle",
    "ConnectionLifecycleError",
    "ConnectionState",
    "DuplicateEventError",
    "FailureClass",
    "IllegalTransitionError",
    "InvalidIdentityCodeError",
]
