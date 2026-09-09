# Host monitoring surface

The host window (主播窗口) is a dedicated, compact monitoring surface served by
the same loopback-only service as OBS. It shows every supported message without
any OBS delivery filtering, using the complete canonical host timeline and an
independent live delivery path, while the frozen OBS protocol v1 surface is left
unchanged.

## Routes

Additive routes serve the host surface (the OBS v1 contract in
`docs/api-protocol.md` is unchanged):

- `GET /host` → 200 UTF-8 HTML (the compact host page). All other methods → 405.
- `GET /assets/{name}` now additionally allow-lists `host.html`, `host.js`, and
  `host.css` with the existing allow-list/traversal rules.
- `GET /ws/host` → WebSocket host delivery. Requires an upgrade (otherwise 400),
  and any query parameter returns 400.
- `GET /host/settings` → 200 `application/json` carrying the current validated
  configuration's non-secret serialization (the same shape the version-1 JSON
  file stores). All other methods → 405.
- `POST /host/settings` → validates a full candidate configuration with the
  existing `ServiceConfig` model and, only on success, persists it through the
  existing atomic `save_config` store. Returns 200 with `restartRequired: true`
  on success and 400 with a clear `error` message on validation failure. All
  other methods → 405.
- `POST /host/deny` → validates a single context-action body
  `{"list": "denyUserIds" | "denyNicknames", "value": "<identity>"}`, loads the
  current validated configuration, merges the entry into exactly the named deny
  list (deterministically deduplicated while preserving every unrelated field),
  revalidates the complete candidate with the existing `ServiceConfig` model,
  and persists it through the existing atomic `save_config` store. Returns 200
  with `restartRequired: true` on success and 400 with a clear `error` message
  on any invalid, stale, malformed, or missing identity. All other methods →
  405.

## Delivery model

The host stream is the complete, original canonical message stream:

- The reconnect snapshot is `DistributionHub.snapshot()` — the full canonical
  host timeline, up to the product-required 1000 messages in deterministic
  oldest-first order. It is never age-trimmed and never filtered.
- Live delivery is the raw message in publish order, before gift aggregation and
  without any OBS filtering. A host subscriber therefore receives messages the
  OBS policy suppresses and each gift individually (not the merged combo).
- On disconnect the page reconnects and treats the next snapshot as a complete
  replacement, exactly like OBS: no replay request, no merge with the old view.

This independence is implemented as a separate host delivery seam in
`DistributionHub`: `host_subscribe()` returns a subscription bounded by
`host_capacity` (default 1000, matching the host timeline) that is fed directly
from `publish()`, in parallel with the filtered/aggregated OBS path
(`subscribe()` / `filtered_snapshot()`). The OBS delivery snapshot and per-client
backpressure queues remain capped at 100 with the existing five-minute
retention.

## Page safety

The host page renders untrusted user and message text only through DOM text
APIs (`textContent`); it never uses `innerHTML` or any HTML sink. It deduplicates
by message ID, caps the DOM at 1000 items (the host timeline bound), and
reconnects on the same bounded schedule as OBS.

## Timeline controls

The Host page adds three bounded, view-local controls that never mutate the
canonical host state, the OBS delivery path, filtering, or the connection:

- **Paused follow.** When the timeline is scrolled away from the bottom, follow
  mode pauses. Messages received while paused are still appended (never lost)
  and counted into an unread total.
- **Return to latest.** A visible, keyboard-accessible button (native `<button>`)
  shows the unread count and, when activated, scrolls to the bottom, resets the
  unread total, and resumes follow mode.
- **Clear.** A native `<button>` removes only the current DOM items and resets the
  view-local follow/unread state. It never touches the WebSocket, the canonical
  store, OBS delivery, or filtering, and it sends no protocol frame.

Every control is rendered and updated with DOM text APIs only (`textContent`).
A snapshot replacement (initial connect or reconnect) deterministically resets
the view-local state — unread count to zero, follow mode resumed, and the prompt
hidden — before re-rendering, so reconnect behavior stays a complete replacement
exactly like OBS.

## Top presentation

The Host page adds one pinned top-presentation card for Super Chat messages. It
is derived entirely client-side from the same complete host stream and never
changes the canonical host state, the OBS delivery path, filtering, or the
protocol:

