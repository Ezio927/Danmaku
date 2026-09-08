---
name: first-vertical-slice
description: Use for Danmaku core, local service, or OBS work in the frozen First Vertical Slice.
---

# First Vertical Slice

Read `docs/architecture.md`, `docs/detailed-design.md`, and the Task scope. For
wire-format work, also read `docs/message-model.md`, `docs/api-protocol.md`, and
every `docs/protocol-fixtures/*.json` file.

Preserve these invariants:

- Python 3.12, one process, one asyncio event loop; `aiohttp` is the only approved
  runtime web dependency.
- Bind exactly `127.0.0.1`; no credentials, real Bilibili connection, database,
  deployment, or desktop UI.
- Treat the protocol fixtures as golden v1 examples. Reject unknown frame keys
  and unsupported versions; do not add optional wire fields informally.
- Keep buffers bounded and mock output deterministic. OBS inserts untrusted text
  only with DOM text APIs, never `innerHTML`.
- Add tests with behavior changes and run the Task-selected fast/full profile.

Changing the frozen model or protocol requires a dedicated design decision and
serialized review by all consuming Tasks.

