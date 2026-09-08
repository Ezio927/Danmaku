# Canonical message model v1

The canonical object is also the `message` object carried by protocol frames.
It has exactly these keys:

| Field | Type and rule |
| --- | --- |
| `id` | string matching `^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$` |
| `sequence` | integer 1 through 9,007,199,254,740,991; strictly increasing per process |
| `receivedAt` | UTC RFC 3339 string `YYYY-MM-DDTHH:MM:SS.mmmZ` |
| `source` | literal `mock` or `bilibili` |
| `kind` | `danmaku`, `gift`, `guard`, or `superChat` |
| `user` | exact object `{"id": string, "name": string}`; ID follows message ID regex, name 1–64 Unicode code points |
| `data` | exact kind-specific object below |

Strings are JSON strings, must be valid Unicode, and must not contain control
characters U+0000–U+001F except no exceptions are permitted after decoding.
Unknown fields, booleans in integer positions, non-finite numbers, and unknown
enum values are invalid.

Kind data:

- `danmaku`: `{"text": string}`; text is 1–500 code points.
- `gift`: `{"giftName": string, "quantity": integer, "totalAmountMilliCny": integer}`;
  name 1–100 code points, quantity 1–1,000,000, amount 0–9,007,199,254,740,991.
- `guard`: `{"tier": "governor"|"admiral"|"captain", "months": integer}`;
  months 1–120.
- `superChat`: `{"text": string, "amountMilliCny": integer, "durationSeconds": integer}`;
  text 1–500 code points, amount 1–9,007,199,254,740,991, duration 1–86,400.

All money is an integer count of one-thousandth CNY; floating point and currency
symbols are forbidden. `receivedAt` is the ordering/audit timestamp. `mock`
messages are deterministic simulation output; `bilibili` messages are produced
offline by the recorded-event adapter (see `docs/bilibili-adapter-contract.md`)
from a validated recorded envelope. v1 carries no platform timestamp or raw
event in either case, and neither source uses live transport. Extensions
require protocolVersion 2; v1 readers reject rather than ignore keys.

