# Build Spec: 族譜 Visual Capture Pipeline

Companion to `zupu-project-brief.md`, which holds the domain model, extraction
schema, entity resolution design, and validation invariants. **Read that first.**
This document covers only the capture half: getting page images off the screen
and into the extraction queue.

---

## 0. What this is

The operator sits and clicks through a page-image viewer at reading speed. A
browser extension silently captures each page as it settles, deduplicates it,
and posts it to a local server. The server queues captures and works through
them with a vision model at its own pace — minutes or hours behind the operator,
who never waits for anything.

```
  operator clicks ──▶ [extension] ──▶ POST ──▶ [local server] ──▶ SQLite queue
   (reading speed)      capture on              dedupe +              │
                        settle, <50ms           quality gate          ▼
                                                              [extraction worker]
                                                               Pass A / B / C
                                                                      │
                                                                      ▼
                                                              assertions ──▶ graph
```

**Design constraint that drives everything: capture must never block the
operator.** Target under 50 ms in the content script. No network waits, no
hashing of full images inline, no model calls. Everything expensive happens
downstream.

### Why the visual approach

Deliberate, not a fallback. It makes zero requests the browser wasn't already
making, so it is unaffected by session-token expiry, tile URL schemes, or
crawler traps. It is literally reading the operator's own screen. It is also the
approach with the least ambiguity about whether it is acceptable — the operator
is a human reading a book they have legitimate access to.

The extension must therefore **never navigate on its own**, never follow links,
and never issue requests to the host beyond what the page already does.

---

## 1. Milestone 0: resolution calibration (do this before writing code)

The single question that determines feasibility. **Do not skip to building.**

The viewer runs in a cross-origin iframe (`dhapi.library.sh.cn/jp/full-img/...`)
embedded in the catalogue page. All console work must target that frame — use
the Console context dropdown.

OpenSeadragon sizes its canvas backing store to `CSS viewport × devicePixelRatio`
and requests pyramid tiles matching that resolution. **The emulated viewport is
the resolution dial.**

Procedure:

1. In the iframe context, record the baseline:
   ```js
   const c = document.querySelector('.openseadragon-canvas canvas');
   console.log(c.width, c.height, devicePixelRatio);
   ```
2. DevTools → device toolbar → custom device, `2400 × 3200`, DPR 2. Reload.
3. Re-measure. Repeat at DPR 3, and at `3600 × 4800`.
4. Find the point where the canvas stops growing, or where zooming to 100% shows
   no new detail. **That plateau is the source's native resolution.** Record it.
5. Export one capture at the best setting and measure characters per pixel on
   the **small annotation tier** — the 字/號 and biographical notes below each
   name, not the large 諱.

### Acceptance gate

| px per small glyph | verdict |
|---|---|
| < 18 | Unusable. Names only, no dates or relationships. Stop and reconsider. |
| 18–28 | Marginal. Viable only with per-column cropping in Pass A. |
| > 28 | Good. Proceed. |

Modelled expectations (10 columns/page, small tier ≈ 0.35 × column width):

| setup | canvas | px/small glyph |
|---|---|---|
| default window | 685 × 847 | ~24 — marginal |
| fullscreen 1440p | 1200 × 1400 | ~42 — good |
| emulated 2400×3200 | 2400 × 3200 | ~84 — good |
| emulated + DPR 2 | 4800 × 6400 | ~168 — likely past native; wasted tokens |

Bigger is not automatically better. Past the pyramid's native resolution you get
upscaled blur at 3× the token cost. Find the plateau and sit just under it.

**Record the chosen viewport/DPR in config.** Every capture must be taken at the
same setting, or downstream crops and coordinates drift.

---

## 2. Extension (Manifest V3)

### 2.1 Structure

```
extension/
  manifest.json
  background.js        service worker: capture fallback, POST to local server
  content.js           injected into the dhapi iframe: settle detection
  popup.html/js        live status: captured count, queue depth, last page
```

Content script must match the **iframe** origin and run with `all_frames: true`.
Matching only the parent catalogue page will silently capture nothing — this is
the most likely first bug.

Permissions: `activeTab`, `scripting`, `storage`, and `tabs` (for the
`captureVisibleTab` fallback). Host permissions limited to
`https://dhapi.library.sh.cn/*`. Nothing broader.

