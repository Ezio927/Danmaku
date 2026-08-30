"""Ordered distribution hub with independent bounded subscriptions."""

from __future__ import annotations

import asyncio
from typing import Any

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
    """Validates, stores, and offers each message to subscribers in order."""

    def __init__(
        self, store: SnapshotStore | None = None, capacity: int = 100
    ) -> None:
        self._store = store if store is not None else SnapshotStore()
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or capacity < 1
        ):
            raise ValueError("capacity must be a positive integer")
        self._capacity = capacity
        self._subscribers: list[Subscription] = []

    def subscribe(self) -> Subscription:
        subscription = Subscription(capacity=self._capacity)
        self._subscribers.append(subscription)
        return subscription

    def publish(self, message: Message) -> None:
        if not isinstance(message, Message):
            raise TypeError("publish requires a Message")
        self._store.append(message)
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