- **View-local lifecycle.** Every received `kind="superChat"` message stays in
  the complete host timeline (the timeline keeps the full, unchanged message)
  and is also admitted to a deterministic FIFO pending queue in receive order.
  The earliest pending Super Chat becomes the single displayed top card exactly
  three seconds (`PRESENTATION_INTERVAL_SECONDS`) after its `receivedAt`; the
  boundary is inclusive.
- **Duration and expiry.** The displayed card expires `data.durationSeconds`
  after it became displayed and promotes the next pending item. Expiry removes
  only the top card; the timeline item is retained.
- **Skip control.** A visible, keyboard-accessible native `<button>` (`Skip`)
  dismisses the active card immediately and promotes the next pending item
  immediately, bypassing both the remaining duration and the three-second
  interval. When no card is displayed, it promotes the earliest pending item.
  It mutates only view-local presentation state and sends no protocol frame.
- **Rendering.** The card renders the user name, the formatted amount, the
  deterministic tier/color derived from `data.amountMilliCny`, the message text,
  the pending count, and the remaining time — all through DOM text APIs only.
  The accent color is applied with a CSS class, never inline markup.
- **Snapshot replacement.** A snapshot deterministically resets and reconstructs
  the view-local presentation state (pending queue and active card) with no
  entry animation, exactly like the timeline reconnect behaviour.

The tier/color is a deterministic, display-only bucket derived from the integer
`data.amountMilliCny` (1 CNY = 1000 milli-CNY):

| amount ≥ (CNY) | color |
| --- | --- |
| ¥10,000 | gold |
| ¥2,000 | red |
| ¥1,000 | pink |
| ¥500 | purple |
| ¥100 | indigo |
| ¥50 | cyan |
| ¥30 | blue |

Amounts below ¥30 fall back to blue. This mapping is presentation-only; the
canonical `Message` model carries no tier or color field.

The top-presentation lifecycle is a client-side mirror of the documented core
`SuperChatLifecycle` (`docs/super-chat-lifecycle.md`). It is fed by the same
host stream and shares the same received/pending/displayed/expired semantics,
independently of the OBS delivery path.

## Connection status

The Host page shows two accessible, view-local status indicators in a single
`role="status"` live region, derived entirely from the existing loopback
`/health` route and the existing WebSocket lifecycle. Nothing here claims any
Bilibili or OBS availability; the page reports only the local service and the
Host feed itself.

- **Local service.** The page probes the existing loopback `GET /health` route
  (same-origin, no query parameters, no credentials) on a bounded, fixed
  five-second cadence. A `200` response whose body is the canonical
  `{"protocolVersion":1,"status":"ok"}` renders `Local service: available`; any
  failed fetch, non-`200` status, or non-`ok` body renders
  `Local service: unavailable`. Before the first probe resolves, the indicator
  stays empty rather than claiming a state it has not observed.
- **Host feed.** The feed indicator is derived deterministically from the
  WebSocket `readyState` and the bounded reconnect schedule:
  `Host feed: connecting` while the socket is `CONNECTING`, `connected` while it
  is `OPEN`, and `reconnecting` after a close schedules a reconnect on the
  existing frozen `[500, 1000, 2000, 4000, 5000]` backoff. When the local
  service is unavailable, the feed reports `Host feed: unavailable`.
- **Determinism and safety.** Every label is a fixed string rendered through DOM
  text APIs (`textContent`) only. No exception message, close reason, code, or
  user content is ever interpolated into the status, so connection failures
  expose no error details, secrets, or user content. The status indicators send
  no protocol frame (the Host client still sends exactly the frozen `hello`
  frame), never mutate canonical state, filtering, or OBS delivery, and do not
  alter the existing reconnect or snapshot-replacement semantics.

The four states — `connecting`, `connected`, `reconnecting`, and `unavailable` —
are therefore each traceable to a single existing behavior: WebSocket connect,
WebSocket open, bounded reconnect scheduling, and the loopback `/health` probe
respectively.

## Settings

The Host page adds a narrow, loopback-only settings surface for editing the
existing non-secret version-1 configuration. It never introduces live reload or
broad runtime reconfiguration: saving only persists a validated candidate, and
the running service applies it on the next start.

### Editable and fixed fields

Exactly six fields are editable:

