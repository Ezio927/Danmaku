# First Vertical Slice test plan

## Automated acceptance

| Layer | Required cases |
| --- | --- |
| Model unit | exact keys; IDs; UTC millisecond timestamp; Unicode/control rules; integer bounds; each kind; money units; unknown-field rejection; fixture round trips |
| Mock/core unit | deterministic four-kind order; monotonic sequence; snapshot oldest-first cap at 100; publish order; independent subscribers; slow subscriber closed without affecting another; process-lifetime ID uniqueness (evicted IDs remain rejected) |
| Protocol | exact health body; route/method/query behavior; exact hello; snapshot first; all golden frames; unsupported version/type; invalid JSON/binary; close codes; no snapshot/increment connection gap |
| Service integration | loopback bind; bind conflict fatal; one and multiple clients; reconnect replacement snapshot; startup/shutdown cleanup; unknown/traversal asset 404 |
| OBS static/browser | transparent body; no chrome/scrollbar; bottom anchor; four renderers; snapshot no animation; increment append/deduplicate; 100-node cap; hostile text remains text; no `innerHTML`; reconnect schedule |

Implementation Tasks add these tests under their allowed scopes. Fast runs
focused unit/protocol tests plus config/fixture validation and `git diff
--check`. Full runs all unit/protocol/integration tests, compile checks,
config/fixture validation, and `git diff --check`. A missing `tests/` directory
is a known design-Task baseline only; after implementation it is a failure.

## Manual browser/OBS smoke

1. Start the future documented mock-service command with defaults and confirm
   `/health` is exactly healthy at protocolVersion 1.
2. Open `http://127.0.0.1:17391/obs` in a browser and OBS Browser Source.
3. Confirm a transparent background, four visibly distinct variants in stable
   order, bottom anchoring, wrapping on resize, and no scrollbar/page chrome.
4. Stop/restart the service; confirm no visible error while disconnected and a
   replacement snapshot followed by new increments after reconnect.
5. Open a second source and confirm closing it does not disturb the first.

Record automation and visual-smoke evidence separately. This slice does not
claim stress, eight-hour stability, Windows installer, real Bilibili, filtering,
gift merging, super-chat lifecycle, desktop UI, credential, or deployment tests.
The process-lifetime `seen_ids` set grows without bound across a run; its
per-ID memory cost and retained-ID growth are measured in a future eight-hour
qualification rather than asserted here.

