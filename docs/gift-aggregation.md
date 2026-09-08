# Gift combo aggregation

Ordinary paid gifts (`kind == "gift"`) are aggregated at the canonical-message
delivery boundary so that a burst of the same gift from the same user is shown
as one combined gift. The canonical host state (the bounded `SnapshotStore`) is
never filtered or merged; aggregation only changes what OBS clients receive on
the snapshot and live delivery paths.

## Grouping key

Gifts are grouped by the exact stable `user.id` and the exact `data.giftName`
already exposed by the canonical v1 model. No room, platform combo, or other
identifier is used, and no field absent from the v1 model is invented. Different
users and different gift names are never merged.

## Sliding window

A gift merges into the current pending aggregate when its key matches and its
`receivedAt` is within the fixed five-second (5000 ms) window of the aggregate's
current anchor — the `receivedAt` of the most recently merged gift. The boundary
is inclusive: an exactly five-second gap still merges. Each new matching gift
extends the window by moving the anchor to its own `receivedAt`.

Merging sums the exact integer `quantity` and the exact integer
`totalAmountMilliCny`. If a merge would exceed a canonical model bound
(`quantity` above 1,000,000 or `totalAmountMilliCny` above
9,007,199,254,740,991), the pending aggregate is finalized first and the gift
opens a new group, so every accepted gift stays representable and none is lost.

## Finalization (deterministic expiry)

A pending aggregate is emitted — never silently dropped — by exactly these
deterministic triggers:

1. A gift with a different key (different user or gift name) arrives.
2. A matching gift arrives more than five seconds after the anchor (a later
   group).
3. A non-gift message (danmaku, guard, or super-chat) arrives.
4. `finalize()` is called explicitly, including on normal service shutdown.

`finalize()` is idempotent: after it flushes the pending aggregate, later calls
emit nothing until another pending aggregate forms. Because triggers 1–3 each
emit the pending aggregate before processing the incoming message, the delivered
stream stays strictly increasing in `sequence` while non-gift messages keep
their original identity and order.

## Identity and shape

An emitted aggregate is a valid canonical v1 `Message` with `kind == "gift"`. It
retains the first gift's identity — `id`, `sequence`, `receivedAt`, `source`, and
`user` — and replaces `data` with the summed `giftName`, `quantity`, and
`totalAmountMilliCny`. The protocol frame shape is unchanged: aggregates travel
through the exact `snapshot` and `message.created` frames as ordinary messages.

## Integration and filtering

Aggregation lives in the core (`danmaku/core/aggregation.py`) and is wired into
`DistributionHub`, which feeds each message through the aggregator and then the
delivery filter. Aggregation runs before filtering, so a combined gift is
evaluated by its summed amount. The canonical `SnapshotStore` (and
`DistributionHub.snapshot()`) continues to hold the original, un-aggregated
messages; only `DistributionHub.filtered_snapshot()` and subscriber increments
expose the aggregate result, identically on both paths.
