"""Bounded oldest-first snapshot of canonical messages."""

from __future__ import annotations

from collections import deque

from .model import Message

__all__ = ["SnapshotStore"]


class SnapshotStore:
    """Retains the latest ``max_messages`` messages in insertion order.

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

    def append(self, message: Message) -> None:
        if not isinstance(message, Message):
            raise TypeError("append requires a Message")
        self._messages.append(message)
        while len(self._messages) > self._max_messages:
            self._messages.popleft()

    def list(self) -> tuple[Message, ...]:
        return tuple(self._messages)

    def __len__(self) -> int:
        return len(self._messages)