### 2.2 Capture path — CONFIRMED: canvas is tainted

`canvas.toDataURL()` **throws `SecurityError` on this viewer.** Verified. The
tiles are served without `Access-Control-Allow-Origin`, so the canvas is
permanently tainted and JavaScript can never read its pixels. This is ordinary
CORS behaviour, not an access-control measure.

**Do not build the `toDataURL` path.** Capture must happen at the compositor
level, where tainting does not apply.

#### Preferred: `chrome.debugger` + `Page.captureScreenshot`

The plain `chrome.tabs.captureVisibleTab()` fallback works, but it captures only
the physical viewport at the physical device pixel ratio — so achievable
resolution would be capped by the operator's actual monitor. The Chrome DevTools
Protocol removes that ceiling:

```js
await chrome.debugger.attach({ tabId }, '1.3');

// Set a large logical viewport and DPR regardless of the real screen.
await chrome.debugger.sendCommand({ tabId }, 'Emulation.setDeviceMetricsOverride', {
  width: 2388, height: 3154, deviceScaleFactor: 1, mobile: false,
});

const { data } = await chrome.debugger.sendCommand({ tabId }, 'Page.captureScreenshot', {
  format: 'png',
  captureBeyondViewport: true,
  clip: { x, y, width, height, scale: 1 },   // canvas bounding rect from the content script
});
```

Properties that matter:

- Reads composited pixels, so **tainting is irrelevant**.
- `Emulation.setDeviceMetricsOverride` sets the viewport programmatically —
  the operator does not need a large monitor, and does not need DevTools open.
- Setting the metrics override causes OpenSeadragon to resize its canvas and
  request higher pyramid levels, which is exactly the mechanism validated in
  Milestone 0.
- `clip` crops to the canvas, avoiding browser chrome and viewer UI.

Costs: requires the `debugger` permission, and Chrome/Edge displays a persistent
"is debugging this browser" banner for the session. Acceptable for a personal
tool; note it in the README so the operator is not alarmed.

Set the metrics override **once at session start**, not per capture — changing it
forces OSD to re-render and re-fetch tiles, which is slow and adds host load.

#### Fallback: `chrome.tabs.captureVisibleTab`

Simpler, no debugger permission, no banner. Capped at the physical viewport ×
real DPR. Viable only if Milestone 0 shows the operator's actual screen already
clears the resolution gate. Implement behind the same interface so the two are
swappable via config.

#### Milestone 0 note

For manual calibration only, DevTools' own **right-click canvas node → Capture
node screenshot** produces a full-resolution PNG and bypasses tainting the same
way. Use it to gather calibration samples; it is not an automatable path.

### 2.3 Settle detection — capture when the page is done rendering

Naive triggers fail. OpenSeadragon streams tiles progressively, so firing on a
page-number change captures a half-loaded blur. The viewer instance is not
reachable from the page (bundled React, `OpenSeadragon` is not global), so its
`fully-loaded-change` event is unavailable.

**Constraint: the canvas is tainted**, so `getImageData` and `drawImage`-then-read
are both unavailable. Pixel signatures cannot be computed in the content script.
Settle detection must use signals that do not touch canvas pixels.

Combine three:

```js
const SETTLE_MS = 700;      // quiet period with no tile activity
const MIN_TILES = 1;        // require at least one tile since the last capture

let tilesSinceCapture = 0, quietTimer = null, lastKey = null;

// 1. Network quiescence — observe tile requests without reading pixels.
new PerformanceObserver((list) => {
  for (const e of list.getEntries()) {
    if (e.initiatorType === 'img' || e.initiatorType === 'fetch'
        || e.initiatorType === 'xmlhttprequest') {
      tilesSinceCapture++;
      clearTimeout(quietTimer);
      quietTimer = setTimeout(maybeCapture, SETTLE_MS);
    }
  }
}).observe({ type: 'resource', buffered: true });

// 2. Page identity — from the viewer UI, not the pixels.
function pageKey() {
  const el = document.querySelector('input[value*="/"], .page-indicator');
  return (el?.value || el?.textContent || '') + '|' + (volumeLabel() || '');
}

function maybeCapture() {
  const key = pageKey();
  if (tilesSinceCapture < MIN_TILES) return;
  if (key && key === lastKey) return;        // 3. dedup on page identity
  lastKey = key;
  tilesSinceCapture = 0;
  requestCapture(key);
}
```

