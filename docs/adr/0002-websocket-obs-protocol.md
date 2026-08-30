# ADR 0002: Versioned loopback WebSocket for OBS

Status: Accepted for the First Vertical Slice

## Context

An OBS Browser Source needs an initial recovery view and ordered live updates.
Multiple sources must not interfere, and the surface must remain local.

## Decision

Serve the page and a versioned JSON WebSocket from the same aiohttp service,
bound exactly to `127.0.0.1`. On connect the server sends one `snapshot`, then
ordered `message.created` frames. All frames have exactly `protocolVersion`,
`type`, and `payload`; protocol version is the JSON integer `1`.

## Alternatives

Polling adds latency and repeat transfer. SSE is one-way but still needs a
separate client-error/version negotiation path. IPC is not directly consumable
by an OBS browser page.

## Consequences

The service must isolate client backpressure and the page must reconnect with
bounded exponential delay. Protocol changes require a new version and fixtures;
v1 is frozen by `docs/api-protocol.md` and `docs/protocol-fixtures/`.

