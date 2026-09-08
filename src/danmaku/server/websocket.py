"""WebSocket handler: snapshot-first, hello validation, ordered increments."""

from __future__ import annotations

import asyncio
import contextlib

from aiohttp import WSMsgType, web

from danmaku.core.hub import Subscription, SubscriptionClosed

from .frames import (
    classify_client_frame,
    error_frame,
    message_created_frame,
    snapshot_frame,
)
from .state import (
    HELLO_TIMEOUT_KEY,
    HUB_KEY,
    SUBSCRIPTIONS_KEY,
    WEBSOCKETS_KEY,
    WS_DONE_KEY,
)

__all__ = ["DEFAULT_HELLO_TIMEOUT", "websocket_handler"]

DEFAULT_HELLO_TIMEOUT = 5.0

_CLOSE_QUIET = 1001
_CLOSE_INVALID = 1008


async def _forward_messages(ws: web.WebSocketResponse, subscription: Subscription) -> None:
    while True:
        try:
            message = await subscription.receive()
        except SubscriptionClosed as exc:
            await ws.close(code=exc.code or _CLOSE_QUIET)
            return
        except asyncio.CancelledError:
            raise
        try:
            await ws.send_str(message_created_frame(message))
        except (ConnectionError, ConnectionResetError, RuntimeError):
            subscription.close(_CLOSE_QUIET)
            return


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    if request.query_string:
        raise web.HTTPBadRequest()

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    app = request.app
    hub = app[HUB_KEY]
    hello_timeout = app[HELLO_TIMEOUT_KEY]

    subscription = hub.subscribe()
    snapshot = hub.filtered_snapshot()

    app[SUBSCRIPTIONS_KEY].add(subscription)
    app[WEBSOCKETS_KEY].add(ws)
    done = asyncio.Event()
    app[WS_DONE_KEY].add(done)

    close_code = _CLOSE_QUIET

    try:
        try:
            await ws.send_str(snapshot_frame(snapshot))
        except (ConnectionError, ConnectionResetError, RuntimeError):
            return ws

        forward_task = asyncio.create_task(_forward_messages(ws, subscription))

        try:
            hello_received = False
            while True:
                try:
                    if hello_received:
                        msg = await ws.receive()
                    else:
                        msg = await asyncio.wait_for(ws.receive(), timeout=hello_timeout)
                except asyncio.TimeoutError:
                    close_code = _CLOSE_INVALID
                    break

                if msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED):
                    close_code = _CLOSE_QUIET
                    break
                if msg.type == WSMsgType.ERROR:
                    close_code = _CLOSE_QUIET
                    break

                if msg.type == WSMsgType.TEXT:
                    if hello_received:
                        error_code = "INVALID_FRAME"
                    else:
                        error_code = classify_client_frame(msg.data)
                elif msg.type == WSMsgType.BINARY:
                    error_code = "INVALID_FRAME"
                else:
                    continue

                if error_code is None:
                    hello_received = True
                    continue

                await ws.send_str(error_frame(error_code))
                close_code = _CLOSE_INVALID
                break
        finally:
            if not forward_task.done():
                forward_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await forward_task
    finally:
        subscription.close(close_code)
        app[SUBSCRIPTIONS_KEY].discard(subscription)
        app[WEBSOCKETS_KEY].discard(ws)
        app[WS_DONE_KEY].discard(done)
        if not ws.closed:
            with contextlib.suppress(Exception):
                await ws.close(code=close_code)
        done.set()

    return ws
