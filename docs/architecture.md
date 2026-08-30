# First Vertical Slice architecture

## Scope and decision

This document covers only deterministic simulated messages rendered by a local
OBS-compatible page. There is no Bilibili connection, desktop window, secret,
filtering, persistence, installer, deployment, or database.

Python 3.12 runs one process and one `asyncio` event loop. The approved runtime
web dependency is `aiohttp`, chosen to provide HTTP, static resources,
WebSockets, and test clients in one stack. Dependency installation and metadata
changes belong to a later implementation Task. PySide6/Qt Widgets is the
future desktop direction only; it is not a dependency and is not installed or
used in this slice.

## Boundaries and flow

```text
deterministic MockSource
  -> canonical Message validation
  -> DistributionHub (ordered publish)
  -> SnapshotStore (oldest-first, max 100)
  -> aiohttp adapter bound to 127.0.0.1
       GET /health   GET /obs   GET /assets/{name}   GET /ws
  -> plain HTML/CSS/JS OBS page (transparent, bottom anchored)
```

Core does not import `aiohttp` or browser code. The service composes the core
and serializes the frozen protocol. The OBS page consumes only protocol frames.
Each WebSocket client has an independent bounded outbound queue; a slow or
failed client is closed without delaying the hub or other clients.

## Security and failure boundary

The listener host is a constant `127.0.0.1`, not a user-supplied interface.
There is no CORS promise, authentication, credential input, remote fetch, or
file-system data store. Browser content is untrusted text and is inserted with
`textContent`/`createTextNode`. A malformed client frame receives a versioned
error, while invalid internal messages fail before publication.

State is bounded in memory: a 100-message snapshot and per-client queue. Service
shutdown stops the mock producer, closes clients, then releases the runner.
Restart loses the snapshot by design.

