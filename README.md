# Danmaku — First Vertical Slice

A loopback-only HTTP/WebSocket service that composes the canonical core with
`aiohttp` and serves the frozen protocol v1.

## Requirements

- Python 3.12
- `aiohttp` (the sole runtime dependency)

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install .
```

This installs the `danmaku` package from this `src/`-layout project together
with its sole runtime dependency (`aiohttp`), and packages the OBS web assets
(`index.html`, `app.js`, `style.css`) as package data so `python -m danmaku`
serves them from an installed environment.

## Run

```bash
.venv/bin/python -m danmaku --port 17391 --cadence-milliseconds 1000
```

If the configured port is already in use, startup fails fast: it prints a
concise error naming `127.0.0.1` and the configured port to standard error and
exits with a non-zero status, never silently choosing another port.

The service binds exactly `127.0.0.1` and exposes:

- `GET /health` — exact `{"protocolVersion":1,"status":"ok"}`
- `GET /obs` — OBS page (served from the packaged asset root)
- `GET /host` — compact Host monitoring page (complete, unfiltered timeline)
- `GET /assets/{name}` — allow-listed packaged assets
- `GET /ws` — snapshot-first WebSocket with ordered `message.created` increments
- `GET /ws/host` — snapshot-first Host WebSocket with the full 1000-message
  canonical host snapshot and unfiltered increments
- `GET /host/settings` — the current non-secret configuration as JSON
- `POST /host/settings` — validate and atomically persist a candidate
  configuration (reports `restartRequired` on success)
- `POST /host/deny` — validate and atomically persist a single OBS deny-list
  entry (`denyUserIds` or `denyNicknames`) for a Host timeline message (reports
  `restartRequired` on success)

The default OBS URL is `http://127.0.0.1:17391/obs`. The default Host URL is
`http://127.0.0.1:17391/host`. See `docs/host-monitoring.md` for the Host
surface and its settings panel.

## Configuration

Startup configuration — including the OBS filtering settings (denied user IDs
and nicknames, denied keywords, and the ordinary-gift threshold) — is persisted
as a versioned JSON file at a deterministic local location
(`~/.config/danmaku/config.json` on Linux/macOS, `%APPDATA%\danmaku\config.json`
on Windows; override with `$DANMAKU_CONFIG` or `--config PATH`). See
`docs/configuration-schema.md` for the full schema, strict rejection rules,
backup and fallback behavior, and the documented precedence:

1. canonical defaults;
2. persisted values from the JSON file;
3. explicit CLI overrides (`--port`, `--cadence-milliseconds`,
   `--gift-threshold-milli-cny`).

A missing file loads canonical defaults (including the `100` milli-CNY gift
threshold and empty deny lists). A corrupt file falls back to the single
retained backup, then to defaults, and prints a diagnostic to standard error.
Saves use a same-filesystem temporary file and an atomic rename, retaining one
previously valid backup.

The Host page (`/host`) includes a loopback-only settings panel for editing the
six editable non-secret fields (`service.port`, `mock.cadenceMilliseconds`,
`obs.denyUserIds`, `obs.denyNicknames`, `obs.keywords`, and
`obs.giftThresholdMilliCny`). A save validates the candidate with
`ServiceConfig` before persisting it atomically and reports that a restart is
required to apply it; `service.host` stays `127.0.0.1` and
`snapshot.maxMessages` stays `100`.

Each Host timeline item also exposes two accessible context actions — **Block
user** (deny the stable `user.id`) and **Block nickname** (deny the normalized
`user.name`). These post to `POST /host/deny`, which merges the single entry
into the named deny list, revalidates the complete configuration, and persists
it atomically; the running policy and canonical Host timeline are never changed,
so a save also reports that a restart is required.

The Host page also includes a read-only **OBS setup** panel that shows the
existing loopback OBS URL (`http://127.0.0.1:<port>/obs`) and copies it through
the browser clipboard for an OBS Browser Source. It is purely client-side: it
adds no route, changes no OBS protocol or filtering, and reports fixed
success/failure feedback without surfacing exceptions or user content. The same
panel shows a deterministic, local-only preview of the four OBS message kinds
(danmaku, gift, guard, and Super Chat), rendered from hardcoded synthetic
samples with the existing overlay presentation styles and never sent to the
core, OBS, or the network. See `docs/host-monitoring.md` for details.

Each Host timeline item also exposes three view-local affordances — **Copy
username** (copy the canonical `user.name`), **Copy text** (copy the canonical
`data.text`, for danmaku and Super Chat), and **Details** (a concise read-only
panel built only from existing canonical fields). They use the browser clipboard
seam and DOM text APIs only, report fixed success/failure feedback without
surfacing exceptions or user content, send no protocol frame, and never mutate
canonical Host state, OBS delivery, filtering, or the deny-list actions.

## Test

Run the full suite:

```bash
.venv/bin/python -m unittest discover -s tests -p "test_*.py" -v
```

Run only the end-to-end slice acceptance test, which drives the real loopback
`aiohttp` Service and observes four ordered deterministic mock messages (one of
each kind) over the real WebSocket:

```bash
.venv/bin/python -m unittest tests.test_vertical_slice -v
```

## Smoke

For the full local runbook — including how automated transport evidence is kept
separate from a manual browser/OBS visual check — see
`docs/phase8-pilot-smoke.md`.
