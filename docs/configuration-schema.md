# First Vertical Slice configuration

Configuration is a single local JSON file with an explicit `configVersion`,
persisted at a deterministic documented location and validated strictly on
load. It carries the loopback service settings plus the OBS delivery filtering
settings (deny lists and the ordinary-gift threshold).

## Schema (current version)

```json
{
  "configVersion": 1,
  "service": {"host": "127.0.0.1", "port": 17391},
  "mock": {"cadenceMilliseconds": 1000},
  "snapshot": {"maxMessages": 100},
  "obs": {
    "denyUserIds": [],
    "denyNicknames": [],
    "keywords": [],
    "giftThresholdMilliCny": 100
  }
}
```

All keys are required and unknown keys are rejected. Values:

- `configVersion`: integer literal `1`. Any other version (including a future
  version) is rejected and the file is treated as invalid.
- `service.host`: constant string `127.0.0.1`; no other address is valid.
- `service.port`: integer 1024–65535; bind conflict is fatal and never selects a
  different port silently.
- `mock.cadenceMilliseconds`: integer 100–60,000.
- `snapshot.maxMessages`: constant integer 100, matching protocol and DOM caps.
- `obs.denyUserIds`: array of strings; matched exactly (IDs are stable, so no
  trimming or casefolding) against `user.id`.
- `obs.denyNicknames`: array of strings; trimmed, Unicode-casefolded, and
  deduplicated at policy build time, then matched for exact equality.
- `obs.keywords`: array of strings; trimmed, Unicode-casefolded, and
  deduplicated at policy build time, then matched as case-insensitive
  substrings of `danmaku`/`superChat` text only.
- `obs.giftThresholdMilliCny`: non-negative integer; ordinary gifts whose
  `totalAmountMilliCny` is below it are suppressed from OBS delivery. Default
  `100` (0.1 CNY).

The deny lists are validated as arrays of strings but are not normalized at
load time; trimming, casefolding, and deduplication happen exactly once, when
the single shared immutable `FilteringPolicy` is built at startup.

## Rejection rules

Malformed JSON, structurally invalid documents, unsupported or future
`configVersion` values, wrongly-typed values (for example a string port or a
non-string deny entry), unknown keys, and out-of-bound values (port outside
1024–65535, cadence outside 100–60,000, negative gift threshold, non-100
`maxMessages`) are all rejected deterministically. Nothing is coerced silently:
a `"100"` string never becomes the integer `100`, and `true` never becomes `1`.

## File location

The primary file is `danmaku/config.json` inside the platform configuration
directory: `$XDG_CONFIG_HOME` (or `~/.config`) on POSIX, `%APPDATA%` on
Windows. The exact path can be overridden, in order of precedence, by the
`--config PATH` startup flag or the `DANMAKU_CONFIG` environment variable.

A single backup file `config.json.bak` sits next to the primary and always
holds the most recently valid previous configuration.

## Load and fallback

- Missing primary file → canonical defaults, no diagnostic.
- Valid primary → loaded and used.
- Corrupt or invalid primary → the valid backup is used when available, with a
  diagnostic on standard error; otherwise canonical defaults are used, with a
  diagnostic on standard error.

## Save

Saves are only triggered by an explicit caller (the future settings UI); the
slice never rewrites configuration implicitly. A save serializes the current
configuration, writes it to a temporary file in the same directory, and
atomically renames it over the primary. If the current primary is still valid,
it is first copied to the backup. A write or rename failure raises and leaves
the primary and the last known-good configuration untouched.

## Precedence

Deterministic precedence, lowest to highest:

1. canonical defaults (port `17391`, cadence `1000`, `maxMessages` `100`,
   empty deny lists, gift threshold `100`);
2. persisted values from the JSON file;
3. explicit CLI overrides (`--port`, `--cadence-milliseconds`,
   `--gift-threshold-milli-cny`) when provided.

A value provided on the command line overrides the persisted value for that run
and is still revalidated against the same bounds. The fixed OBS URL remains
`http://127.0.0.1:17391/obs`.
