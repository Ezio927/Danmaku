# OBS snapshot retention

The OBS delivery snapshot (the `snapshot` frame sent on connect and reconnect)
is trimmed to the most recent five minutes of delivered messages, on top of the
existing 100-message delivery bound. The canonical host state and live
`message.created` delivery are never affected: the host timeline retains up to
1000 messages independently of the OBS 100-message delivery bound (see
`docs/host-timeline-retention.md`).

## Window

The snapshot retention window is a fixed 300,000 ms (five minutes) measured from
the current instant supplied by the hub's injected clock. A message is retained
when its `receivedAt` is within the window. The boundary is inclusive: a message
exactly five minutes old is still included, while anything older is excluded.
The snapshot is capped at 100 messages by the delivered store, so the result
holds at most 100 messages in strictly increasing sequence order, oldest first.
This 100-message OBS delivery cap is independent of the canonical host store's
1000-message bound.

## Clock seam

`DistributionHub` accepts an optional zero-argument `clock` callable returning
the current UTC instant in integer milliseconds since the epoch. It defaults to
the wall clock, but tests inject a fixed clock so the five-minute boundary is
deterministic. The clock is evaluated each time a snapshot is built, so a
reconnecting client sees the window relative to the moment it connects.

## Canonical state and live delivery

Retention applies only on the snapshot read path
(`DistributionHub.filtered_snapshot()`). The canonical `SnapshotStore` (and
`DistributionHub.snapshot()`) continues to hold every accepted message up to its
own 1000-message host timeline bound and is never trimmed by age. Live delivery
is unchanged: subscribers still receive every message in publish order,
including messages that have aged out of the snapshot window. Aggregation and
filtering still run before the retention trim, so the trimmed snapshot carries
exactly the same aggregated, filtered messages a subscriber was offered.

## Protocol compatibility

The `snapshot` frame grammar is unchanged: it still holds 0–100 canonical
messages in strictly increasing sequence order, oldest first. Retention only
removes older messages, and sequence gaps are already permitted by the v1
filtering semantics. No protocol-v1 frame, hello behavior, or reconnect
replacement semantics change.
