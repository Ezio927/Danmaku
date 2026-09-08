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

Expected result: all four cases pass. The WebSocket case asserts four
`message.created` increments whose kinds cover `danmaku`, `gift`, `guard`, and
`superChat` in the deterministic cyclic order, with strictly increasing
sequences and the fixture-equivalent per-kind payloads. The remaining cases
drive the real packaged `/obs` page and its assets over the loopback service.

```bash
.venv/bin/python -m unittest discover -s tests -p "test_*.py" -v
```

Expected result: the full suite (166 cases as of this run) passes. This is the
"full" verification profile for the Task.

### Observed evidence (recorded by this task)

- `tests.test_vertical_slice.VerticalSliceE2ETests.test_observes_four_ordered_kinds_over_real_websocket` — **PASS**, stable across repeated runs.
- `tests.test_vertical_slice.VerticalSliceE2ETests.test_real_service_serves_health_and_packaged_obs_page` — **PASS**.
- `tests.test_vertical_slice.VerticalSliceE2ETests.test_obs_references_only_assets_that_return_200` — **PASS**: parses the served `/obs` HTML and asserts every referenced `/assets/*` resource returns 200 over the real service.
- `tests.test_vertical_slice.VerticalSliceE2ETests.test_assets_serve_correct_content_types` — **PASS**: `/assets/app.js` serves `text/javascript` and `/assets/style.css` serves `text/css`.
- `tests.test_service.ServiceIntegrationTests.test_unknown_and_traversal_assets_404` — **PASS**: `app.js`/`style.css` serve 200 while unknown and traversal-like paths remain 404.
- `tests.test_message_model.CanonicalConstructionTests` — **PASS**: direct `User`/`Message` construction goes through canonical validation, and invalid instances fail to construct.
- `tests.test_distribution.DistributionHubOrderingTests` — **PASS**: the hub rejects duplicate IDs and non-strictly-increasing sequences before snapshot/broadcast, including against existing snapshot state.
- `tests.test_protocol` HEAD/405 cases — **PASS**: `HEAD` and every non-`GET` method return 405 on all four frozen `GET` routes.
- `tests.test_installation` — **PASS**: `pyproject.toml` declares the web assets as package data and the `python -m danmaku` entry point parses its documented startup flags.
- Full `unittest discover` — **166/166 OK**.

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

## Evidence separation

| Evidence | Kind | Recorded by |
| --- | --- | --- |
| Four ordered kinds over the real loopback WebSocket | automated | this task (PASS) |
| Packaged `/obs` page references only assets that return 200 | automated | this task (PASS) |
| `/assets/app.js` and `/assets/style.css` content types | automated | this task (PASS) |
| Canonical-valid construction; hub duplicate-ID/sequence rejection; HEAD→405 | automated | this task (PASS) |
| Installation/package-data/entry-point checks | automated | this task (PASS) |
| Full project verification | automated | this task (166/166 PASS) |
| Transparent rendering, bottom anchor, four visible variants | manual visual | **not performed** — pending operator |
| OBS Browser Source automation | manual / tooling | **not claimed** |

Do not treat the passing automated tests as proof of a visual check; a browser
or OBS screen is the only valid evidence for the visual row.
