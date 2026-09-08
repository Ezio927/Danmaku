"""Ordered distribution hub with independent bounded subscriptions."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from .aggregation import GiftAggregator
from .model import Message
from .snapshot import SnapshotStore

__all__ = ["DistributionHub", "Subscription", "SubscriptionClosed"]


class SubscriptionClosed(Exception):
    """Raised when a subscription is closed and drained.

    ``code`` carries the closure code so an adapter can map it to a transport
    close code (e.g. WebSocket close code 1013 for a slow subscriber).
    """

    def __init__(self, code: int | None = None, message: str = "") -> None:
        self.code = code
        super().__init__(message)


_SENTINEL = object()


class Subscription:
    """A bounded per-subscriber queue with async receive and explicit close."""

    def __init__(self, capacity: int = 100) -> None:
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity < 1
        ):
            raise ValueError("capacity must be a positive integer")
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=capacity)
        self._closed = False
        self._close_code: int | None = None

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def close_code(self) -> int | None:
        return self._close_code

    def _offer(self, message: Message) -> None:
        self._queue.put_nowait(message)

    def close(self, code: int | None = 1000) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_code = code
        try:
            self._queue.put_nowait(_SENTINEL)
        except asyncio.QueueFull:
            pass

    async def receive(self) -> Message:
        if self._closed and self._queue.empty():
            raise SubscriptionClosed(self._close_code)
        item = await self._queue.get()
        if item is _SENTINEL:
            raise SubscriptionClosed(self._close_code)
        return item


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
    """

    def __init__(
        self,
        store: SnapshotStore | None = None,
        capacity: int = 100,
        filter: Callable[[Message], bool] | None = None,
        aggregator: GiftAggregator | None = None,
    ) -> None:
        self._store = store if store is not None else SnapshotStore()
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity < 1
        ):
            raise ValueError("capacity must be a positive integer")
        if filter is not None and not callable(filter):
            raise TypeError("filter must be callable or None")
        if aggregator is None:
            aggregator = GiftAggregator()
        elif not (
            callable(getattr(aggregator, "accept", None))
            and callable(getattr(aggregator, "finalize", None))
        ):
            raise TypeError("aggregator must expose accept() and finalize()")
        self._capacity = capacity
        self._filter = filter
        self._aggregator = aggregator
        self._delivered = SnapshotStore(max_messages=self._store.max_messages)
        self._subscribers: list[Subscription] = []

    @property
    def aggregator(self) -> GiftAggregator:
        return self._aggregator

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
            try:
                subscription._offer(message)
            except asyncio.QueueFull:
                subscription.close(code=1013)

    def snapshot(self) -> tuple[Message, ...]:
        return self._store.list()

    def filtered_snapshot(self) -> tuple[Message, ...]:
        """Return the oldest-first delivery snapshot.

        This is the canonical snapshot with ordinary gifts aggregated and
        suppressed messages excluded. It matches exactly what a subscriber has
        been offered, so a client's snapshot and its increments never disagree.
        """
        return self._delivered.list()
