"""HTTP handlers for health, the OBS page, and allow-listed assets."""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from .state import ASSET_ROOT_KEY

__all__ = ["ASSETS", "HEALTH_BODY", "asset_handler", "health_handler", "obs_handler"]

HEALTH_BODY = '{"protocolVersion":1,"status":"ok"}'

ASSETS: dict[str, str] = {
    "index.html": "text/html; charset=utf-8",
    "obs.js": "text/javascript; charset=utf-8",
    "obs.css": "text/css; charset=utf-8",
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


async def asset_handler(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    path = _resolve_asset(request.app[ASSET_ROOT_KEY], name)
    if path is None:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Type": ASSETS[name]})
