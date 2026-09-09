# Recorded Bilibili adapter contract

This document defines the frozen input contract that the offline Bilibili
adapter accepts and the exact normalization into the canonical message model
(see `docs/message-model.md`). The adapter is deterministic and offline-only:
it validates a recorded input envelope and maps it into a
`source="bilibili"` message. It performs no network access, authentication,
persistence, or live transport, and it never redefines the protocol-v1 frame
grammar.

## Input envelope

A recorded Bilibili event is a JSON object with exactly these keys. “Exact”
means no additional or missing object keys.

| Field | Type and rule |
| --- | --- |
| `cmd` | string; exactly one of `DANMU_MSG`, `SEND_GIFT`, `GUARD_BUY`, `SUPER_CHAT_MESSAGE` |
| `eventId` | string matching `^[A-Za-z0-9][A-Za-z0-9._:-]{0,55}$`; the stable recorded event identity |
| `timestamp` | integer, non-negative, UTC Unix epoch milliseconds |
| `user` | exact object `{"uid": string, "uname": string}`; `uid` matches the message-ID regex, `uname` is 1–64 code points |
| `data` | exact kind-specific object below |

`timestamp` must be an integer and representable as a UTC calendar timestamp
(year 1–9999). `uid` and `uname` are the stable user identity and display name;
they are carried verbatim into `user.id` and `user.name`.

## Command and data mappings

| `cmd` | canonical `kind` | `data` input | canonical `data` |
| --- | --- | --- | --- |
| `DANMU_MSG` | `danmaku` | `{"text": string}` | `{"text": string}` |
| `SEND_GIFT` | `gift` | `{"giftName": string, "num": integer, "unitPriceMilliCny": integer}` optionally plus the combo metadata fields below | `{"giftName": string, "quantity": num, "totalAmountMilliCny": num * unitPriceMilliCny}` |
| `GUARD_BUY` | `guard` | `{"guardLevel": integer, "num": integer}` | `{"tier": tier, "months": num}` |
| `SUPER_CHAT_MESSAGE` | `superChat` | `{"message": string, "priceMilliCny": integer, "time": integer}` | `{"text": message, "amountMilliCny": priceMilliCny, "durationSeconds": time}` |

## Gift combo metadata

A recorded `SEND_GIFT` envelope may optionally carry the platform combo
identity and cumulative gift fields. These are accepted only as a complete,
all-or-nothing set of three keys added to the base gift `data` object:

| Field | Type and rule |
| --- | --- |
| `comboId` | string matching `^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$`; the stable platform combo identity of one gift burst |
| `totalNum` | integer 1–1,000,000; the platform-reported cumulative quantity for the combo so far |
| `totalCoin` | integer 0–9,007,199,254,740,991; the platform-reported cumulative total in milli-CNY for the combo so far |

When the complete set is present and well-typed, it is normalized into an
internal `GiftPlatformMeta` carried on the canonical message
(`message.platform_meta`). This metadata is internal only: it is never added to
the canonical v1 `data`, never serialized into a protocol frame, and never
changes the canonical `data` mapping above.

The cumulative values are inclusive of the current event. The canonical
`quantity` and `totalAmountMilliCny` therefore remain the per-event increment
(`num` and `num * unitPriceMilliCny`); the aggregator derives the combo's
combined totals from the cumulative fields at the aggregation boundary.

### Metadata usability

- **Missing** (no combo keys) → no metadata; the aggregator falls back to the
  bounded five-second sliding-window grouping.
- **Malformed** (a partial key set, a wrong type, an out-of-range value, or a
  control character in `comboId`) → rejected with `BilibiliAdapterError`.
- **Unusable** (well-typed but internally inconsistent: `totalNum < num` or
  `totalCoin < num * unitPriceMilliCny`) → the metadata is dropped (`None`), so
  the aggregator falls back to the sliding window.

Guard level mapping:

| `guardLevel` | canonical `tier` |
| --- | --- |
| `1` | `captain` (舰长) |
| `2` | `admiral` (提督) |
| `3` | `governor` (总督) |

Guards are normalized as `kind="guard"` and are never subject to the ordinary
gift delivery threshold (see `docs/configuration-schema.md`).

## Value bounds

| Field | bound |
| --- | --- |
| `data.text`, `data.message` | 1–500 code points |
| `data.giftName` | 1–100 code points |
| `data.num` (gift) | 1–1,000,000 |
| `data.unitPriceMilliCny` | 0–9,007,199,254,740,991 |
| `data.comboId` | 1–64 code points, ID regex |
| `data.totalNum` | 1–1,000,000 |
| `data.totalCoin` | 0–9,007,199,254,740,991 |
| `data.num` (guard) | 1–120 |
| `data.priceMilliCny` | 1–9,007,199,254,740,991 |
| `data.time` | 1–86,400 |

All money is an integer count of one-thousandth CNY. The gift total is the exact
integer product `num * unitPriceMilliCny`; it must not exceed
9,007,199,254,740,991. Floating-point, boolean, or string money values are
rejected, never coerced.

## Identity, sequence, and duplicates

- The canonical message `id` is the deterministic string `"bilibili:" + eventId`.
- `receivedAt` is the recorded `timestamp` normalized to the RFC 3339 UTC string
  `YYYY-MM-DDTHH:MM:SS.mmmZ` with millisecond precision.
- The adapter assigns a process-local, strictly increasing `sequence`. The seam
  is `BilibiliAdapter(start_sequence=...)`; each successful `normalize` call
  increments the counter by exactly one, and `next_sequence` exposes the next
  value.
- A repeated `eventId` is rejected explicitly with `DuplicateEventError` for the
  lifetime of the adapter instance. Duplicate detection happens only after the
  record has fully validated, so a rejected record never mutates adapter state.

## Rejection rules

The adapter rejects, without silent coercion:

- an unknown `cmd` or an unsupported `guardLevel` enum value;
- missing or unknown keys at the envelope, `user`, or `data` level;
- wrong types (for example a non-string `cmd`, a non-integer `timestamp`,
  `num`, `unitPriceMilliCny`, `priceMilliCny`, `time`, or `guardLevel`, or a
  non-object `user`/`data`);
- invalid values (out-of-bounds quantities, months, durations, amounts, or
  empty/too-long text);
- malformed combo metadata (a partial `comboId`/`totalNum`/`totalCoin` set,
  a wrong-typed or out-of-range cumulative value, or a control character in
  `comboId`);
- malformed timestamps (negative, boolean, floating-point, non-finite, or not
  representable as a UTC calendar timestamp);
- non-integer or non-finite money (`NaN`, `Infinity`, `-Infinity`, or any
  non-integer value).

Control characters (U+0000–U+001F) in any string are rejected.

Well-typed but internally inconsistent combo metadata (`totalNum < num` or
`totalCoin < num * unitPriceMilliCny`) is not rejected: it is treated as
unusable and dropped, so the aggregator falls back to the sliding window.

## Fixtures

Representative non-sensitive examples live in `docs/bilibili-fixtures/`:

- `danmaku.json`
- `gift.json`
- `gift-combo.json` (a `SEND_GIFT` carrying platform combo identity and
  cumulative fields)
- `guard.json`
- `super-chat.json`

Each fixture is a single valid recorded envelope. They use synthetic user IDs,
display names, and text; no real account or credential data is present.