Why this shape: tile fetches stop when rendering completes, so a quiet period is
a good proxy for "settled". Requiring at least one tile since the last capture
prevents firing on unrelated requests. Page identity from the UI replaces the
pixel signature as the dedup key.

**Deduplication moves server-side.** The server receives decoded PNG bytes and
can hash them freely — no tainting there. Compute a dHash on arrival and reject
near-duplicates (Hamming ≤ 5). The content script's `pageKey` check is only a
cheap first filter.

Tune `SETTLE_MS` against the actual viewer. Too short captures mid-stream; too
long and a fast operator outruns it. Start at 700 ms and check results against
the blur gate.

### 2.4 Page metadata

Read the page indicator (`13/237`) and the volume selector (`第1册`) from the
**parent** page DOM and pass them down, or scrape them from the iframe if
present. Attach to every capture. Treat them as a hint only — §2.1b of the
project brief establishes that the 版心 page number printed on the leaf is the
authoritative sequence key, and it is extracted later from the image itself.

### 2.5 Transport

POST to `http://localhost:8787/capture` as JSON:

```json
{
  "signature": "a3f9…",
  "image_b64": "iVBORw0…",
  "captured_at": "2026-08-06T15:12:03Z",
  "viewer_page": "13/237",
  "viewer_volume": "第1册",
  "canvas_w": 2400, "canvas_h": 3200,
  "dpr": 2,
  "capture_method": "toDataURL"
}
```

Fire-and-forget. On failure, buffer in `chrome.storage.local` (cap ~50 images)
and retry on the next successful POST. The operator must never see an error
mid-session — surface failures in the popup badge instead.

---

## 3. Local server

Small FastAPI or Express app, SQLite, filesystem for images.

### 3.1 `POST /capture`

Must return in under ~20 ms. Do the minimum inline:

1. If `signature` already in `captures`, increment `seen_count`, return `dedup`.
2. Write PNG to `data/images/{sha256}.png`.
3. Insert row with `status='pending'`.
4. Return `{ok: true, queue_depth: N}`.

Everything else — quality gating, cropping, model calls — happens in the worker.

### 3.2 Quality gate (worker, before extraction)

Cheap rejects that save real money:

- **Blur:** variance of the Laplacian, via OpenCV or a small NumPy kernel. Build
  a threshold empirically from ~20 known-good captures; flag anything in the
  bottom decile rather than hard-rejecting.
- **Near-duplicate:** Hamming distance on a dHash against recent captures.
  Catches the same page recaptured after a slight pan, which the exact-match
  signature misses.
- **Crop to the page:** the canvas may include letterboxing. Detect the page
  boundary by column/row ink projection and crop. Store the crop box; do not
  overwrite the original.

Flagged captures go to a `review` bucket in the operator UI, not the bin. A page
captured badly once and never revisited is a silent hole in the genealogy.

### 3.3 Schema

```sql
CREATE TABLE captures (
  id INTEGER PRIMARY KEY,
  signature TEXT UNIQUE, sha256 TEXT, path TEXT,
  captured_at TEXT, viewer_page TEXT, viewer_volume TEXT,
  canvas_w INT, canvas_h INT, dpr REAL, capture_method TEXT,
  seen_count INT DEFAULT 1,
  blur_score REAL, ink_ratio REAL, crop_box TEXT,
  status TEXT DEFAULT 'pending'   -- pending|ok|flagged|extracted|failed
);

CREATE TABLE extractions (
  id INTEGER PRIMARY KEY,
  capture_id INT REFERENCES captures(id),
  pass TEXT,                      -- A|B|C
  prompt_version TEXT,
  model TEXT,
  payload TEXT,                   -- JSON
  input_tokens INT, output_tokens INT,
  created_at TEXT,
  UNIQUE(capture_id, pass, prompt_version)
);
```

The `UNIQUE(capture_id, pass, prompt_version)` constraint is what makes the whole
thing re-runnable: bump the prompt version and re-extract without touching
anything already done at the old version.

### 3.4 Worker

