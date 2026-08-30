# Phase 8 — pilot smoke runbook

This runbook captures how to validate the integrated first vertical slice
locally and how to record evidence honestly. It keeps **automated transport
evidence** strictly separate from the **manual browser/OBS visual check** so
that no automated test is ever described as a human visual confirmation.

## Prerequisites

- Python 3.12 in `.venv` (see `README.md` for setup).
- The frozen v1 contract is `docs/api-protocol.md` plus
  `docs/protocol-fixtures/*.json`; do not redefine it during a smoke run.

## 1. Automated evidence (what this worktree actually ran)

The end-to-end test drives the real loopback `aiohttp` Service on an ephemeral
port and observes messages that flowed `MockSource -> DistributionHub ->
aiohttp WebSocket -> protocol v1`. No `TestClient` or manual publish is used.

```bash
.venv/bin/python -m unittest tests.test_vertical_slice -v
```

Expected result: both cases pass, and the WebSocket case asserts four
`message.created` increments whose kinds cover `danmaku`, `gift`, `guard`, and
`superChat` in the deterministic cyclic order, with strictly increasing
sequences and the fixture-equivalent per-kind payloads.

```bash
.venv/bin/python -m unittest discover -s tests -p "test_*.py" -v
```

Expected result: the full suite (all 135 cases as of this run) passes. This is
the "full" verification profile for the Task.

### Observed evidence (recorded by this task)

- `tests.test_vertical_slice.VerticalSliceE2ETests.test_observes_four_ordered_kinds_over_real_websocket` — **PASS**, stable across repeated runs.
- `tests.test_vertical_slice.VerticalSliceE2ETests.test_real_service_serves_health_and_packaged_obs_page` — **PASS**.
- Full `unittest discover` — **135/135 OK**.

## 2. Manual browser/OBS visual check (not performed here)

This task did **not** open a browser or OBS and makes no visual claim. The
following steps are the manual procedure for a human operator; their result
must be recorded by that operator, not inferred from the automated tests above.

1. Start the service:

   ```bash
   .venv/bin/python -m danmaku --port 17391 --cadence-milliseconds 1000
   ```

2. Confirm the health endpoint before anything visual:

   ```bash
   curl -s http://127.0.0.1:17391/health
   # expected exact body: {"protocolVersion":1,"status":"ok"}
   ```

3. Open `http://127.0.0.1:17391/obs` in a browser, then add it as an OBS
   Browser Source and check: transparent background, bottom-anchored messages,
   four visibly distinct variants in stable order, text wrapping on resize, and
   no scrollbar or page chrome.

4. Stop and restart the service and confirm the page stays visually transparent
   while disconnected, then shows a replacement snapshot plus new increments.

### Known blocking issue for step 3 (observed, out of scope to fix here)

The packaged page served at `/obs` (`index.html`) references `/assets/style.css`
and `/assets/app.js`, but the service asset allow-list in
`src/danmaku/server/handlers.py` only admits `index.html`, `obs.js`, and
`obs.css`. A programmatic check over the real service shows:

```text
/obs                  200 text/html
/assets/index.html    200 text/html
/assets/app.js        404
/assets/style.css     404
```

Consequently the browser page will not yet load its stylesheet or client script,
so the transparent, message-rendering visual smoke is **blocked** until the
asset allow-list is reconciled with the packaged filenames. This is a
cross-slice integration gap owned by the local-service / OBS-page Tasks and is
outside this Task's `src/**` scope. The automated transport evidence in
section 1 is unaffected and remains green.

## Evidence separation

| Evidence | Kind | Recorded by |
| --- | --- | --- |
| Four ordered kinds over the real loopback WebSocket | automated | this task (PASS) |
| Full project verification | automated | this task (135/135 PASS) |
| Transparent rendering, bottom anchor, four visible variants | manual visual | **not performed** — pending operator + asset fix above |
| OBS Browser Source automation | manual / tooling | **not claimed** |

Do not treat the passing automated tests as proof of a visual check; a browser
or OBS screen is the only valid evidence for the visual row.
