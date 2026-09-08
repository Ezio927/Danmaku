# Bilibili connection lifecycle contract

This document defines the credential-free, transport-independent Bilibili
connection lifecycle state machine exposed at `danmaku.bilibili.lifecycle`
(`ConnectionLifecycle`, `ConnectionState`, `FailureClass`, `BackoffPolicy`).
It performs no network access, stores no credentials or identity code, and has
no persistence or external side effects. It only decides which state is legal
next, classifies failures, and schedules bounded exponential reconnect delays
for a future transport to consume.

## Canonical states

The six canonical states are exposed as `ConnectionState` string values:

| State | Value |
| --- | --- |
| `UNCONFIGURED` | `unconfigured` |
| `WAITING_IDENTITY` | `waiting for identity code` |
| `STARTING_SESSION` | `starting session` |
| `CONNECTING` | `connecting` |
| `CONNECTED` | `connected` |
| `RECONNECTING` | `reconnecting` |

`ConnectionLifecycle.state` always returns exactly one of these members and
starts at `unconfigured`.

## Failure classes

The five distinct failure classes are exposed as `FailureClass` string values:

| Class | Value | Behaviour |
| --- | --- | --- |
| `CREDENTIAL` | `credential` | fail-closed stop, no retry |
| `IDENTITY_CODE` | `identity-code` | fail-closed stop, no retry |
| `NOT_LIVE` | `not-live` | fail-closed stop, no retry |
| `NETWORK` | `network` | retryable with backoff |
| `PLATFORM` | `platform` | retryable with backoff |

## Legal transitions

Each event is legal only in the listed states; any other call raises
`IllegalTransitionError` and leaves state and attempt accounting unchanged.

| Event | Legal from | Result |
| --- | --- | --- |
| `configure()` | `unconfigured` | `waiting for identity code` |
| `submit_identity_code(code)` | `waiting for identity code` | `starting session` |
| `start_session()` | `starting session` | `connecting` |
| `connect()` | `reconnecting` | `connecting` |
| `connected()` | `connecting` | `connected` (resets attempt) |
| `reconnect()` | `connected` | `reconnecting` (increments attempt) |
| `stop()` | `starting session`, `connecting`, `connected`, `reconnecting` | `waiting for identity code` (resets attempt) |
| `report_failure(network \| platform)` | `connecting`, `connected` | `reconnecting` (increments attempt) |
| `report_failure(credential)` | `starting session`, `connecting`, `connected`, `reconnecting` | `unconfigured` (resets attempt) |
| `report_failure(identity-code \| not-live)` | `starting session`, `connecting`, `connected`, `reconnecting` | `waiting for identity code` (resets attempt) |

The identity code is validated but never stored. `submit_identity_code` accepts
a non-empty string of at most 256 code points with no control characters and
raises `InvalidIdentityCodeError` otherwise, without changing state.

## Reconnect scheduling

`BackoffPolicy` is an immutable schedule with injectable
`base_delay_milliseconds` (default `500`), `max_delay_milliseconds` (default
`5000`), and `multiplier` (default `2`). `delay_for(attempt)` returns
`base * multiplier ** (attempt - 1)` capped at `max_delay_milliseconds`, so the
default schedule is `500, 1000, 2000, 4000, 5000, 5000, ...` and every delay is
bounded regardless of attempt count.

`ConnectionLifecycle.attempt` is the current reconnect attempt number
(1-indexed, `0` when no reconnect cycle is active) and
`next_delay_milliseconds` is the scheduled delay (`0` outside `reconnecting`).
A `connected` transition resets the attempt counter to `0`, so the next failure
starts again at the base delay. `reconnect()` and a retryable
`report_failure` each increment the attempt counter.

## Out of scope

The lifecycle adds no credentials, cookies, QR/login, session handling,
persistence, network transport, or external side effects. The recorded-event
adapter, canonical message model, protocol-v1 frames, OBS filtering and
configuration, and gift aggregation are unchanged.