- `service.port` (integer 1024–65535);
- `mock.cadenceMilliseconds` (integer 100–60,000);
- `obs.denyUserIds` (array of strings, matched exactly);
- `obs.denyNicknames` (array of strings, normalized at policy build);
- `obs.keywords` (array of strings, normalized at policy build);
- `obs.giftThresholdMilliCny` (non-negative integer).

`service.host` remains fixed at `127.0.0.1`, `snapshot.maxMessages` remains
fixed at `100`, and `configVersion` remains fixed at `1`. The settings form
renders the fixed values as disabled, read-only inputs and never sends an
altered value for them; the server-side `ServiceConfig` validation rejects any
candidate that changes them anyway.

### Save vs. apply

A save is a write to the persisted configuration only:

1. The page reads the current configuration with `GET /host/settings`.
2. The user edits the six editable fields and submits a full candidate (the
   fixed fields are copied through unchanged).
3. The server validates the candidate with the existing `ServiceConfig.from_dict`
   before anything is written. An invalid candidate returns 400 with a clear
   message (for example `service.port must be between 1024 and 65535`) and never
   touches the primary or backup files.
4. A valid candidate is persisted through the existing atomic `save_config`
   store, which retains one previously valid backup.
5. The server responds with `restartRequired: true`; the running service keeps
   its current configuration, so the change takes effect only on the next start.

There is no live reload, no in-place `FilteringPolicy` rebuild, and no broad
runtime reconfiguration seam.

### Safety

The settings API and UI expose only the non-secret version-1 configuration
above. They carry no credentials, Access Key Secret, identity code, cookies,
tokens, auth bodies, or future Bilibili secrets. Every value and message is
rendered with DOM text APIs only (`textContent`, `value`); the settings panel
uses no `innerHTML` or any HTML sink.

## Context actions

Every Host timeline item exposes two accessible, view-local actions that persist
the represented user's existing OBS deny-list entry — the stable `user.id` into
`obs.denyUserIds`, and the normalized `user.name` into `obs.denyNicknames` —
without changing the canonical Host state or any runtime filtering semantics.

### Actions

- **Block user.** A native `<button>` posts `{"list": "denyUserIds", "value":
  <user.id>}` to the loopback `POST /host/deny` route.
- **Block nickname.** A native `<button>` posts `{"list": "denyNicknames",
  "value": <user.name>}` to the same route. The server trims and casefolds the
  nickname using the existing `FilteringPolicy` normalization, so matching
  stays exact-equality against the normalized deny set (no substring matching).

Each action updates only the named deny list, leaves `keywords`,
`giftThresholdMilliCny`, `service.*`, and `snapshot.*` untouched, and is
idempotent: repeating an already-present identity writes the same single entry.

### Save vs. apply

A context action is a configuration write only:

1. The server loads the current validated configuration (primary → backup →
   defaults, exactly like `GET /host/settings`).
2. It merges the single normalized entry into the selected deny list with
   deterministic deduplication.
3. It revalidates the complete merged candidate with the existing
   `ServiceConfig` model before anything is written.
4. A valid candidate is persisted through the existing atomic `save_config`
   store, which retains one previously valid backup.
5. The server responds with `restartRequired: true`; the running service keeps
   its current configuration, so the change takes effect only on the next start.

There is no live reload, no in-place `FilteringPolicy` rebuild, and no mutation
of the running policy or the complete canonical Host timeline.

### Failure handling

Invalid, stale, malformed, or missing-identity actions fail safely with a clear
`error` message and a `saved: false` response, and never replace the last valid
primary or backup configuration:

- a body that is not valid JSON, not an object, or that carries unknown or
  missing keys is rejected;
- a `list` value other than `denyUserIds` or `denyNicknames` is rejected;
- a non-string or empty `value` is rejected;
- a `denyUserIds` value that does not match the canonical message identity shape
  is rejected; and
- a `denyNicknames` value that normalizes to an empty string is rejected.

## Boundaries

The host surface adds no credentials, live Bilibili transport, external network
access, deployment, database, or protocol v2. It reuses the existing
protocol v1 frame grammar (`snapshot`, `message.created`, `error`, and the exact
`hello` client frame) on a separate endpoint; the OBS `/ws` endpoint, its
filtering, its queues, and the frozen protocol fixtures are unchanged. The
settings surface and the context actions add no live reload or broad runtime
reconfiguration: they only validate and persist the existing non-secret
configuration, and they introduce no new filtering predicate, credential field,
or Bilibili authentication behavior.
