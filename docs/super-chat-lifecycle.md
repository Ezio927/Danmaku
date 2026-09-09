# Host Super Chat lifecycle

The host surface models a deterministic top-presentation lifecycle for every
received Super Chat at the core/host boundary, independently of the OBS delivery
path and without any protocol-v1 change. A Super Chat is a canonical
`kind="superChat"` message (see `docs/message-model.md`); its complete message is
always retained in the canonical host timeline (`SnapshotStore` /
`DistributionHub.snapshot()`), while this lifecycle tracks only whether it is
pending, displayed (the top card), expired, or deleted.

## States

| State | Meaning |
| --- | --- |
| `pending` | Received and retained in canonical host state; waiting for its presentation turn. |
| `displayed` | The single active top presentation (the pinned card). |
| `expired` | Its presentation duration elapsed and it left the top slot; the timeline item is retained. |
| `deleted` | Manually removed from the active top/card presentation; the timeline item is retained and clearly marked deleted with a `deletedAt` instant. |

Exactly one Super Chat can be `displayed` at a time. The lifecycle never mutates
the canonical `Message`; the message model, the protocol-v1 frame grammar, and
the OBS delivery path are unchanged.

## Presentation interval

`PRESENTATION_INTERVAL_SECONDS` is the fixed three-second presentation interval.
A received Super Chat first enters `pending` in deterministic receive order
(the canonical store guarantees strictly increasing sequence order, so this is
sequence order). The earliest not-yet-displayed Super Chat — the front of the
pending queue — is the single candidate for the top slot, and it becomes
`displayed` once exactly three seconds have elapsed since its `receivedAt`. The
boundary is inclusive: a Super Chat received at `T` is displayed at `T + 3s`.

## Duration and expiry

Once displayed, a Super Chat is the only top presentation and expires
`data.durationSeconds` after it became displayed. Expiry removes only the top
presentation and frees the slot for the next due pending Super Chat; the message
itself remains in the canonical host timeline. The display start and expiry
instants are therefore pure functions of `receivedAt` and `durationSeconds`, so
replayed `advance()` calls are deterministic regardless of when they are made.

## Manual operations

The lifecycle exposes synchronous, deterministic public seams for the host
operator:

- `select(message_id)` reorders a `pending` Super Chat to the front of the queue
  so it is the next one displayed (manual selection).
- `skip()` expires the current `displayed` Super Chat at the current clock
  instant and promotes the next pending Super Chat immediately, bypassing both
  the remaining duration and the presentation interval (manual advance).
- `delete(message_id)` removes a `pending` or `displayed` Super Chat from
  presentation and marks its retained timeline record `deleted` with the current
  clock instant (`deletedAt`). Deleting an already deleted record is idempotent;
  deleting an expired record is a no-op because it already left presentation.

The clock is the same injectable millisecond seam the hub uses for snapshot
retention, so all time boundaries are deterministic in tests.

## Composition

`DistributionHub.publish` feeds every accepted `kind="superChat"` message to the
composed `SuperChatLifecycle` immediately after it is retained in the canonical
store and delivered to host subscribers. The lifecycle is exposed as
`DistributionHub.superchat` and advanced explicitly by its consumer through
`advance()`, `select()`, `skip()`, and `delete()`.

## Boundaries

This lifecycle adds no credentials, authentication, live Bilibili transport,
external network access, database, deployment, public exposure, or protocol v2.
It invents no update/delete protocol frames and changes no existing protocol-v1
frame, hello behavior, OBS filtering, or delivery queue. The host timeline
retention and OBS delivery capacities are unchanged.
