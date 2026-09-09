"""HTTP handlers for health, the OBS page, the Host page, and allow-listed assets."""

from __future__ import annotations

import json
from pathlib import Path

from aiohttp import web

from .config import ConfigError, ServiceConfig
from .config_store import load_config, save_config
from .state import ASSET_ROOT_KEY, CONFIG_PATH_KEY

__all__ = [
    "ASSETS",
    "HEALTH_BODY",
    "SETTINGS_PROTOCOL_VERSION",
    "asset_handler",
    "health_handler",
    "host_handler",
    "obs_handler",
    "settings_handler",
]

HEALTH_BODY = '{"protocolVersion":1,"status":"ok"}'

#: The Host settings surface is a narrow additive local API; it is not part of
#: the frozen OBS protocol v1 contract and carries its own explicit version.
SETTINGS_PROTOCOL_VERSION = 1

ASSETS: dict[str, str] = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
    "host.html": "text/html; charset=utf-8",
    "host.js": "text/javascript; charset=utf-8",
    "host.css": "text/css; charset=utf-8",
}


async def health_handler(request: web.Request) -> web.Response:
    return web.Response(
        text=HEALTH_BODY, content_type="application/json", charset="utf-8"
    )


def _resolve_asset(asset_root: Path, name: str) -> Path | None:
    if name not in ASSETS:
        return None
    root = asset_root.resolve()
    candidate = (root / name).resolve()
    if candidate.parent != root:
        return None
    if not candidate.is_file():
        return None
    return candidate


async def obs_handler(request: web.Request) -> web.Response:
    path = _resolve_asset(request.app[ASSET_ROOT_KEY], "index.html")
    if path is None:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Type": ASSETS["index.html"]})


async def host_handler(request: web.Request) -> web.Response:
    path = _resolve_asset(request.app[ASSET_ROOT_KEY], "host.html")
    if path is None:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Type": ASSETS["host.html"]})


async def asset_handler(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    path = _resolve_asset(request.app[ASSET_ROOT_KEY], name)
    if path is None:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Type": ASSETS[name]})


def _settings_body(config: ServiceConfig) -> dict[str, object]:
    """Return the narrow, non-secret settings payload for a validated config."""
    return {
        "protocolVersion": SETTINGS_PROTOCOL_VERSION,
        "config": config.to_dict(),
    }


def _settings_saved() -> dict[str, object]:
    return {
        "protocolVersion": SETTINGS_PROTOCOL_VERSION,
        "saved": True,
        "restartRequired": True,
    }


def _settings_error(message: str, status: int = 400) -> web.Response:
    return web.json_response(
        {
            "protocolVersion": SETTINGS_PROTOCOL_VERSION,
            "saved": False,
            "error": message,
        },
        status=status,
    )


async def settings_handler(request: web.Request) -> web.Response:
    """Serve the narrow Host settings API: GET the current config, POST a candidate.

    ``GET /host/settings`` returns the current validated configuration's
    non-secret serialization (the same shape the version-1 JSON file stores).
    ``POST /host/settings`` accepts a full candidate configuration, validates it
    with :class:`ServiceConfig`, and — only after validation succeeds — persists
    it through the existing atomic :func:`save_config` store. Successful saves
    report ``restartRequired`` because the running service has no safe apply
    seam; invalid candidates are rejected with a clear message and never touch
    the primary or backup files.
    """
    if request.method == "POST":
        return await _settings_save(request)
    return _settings_load(request)


def _settings_load(request: web.Request) -> web.Response:
    path = request.app[CONFIG_PATH_KEY]
    result = load_config(path)
    return web.json_response(_settings_body(result.config))


async def _settings_save(request: web.Request) -> web.Response:
    path = request.app[CONFIG_PATH_KEY]

    try:
        candidate = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
        return _settings_error("settings body must be valid JSON")

    if not isinstance(candidate, dict):
        return _settings_error("settings body must be an object")

    try:
        config = ServiceConfig.from_dict(candidate)
    except (ConfigError, TypeError, ValueError) as exc:
        return _settings_error(str(exc))

    try:
        save_config(config, path)
    except OSError:
        return _settings_error("failed to save configuration", status=500)

    return web.json_response(_settings_saved())
