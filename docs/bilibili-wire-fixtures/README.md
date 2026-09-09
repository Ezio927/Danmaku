# Bilibili wire fixtures

These fixtures are representative, non-sensitive recorded Bilibili WebSocket
frames. Each JSON file documents one frame: its `name`, `description`,
`protocolVersion`, `operation`, `sequence`, and the exact `frameHex` bytes (hex
encoding of the full 16-byte header plus body). No credentials, cookies, or real
account data are present.

The wire framing and decode rules are documented in
`docs/bilibili-wire-contract.md`; the event envelopes embedded in the message
fixtures are the same recorded envelopes as `docs/bilibili-fixtures/*.json`.

| Fixture | Operation | Version | Contents |
| --- | --- | --- | --- |
| `heartbeat.json` | 2 heartbeat | 1 | empty keep-alive body |
| `heartbeat-reply.json` | 3 heartbeat-reply | 1 | 4-byte online-popularity body (12345) |
| `message-plain-danmaku.json` | 5 message | 0 plain | the danmaku envelope, uncompressed |
| `message-zlib-danmaku.json` | 5 message | 2 zlib | the danmaku envelope inside one compressed inner packet |
| `message-zlib-multi.json` | 5 message | 2 zlib | the danmaku and gift envelopes inside two concatenated inner packets |
| `malformed-truncated.json` | 5 message | 1 | header declares 100 bytes but only the 16-byte header is present |
| `unsupported-operation.json` | 99 unknown | 1 | an unknown operation, classified explicitly |

## Regenerating the compressed fixtures

The zlib-compressed bytes are stable to decode across zlib versions, but the
`frameHex` values were generated with Python 3.12's standard `zlib.compress`
defaults. They are checked in as recorded bytes; the decode tests only verify
that these bytes decompress to the expected envelopes, not that re-encoding
reproduces the exact same bytes.
