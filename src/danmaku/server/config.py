"""First Vertical Slice service configuration validation.

The frozen conceptual object is described in ``docs/configuration-schema.md``:

.. code-block:: json

    {
      "configVersion": 1,
      "service": {"host": "127.0.0.1", "port": 17391},
      "mock": {"cadenceMilliseconds": 1000},
      "snapshot": {"maxMessages": 100}
    }

All keys are required and unknown keys are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

__all__ = ["ConfigError", "ServiceConfig"]

HOST = "127.0.0.1"
DEFAULT_PORT = 17391
DEFAULT_CADENCE_MILLISECONDS = 1000
MAX_MESSAGES = 100

PORT_MIN = 1024
PORT_MAX = 65535
CADENCE_MIN = 100
CADENCE_MAX = 60_000

_CONFIG_KEYS = frozenset({"configVersion", "service", "mock", "snapshot"})
_SERVICE_KEYS = frozenset({"host", "port"})
_MOCK_KEYS = frozenset({"cadenceMilliseconds"})
_SNAPSHOT_KEYS = frozenset({"maxMessages"})


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


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    """Validated startup configuration for the loopback service."""

    host: str = HOST
    port: int = DEFAULT_PORT
    cadence_milliseconds: int = DEFAULT_CADENCE_MILLISECONDS
    max_messages: int = MAX_MESSAGES

    def __post_init__(self) -> None:
        if self.host != HOST:
            raise ConfigError(f"service.host must be exactly {HOST!r}")
        _validate_integer(self.port, "service.port", PORT_MIN, PORT_MAX)
        _validate_integer(
            self.cadence_milliseconds, "mock.cadenceMilliseconds", CADENCE_MIN, CADENCE_MAX
        )
        if self.max_messages != MAX_MESSAGES:
            raise ConfigError(f"snapshot.maxMessages must be exactly {MAX_MESSAGES}")

    @classmethod
    def default(cls) -> "ServiceConfig":
        return cls()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ServiceConfig":
        _require_object(value, "config")
        _exact_keys(value, _CONFIG_KEYS, "config")

        version = value["configVersion"]
        if isinstance(version, bool) or not isinstance(version, int) or version != 1:
            raise ConfigError("configVersion must be the integer 1")

        service = _require_object(value["service"], "service")
        _exact_keys(service, _SERVICE_KEYS, "service")

        mock = _require_object(value["mock"], "mock")
        _exact_keys(mock, _MOCK_KEYS, "mock")

        snapshot = _require_object(value["snapshot"], "snapshot")
        _exact_keys(snapshot, _SNAPSHOT_KEYS, "snapshot")

        return cls(
            host=service["host"],
            port=service["port"],
            cadence_milliseconds=mock["cadenceMilliseconds"],
            max_messages=snapshot["maxMessages"],
        )
