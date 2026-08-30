# Danmaku — First Vertical Slice

A loopback-only HTTP/WebSocket service that composes the canonical core with
`aiohttp` and serves the frozen protocol v1.

## Requirements

- Python 3.12
- `aiohttp` (the sole runtime dependency)

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Run

```bash
.venv/bin/python -m danmaku --port 17391 --cadence-milliseconds 1000
```

The service binds exactly `127.0.0.1` and exposes:

- `GET /health` — exact `{"protocolVersion":1,"status":"ok"}`
- `GET /obs` — OBS page (served from the packaged asset root)
- `GET /assets/{name}` — allow-listed packaged assets
- `GET /ws` — snapshot-first WebSocket with ordered `message.created` increments

The default OBS URL is `http://127.0.0.1:17391/obs`.

## Test

```bash
.venv/bin/python -m unittest discover -s tests -p "test_*.py" -v
```
