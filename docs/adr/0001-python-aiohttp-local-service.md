# ADR 0001: Python and aiohttp local service

Status: Accepted for the First Vertical Slice

## Context

The slice needs one loopback HTTP server, static files, WebSockets, asynchronous
fan-out, and deterministic tests. Existing CI points to Python 3.12.

## Decision

Use Python 3.12, one process, and one `asyncio` event loop. Approve `aiohttp` as
the sole runtime web dependency for HTTP, static serving, WebSockets, and test
clients. Record PySide6/Qt Widgets only as a future desktop direction; it is not
installed, imported, or implemented in this slice.

## Alternatives

FastAPI plus a server and WebSocket stack adds layers this slice does not need.
Flask requires a separate async/WebSocket approach. Standard-library HTTP lacks
the required server WebSocket support. Tkinter is not selected for the future
Windows host because Qt better fits tray, packaging, and richer list rendering.

## Consequences

Later implementation must pin aiohttp in project dependency metadata and keep
it out of core. Adding another web framework or PySide6 needs a new decision.

