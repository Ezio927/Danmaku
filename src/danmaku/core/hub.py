"""Ordered distribution hub with independently bounded subscriptions.

Each subscriber owns a bounded FIFO queue. Under overload, the oldest queued
ordinary danmaku (``kind == "danmaku"``) is evicted to admit the next message;
paid interactions (gift, guard, superChat) are never evicted. A full queue with
no evictable danmaku refuses the offer and the hub fails that subscriber closed
through the existing slow-client close.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Callable

from .aggregation import GiftAggregator
from .model import Message
from .snapshot import SnapshotStore

__all__ = [
    "SNAPSHOT_RETENTION_MILLISECONDS",
    "DistributionHub",
    "Subscription",
    "SubscriptionClosed",
]

#: OBS reconnect snapshots retain only messages received within this fixed
#: window of the injected clock. The boundary is inclusive: a message exactly
#: this old is still included.
SNAPSHOT_RETENTION_MILLISECONDS = 300_000

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _received_at_millis(value: str) -> int:
    """Return the exact integer millisecond offset of an RFC 3339 timestamp."""
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    delta = parsed - _EPOCH
    return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000


def _default_clock() -> int:
    """Return the current UTC instant in integer milliseconds since the epoch."""
    return time.time_ns() // 1_000_000


class SubscriptionClosed(Exception):
    """Raised when a subscription is closed and drained.

    ``code`` carries the closure code so an adapter can map it to a transport
    close code (e.g. WebSocket close code 1013 for a slow subscriber).
    """

    def __init__(self, code: int | None = None, message: str = "") -> None:
        self.code = code
        super().__init__(message)


#: The only message kind eligible for overload eviction. Everything else is a
#: paid interaction that must never be silently dropped from a queue.
_EVICTABLE_KIND = "danmaku"

#: WebSocket close code for a slow subscriber whose full queue has no evictable
#: ordinary danmaku.
_SLOW_SUBSCRIBER_CLOSE = 1013


class Subscription:
    """A bounded per-subscriber FIFO queue with async receive and explicit close.

    The queue is bounded at ``capacity`` retained messages. When it is full, the
    oldest queued ordinary danmaku is evicted to admit the new message. Paid
    interactions (gift, guard, superChat) are never evicted; a full queue with no
    evictable danmaku refuses the offer so the hub can close the subscriber.
    """

    def __init__(self, capacity: int = 100) -> None:
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity < 1
        ):
            raise ValueError("capacity must be a positive integer")
        self._capacity = capacity
        self._queue: deque[Message] = deque()
        self._event = asyncio.Event()
        self._closed = False
        self._close_code: int | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def close_code(self) -> int | None:
        return self._close_code

    def _offer(self, message: Message) -> bool:
        """Admit ``message``, evicting the oldest queued danmaku if full.

        Returns ``True`` when the message was admitted (with or without an
        eviction) and ``False`` when the queue is full and contains no
        evictable ordinary danmaku, so the caller must close the subscriber.
        """
        if len(self._queue) < self._capacity:
            self._queue.append(message)
            self._event.set()
            return True
        for index, queued in enumerate(self._queue):
            if queued.kind == _EVICTABLE_KIND:
                del self._queue[index]
                self._queue.append(message)
                self._event.set()
                return True
        return False

    def close(self, code: int | None = 1000) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_code = code
        self._event.set()

    async def receive(self) -> Message:
        while True:
            self._event.clear()
            if self._queue:
                return self._queue.popleft()
            if self._closed:
                raise SubscriptionClosed(self._close_code)
            await self._event.wait()


class DistributionHub:
    """Validates, stores, aggregates, filters, and distributes messages in order.

    The canonical ``SnapshotStore`` always receives the original, un-aggregated
    message unchanged. The delivery path — subscriber increments and
    :meth:`filtered_snapshot` — instead exposes the result of running each
    message through the injected ``aggregator`` (default
    :class:`GiftAggregator`) and then the optional ``filter`` predicate, so the
    snapshot and live delivery always agree on the same aggregate result.

    ``filter`` is an optional predicate ``(Message) -> bool`` returning ``True``
    for messages that must be suppressed from delivery. ``aggregator`` must
    expose ``accept(Message) -> tuple[Message, ...]`` and
    ``finalize() -> tuple[Message, ...]``.

    ``clock`` is an optional zero-argument callable returning the current UTC
    instant in integer milliseconds since the epoch. It is the deterministic
    timestamp seam used by :meth:`filtered_snapshot` to trim the delivery
    snapshot to ``snapshot_retention_milliseconds``. The canonical
    :meth:`snapshot` and live subscriber delivery are never affected by the
    clock.

    ``delivered_capacity`` bounds the OBS delivery snapshot exposed by
    :meth:`filtered_snapshot`, independently of the canonical ``store`` bound
    and of the per-subscriber ``capacity``. The host timeline may therefore
    retain up to ``store.max_messages`` accepted messages (for example 1000)
    while the OBS reconnect snapshot and per-client backpressure stay capped at
    their own (for example 100) limits.
    """

    def __init__(
        self,
        store: SnapshotStore | None = None,
        capacity: int = 100,
        delivered_capacity: int = 100,
        filter: Callable[[Message], bool] | None = None,
        aggregator: GiftAggregator | None = None,
        clock: Callable[[], int] | None = None,
        snapshot_retention_milliseconds: int = SNAPSHOT_RETENTION_MILLISECONDS,
    ) -> None:
        self._store = store if store is not None else SnapshotStore()
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity < 1
        ):
            raise ValueError("capacity must be a positive integer")
        if (
            isinstance(delivered_capacity, bool)
            or not isinstance(delivered_capacity, int)
            or delivered_capacity < 1
        ):
            raise ValueError("delivered_capacity must be a positive integer")
        if filter is not None and not callable(filter):
            raise TypeError("filter must be callable or None")
        if aggregator is None:
            aggregator = GiftAggregator()
        elif not (
            callable(getattr(aggregator, "accept", None))
            and callable(getattr(aggregator, "finalize", None))
        ):
            raise TypeError("aggregator must expose accept() and finalize()")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable or None")
        if (
            isinstance(snapshot_retention_milliseconds, bool)
            or not isinstance(snapshot_retention_milliseconds, int)
            or snapshot_retention_milliseconds < 0
        ):
            raise ValueError(
                "snapshot_retention_milliseconds must be a non-negative integer"
            )
        self._capacity = capacity
        self._delivered_capacity = delivered_capacity
        self._filter = filter
        self._aggregator = aggregator
        self._clock = clock if clock is not None else _default_clock
        self._snapshot_retention_milliseconds = snapshot_retention_milliseconds
        self._delivered = SnapshotStore(max_messages=delivered_capacity)
        self._subscribers: list[Subscription] = []

    @property
    def aggregator(self) -> GiftAggregator:
        return self._aggregator

    @property
    def delivered_capacity(self) -> int:
        return self._delivered_capacity

    def subscribe(self) -> Subscription:
        subscription = Subscription(capacity=self._capacity)
        self._subscribers.append(subscription)
        return subscription

    def publish(self, message: Message) -> None:
        if not isinstance(message, Message):
            raise TypeError("publish requires a Message")
        self._store.append(message)
        for delivered in self._aggregator.accept(message):
            self._deliver(delivered)

    def finalize(self) -> None:
        """Emit any pending aggregated gifts through the delivery path.

        Idempotent: after a finalize, later calls emit nothing until another
        pending aggregate forms.
        """
        for delivered in self._aggregator.finalize():
            self._deliver(delivered)

    def _deliver(self, message: Message) -> None:
        if self._filter is not None and self._filter(message):
            return
        self._delivered.append(message)
        for subscription in list(self._subscribers):
            if subscription.closed:
                self._subscribers.remove(subscription)
                continue
            if not subscription._offer(message):
                subscription.close(code=_SLOW_SUBSCRIBER_CLOSE)

    def snapshot(self) -> tuple[Message, ...]:
        return self._store.list()

    def filtered_snapshot(self) -> tuple[Message, ...]:
        """Return the oldest-first delivery snapshot within the retention window.

        This is the canonical snapshot with ordinary gifts aggregated and
        suppressed messages excluded, then trimmed to messages whose
        ``receivedAt`` is within ``snapshot_retention_milliseconds`` of the
        injected clock. The lower boundary is inclusive and the result is capped
        at ``delivered_capacity`` entries by the delivered store. The trim is
        applied on read, so a reconnecting client receives the most recent five
        minutes without affecting live subscriber delivery or the complete
        canonical :meth:`snapshot`.
        """
        cutoff = self._clock() - self._snapshot_retention_milliseconds
        return tuple(
            message
            for message in self._delivered.list()
            if _received_at_millis(message.received_at) >= cutoff
        )
