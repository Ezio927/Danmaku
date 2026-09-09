"""Deterministic offline recorded-Bilibili event adapter.

``BilibiliAdapter`` validates a documented, recorded Bilibili input envelope and
normalizes the supported danmaku, gift, guard, and super-chat events into the
canonical :class:`~danmaku.core.model.Message` model with ``source="bilibili"``.

The adapter is offline-only: it reads frozen, recorded input and performs no
network access, authentication, persistence, or live transport. Identity and
sequence assignment are deterministic and documented in
``docs/bilibili-adapter-contract.md``:

* the canonical message id is ``"bilibili:" + eventId``;
* the process-local sequence is strictly increasing, seeded by
  ``start_sequence`` and observable through :attr:`BilibiliAdapter.next_sequence`;
* a repeated ``eventId`` is rejected explicitly with :class:`DuplicateEventError`.

A recorded ``SEND_GIFT`` envelope may optionally carry the platform combo
identity and cumulative gift fields ``comboId``, ``totalNum``, and ``totalCoin``.
When the complete, well-typed set is present and internally consistent, it is
normalized into an internal :class:`~danmaku.core.platform.GiftPlatformMeta`
carried on the canonical message (``message.platform_meta``) but never
serialized into the v1 model. Missing or unusable metadata yields ``None``, so
the aggregator falls back to its bounded five-second sliding-window behaviour.

All validation is strict: unknown commands and enum values, missing or unknown
keys, wrong types, invalid values, malformed timestamps, and non-integer or
non-finite money are rejected without silent coercion.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from danmaku.core.model import ID_PATTERN, MAX_SEQUENCE, Message
from danmaku.core.platform import GIFT_QUANTITY_MAX, GiftPlatformMeta

__all__ = [
    "BilibiliAdapter",
    "BilibiliAdapterError",
    "DuplicateEventError",
]

MESSAGE_ID_PREFIX = "bilibili:"
MAX_EVENT_ID_LENGTH = 64 - len(MESSAGE_ID_PREFIX)

# Bilibili command -> canonical kind mapping.
CMD_TO_KIND = {
    "DANMU_MSG": "danmaku",
    "SEND_GIFT": "gift",
    "GUARD_BUY": "guard",
    "SUPER_CHAT_MESSAGE": "superChat",
}

# Bilibili guard level -> canonical tier mapping.
GUARD_LEVEL_TO_TIER = {
    1: "captain",
    2: "admiral",
    3: "governor",
}

_ENVELOPE_KEYS = frozenset({"cmd", "eventId", "timestamp", "user", "data"})
_USER_KEYS = frozenset({"uid", "uname"})

_DATA_KEYS = {
    "danmaku": frozenset({"text"}),
    "gift": frozenset({"giftName", "num", "unitPriceMilliCny"}),
    "guard": frozenset({"guardLevel", "num"}),
    "superChat": frozenset({"message", "priceMilliCny", "time"}),
}

# Optional recorded platform combo identity and cumulative gift fields. They are
# accepted only as a complete, all-or-nothing set on ``SEND_GIFT`` and are never
# serialized: they travel as internal :class:`~danmaku.core.platform.GiftPlatformMeta`.
_GIFT_COMBO_KEYS = frozenset({"comboId", "totalNum", "totalCoin"})


class BilibiliAdapterError(ValueError):
    """Raised when a recorded Bilibili input envelope is invalid."""


class DuplicateEventError(BilibiliAdapterError):
    """Raised when a recorded event identity has already been normalized."""


def _reject_constant(value: str) -> None:
    raise BilibiliAdapterError(f"non-finite numbers are not allowed: {value}")


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BilibiliAdapterError(f"{field} must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: frozenset[str], field: str) -> None:
    actual = set(value)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    details = []
    if missing:
        details.append(f"missing {missing}")
    if unknown:
        details.append(f"unknown {unknown}")
    raise BilibiliAdapterError(
        f"{field} must have exactly keys {sorted(expected)}; {' and '.join(details)}"
    )


def _validate_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise BilibiliAdapterError(f"{field} must be a string")
    if any(ord(char) < 0x20 for char in value):
        raise BilibiliAdapterError(f"{field} must not contain control characters")
    return value


def _validate_text(value: Any, field: str, minimum: int, maximum: int) -> str:
    value = _validate_string(value, field)
    if not minimum <= len(value) <= maximum:
        raise BilibiliAdapterError(f"{field} must be {minimum}..{maximum} code points")
    return value


def _validate_integer(
    value: Any, field: str, minimum: int, maximum: int
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BilibiliAdapterError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise BilibiliAdapterError(f"{field} must be between {minimum} and {maximum}")
    return value


def _validate_cmd(value: Any) -> str:
    if not isinstance(value, str):
        raise BilibiliAdapterError("cmd must be a string")
    kind = CMD_TO_KIND.get(value)
    if kind is None:
        raise BilibiliAdapterError(f"cmd must be one of {sorted(CMD_TO_KIND)}")
    return kind


def _validate_event_id(value: Any) -> str:
    value = _validate_string(value, "eventId")
    if len(value) > MAX_EVENT_ID_LENGTH:
        raise BilibiliAdapterError(
            f"eventId must be at most {MAX_EVENT_ID_LENGTH} characters"
        )
    if not ID_PATTERN.fullmatch(value):
        raise BilibiliAdapterError(f"eventId must match {ID_PATTERN.pattern!r}")
    return value


def _format_timestamp(timestamp_ms: int) -> str:
    seconds, millis = divmod(timestamp_ms, 1000)
    try:
        value = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, ValueError, OSError) as exc:
        raise BilibiliAdapterError(
            f"timestamp is not representable as a UTC timestamp: {timestamp_ms!r}"
        ) from exc
    value += timedelta(milliseconds=millis)
    return f"{value.strftime('%Y-%m-%dT%H:%M:%S')}.{millis:03d}Z"


def _validate_user(value: Any) -> tuple[str, str]:
    user = _require_object(value, "user")
    _exact_keys(user, _USER_KEYS, "user")
    uid = _validate_string(user["uid"], "user.uid")
    if not ID_PATTERN.fullmatch(uid):
        raise BilibiliAdapterError(f"user.uid must match {ID_PATTERN.pattern!r}")
    uname = _validate_text(user["uname"], "user.uname", 1, 64)
    return uid, uname


def _validate_danmaku_data(value: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(value, _DATA_KEYS["danmaku"], "data")
    return {"text": _validate_text(value["text"], "data.text", 1, 500)}


def _validate_gift_combo_meta(
    value: Mapping[str, Any], num: int, total: int
) -> "GiftPlatformMeta | None":
    """Validate the optional ``SEND_GIFT`` combo metadata, returning ``None``
    when the metadata is well-typed but unusable (internally inconsistent).

    Structural problems — wrong types or out-of-range cumulative values — are
    rejected as :class:`BilibiliAdapterError`. A well-typed but contradictory
    cumulative (below the current event's own contribution) is treated as
    unusable and dropped, so the aggregator falls back to the sliding window.
    """
    combo_id = value["comboId"]
    if not isinstance(combo_id, str):
        raise BilibiliAdapterError("data.comboId must be a string")
    if any(ord(char) < 0x20 for char in combo_id):
        raise BilibiliAdapterError(
            "data.comboId must not contain control characters"
        )
    if not ID_PATTERN.fullmatch(combo_id):
        raise BilibiliAdapterError(f"data.comboId must match {ID_PATTERN.pattern!r}")

    total_num = _validate_integer(value["totalNum"], "data.totalNum", 1, GIFT_QUANTITY_MAX)
    total_coin = _validate_integer(
        value["totalCoin"], "data.totalCoin", 0, MAX_SEQUENCE
    )

    # A cumulative total must not be below the current event's own contribution:
    # otherwise the recorded values are contradictory and unusable for the
    # deterministic cumulative merge, so the adapter drops the metadata (None)
    # rather than fabricating a delta.
    if total_num < num or total_coin < total:
        return None

    return GiftPlatformMeta(
        combo_id=combo_id,
        cumulative_quantity=total_num,
        cumulative_amount_milli_cny=total_coin,
    )


def _validate_gift_data(
    value: Mapping[str, Any],
) -> tuple[dict[str, Any], "GiftPlatformMeta | None"]:
    base_keys = _DATA_KEYS["gift"]
    actual = set(value)
    if actual != base_keys and actual != (base_keys | _GIFT_COMBO_KEYS):
        raise BilibiliAdapterError(
            f"data must have exactly keys {sorted(base_keys)}, optionally plus "
            f"all of {sorted(_GIFT_COMBO_KEYS)}"
        )
    gift_name = _validate_text(value["giftName"], "data.giftName", 1, 100)
    num = _validate_integer(value["num"], "data.num", 1, 1_000_000)
    unit_price = _validate_integer(
        value["unitPriceMilliCny"], "data.unitPriceMilliCny", 0, MAX_SEQUENCE
    )
    total = num * unit_price
    if total > MAX_SEQUENCE:
        raise BilibiliAdapterError(
            "data.totalAmountMilliCny (num * unitPriceMilliCny) "
            f"exceeds {MAX_SEQUENCE}"
        )
    meta = (
        _validate_gift_combo_meta(value, num, total)
        if _GIFT_COMBO_KEYS.issubset(actual)
        else None
    )
    return (
        {
            "giftName": gift_name,
            "quantity": num,
            "totalAmountMilliCny": total,
        },
        meta,
    )


def _validate_guard_data(value: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(value, _DATA_KEYS["guard"], "data")
    raw_level = value["guardLevel"]
    if isinstance(raw_level, bool) or not isinstance(raw_level, int):
        raise BilibiliAdapterError("data.guardLevel must be an integer")
    tier = GUARD_LEVEL_TO_TIER.get(raw_level)
    if tier is None:
        raise BilibiliAdapterError(
            "data.guardLevel must be one of 1 (captain), 2 (admiral), 3 (governor)"
        )
    months = _validate_integer(value["num"], "data.num", 1, 120)
    return {"tier": tier, "months": months}


def _validate_super_chat_data(value: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(value, _DATA_KEYS["superChat"], "data")
    text = _validate_text(value["message"], "data.message", 1, 500)
    amount = _validate_integer(
        value["priceMilliCny"], "data.priceMilliCny", 1, MAX_SEQUENCE
    )
    duration = _validate_integer(value["time"], "data.time", 1, 86_400)
    return {"text": text, "amountMilliCny": amount, "durationSeconds": duration}


def _validate_data(
    kind: str, value: Mapping[str, Any]
) -> tuple[dict[str, Any], "GiftPlatformMeta | None"]:
    if kind == "danmaku":
        return _validate_danmaku_data(value), None
    if kind == "gift":
        return _validate_gift_data(value)
    if kind == "guard":
        return _validate_guard_data(value), None
    return _validate_super_chat_data(value), None


def _validate_envelope(record: Mapping[str, Any]) -> dict[str, Any]:
    _require_object(record, "record")
    _exact_keys(record, _ENVELOPE_KEYS, "record")

    kind = _validate_cmd(record["cmd"])
    event_id = _validate_event_id(record["eventId"])

    timestamp_ms = record["timestamp"]
    if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int):
        raise BilibiliAdapterError("timestamp must be an integer")
    if timestamp_ms < 0:
        raise BilibiliAdapterError("timestamp must be >= 0")
    received_at = _format_timestamp(timestamp_ms)

    uid, uname = _validate_user(record["user"])
    data = _require_object(record["data"], "data")
    canonical_data, platform_meta = _validate_data(kind, data)

    return {
        "kind": kind,
        "eventId": event_id,
        "receivedAt": received_at,
        "uid": uid,
        "uname": uname,
        "data": canonical_data,
        "platformMeta": platform_meta,
    }


class BilibiliAdapter:
    """Normalize recorded Bilibili input envelopes into canonical messages.

    The adapter is a stateful, process-local normalizer: it assigns strictly
    increasing sequences starting at ``start_sequence`` and rejects a repeated
    recorded ``eventId`` for the lifetime of the instance. Construction and
    :meth:`normalize` are synchronous and deterministic; there is no network or
    clock input.
    """

    def __init__(self, *, start_sequence: int = 1) -> None:
        if (
            isinstance(start_sequence, bool)
            or not isinstance(start_sequence, int)
            or start_sequence < 1
        ):
            raise ValueError("start_sequence must be a positive integer")
        self._next_sequence = start_sequence
        self._seen_event_ids: set[str] = set()

    @property
    def next_sequence(self) -> int:
        """The sequence that the next normalized message will receive."""
        return self._next_sequence

    def normalize(self, record: Mapping[str, Any]) -> Message:
        """Validate ``record`` and return its canonical ``source="bilibili"`` message.

        Raises :class:`BilibiliAdapterError` for invalid input and
        :class:`DuplicateEventError` when the recorded ``eventId`` repeats.
        """
        envelope = _validate_envelope(record)
        event_id = envelope["eventId"]
        if event_id in self._seen_event_ids:
            raise DuplicateEventError(
                f"duplicate recorded event identity: {event_id!r}"
            )

        message = Message.from_dict(
            {
                "id": MESSAGE_ID_PREFIX + event_id,
                "sequence": self._next_sequence,
                "receivedAt": envelope["receivedAt"],
                "source": "bilibili",
                "kind": envelope["kind"],
                "user": {"id": envelope["uid"], "name": envelope["uname"]},
                "data": envelope["data"],
            },
            platform_meta=envelope["platformMeta"],
        )
        self._seen_event_ids.add(event_id)
        self._next_sequence += 1
        return message

    def normalize_json(self, text: str) -> Message:
        """Parse a JSON string envelope and normalize it.

        Non-finite JSON numbers (``NaN``/``Infinity``) are rejected rather than
        coerced.
        """
        if not isinstance(text, str):
            raise BilibiliAdapterError("record JSON must be a string")
        try:
            value = json.loads(text, parse_constant=_reject_constant)
        except json.JSONDecodeError as exc:
            raise BilibiliAdapterError("record is not valid JSON") from exc
        return self.normalize(value)
