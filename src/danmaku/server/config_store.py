"""Safe, versioned local JSON persistence for startup configuration.

The primary configuration file lives at a deterministic, documented location
(see :func:`default_config_path`) and carries an explicit ``configVersion``.
Loads fall back from the primary file to a single retained backup and finally
to canonical defaults, exposing a diagnostic whenever fallback occurs. Saves
write a same-filesystem temporary file and rename it atomically over the
primary, retaining exactly one previously valid backup, so a write or rename
failure can never leave a partial primary or destroy the only known-good
configuration.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, ServiceConfig

__all__ = [
    "BACKUP_SUFFIX",
    "PRIMARY_FILENAME",
    "LoadResult",
    "default_config_path",
    "load_config",
    "save_config",
]

PRIMARY_FILENAME = "config.json"
BACKUP_SUFFIX = ".bak"

_DIR_NAME = "danmaku"
_CONFIG_PATH_ENV = "DANMAKU_CONFIG"


@dataclass(frozen=True)
class LoadResult:
    """Outcome of a configuration load.

    ``source`` is one of ``"primary"``, ``"backup"``, or ``"defaults"``.
    ``diagnostic`` is a human-readable explanation present only when the
    primary (and possibly the backup) could not be used.
    """

    config: ServiceConfig
    source: str
    diagnostic: str | None = None


def default_config_path() -> Path:
    """Return the deterministic default path of the primary config file.

    When the ``DANMAKU_CONFIG`` environment variable is set, it names the exact
    primary file path. Otherwise the file is ``danmaku/config.json`` inside the
    platform configuration directory: ``$XDG_CONFIG_HOME`` (or ``~/.config``)
    on POSIX, ``%APPDATA%`` on Windows.
    """
    override = os.environ.get(_CONFIG_PATH_ENV)
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / _DIR_NAME / PRIMARY_FILENAME


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite numbers are not allowed: {value}")


def _read_validated(path: Path) -> ServiceConfig:
    text = path.read_text(encoding="utf-8")
    value = json.loads(text, parse_constant=_reject_constant)
    return ServiceConfig.from_dict(value)


def _attempt(path: Path) -> tuple[ServiceConfig | None, str | None]:
    """Return the validated config, or ``(None, reason)`` on any failure."""
    try:
        return _read_validated(path), None
    except (OSError, json.JSONDecodeError, ConfigError, TypeError, ValueError) as exc:
        return None, f"{path}: {exc}"


def load_config(path: Path | str) -> LoadResult:
    """Load configuration with ``primary -> backup -> defaults`` fallback.

    A missing primary file yields canonical defaults with no diagnostic. A
    corrupt or invalid primary falls back to the single valid backup when one
    exists, and otherwise to canonical defaults; both cases carry a diagnostic.
    """
    primary = Path(path)
    backup = primary.with_name(primary.name + BACKUP_SUFFIX)

    if not primary.exists():
        # A missing primary is not an error: load canonical defaults.
        return LoadResult(ServiceConfig.default(), "defaults")

    config, primary_reason = _attempt(primary)
    if config is not None:
        return LoadResult(config, "primary")

    if backup.exists():
        config, backup_reason = _attempt(backup)
        if config is not None:
            return LoadResult(
                config,
                "backup",
                f"primary config {primary} is invalid ({primary_reason}); "
                "loaded the last valid backup",
            )
        return LoadResult(
            ServiceConfig.default(),
            "defaults",
            f"primary config {primary} is invalid ({primary_reason}) and backup "
            f"{backup} is invalid ({backup_reason}); starting with safe defaults",
        )

    return LoadResult(
        ServiceConfig.default(),
        "defaults",
        f"primary config {primary} is invalid ({primary_reason}) and no backup "
        "exists; starting with safe defaults",
    )


def _serialize(config: ServiceConfig) -> str:
    return json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n"


def _write_atomic(path: Path, data: str) -> None:
    """Write ``data`` to ``path`` via a same-directory temp file and rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def save_config(config: ServiceConfig, path: Path | str) -> Path:
    """Atomically persist ``config`` and retain one previously valid backup.

    If the current primary file exists and is still valid, it is first written
    to the backup path. The new configuration is then written to a temporary
    file in the same directory and atomically renamed over the primary. A write
    or rename failure raises and leaves the primary (and the last known-good
    configuration) untouched.
    """
    primary = Path(path)
    backup = primary.with_name(primary.name + BACKUP_SUFFIX)

    data = _serialize(config)

    if primary.exists():
        current, _ = _attempt(primary)
        if current is not None:
            _write_atomic(backup, _serialize(current))

    _write_atomic(primary, data)
    return primary
