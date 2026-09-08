# Local protocol version 1

This document and the JSON fixtures are the normative, frozen contract. “Exact”
means no additional or missing object keys. JSON numbers described as integers
must be emitted without a fraction and must satisfy the model bounds.

## HTTP

The origin is `http://127.0.0.1:17391`; configuration may change only the port.

- `GET /health` → 200 `application/json` exact body
  `{"protocolVersion":1,"status":"ok"}`. All other methods → 405.
- `GET /obs` → 200 UTF-8 HTML. All other methods → 405.
- `GET /assets/{name}` → 200 for packaged, allow-listed assets; unknown names or
  traversal → 404. All other methods → 405.
- `GET /ws` requires a WebSocket upgrade; otherwise 400. No query parameters
  are defined and any query parameter returns 400.
- Any other route → 404. Error HTTP bodies are not part of the v1 contract.

## Frame grammar

Every server frame is a UTF-8 JSON text object with exactly:

```json
{"protocolVersion":1,"type":"<type>","payload":{}}
```

Server types:

- `snapshot`: payload exact key `messages`, an array of 0–100 canonical messages
  in strictly increasing sequence order, oldest first.
- `message.created`: payload exact key `message`, one canonical message. Across
  snapshot and increments, IDs and sequences are unique and increments increase.
- `error`: payload exact keys `code` and `message`. Both are strings; code is one
  of the following fixed pairs and contains no echoed input:

| `code` | exact `message` |
| --- | --- |
| `INVALID_JSON` | `Client frame is not valid JSON.` |
| `INVALID_FRAME` | `Client frame does not match the protocol.` |
| `UNSUPPORTED_VERSION` | `Only protocolVersion 1 is supported.` |
| `UNSUPPORTED_TYPE` | `Only the hello client frame is supported.` |

The sole client frame is exact:

```json
{"protocolVersion":1,"type":"hello","payload":{}}
```

## Connection and recovery

After upgrade, the server sends one snapshot before any increment. The client
must then send exactly one `hello` within 5 seconds; increments may arrive before
hello, and hello is only validation, not snapshot negotiation. A missing hello
closes with 1008 and no error frame. A second hello is `INVALID_FRAME`. A text
frame above 65,536 UTF-8 bytes or a binary frame is `INVALID_FRAME`. Any invalid
client frame receives exactly one corresponding error and the server closes with
1008. Normal shutdown uses 1001; a slow client's full queue uses 1013.

On disconnect, the page reconnects and treats the next snapshot as complete
replacement state. It must not request replay or merge the old view. Multiple
clients receive independent snapshots/queues and cannot affect each other.
There are no client mutations, acknowledgements, update/delete frames, heartbeat
application frames, client-requested filtering, style sync, compression promise,
or resume token in v1.

## Server-side delivery filtering

The service applies one immutable filtering policy per startup, shared by every
connected client. A message the policy suppresses is excluded from the snapshot
and from increments delivered to clients, but remains in the canonical host
state. Filtering is purely server-side and silent: there is no client
negotiation, no query parameter, no error frame, and no protocol change. A
snapshot still holds 0–100 messages in strictly increasing sequence order, and
an increment still increases; suppressed messages simply never appear, so
sequences may contain gaps.

## Fixture index

- `client-hello.json`: sole valid client frame.
- `snapshot.json`: four ordered variants, complete replacement state.
- `message-created-*.json`: one increment for each variant.
- `error-unsupported-version.json`: canonical error shape.
