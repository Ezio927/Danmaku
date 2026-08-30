"""Deterministic four-kind mock message source."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Callable

from danmaku.core.model import Message

__all__ = ["MockSource"]

KINDS = ("danmaku", "gift", "guard", "superChat")

_TEMPLATES = {
    "danmaku": {
        "user": ("user:alice", "Alice"),
        "data": {"text": "Hello <OBS> & everyone"},
    },
    "gift": {
        "user": ("user:bob", "Bob"),
        "data": {"giftName": "Star", "quantity": 2, "totalAmountMilliCny": 1000},
    },
    "guard": {
        "user": ("user:dana", "Dana"),
        "data": {"tier": "captain", "months": 1},
    },
    "superChat": {
        "user": ("user:carol", "Carol"),
        "data": {"text": "Great stream", "amountMilliCny": 30000, "durationSeconds": 60},
    },
}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_received_at(value: datetime) -> str:
    value = _as_utc(value)
    return f"{value.strftime('%Y-%m-%dT%H:%M:%S')}.{value.microsecond // 1000:03d}Z"


class MockSource:
    """Async producer emitting the four approved kinds cyclically.

    Output is deterministic given ``cadence``, ``start_sequence``, and ``clock``.
    """

    def __init__(
        self,
        cadence: float = 1.0,
        start_sequence: int = 1,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if isinstance(cadence, bool) or cadence < 0:
            raise ValueError("cadence must be a non-negative number")
        if isinstance(start_sequence, bool) or not isinstance(start_sequence, int) or start_sequence < 1:
            raise ValueError("start_sequence must be a positive integer")
        self._cadence = cadence
        self._start_sequence = start_sequence
        self._clock = clock if clock is not None else (lambda: datetime.now(timezone.utc))
        self._base_time = _as_utc(self._clock())
        self._next = start_sequence

    def build_message(self, sequence: int) -> Message:
        if sequence < self._start_sequence:
            raise ValueError("sequence must be >= start_sequence")
        index = sequence - self._start_sequence
        kind = KINDS[index % len(KINDS)]
        template = _TEMPLATES[kind]
        user_id, user_name = template["user"]
        received_at = _format_received_at(
            self._base_time + timedelta(seconds=index * self._cadence)
        )
        return Message.from_dict(
            {
                "id": f"mock:{sequence:04d}",
                "sequence": sequence,
                "receivedAt": received_at,
                "source": "mock",
                "kind": kind,
                "user": {"id": user_id, "name": user_name},
                "data": dict(template["data"]),
            }
        )

    def __aiter__(self) -> "MockSource":
        return self

    async def __anext__(self) -> Message:
        sequence = self._next
        self._next += 1
        if sequence != self._start_sequence:
            await asyncio.sleep(self._cadence)
        return self.build_message(sequence)
