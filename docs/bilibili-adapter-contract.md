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
| `SEND_GIFT` | `gift` | `{"giftName": string, "num": integer, "unitPriceMilliCny": integer}` | `{"giftName": string, "quantity": num, "totalAmountMilliCny": num * unitPriceMilliCny}` |
| `GUARD_BUY` | `guard` | `{"guardLevel": integer, "num": integer}` | `{"tier": tier, "months": num}` |
| `SUPER_CHAT_MESSAGE` | `superChat` | `{"message": string, "priceMilliCny": integer, "time": integer}` | `{"text": message, "amountMilliCny": priceMilliCny, "durationSeconds": time}` |

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
- malformed timestamps (negative, boolean, floating-point, non-finite, or not
  representable as a UTC calendar timestamp);
- non-integer or non-finite money (`NaN`, `Infinity`, `-Infinity`, or any
  non-integer value).

Control characters (U+0000–U+001F) in any string are rejected.

## Fixtures

Representative non-sensitive examples live in `docs/bilibili-fixtures/`:

- `danmaku.json`
- `gift.json`
- `guard.json`
- `super-chat.json`

Each fixture is a single valid recorded envelope. They use synthetic user IDs,
display names, and text; no real account or credential data is present.
