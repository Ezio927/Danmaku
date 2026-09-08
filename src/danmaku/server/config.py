"""First Vertical Slice service configuration validation.

The validated startup configuration is described in
``docs/configuration-schema.md``:

.. code-block:: json

    {
      "configVersion": 1,
      "service": {"host": "127.0.0.1", "port": 17391},
      "mock": {"cadenceMilliseconds": 1000},
      "snapshot": {"maxMessages": 100},
      "obs": {
        "denyUserIds": [],
        "denyNicknames": [],
        "keywords": [],
        "giftThresholdMilliCny": 100
      }
    }

All keys are required and unknown keys are rejected. The ``obs`` deny lists are
validated as arrays of strings but are not normalized here: trimming,
casefolding, and deduplication happen once inside :class:`FilteringPolicy`,
which is built from the validated values at startup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .filtering import DEFAULT_GIFT_THRESHOLD_MILLI_CNY, FilteringPolicy

__all__ = [
    "CONFIG_VERSION",
    "DEFAULT_CADENCE_MILLISECONDS",
    "DEFAULT_PORT",
    "HOST",
    "MAX_MESSAGES",
    "ConfigError",
    "ServiceConfig",
]

HOST = "127.0.0.1"
DEFAULT_PORT = 17391
DEFAULT_CADENCE_MILLISECONDS = 1000
MAX_MESSAGES = 100

CONFIG_VERSION = 1

PORT_MIN = 1024
PORT_MAX = 65535
CADENCE_MIN = 100
CADENCE_MAX = 60_000

_CONFIG_KEYS = frozenset({"configVersion", "service", "mock", "snapshot", "obs"})
_SERVICE_KEYS = frozenset({"host", "port"})
_MOCK_KEYS = frozenset({"cadenceMilliseconds"})
_SNAPSHOT_KEYS = frozenset({"maxMessages"})
_OBS_KEYS = frozenset(
    {"denyUserIds", "denyNicknames", "keywords", "giftThresholdMilliCny"}
)


class ConfigError(ValueError):
    """Raised when configuration does not match the frozen schema."""


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
    raise ConfigError(
        f"{field} must have exactly keys {sorted(expected)}; {' and '.join(details)}"
    )


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{field} must be an object")
    return value


def _validate_integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise ConfigError(f"{field} must be between {minimum} and {maximum}")
    return value


def _validate_gift_threshold(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{field} must be an integer")
    if value < 0:
        raise ConfigError(f"{field} must be >= 0")
    return value


def _validate_string_array(value: Any, field: str) -> frozenset[str]:
    if isinstance(value, str) or not isinstance(value, (list, tuple, set, frozenset)):
        raise ConfigError(f"{field} must be an array of strings")
    if any(not isinstance(entry, str) for entry in value):
        raise ConfigError(f"{field} entries must be strings")
    return frozenset(value)


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    """Validated startup configuration for the loopback service.

    Service, mock, and snapshot values plus the raw (not yet normalized) OBS
    delivery filtering settings. Deny lists are stored as :class:`frozenset`
    (order-insensitive, duplicates collapsed) and normalized into a shared
    :class:`FilteringPolicy` by :meth:`build_policy`.
    """

    host: str = HOST
    port: int = DEFAULT_PORT
    cadence_milliseconds: int = DEFAULT_CADENCE_MILLISECONDS
    max_messages: int = MAX_MESSAGES
    deny_user_ids: frozenset[str] = frozenset()
    deny_nicknames: frozenset[str] = frozenset()
    keywords: frozenset[str] = frozenset()
    gift_threshold_milli_cny: int = DEFAULT_GIFT_THRESHOLD_MILLI_CNY

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or self.host != HOST:
            raise ConfigError(f"service.host must be exactly {HOST!r}")
        _validate_integer(self.port, "service.port", PORT_MIN, PORT_MAX)
        _validate_integer(
            self.cadence_milliseconds, "mock.cadenceMilliseconds", CADENCE_MIN, CADENCE_MAX
        )
        if (
            isinstance(self.max_messages, bool)
            or not isinstance(self.max_messages, int)
        ):
            raise ConfigError("snapshot.maxMessages must be an integer")
        if self.max_messages != MAX_MESSAGES:
            raise ConfigError(f"snapshot.maxMessages must be exactly {MAX_MESSAGES}")
        object.__setattr__(
            self, "deny_user_ids", _validate_string_array(self.deny_user_ids, "obs.denyUserIds")
        )
        object.__setattr__(
            self,
            "deny_nicknames",
            _validate_string_array(self.deny_nicknames, "obs.denyNicknames"),
        )
        object.__setattr__(
            self, "keywords", _validate_string_array(self.keywords, "obs.keywords")
        )
        _validate_gift_threshold(self.gift_threshold_milli_cny, "obs.giftThresholdMilliCny")

    @classmethod
    def default(cls) -> "ServiceConfig":
        return cls()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ServiceConfig":
        _require_object(value, "config")
        _exact_keys(value, _CONFIG_KEYS, "config")

        version = value["configVersion"]
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != CONFIG_VERSION
        ):
            raise ConfigError(f"configVersion must be the integer {CONFIG_VERSION}")

        service = _require_object(value["service"], "service")
        _exact_keys(service, _SERVICE_KEYS, "service")

        mock = _require_object(value["mock"], "mock")
        _exact_keys(mock, _MOCK_KEYS, "mock")

        snapshot = _require_object(value["snapshot"], "snapshot")
        _exact_keys(snapshot, _SNAPSHOT_KEYS, "snapshot")

        obs = _require_object(value["obs"], "obs")
        _exact_keys(obs, _OBS_KEYS, "obs")

        return cls(
            host=service["host"],
            port=service["port"],
            cadence_milliseconds=mock["cadenceMilliseconds"],
            max_messages=snapshot["maxMessages"],
            deny_user_ids=obs["denyUserIds"],
            deny_nicknames=obs["denyNicknames"],
            keywords=obs["keywords"],
            gift_threshold_milli_cny=obs["giftThresholdMilliCny"],
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the current-version serialization of this configuration."""
        return {
            "configVersion": CONFIG_VERSION,
            "service": {"host": self.host, "port": self.port},
            "mock": {"cadenceMilliseconds": self.cadence_milliseconds},
            "snapshot": {"maxMessages": self.max_messages},
            "obs": {
                "denyUserIds": sorted(self.deny_user_ids),
                "denyNicknames": sorted(self.deny_nicknames),
                "keywords": sorted(self.keywords),
                "giftThresholdMilliCny": self.gift_threshold_milli_cny,
            },
        }

    def build_policy(self) -> FilteringPolicy:
        """Compose the single shared, normalized filtering policy from settings."""
        return FilteringPolicy(
            deny_user_ids=self.deny_user_ids,
            deny_nicknames=self.deny_nicknames,
            keywords=self.keywords,
            gift_threshold_milli_cny=self.gift_threshold_milli_cny,
        )
