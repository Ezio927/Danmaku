"""Bounded oldest-first snapshot of canonical messages."""

from __future__ import annotations

from collections import deque

from .model import Message

__all__ = ["DuplicateIdError", "NonIncreasingSequenceError", "SnapshotStore"]


class DuplicateIdError(ValueError):
    """Raised when a message id is already present in the snapshot."""


class NonIncreasingSequenceError(ValueError):
    """Raised when a message sequence does not strictly increase."""


class SnapshotStore:
    """Retains the latest ``max_messages`` messages in insertion order.

    Appends are ordered and unique: a message whose id is already present, or
    whose sequence does not strictly exceed the latest stored sequence, is
    rejected before it enters the snapshot.

    The payload snapshot is bounded at ``max_messages`` messages, but every
    canonical id ever accepted is retained in a process-lifetime ``seen_ids``
    set so that a duplicate id is rejected even after its payload has been
    evicted.

    ``list()`` returns an immutable oldest-first copy.
    """

    def __init__(self, max_messages: int = 100) -> None:
        if (
            isinstance(max_messages, bool)
            or not isinstance(max_messages, int)
            or max_messages < 1
        ):
            raise ValueError("max_messages must be a positive integer")
        self._max_messages = max_messages
        self._messages: deque[Message] = deque()
        self._seen_ids: set[str] = set()

    def append(self, message: Message) -> None:
        if not isinstance(message, Message):
            raise TypeError("append requires a Message")
        if self._messages and message.sequence <= self._messages[-1].sequence:
            raise NonIncreasingSequenceError(
                f"sequence must strictly increase: "
                f"{message.sequence} <= {self._messages[-1].sequence}"
            )
        if message.id in self._seen_ids:
            raise DuplicateIdError(f"duplicate message id: {message.id!r}")
        self._messages.append(message)
        self._seen_ids.add(message.id)
        while len(self._messages) > self._max_messages:
            self._messages.popleft()

    def list(self) -> tuple[Message, ...]:
        return tuple(self._messages)

    def __len__(self) -> int:
        return len(self._messages)
