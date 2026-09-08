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

## Acceptance boundary

This design-onboarding Task adds no application code: no core, OBS page, local
service, or server exists yet. Therefore it makes no runtime bind claim.
`127.0.0.1` is the frozen host invariant that later implementation must satisfy;
binding any other interface is prohibited.

| Task boundary | Accepted or deferred evidence |
| --- | --- |
| This design-onboarding Task | Accepts the architecture, frozen v1 protocol and fixtures, test seams, and loopback-only invariant as unambiguous documentation. |
| Future core implementation Task | Implements and unit-tests message validation, deterministic mock production, bounded snapshots, and ordered subscriber distribution. |
| Future OBS-page implementation Task | Implements and tests safe rendering, bounded DOM behavior, snapshot/increment handling, and reconnect behavior. |
| Future local-service implementation Task | Implements the HTTP/WebSocket adapter and proves that its listener binds exactly `127.0.0.1`, including bind-failure and lifecycle tests. |
| Future system-validation Task | Runs end-to-end browser/OBS checks against the implemented core, page, and loopback service; it does not retroactively make runtime behavior an acceptance criterion here. |

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
Each WebSocket client has an independent bounded outbound queue. Under overload,
the oldest queued ordinary danmaku is evicted to admit the next message while
gift, guard, and super-chat messages are preserved; a slow or failed client with
no evictable danmaku is closed without delaying the hub or other clients.

## Security and failure boundary

The listener host is a constant `127.0.0.1`, not a user-supplied interface.
There is no CORS promise, authentication, credential input, remote fetch, or
file-system data store. Browser content is untrusted text and is inserted with
`textContent`/`createTextNode`. A malformed client frame receives a versioned
error, while invalid internal messages fail before publication.

State is bounded in memory: the snapshot retains at most 100 message payloads in
oldest-first order, plus a process-lifetime set of canonical ID strings used for
duplicate rejection. Payload eviction never removes an ID from that set, so a
duplicate ID stays rejected for the life of the store while the payload stays
bounded at 100. This deliberately trades unbounded growth of a small string set
against guaranteed process-lifetime message-ID uniqueness. The per-ID memory
cost and total retained-ID growth are measured in a future eight-hour
qualification, not claimed here. Service shutdown stops the mock producer,
closes clients, then releases the runner. Restart loses both the snapshot and
the seen-ID set by design.
