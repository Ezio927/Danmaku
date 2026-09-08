# First Vertical Slice detailed design

## Components and seams

`Message` is an immutable validated value with serialization at the core edge.
`MockSource` is an async producer parameterized by cadence and a monotonic
sequence seed; it emits danmaku, gift, guard, and super chat cyclically using
fixed fixture-equivalent values. Its cancellation is normal shutdown.

`SnapshotStore.append(message)` retains the latest 100 messages and returns no
transport objects. `SnapshotStore.list()` returns an immutable oldest-first
copy. The payload snapshot is bounded at 100 messages, but the store keeps every
accepted canonical id in a process-lifetime `seen_ids` set that is never
evicted, so a duplicate id is rejected even after its payload leaves the
snapshot. `DistributionHub.publish(message)` validates, appends, assigns no new
identity, and offers the same message to subscribers in publish order.
Subscriptions expose async receive and explicit close; each has capacity 100.
If a subscriber queue is full, that subscriber is closed with WebSocket code
1013 rather than dropping or reordering messages.

The aiohttp composition root owns source, hub, store, runner, and client tasks.
Startup binds `127.0.0.1` at configured port, then starts the producer. Bind
failure is fatal; no fallback host or port is selected. Shutdown cancels and
awaits the producer, closes subscriptions/WebSockets, then cleans up aiohttp.

## HTTP and WebSocket lifecycle

`/health` reads service state without exposing configuration. `/obs` and known
assets are packaged resources; traversal and unknown assets return 404. `/ws`
completes the upgrade, subscribes, captures the store, sends one snapshot, then
forwards increments. Composition must serialize connection setup with publish
so no message can be absent from both snapshot and increments.

The server accepts only text client frames containing the exact v1 `hello`
object. It answers a valid hello with no extra frame. Invalid JSON, shape,
version, or type gets one error frame and close code 1008. Binary frames get
`INVALID_FRAME`. Ping/pong is handled by aiohttp.

## OBS page

The page starts empty and transparent, opens `ws://127.0.0.1:<port>/ws`, sends
the v1 hello, replaces its list on `snapshot`, and appends on
`message.created`. It deduplicates by message ID, caps the DOM at 100, and uses
document order oldest-to-newest in a bottom-anchored container. Snapshot items
have no entrance animation; increments may animate. Connection errors remain
visually transparent. Reconnect delays are 0.5, 1, 2, 4, then 5 seconds, reset
after a received snapshot.

Each variant is rendered by an explicit type switch. All user, message, and
gift text uses `textContent`; URLs and raw HTML are absent from v1.

## Test seams

Tests inject a clock/source cadence, call model parsing directly, subscribe to
the hub without HTTP, and create aiohttp test clients without real ports. A
WebSocket integration test uses a loopback ephemeral port. Static safety tests
assert absence of `innerHTML` and exercise hostile fixture text as text.

