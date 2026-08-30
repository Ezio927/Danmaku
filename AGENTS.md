# Danmaku agent guide

This repository is onboarded to `/home/ezio/agent-workflow`. A persisted Task is
the work order; its allow/deny scope, state, worktree mapping, and real command
exit codes outrank prose instructions.

- Work only in the Harness-registered Task worktree and branch. Run the Python
  workspace guard before writing. Never edit `main` directly.
- Keep the First Vertical Slice loopback-only: bind `127.0.0.1`, accept no
  credentials, and add no database or deployment path.
- The frozen v1 contract is `docs/api-protocol.md` plus
  `docs/protocol-fixtures/*.json`. Consumers must not redefine it.
- `aiohttp` is the approved future runtime dependency. PySide6 is future-only
  and must not be installed for this slice.
- Follow `.agent/skills/first-vertical-slice/SKILL.md` for slice changes and run
  the Task's configured verification profile before handoff.

