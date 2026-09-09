"""Deterministic ordinary-gift aggregation with platform combo fidelity.

This module is a pure core abstraction: it consumes canonical
:class:`~danmaku.core.model.Message` values and emits canonical
:class:`~danmaku.core.model.Message` values, with no knowledge of the OBS page,
the WebSocket protocol, the delivery filtering policy, or any transport
concern. Behaviour is documented in ``docs/gift-aggregation.md``:

* Only ``kind == "gift"`` messages are aggregated. Danmaku, guard, and
  super-chat messages pass through unchanged and are never merged with gifts
  or with one another.
* When a gift carries reliable platform combo metadata
  (:attr:`Message.platform_meta`), it is grouped by the stable platform combo
  identity (``combo_id``) and its cumulative ``quantity`` and
  ``totalAmountMilliCny`` are applied as the exact delta between consecutive
  events of the same combo, so the cumulative totals are never double-counted.
* When platform metadata is missing, unusable (non-monotonic cumulative
  values), or invalid, the aggregator falls back to grouping by the exact
  stable ``user.id`` and ``data.giftName`` and summing the exact per-event
  ``quantity`` and ``totalAmountMilliCny``, within the same bounded sliding
  window.
* A gift merges into the current pending aggregate when it has the same key and
  its ``receivedAt`` is within ``window_milliseconds`` (default 5000) of the
  aggregate's current anchor — the most recently merged gift's ``receivedAt``.
  The boundary is inclusive: an exact five-second gap still merges. Merging
  extends the anchor to the new gift's ``receivedAt``.
* Any other input — a gift with a different key, a matching gift beyond the
  window, or a non-gift message — first finalizes (emits) the pending
  aggregate, then is processed. This keeps the delivered stream strictly
  increasing in ``sequence`` while non-gift messages retain their original
  identity and order.
* ``finalize()`` emits any remaining pending aggregate deterministically, so no
  accepted gift disappears on normal source shutdown or explicit finalization.

The emitted aggregate keeps the first gift's identity (``id``, ``sequence``,
``receivedAt``, ``source``, and ``user``) and replaces its ``data`` with the
exact combined ``giftName``, ``quantity``, and ``totalAmountMilliCny``. The
aggregate carries no platform metadata: it is a plain canonical v1 gift message.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .model import MAX_SEQUENCE, Message
from .platform import GIFT_QUANTITY_MAX, GiftPlatformMeta

__all__ = [
    "DEFAULT_WINDOW_MILLISECONDS",
    "GIFT_QUANTITY_MAX",
    "GiftAggregator",
]

DEFAULT_WINDOW_MILLISECONDS = 5000

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _received_at_millis(value: str) -> int:
    """Return the exact integer millisecond offset of an RFC 3339 timestamp."""
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    delta = parsed - _EPOCH
    return delta.days * 86_400_000 + delta.seconds * 1000 + delta.microseconds // 1000


def _would_overflow(pending: "_PendingGift", message: Message) -> bool:
    """Return ``True`` if summing ``message`` would exceed a model bound."""
    return (
        pending.quantity + message.data["quantity"] > GIFT_QUANTITY_MAX
        or pending.total_amount_milli_cny + message.data["totalAmountMilliCny"]
        > MAX_SEQUENCE
    )


def _combo_deltas(
    pending: "_PendingGift", meta: GiftPlatformMeta
) -> tuple[int, int] | None:
    """Return the ``(quantity, amount)`` deltas for merging ``meta`` into a combo.

    The platform reports running cumulative totals, so the delta between the
    pending combo's last cumulative values and ``meta``'s cumulative values is
    the new contribution. Non-monotonic cumulative values (a cumulative that
    went backwards) are unusable and return ``None``.
    """
    delta_quantity = meta.cumulative_quantity - pending.last_cum_quantity
    delta_amount = (
        meta.cumulative_amount_milli_cny - pending.last_cum_amount_milli_cny
    )
    if delta_quantity < 0 or delta_amount < 0:
        return None
    return delta_quantity, delta_amount


@dataclass
class _PendingGift:
    """A not-yet-emitted aggregate: one group of matching gifts.

    A fallback group keys by ``("user", user_id, gift_name)`` and sums the
    per-event ``quantity``/``totalAmountMilliCny``. A combo group keys by
    ``("combo", combo_id)``, applies cumulative deltas, and tracks the last
    cumulative values in ``last_cum_quantity``/``last_cum_amount_milli_cny`` so
    the next event can derive its delta without re-summing the running totals.
    """

    key: tuple[str, ...]
    first: Message
    anchor_millis: int
    quantity: int
    total_amount_milli_cny: int
    last_cum_quantity: int | None = None
    last_cum_amount_milli_cny: int | None = None

    def to_message(self) -> Message:
        return Message.from_dict(
            {
                "id": self.first.id,
                "sequence": self.first.sequence,
                "receivedAt": self.first.received_at,
                "source": self.first.source,
                "kind": "gift",
                "user": self.first.user.to_dict(),
                "data": {
                    "giftName": self.first.data["giftName"],
                    "quantity": self.quantity,
                    "totalAmountMilliCny": self.total_amount_milli_cny,
                },
            }
        )


class GiftAggregator:
    """Group ordinary paid gifts by platform combo identity or user + gift name.

    Gifts carrying reliable platform combo metadata are grouped by ``combo_id``
    with cumulative delta updates; everything else falls back to the bounded
    five-second sliding-window grouping by ``user.id`` + ``data.giftName`` with
    summed values. The aggregator is synchronous and deterministic: it holds at
    most one pending aggregate and emits that aggregate only when a boundary
    arrives (a non-gift message, a gift with a different key, a matching gift
    beyond the window, or an unusable cumulative) or when :meth:`finalize` is
    called explicitly.
    """

    def __init__(self, window_milliseconds: int = DEFAULT_WINDOW_MILLISECONDS) -> None:
        if isinstance(window_milliseconds, bool) or not isinstance(
            window_milliseconds, int
        ):
            raise TypeError("window_milliseconds must be an integer")
        if window_milliseconds < 0:
            raise ValueError("window_milliseconds must be >= 0")
        self._window_milliseconds = window_milliseconds
        self._pending: _PendingGift | None = None

    @property
    def window_milliseconds(self) -> int:
        return self._window_milliseconds

    @property
    def has_pending(self) -> bool:
        """Return ``True`` when an aggregate is still waiting to be emitted."""
        return self._pending is not None

    def accept(self, message: Message) -> tuple[Message, ...]:
        """Accept one message and return the delivery messages to emit.

        A non-gift message first flushes any pending aggregate and then returns
        itself unchanged. A matching gift within the window is absorbed into the
        pending aggregate (returning nothing). Any other gift first flushes the
        pending aggregate and then opens a new group.
        """
        if not isinstance(message, Message):
            raise TypeError("accept requires a Message")

        if message.kind != "gift":
            emitted = self._flush()
            return emitted + (message,)

        arrived = _received_at_millis(message.received_at)
        pending = self._pending
        meta = message.platform_meta

        if meta is not None:
            key = ("combo", meta.combo_id)
            if (
                pending is not None
                and pending.key == key
                and arrived - pending.anchor_millis <= self._window_milliseconds
            ):
                deltas = _combo_deltas(pending, meta)
                if deltas is not None:
                    delta_quantity, delta_amount = deltas
                    pending.quantity += delta_quantity
                    pending.total_amount_milli_cny += delta_amount
                    pending.last_cum_quantity = meta.cumulative_quantity
                    pending.last_cum_amount_milli_cny = (
                        meta.cumulative_amount_milli_cny
                    )
                    pending.anchor_millis = arrived
                    return ()
                # Non-monotonic cumulative values are unusable: fall back to the
                # plain sum grouping for this event.
                emitted = self._flush()
                self._pending = self._open_fallback(message, arrived)
                return emitted
            emitted = self._flush()
            self._pending = self._open_combo(message, arrived, meta)
            return emitted

        key = ("user", message.user.id, message.data["giftName"])
        if (
            pending is not None
            and pending.key == key
            and arrived - pending.anchor_millis <= self._window_milliseconds
            and not _would_overflow(pending, message)
        ):
            pending.quantity += message.data["quantity"]
            pending.total_amount_milli_cny += message.data["totalAmountMilliCny"]
            pending.anchor_millis = arrived
            return ()

        emitted = self._flush()
        self._pending = self._open_fallback(message, arrived)
        return emitted

    def finalize(self) -> tuple[Message, ...]:
        """Emit the pending aggregate, if any, and clear it. Idempotent."""
        return self._flush()

    def _flush(self) -> tuple[Message, ...]:
        pending = self._pending
        if pending is None:
            return ()
        self._pending = None
        return (pending.to_message(),)

    @staticmethod
    def _open_combo(
        message: Message, arrived: int, meta: GiftPlatformMeta
    ) -> "_PendingGift":
        return _PendingGift(
            key=("combo", meta.combo_id),
            first=message,
            anchor_millis=arrived,
            quantity=meta.cumulative_quantity,
            total_amount_milli_cny=meta.cumulative_amount_milli_cny,
            last_cum_quantity=meta.cumulative_quantity,
            last_cum_amount_milli_cny=meta.cumulative_amount_milli_cny,
        )

    @staticmethod
    def _open_fallback(message: Message, arrived: int) -> "_PendingGift":
        return _PendingGift(
            key=("user", message.user.id, message.data["giftName"]),
            first=message,
            anchor_millis=arrived,
            quantity=message.data["quantity"],
            total_amount_milli_cny=message.data["totalAmountMilliCny"],
        )
