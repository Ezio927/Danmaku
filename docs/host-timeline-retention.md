# Host timeline retention

The host window (主播窗口) shows every supported message and must keep the most
recent 1000 messages in memory, evicting only the oldest payload after the 1001st
message. The OBS delivery path stays separately bounded at 100 messages with the
existing five-minute snapshot retention.

## Capacities

Three capacities are deliberately independent:

| Capacity | Value | Where configured |
| --- | --- | --- |
| Canonical host timeline | 1000 | `HOST_TIMELINE_MAX_MESSAGES` in `danmaku/server/runner.py`, applied to the canonical `SnapshotStore` |
| OBS delivery snapshot | 100 | `snapshot.maxMessages` (frozen at 100), applied to `DistributionHub.delivered_capacity` |
| Per-client backpressure queue | 100 | `snapshot.maxMessages` (frozen at 100), applied to `DistributionHub.capacity` |

`SnapshotStore` remains a single bounded oldest-first store parameterized by
`max_messages`; the host and OBS limits differ only because the service composes
two distinct stores with distinct bounds. `DistributionHub` accepts a
`delivered_capacity` so its delivery snapshot (used by
`DistributionHub.filtered_snapshot()`) no longer inherits the canonical store's
bound.

## Behavior

- `SnapshotStore.append` retains up to 1000 accepted messages in strictly
  increasing sequence order, oldest first, and evicts only the oldest payload
  once a 1001st message is accepted.
- Every accepted canonical id stays in the process-lifetime `seen_ids` set, so a
  duplicate id is rejected even after its payload has been evicted.
- `DistributionHub.snapshot()` returns the full (up to 1000-message) host
  timeline and is never age-trimmed.
- `DistributionHub.filtered_snapshot()` remains capped at 100 delivered messages
  and trimmed to the five-minute retention window; live subscriber delivery and
  per-client backpressure are unchanged.
- The v1 `snapshot` frame still carries 0–100 messages, matching the frozen
  protocol; the larger host timeline is not exposed on the wire.

## Rationale

The product requires the host replay buffer to keep the most recent 1000 messages
(`docs/danmaku_requirements.md` §3.2) while OBS keeps its existing 100-message
delivery bound and five-minute retention. Separating the canonical host store
from the OBS delivery store makes both limits independent without changing the
frozen v1 protocol or configuration schema.
