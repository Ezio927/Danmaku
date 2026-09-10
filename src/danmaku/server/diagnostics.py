"""Local-only operational diagnostics boundary.

A small, allow-listed logging surface that records only safe operational
lifecycle events — startup, shutdown, configuration fallback, and bind failure —
to a deterministic local rotating log file. It never records private message
content (danmaku or Super Chat text, usernames, full user IDs) or credential
material (access keys, secrets, identity codes, cookies, tokens, auth bodies,
raw packets), and it never touches the network: every write goes to the local
filesystem only.

The active log respects the canonical 10 MiB limit and retains at most five
rotated backups. ``backup_count=0`` is supported explicitly and deterministically:
on rollover the active log is truncated in place and no backup is ever retained,
so the log stays bounded without growing a numbered backup chain.

Behaviour is documented in ``docs/diagnostics.md``.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .config_store import default_config_path

__all__ = [
    "ALLOWED_EVENTS",
    "BACKUP_COUNT",
    "EVENT_FIELDS",
    "LOG_FILENAME",
    "MAX_LOG_BYTES",
    "REDACTED",
    "Diagnostics",
    "default_log_path",
    "redact",
]

LOG_FILENAME = "danmaku.log"

#: Canonical active-log size limit: 10 MiB.
MAX_LOG_BYTES = 10 * 1024 * 1024

#: At most five rotated backups are retained.
BACKUP_COUNT = 5

#: Fixed placeholder used for every redacted value.
REDACTED = "<redacted>"

#: Longest field value retained before truncation.
_MAX_FIELD_LENGTH = 256

#: The only event kinds the boundary will record. Anything else is rejected so
#: private message content and credential material have no structural path into
#: ordinary diagnostics.
ALLOWED_EVENTS = frozenset(
    {"startup", "shutdown", "config_fallback", "bind_failure"}
)

#: Allow-listed field names per event kind. Unknown kinds or fields raise
#: :class:`ValueError`, keeping the recorded surface strictly operational.
EVENT_FIELDS: Mapping[str, frozenset[str]] = {
    "startup": frozenset({"host", "port"}),
    "shutdown": frozenset(),
    "config_fallback": frozenset({"source", "reason"}),
    "bind_failure": frozenset({"host", "port"}),
}


def redact(_value: object) -> str:
    """Return the fixed redaction placeholder for a sensitive value."""
    return REDACTED


def default_log_path() -> Path:
    """Return the deterministic default local path of the operational log.

    The log lives next to the deterministic configuration file, so it follows
    the same platform configuration directory and the ``DANMAKU_CONFIG``
    override.
    """
    return default_config_path().with_name(LOG_FILENAME)


def _truncate(value: str) -> str:
    if len(value) <= _MAX_FIELD_LENGTH:
        return value
    return value[:_MAX_FIELD_LENGTH]


def _coerce(value: object) -> object:
    """Coerce a field value to a safe scalar, redacting anything opaque.

    Strings are length-bounded. Numbers pass through. Booleans, mappings,
    sequences, and bytes are redacted so a structured or binary value can never
    be serialized into ordinary diagnostics.
    """
    if isinstance(value, str):
        return _truncate(value)
    if isinstance(value, bool):
        return REDACTED
    if isinstance(value, (int, float)):
        return value
    return REDACTED


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Diagnostics:
    """Local-only rotating operational log.

    ``path`` is the log file location. When it is ``None`` the boundary is
    disabled: :meth:`record` and :meth:`close` become no-ops, so callers can
    default to a silent instance without touching the filesystem.

    The active log is rotated at ``max_bytes``; ``backup_count`` caps how many
    numbered backups (``.1`` ... ``.N``) are retained. ``backup_count=0``
    truncates the active log in place on rollover and retains no backups, so the
    log stays bounded deterministically.
    """

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        max_bytes: int = MAX_LOG_BYTES,
        backup_count: int = BACKUP_COUNT,
    ) -> None:
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or max_bytes <= 0
        ):
            raise ValueError("max_bytes must be a positive integer")
        if (
            isinstance(backup_count, bool)
            or not isinstance(backup_count, int)
            or backup_count < 0
        ):
            raise ValueError("backup_count must be a non-negative integer")
        self._path = Path(path) if path is not None else None
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._lock = threading.Lock()

    @property
    def path(self) -> Path | None:
        """The active log path, or ``None`` when the boundary is disabled."""
        return self._path

    @property
    def max_bytes(self) -> int:
        """The active-log rollover threshold in bytes."""
        return self._max_bytes

    @property
    def backup_count(self) -> int:
        """The maximum number of rotated backups retained."""
        return self._backup_count

    @property
    def enabled(self) -> bool:
        """Whether the boundary writes records to the filesystem."""
        return self._path is not None

    def record(self, kind: str, **fields: object) -> None:
        """Append one allow-listed operational event.

        Unknown event kinds and unknown field names raise :class:`ValueError`,
        which keeps private message content and credential material from ever
        entering ordinary diagnostics. Field values are coerced to safe scalars
        (strings truncated, opaque values redacted) and serialized as a single
        JSON line. Filesystem errors are swallowed so diagnostics can never
        break service operation.
        """
        if self._path is None:
            return
        allowed = EVENT_FIELDS.get(kind)
        if allowed is None:
            raise ValueError(f"unknown diagnostic event {kind!r}")
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(
                f"unknown field(s) for {kind!r}: {sorted(unknown)}"
            )
        payload: dict[str, object] = {"timestamp": _utc_timestamp(), "event": kind}
        for name, value in fields.items():
            payload[name] = _coerce(value)
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        self._append(line)

    def _append(self, line: str) -> None:
        with self._lock:
            try:
                assert self._path is not None
                self._path.parent.mkdir(parents=True, exist_ok=True)
                encoded = line.encode("utf-8")
                if (
                    self._path.exists()
                    and self._path.stat().st_size + len(encoded) >= self._max_bytes
                ):
                    self._rotate()
                with open(self._path, "a", encoding="utf-8") as handle:
                    handle.write(line)
            except OSError:
                # Diagnostics are best-effort; never fail the service.
                return

    def _rotate(self) -> None:
        assert self._path is not None
        if self._backup_count == 0:
            # Explicit, deterministic, bounded: truncate the active log in
            # place and retain no backups. (A plain append-mode rollover would
            # leave the file growing without bound.)
            with open(self._path, "w", encoding="utf-8"):
                pass
            return
        for index in range(self._backup_count - 1, 0, -1):
            source = self._backup_path(index)
            target = self._backup_path(index + 1)
            if source.exists():
                if target.exists():
                    target.unlink()
                source.replace(target)
        target = self._backup_path(1)
        if target.exists():
            target.unlink()
        self._path.replace(target)

    def _backup_path(self, index: int) -> Path:
        assert self._path is not None
        return Path(f"{self._path}.{index}")

    def close(self) -> None:
        """Release the log boundary.

        The boundary opens the log per append, so there is no persistent stream
        to close; this is a no-op kept for lifecycle symmetry.
        """


#: A disabled, no-op diagnostics boundary used as a safe default by callers
#: that have not been given an explicit local log path.
NULL_DIAGNOSTICS = Diagnostics(None)
