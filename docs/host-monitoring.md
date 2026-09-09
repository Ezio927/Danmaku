# Host monitoring surface

The host window (主播窗口) is a dedicated, compact monitoring surface served by
the same loopback-only service as OBS. It shows every supported message without
any OBS delivery filtering, using the complete canonical host timeline and an
independent live delivery path, while the frozen OBS protocol v1 surface is left
unchanged.

## Routes

Two additive routes serve the host surface (the OBS v1 contract in
`docs/api-protocol.md` is unchanged):

- `GET /host` → 200 UTF-8 HTML (the compact host page). All other methods → 405.
- `GET /assets/{name}` now additionally allow-lists `host.html`, `host.js`, and
  `host.css` with the existing allow-list/traversal rules.
- `GET /ws/host` → WebSocket host delivery. Requires an upgrade (otherwise 400),
  and any query parameter returns 400.

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

## Boundaries

The host surface adds no credentials, live Bilibili transport, external network
access, deployment, database, settings UI, or protocol v2. It reuses the existing
protocol v1 frame grammar (`snapshot`, `message.created`, `error`, and the exact
`hello` client frame) on a separate endpoint; the OBS `/ws` endpoint, its
filtering, its queues, and the frozen protocol fixtures are unchanged.