Independent process, polls for `status='ok'`. Concurrency 2–4. Exponential
backoff on API errors. **Prompt-cache the system prompt** — it is large and
identical across every page, so caching cuts input cost by ~90% on the dominant
term.

Passes A / B / C, the tool schema, and the extraction prompts are specified in
§3.2 of the project brief. Do not re-derive them here.

### 3.5 Operator UI

Single page at `localhost:8787`:

- Live counters: captured, queued, extracted, flagged.
- **Coverage strip** — the highest-value widget. Volume × page grid, one cell
  per page, coloured by status. The operator instantly sees which pages were
  missed and can go back and re-click them. Without this you find the gaps after
  the browsing session, when re-finding them is tedious.
- Recent captures with thumbnails and blur scores.
- Flagged queue with side-by-side image and extraction.

---

## 4. Build order

1. **Milestone 0 calibration.** Numbers recorded, gate passed. *Nothing else
   starts until this is done.*
2. Local server: `POST /capture`, dedup, SQLite, image storage. Test with `curl`.
3. Extension: manifest, iframe injection, settle detection, POST. Verify by
   clicking through 20 pages and confirming 20 rows and 20 sane PNGs.
4. Quality gate + crop, run over those 20.
5. Hand-transcribe 3 pages fully. Tedious, non-negotiable — it is the only way
   to evaluate any later change.
6. Pass A on the 20. Compare against ground truth. Iterate on cropping strategy
   before touching prompt wording; cropping dominates.
7. Pass B, schema from the project brief.
8. Coverage strip UI.
9. Full 第1册 run.
10. Entity resolution — see project brief §5.

Steps 1–6 are where the project succeeds or fails. Steps 7 onward are ordinary
engineering.

---

## 5. Gotchas

| Problem | Handling |
|---|---|
| Content script not injected into the iframe | `all_frames: true` and match the `dhapi.library.sh.cn` origin, not the catalogue origin. Verify by logging `location.href` from the content script. |
| `toDataURL` throws `SecurityError` | Tainted canvas. Latch the probe result at startup and switch to `captureVisibleTab` + crop. Build both paths from the start. |
| Captures are blurry | Settle frames too few, or captured mid-tile-stream. Raise `SETTLE_FRAMES`, and confirm the blur gate is actually catching them. |
| Everything captured at the wrong size | Device emulation reset on reload. Pin viewport/DPR in config and assert on every capture that `canvas_w` matches; warn loudly in the popup on mismatch. |
| Duplicate rows for one page | Signature is too sensitive — antialiasing jitter between renders. Fall back to dHash + Hamming ≤ 5 rather than exact signature match. |
| Missed pages | Operator clicked faster than settle time. The coverage strip is the safety net; also consider a popup warning when page N+1 arrives before page N has settled. |
| Session token expires mid-browse | Only affects the viewer, not the extension. The operator reloads; capture continues. Do not attempt to refresh or persist tokens. |
| Service worker sleeps (MV3) | Keep the worker stateless. All state lives in the local server, not the extension. |

---

## 6. Notes for the ML-interesting parts

The OCR is the boring half — a vision model with good cropping does most of it.
The parts with actual research texture:

- **Entity resolution under name collision.** Same person under 諱 / 字 / 行第
  across four mentions; different people sharing a name across branches.
  Blocking + pairwise scoring, with the constraint that a false merge grafts one
  branch onto another and is near-undetectable downstream, while a false split
  is obvious and cheap to fix. Strongly asymmetric loss — worth designing the
  threshold around explicitly.
- **Active learning on the review queue.** Human adjudications on merge
  decisions are labels. Use them to fit the pairwise scorer rather than
  hand-tuning weights, and order the queue by expected information gain instead
  of raw confidence.
- **Calibration.** Model self-reported confidence is not calibrated out of the
  box. With 3 pages of ground truth you can fit a reliability curve and remap it,
  which materially improves review triage.
- **Constraint propagation.** The 字輩 poem, sibling counts, sexagenary/reign-year
  redundancy, and generational birth-year monotonicity form a constraint system
  over the extracted graph. Propagating them can correct OCR errors the vision
  model had no way to catch — a low-confidence character that only has one
  reading consistent with its generation is effectively solved.

That last one is the genuinely satisfying bit: the document's own internal
redundancy is strong enough to repair its own transcription.
