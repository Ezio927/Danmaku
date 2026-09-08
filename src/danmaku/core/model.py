"""Canonical message model v1.

The :class:`Message` value object is the frozen, validated canonical shape
described in ``docs/message-model.md``. Construction always goes through
validation, so a ``Message`` instance is a guarantee that its fields are valid.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

__all__ = ["Message", "User", "ValidationError"]


class ValidationError(ValueError):
    """Raised when a value does not match the canonical message model."""


ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
RECEIVED_AT_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)

MAX_SEQUENCE = 9_007_199_254_740_991
KINDS = frozenset({"danmaku", "gift", "guard", "superChat"})
GUARD_TIERS = frozenset({"governor", "admiral", "captain"})

MESSAGE_KEYS = frozenset(
    {"id", "sequence", "receivedAt", "source", "kind", "user", "data"}
)


def _reject_constant(value: str) -> None:
    raise ValidationError(f"non-finite numbers are not allowed: {value}")


def _require_object(value: Any, field: str) -> None:
    if not isinstance(value, dict):
        raise ValidationError(f"{field} must be an object")


def _exact_keys(value: dict[str, Any], expected: set[str], field: str) -> None:
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
    raise ValidationError(
        f"{field} must have exactly keys {sorted(expected)}; {' and '.join(details)}"
    )


def _validate_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    if any(ord(char) < 0x20 for char in value):
        raise ValidationError(f"{field} must not contain control characters")
    return value


def _validate_text(value: Any, field: str, minimum: int, maximum: int) -> str:
    value = _validate_string(value, field)
    length = len(value)
    if not minimum <= length <= maximum:
        raise ValidationError(f"{field} must be {minimum}..{maximum} code points")
    return value


def _validate_integer(
    value: Any, field: str, minimum: int, maximum: int
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise ValidationError(f"{field} must be between {minimum} and {maximum}")
    return value


def _validate_id(value: Any, field: str) -> str:
    value = _validate_string(value, field)
    if not ID_PATTERN.fullmatch(value):
        raise ValidationError(f"{field} must match {ID_PATTERN.pattern!r}")
    return value


def _validate_source(value: Any) -> str:
    if value not in ("mock", "bilibili"):
        raise ValidationError("source must be 'mock' or 'bilibili'")
    return value


def _validate_kind(value: Any) -> str:
    if value not in KINDS:
        raise ValidationError(f"kind must be one of {sorted(KINDS)}")
    return value


def _validate_received_at(value: Any) -> str:
    if not isinstance(value, str) or not RECEIVED_AT_PATTERN.fullmatch(value):
        raise ValidationError(
            "receivedAt must be an RFC 3339 UTC timestamp like "
            "YYYY-MM-DDTHH:MM:SS.mmmZ"
        )
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValidationError("receivedAt is not a valid timestamp") from exc
    return value


def _coerce_user(value: Any) -> "User":
    if isinstance(value, User):
        return value
    _require_object(value, "user")
    _exact_keys(value, {"id", "name"}, "user")
    return User(id=value["id"], name=value["name"])


def _validate_data(kind: str, value: Any) -> dict[str, Any]:
    _require_object(value, "data")
    if kind == "danmaku":
        _exact_keys(value, {"text"}, "data")
        return {"text": _validate_text(value["text"], "data.text", 1, 500)}
    if kind == "gift":
        _exact_keys(value, {"giftName", "quantity", "totalAmountMilliCny"}, "data")
        return {
            "giftName": _validate_text(value["giftName"], "data.giftName", 1, 100),
            "quantity": _validate_integer(
                value["quantity"], "data.quantity", 1, 1_000_000
            ),
            "totalAmountMilliCny": _validate_integer(
                value["totalAmountMilliCny"],
                "data.totalAmountMilliCny",
                0,
                MAX_SEQUENCE,
            ),
        }
    if kind == "guard":
        _exact_keys(value, {"tier", "months"}, "data")
        tier = value["tier"]
        if tier not in GUARD_TIERS:
            raise ValidationError(
                "data.tier must be one of governor, admiral, captain"
            )
        return {
            "tier": tier,
            "months": _validate_integer(value["months"], "data.months", 1, 120),
        }
    if kind == "superChat":
        _exact_keys(value, {"text", "amountMilliCny", "durationSeconds"}, "data")
        return {
            "text": _validate_text(value["text"], "data.text", 1, 500),
            "amountMilliCny": _validate_integer(
                value["amountMilliCny"], "data.amountMilliCny", 1, MAX_SEQUENCE
            ),
            "durationSeconds": _validate_integer(
                value["durationSeconds"], "data.durationSeconds", 1, 86_400
            ),
        }
    raise ValidationError(f"unknown kind: {kind!r}")


@dataclass(frozen=True, slots=True)
class User:
    """The canonical user object carried inside a message."""

    id: str
    name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validate_id(self.id, "user.id"))
        object.__setattr__(self, "name", _validate_text(self.name, "user.name", 1, 64))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name}


@dataclass(frozen=True, slots=True)
class Message:
    """An immutable, validated canonical message."""

    id: str
    sequence: int
    received_at: str
    source: str
    kind: str
    user: User
    data: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validate_id(self.id, "id"))
        object.__setattr__(
            self, "sequence", _validate_integer(self.sequence, "sequence", 1, MAX_SEQUENCE)
        )
        object.__setattr__(self, "received_at", _validate_received_at(self.received_at))
        object.__setattr__(self, "source", _validate_source(self.source))
        kind = _validate_kind(self.kind)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "user", _coerce_user(self.user))
        object.__setattr__(self, "data", MappingProxyType(_validate_data(kind, self.data)))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Message":
        _require_object(value, "message")
        _exact_keys(value, MESSAGE_KEYS, "message")
        return cls(
            id=value["id"],
            sequence=value["sequence"],
            received_at=value["receivedAt"],
            source=value["source"],
            kind=value["kind"],
            user=value["user"],
            data=value["data"],
        )

    @classmethod
    def parse(cls, text: str) -> "Message":
        if not isinstance(text, str):
            raise ValidationError("message JSON must be a string")
        try:
            value = json.loads(text, parse_constant=_reject_constant)
        except json.JSONDecodeError as exc:
            raise ValidationError("message is not valid JSON") from exc
        return cls.from_dict(value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sequence": self.sequence,
            "receivedAt": self.received_at,
            "source": self.source,
            "kind": self.kind,
            "user": self.user.to_dict(),
            "data": dict(self.data),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))
