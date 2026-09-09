"""WebSocket handlers: snapshot-first, hello validation, ordered increments.

The OBS endpoint (``/ws``) serves the aggregated, filtered delivery stream with
the five-minute, 100-message delivery snapshot. The Host endpoint (``/ws/host``)
serves the complete, unfiltered canonical host stream with the full 1000-message
host-timeline snapshot. Both share the exact same frame grammar and hello
validation semantics.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Callable

from aiohttp import WSMsgType, web

from danmaku.core.hub import Subscription, SubscriptionClosed
from danmaku.core.model import Message

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

__all__ = ["DEFAULT_HELLO_TIMEOUT", "host_websocket_handler", "websocket_handler"]

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


async def _websocket_session(
    request: web.Request,
    subscribe: Callable[[], Subscription],
    snapshot: Callable[[], tuple[Message, ...]],
) -> web.WebSocketResponse:
    if request.query_string:
        raise web.HTTPBadRequest()

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    app = request.app
    hello_timeout = app[HELLO_TIMEOUT_KEY]

    subscription = subscribe()
    messages = snapshot()

    app[SUBSCRIPTIONS_KEY].add(subscription)
    app[WEBSOCKETS_KEY].add(ws)
    done = asyncio.Event()
    app[WS_DONE_KEY].add(done)

    close_code = _CLOSE_QUIET

    try:
        try:
            await ws.send_str(snapshot_frame(messages))
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


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    hub = request.app[HUB_KEY]
    return await _websocket_session(request, hub.subscribe, hub.filtered_snapshot)


async def host_websocket_handler(request: web.Request) -> web.WebSocketResponse:
    hub = request.app[HUB_KEY]
    return await _websocket_session(request, hub.host_subscribe, hub.snapshot)
