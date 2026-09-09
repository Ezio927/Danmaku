# Bilibili Open Live client core contract

This document defines the deterministic, offline-testable client core for the
future Bilibili Open Live (开放平台) transport. The core models the
`start` -> `heartbeat` -> `end` session lifecycle: it constructs signed HTTP
requests, validates the strict session response models, frames the WebSocket
bootstrap/auth handshake, and reuses the existing connection lifecycle and
bounded failure/reconnect semantics.

It is credential-free and offline-only: it never requests, stores, or logs real
credentials or identity codes, and all network traffic flows only through
injected HTTP and WebSocket seams so every behaviour is reachable with
deterministic in-memory fakes.

Modules live under `danmaku.bilibili`:

| Module | Responsibility |
| --- | --- |
| `signing` | `Secret`/`AppCredentials`/`IdentityCode` value objects, redaction, and deterministic request signing |
| `openlive` | Start/heartbeat/end request and response models, the shared envelope, and WebSocket auth framing |
| `client` | `OpenLiveClient` orchestration, transport seams, and failure classification |

## Credential and identity model

Credentials and identity codes are represented structurally but are never real.
Values are supplied synthetically in tests; the implementation performs no
account lookup, persistence, logging, or live use of them.

| Value | Type | Redaction |
| --- | --- | --- |
| access key id | plain `str` | public, never redacted |
| access key secret | `Secret` | `repr`/`str` redact to `<redacted>`; raw value only via `.value` |
| room identity code | `IdentityCode` | `repr`/`str` redact; raw value only via `.value` |
| WebSocket auth body | `Secret` | redacted like the access key secret |

The identity code is validated (a non-empty string of at most 256 code points
with no control characters) and is never stored by the client after the `start`
call completes.

## Request signing and canonicalization

Every Open Live HTTP request is a signed `POST` with a JSON body. Signing is
deterministic: for a fixed access key, secret, body, timestamp, and nonce the
signature is always identical.

The signing headers are:

| Header | Value |
| --- | --- |
| `Content-Type` | `application/json` |
| `x-bili-accesskeyid` | the access key id |
| `x-bili-content-md5` | lowercase 32-hex MD5 of the request body bytes |
| `x-bili-signature-method` | `HMAC-SHA256` |
| `x-bili-signature-nonce` | a caller-supplied nonce |
| `x-bili-signature-version` | `1.0` |
| `x-bili-timestamp` | the signing timestamp in Unix seconds |
| `Authorization` | `{access_key_id}:{signature}` |

The canonical signature string is built from six lines joined by `\n` (no
trailing newline):

```text
x-bili-accesskeyid:{access_key_id}
x-bili-content-md5:{content_md5}
x-bili-signature-method:HMAC-SHA256
x-bili-signature-nonce:{nonce}
x-bili-signature-version:1.0
x-bili-timestamp:{timestamp}
```

The signature is `base64(HMAC-SHA256(access_key_secret, canonical_string))`.
The canonical string and signature contain no secret material (the secret is
only the HMAC key), so they are safe to record in fixtures and diagnostics.

### Worked synthetic example

With:

```text
access_key_id   = synthetic-access-key-0001
access_key_secret = synthetic-access-key-secret-0001
method          = POST
path            = /v2/app/start
body            = {"code":"synthetic-identity-code-0001","app_id":12345}
timestamp       = 1735689600
nonce           = synthetic-nonce-0001
```

the canonical string is:

```text
x-bili-accesskeyid:synthetic-access-key-0001
x-bili-content-md5:a6c6b4e37f42620d5ee1b8bcfdc5a600
x-bili-signature-method:HMAC-SHA256
x-bili-signature-nonce:synthetic-nonce-0001
x-bili-signature-version:1.0
x-bili-timestamp:1735689600
```

and the signature is:

```text
cqvKgVIwH1r8uYVDMMQGU/+CZv9HD5M7+Om6TivvRqM=
```

## Endpoints and request/response models

The base URL is `https://live-open.biliapi.com` and is injectable. The three
session endpoints are:

| Endpoint | Request body (exact keys, in order) |
| --- | --- |
| `POST /v2/app/start` | `{"code": <identity code>, "app_id": <positive int>}` |
| `POST /v2/app/heartbeat` | `{"game_id": <non-empty string>}` |
| `POST /v2/app/end` | `{"app_id": <positive int>, "game_id": <non-empty string>}` |

Every response shares one envelope with exactly the keys `code` (integer),
`message` (string), and `data`. `code == 0` means success and requires `data`
to be an object; a non-zero `code` is a business error (its `data` is not
interpreted). Non-finite JSON numbers are rejected.

Start success `data` has exactly `game_info` and `websocket_info`:

| Field | Rule |
| --- | --- |
| `game_info.game_id` | non-empty string |
| `websocket_info.wss_link` | non-empty array of `ws://`/`wss://` URLs |
| `websocket_info.auth_body` | a JSON object string carried as the AUTH frame body |

Heartbeat success `data` may carry an optional positive-integer `interval`
(seconds); any other key is rejected. End success `data` is an object with no
modeled fields.

Malformed or unexpected responses (invalid UTF-8/JSON, non-object bodies,
missing/extra keys, wrong types) are rejected with `MalformedResponseError`
without coercion.

## WebSocket bootstrap and auth framing

The start response's `websocket_info` drives the WebSocket handshake: the
client connects to `wss_link[0]`, sends an AUTH frame (operation `7`, plain
body) whose body is the exact `auth_body` string, and reads one AUTH_REPLY
frame (operation `8`). Framing reuses the existing wire codec
(`encode_frame`/`decode`) and never changes protocol-v1 product frames; AUTH and
AUTH_REPLY remain control packets that produce no product messages.

An auth reply body must be a JSON object with an integer `code`; `code == 0`
means success. Any other code is an `auth` failure.

## Lifecycle integration and failure classification

`OpenLiveClient` composes the existing `ConnectionLifecycle` state machine and
reuses its bounded failure/reconnect semantics. Failures are classified onto
the seven `FailureClass` values:

| Failure | Business/transport signal | `FailureClass` | Retry? | Result state |
| --- | --- | --- | --- | --- |
| credential | HTTP 401/403 or `code` 1100/1101 | `credential` | no | `unconfigured` |
| identity | `code` 1000/1001 | `identity-code` | no | `waiting for identity code` |
| not live | `code` 1200 | `not-live` | no | `waiting for identity code` |
| auth | auth-reply `code != 0` | `auth` | no | `waiting for identity code` |
| malformed platform | unparsable/schema-violating response | `malformed-platform` | no | `waiting for identity code` |
| network | transport `OSError`/`TransportError` | `network` | yes | `reconnecting` |
| platform transient | other non-zero business `code` | `platform` | yes | `reconnecting` |

Credential, identity, auth, and malformed-platform failures are fail-closed and
never blindly retried; network and transient platform failures use the existing
bounded exponential backoff. A fail-closed failure tears down the socket and
clears session data; a retryable failure retains the session for reconnect.

## Transport seams

The client accepts two injected seams so tests are deterministic with no
external calls:

- `HttpTransport.request(method, url, headers, body) -> HttpResponse`
  (`HttpResponse` carries `status` and `body` bytes).
- `WebSocketTransport` with `connect`, `send`, `receive`, and `close`.

The signing timestamp and nonce are supplied by injectable `clock` and
`nonce_source` callables, defaulting to `time.time()` and a UUID but overridden
with fixed values in tests.

## Secret redaction

Sensitive values (access key secret, identity code, auth body) redact
themselves in `repr`, `str`, exception messages, audit summaries, and request
debugging output. `SignedRequest.__repr__` and `SignedRequest.summary()` never
expose the request body (which may carry the identity code), and
`Envelope.__repr__` redacts `data` (which may carry the auth body). Client
failure details carry only the fixed failure class and code, never echoed secret
material. Clean shutdown clears all session data (`game_id`, WebSocket info, and
the last request summary).

## Out of scope

The client core adds no live HTTP/WebSocket transport (only injected seams), no
real credential or identity-code input, no persistence, and no Brotli support.
The recorded-event adapter, canonical message model, protocol-v1 frames, the
loopback-only service, OBS filtering, configuration, and gift aggregation are
unchanged.
