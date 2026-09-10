# Operational diagnostics

The service records a small, allow-listed set of operational lifecycle events to
a deterministic local log. This is a diagnostics boundary only: it exists to make
startup, shutdown, configuration fallback, and bind failures observable, never to
capture message traffic, user content, or credential material.

## What is recorded

Exactly four event kinds are allowed; anything else is rejected with a
`ValueError` so private content has no structural path into the log:

| Event              | Recorded fields       | Emitted at                                        |
| ---                | ---                   | ---                                               |
| `startup`          | `host`, `port`        | after the loopback bind succeeds                  |
| `shutdown`         | —                     | end of service shutdown                           |
| `config_fallback`  | `source`, `reason`    | when configuration fell back to backup or defaults |
| `bind_failure`     | `host`, `port`        | when the loopback port cannot be bound            |

Each event is serialized as one JSON line with a UTC `timestamp` and the
`event` kind. Field values are bounded: strings are truncated at 256 code
points, numbers pass through, and any other value (mappings, sequences, bytes,
booleans) is replaced with the fixed `<redacted>` placeholder.

## What is never recorded

Ordinary diagnostics never contain:

- credential material — access keys, secrets, passwords, authorization values;
- identity codes, cookies, tokens, or auth request bodies;
- raw protocol packets or wire frames;
- danmaku or Super Chat text, gift details, or any message payload;
- raw usernames or full user IDs.

This is enforced structurally: the boundary only accepts the four operational
event kinds and their fixed field names, and the service never routes message
content through it. The same event log also excludes the OBS deny-list entries
and keywords stored in configuration — only the *fact* and *reason* of a
configuration fallback is recorded, never the file's contents.

## Location

The log lives next to the deterministic configuration file as
`danmaku.log`: `~/.config/danmaku/danmaku.log` on Linux/macOS,
`%APPDATA%\danmaku\danmaku.log` on Windows. The `DANMAKU_CONFIG` environment
variable and the `--config PATH` startup flag move the log alongside the chosen
configuration file. The boundary never opens a network socket; it writes to the
local filesystem only.

## Rotation and limits

The active log respects a canonical 10 MiB limit (`10 * 1024 * 1024` bytes) and
retains at most five rotated backups (`.1` … `.5`). Before a record that would
reach the limit, the active log is rolled to `.1` and the existing backups are
shifted, with the oldest dropped.

`backup_count=0` is supported explicitly and deterministically: on rollover the
active log is truncated in place and no backup is ever retained, so the log
stays bounded without a numbered backup chain. This differs from a plain
append-mode rollover, which would leave the active file growing without bound.

## Failure behavior

Diagnostics are best-effort. Filesystem errors (for example an unwritable
configuration directory) are swallowed so recording can never fail service
startup, shutdown, or operation. A disabled boundary (constructed with no path)
records nothing, and is the default used by callers that have not been given an
explicit local log path.
