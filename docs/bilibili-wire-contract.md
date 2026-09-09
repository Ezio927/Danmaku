# Offline Bilibili wire contract

This document defines the deterministic, credential-free decoding contract for
the documented Bilibili live-platform WebSocket binary framing. The codec lives
at `danmaku.bilibili.wire` and reuses the existing recorded-event adapter
(`docs/bilibili-adapter-contract.md`) for event normalization. It performs no
network access, no credential handling, and no live transport.

## Framing

Each recorded frame is a single big-endian packet:

| Offset | Size | Field | Rule |
| --- | --- | --- | --- |
| 0 | 4 | `packetLength` | unsigned int; total length including the 16-byte header; 16..1,048,576 |
| 4 | 2 | `headerLength` | unsigned int; must be exactly 16 |
| 6 | 2 | `protocolVersion` | unsigned int; one of `0`, `1`, `2`, `3` |
| 8 | 4 | `operation` | unsigned int; one of `2`, `3`, `5`, `7`, `8` |
| 12 | 4 | `sequence` | unsigned int; opaque, carried verbatim |

The body occupies bytes 16..`packetLength`. A top-level frame must occupy the
entire buffer exactly: fewer bytes than `packetLength` are `TruncatedPacketError`
and extra trailing bytes are `MalformedFrameError`.

## Operations

| Operation | Value | Classification |
| --- | --- | --- |
| `HEARTBEAT` | 2 | control; never produces a product message |
| `HEARTBEAT_REPLY` | 3 | control; never produces a product message |
| `MESSAGE` | 5 | event payload; extracted into envelopes |
| `AUTH` | 7 | control; classified, body never interpreted |
| `AUTH_REPLY` | 8 | control; classified, body never interpreted |

Any other operation value raises `UnsupportedOperationError`. Control packets
are returned as `ControlFrame` values with their raw body carried but never
parsed into product messages.

## Body encodings (`protocolVersion`)

| Version | Meaning | Behaviour for `MESSAGE` |
| --- | --- | --- |
| `0` plain | uncompressed UTF-8 JSON | the body is one event envelope |
| `1` heartbeat | opaque keep-alive body | rejected for `MESSAGE` |
| `2` zlib | zlib-compressed payload | decompressed, then parsed as one or more inner packets |
| `3` brotli | Brotli-compressed payload | rejected with `UnsupportedProtocolVersionError` |

A zlib body, after decompression, is a stream of concatenated inner packets,
each with the same 16-byte header. Every inner packet is decoded recursively
(plain JSON envelope or nested zlib) up to a nesting depth of 4. Decompressed
output is bounded at 4,194,304 bytes. Inner packets whose declared length
overruns the decompressed stream are `TruncatedPacketError`.

## Envelope extraction

A `MESSAGE` envelope is decoded as strict UTF-8, validated as a single JSON
object, and returned as a `MessageEnvelope` carrying the exact decoded text.
Non-finite JSON numbers (`NaN`, `Infinity`) are rejected. The extracted text is
then normalized by the existing `BilibiliAdapter` boundary through
`BilibiliWireCodec.decode_messages`, which preserves the documented event
identity (`bilibili:` + `eventId`), sequence, timestamp, user, and kind-specific
payload semantics without redefining them.

## Rejection rules

The codec rejects, without silent coercion:

- truncated headers or bodies (`TruncatedPacketError`);
- declared or actual frames above 1,048,576 bytes, or decompressed bodies above
  4,194,304 bytes (`OversizedPacketError`);
- a `headerLength` other than 16, a `packetLength` below 16, trailing bytes,
  non-UTF-8 or non-object JSON bodies, an empty zlib payload, or `MESSAGE` with
  the heartbeat version (`MalformedFrameError`);
- an unknown `protocolVersion` or Brotli (`UnsupportedProtocolVersionError`);
- an unknown `operation` (`UnsupportedOperationError`);
- an invalid or incomplete zlib stream (`PayloadDecodeError`).

Envelope-level validation (unknown `cmd`, wrong types, malformed timestamps,
out-of-bounds money) is performed by the adapter and is unchanged from
`docs/bilibili-adapter-contract.md`.

## Bounds

| Constant | Value |
| --- | --- |
| `HEADER_LENGTH` | 16 |
| `MAX_PACKET_LENGTH` | 1,048,576 bytes |
| `MAX_DECOMPRESSED_LENGTH` | 4,194,304 bytes |
| `MAX_NESTING_DEPTH` | 4 |

## Out of scope

The wire codec adds no credentials, cookies, QR/login, identity codes, auth
payload interpretation, or secret persistence; no live Bilibili/Open Platform
transport; and no Brotli decompression. Protocol v1 local frames, the canonical
message model, the recorded-event adapter, the loopback-only service behaviour,
and the connection lifecycle semantics are unchanged.
