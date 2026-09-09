# Gift combo aggregation

Ordinary paid gifts (`kind == "gift"`) are aggregated at the canonical-message
delivery boundary so that a burst of the same gift from the same user is shown
as one combined gift. When the recorded Bilibili adapter supplies reliable
platform combo identity and cumulative gift fields, the burst is grouped by that
platform combo identity and its combined totals are derived deterministically
from the cumulative fields without double-counting; otherwise the bounded
five-second sliding-window sum is used. The canonical host state (the bounded
`SnapshotStore`) is never filtered or merged; aggregation only changes what OBS
clients receive on the snapshot and live delivery paths.

## Grouping key

A gift that carries reliable platform combo metadata (an internal
`GiftPlatformMeta` on `message.platform_meta`, produced by the recorded-event
adapter from the optional `comboId`/`totalNum`/`totalCoin` fields) is grouped by
the exact stable platform combo identity (`combo_id`). A gift with no such
metadata — because it was absent, unusable, or the message came from the mock
source — is grouped by the exact stable `user.id` and the exact `data.giftName`
already exposed by the canonical v1 model.

The two grouping modes are disjoint, so a platform combo group and a
user+gift-name group can never merge. Different users, different gift names,
and different platform combos are never merged, and no field absent from the
v1 model is invented: the combo metadata is internal and never serialized.

## Sliding window and cumulative updates

A gift merges into the current pending aggregate when its key matches and its
`receivedAt` is within the fixed five-second (5000 ms) window of the aggregate's
current anchor — the `receivedAt` of the most recently merged gift. The boundary
is inclusive: an exactly five-second gap still merges. Each new matching gift
extends the window by moving the anchor to its own `receivedAt`.

For a fallback (user + gift name) group, merging sums the exact integer
`quantity` and the exact integer `totalAmountMilliCny`. If a merge would exceed
a canonical model bound (`quantity` above 1,000,000 or `totalAmountMilliCny`
above 9,007,199,254,740,991), the pending aggregate is finalized first and the
gift opens a new group, so every accepted gift stays representable and none is
lost.

For a platform combo group, the canonical per-event `data` still carries the
increment (`num`, `num * unitPriceMilliCny`), while the metadata carries the
platform-reported running totals. The aggregator therefore applies only the
exact delta between two consecutive events of the same combo:

```
deltaQuantity = current totalNum − last totalNum
deltaAmount   = current totalCoin − last totalCoin
```

so the running cumulative totals are never summed and never double-counted. A
combo whose cumulative values move backwards (a non-monotonic `totalNum` or
`totalCoin`) is unusable: the pending combo is finalized and the event falls
back to the plain sum grouping. Because the adapter bounds `totalNum` and
`totalCoin` to the canonical model bounds, a combo aggregate is always
representable.

## Finalization (deterministic expiry)

A pending aggregate is emitted — never silently dropped — by exactly these
deterministic triggers:

1. A gift with a different key (different user, gift name, or platform combo)
   arrives.
2. A matching gift arrives more than five seconds after the anchor (a later
   group).
3. A non-gift message (danmaku, guard, or super-chat) arrives.
4. A combo event reports a non-monotonic cumulative (unusable metadata).
5. `finalize()` is called explicitly, including on normal service shutdown.

`finalize()` is idempotent: after it flushes the pending aggregate, later calls
emit nothing until another pending aggregate forms. Because triggers 1–4 each
emit the pending aggregate before processing the incoming message, the delivered
stream stays strictly increasing in `sequence` while non-gift messages keep
their original identity and order.

## Identity and shape

An emitted aggregate is a valid canonical v1 `Message` with `kind == "gift"`. It
retains the first gift's identity — `id`, `sequence`, `receivedAt`, `source`, and
`user` — and replaces `data` with the combined `giftName`, `quantity`, and
`totalAmountMilliCny`. The aggregate carries no platform combo metadata: it is a
plain canonical gift message. The protocol frame shape is unchanged: aggregates
travel through the exact `snapshot` and `message.created` frames as ordinary
messages, and platform combo identity and cumulative fields are never exposed in
v1 serialization.

## Integration and filtering

Aggregation lives in the core (`danmaku/core/aggregation.py`) and is wired into
`DistributionHub`, which feeds each message through the aggregator and then the
delivery filter. Aggregation runs before filtering, so a combined gift is
evaluated by its summed amount. The canonical `SnapshotStore` (and
`DistributionHub.snapshot()`) continues to hold the original, un-aggregated
messages; only `DistributionHub.filtered_snapshot()` and subscriber increments
expose the aggregate result, identically on both paths.
