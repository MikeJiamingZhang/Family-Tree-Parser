# Build Spec v2: 族譜 IIIF Harvester

**Supersedes `zupu-capture-spec.md` entirely.** Delete that file. The browser
extension, `chrome.debugger` capture, settle detection, tainted-canvas
workaround, and screenshot quality gating are all obsolete and must not be
built.

Companion to `zupu-project-brief.md`, which holds the domain model, extraction
schema, entity resolution design, and validation invariants. That document is
**unchanged and still authoritative** — everything downstream of image
acquisition is unaffected.

---

## 0. What changed

The `/pdfview/` React viewer is one of two front-ends over the same content. The
other is **Mirador**, a standard IIIF viewer, and it exposes the collection
through the plain IIIF Presentation and Image APIs:

```
Presentation manifest
  http://fullimg.library.sh.cn/manifests/jiapu/STJP014805.json

Per-canvas metadata
  http://fullimg.library.sh.cn/manifests/jiapu/STJP014805/canvas/{imageId}.json

Image API service
  http://fullimg.library.sh.cn/iiif/jiapu/STJP014805/{imageId}.tif
      /{region}/{size}/{rotation}/default.jpg
```

No token. No authentication. No cross-origin restriction. **1,280 page images**
in this work.

IIIF exists precisely so that third parties can retrieve images
programmatically; using it as documented is the sanctioned path, not a
workaround. Conduct requirements in §4 still apply — 1,280 requests is still a
crawl.

---

## 1. Milestone 0 (revised): read `info.json`

Replaces all prior canvas-measurement calibration. One request:

```
http://fullimg.library.sh.cn/iiif/jiapu/STJP014805/{anyImageId}.tif/info.json
```

Returns the IIIF Image Information document. Record:

| field | meaning |
|---|---|
| `width`, `height` | **true native resolution of the scan** |
| `sizes` | pre-generated derivative sizes the server will serve cheaply |
| `tiles` | tile size and scale factors |
| `profile` / `extraFeatures` | which region and size parameters are supported |

**Acceptance gate**, using the small annotation tier as before:

```
px_per_small_glyph ≈ (width / columns_per_page) × 0.35     # ~10 columns typical
```

Under 18 is unusable; 18–28 marginal; over 28 proceed. Record the measured value
in `calibration.md`.

Check the profile before assuming `full/max` works. IIIF Level 0 servers serve
only pre-generated sizes; Level 1 and 2 support arbitrary region and size
requests. If `max` is unsupported, take the largest entry in `sizes`.

---

## 2. Architecture

```
manifest.json ──▶ canvas list (1280 pages, labels, order)
                        │
                  info.json per image  (cache; likely uniform across the work)
                        │
                  image fetch  ──▶ data/images/{imageId}.jpg
                        │
                  extraction worker (Pass A/B/C — see project brief §3.2)
                        │
                  assertions ──▶ entity resolution ──▶ graph
```

Four small components. No browser involvement at any stage.

### 2.1 Manifest harvester

Fetch the manifest once, cache to disk, parse the canvas list. For each canvas
record: `imageId`, sequence index, canvas label, and the image service base URL.

The manifest is the **authoritative page ordering and page count** — better than
the viewer's paging, which was only ever a proxy for it. Check `structures` /
`ranges` in the manifest: if the library encoded the 冊 divisions or a table of
contents there, that is volume segmentation for free, and it replaces the
guesswork in project brief §10.

### 2.2 Image fetcher

Serial, rate-limited, resumable. Skip anything already on disk. Cache by
`imageId`, which is stable and globally unique — no hashing needed.

Request the largest size the profile supports:

```
{base}/{imageId}.tif/full/max/0/default.jpg
```

### 2.3 Region requests replace local cropping

**The most useful consequence of this discovery.** IIIF supports server-side
region extraction:

```
{base}/{imageId}.tif/{x},{y},{w},{h}/{size}/0/default.jpg
```

So the column-strip cropping that Pass A needs (project brief §3.2) can be
requested directly rather than downloading a full page and cropping locally.
Two benefits: strips arrive at exactly the pixel dimensions the vision model
should receive, so no resampling loss; and the page-boundary auto-crop that was
mandatory under the screenshot approach becomes unnecessary — there is no
letterbox in an IIIF response.

Suggested flow: fetch the full page once at moderate size for layout detection
and page-type triage, compute column boundaries by ink projection, then request
each column strip at full resolution as its own region request. Cache both.

If the profile turns out to be Level 0, region requests are unavailable —
fall back to fetching full pages and cropping locally with Pillow.

### 2.4 Extraction worker

**Unchanged from the project brief.** Passes A / B / C, the tool schema, the
prompts, and the assertion store are all as specified there. The only
difference is that images arrive from disk via the fetcher rather than from a
capture queue.

Keep the `UNIQUE(capture_id, pass, prompt_version)` constraint — re-runnability
matters just as much here.

### 2.5 Operator UI

Still worth building, but simpler. Coverage is now known up front from the
manifest, so the coverage strip becomes a straightforward progress view rather
than a gap-detection safety net. Priority order: review queue for flagged
extractions, side-by-side image and assertions, merge/split controls, graph view.

---

## 3. Schema changes

```sql
CREATE TABLE pages (
  image_id TEXT PRIMARY KEY,        -- from the manifest; stable and unique
  seq INT,                          -- manifest order
  canvas_label TEXT,
  volume TEXT,                      -- from manifest structures if present
  native_w INT, native_h INT,       -- from info.json
  image_path TEXT,
  page_type TEXT,                   -- 封面|序|凡例|世系|齒錄|blank, from triage
  status TEXT DEFAULT 'pending'
);

CREATE TABLE regions (              -- column strips fetched via IIIF region
  id INTEGER PRIMARY KEY,
  image_id TEXT REFERENCES pages(image_id),
  x INT, y INT, w INT, h INT,
  requested_size TEXT,
  path TEXT
);
```

`extractions` is unchanged. The `captures` table is deleted — there are no
captures any more, only pages.

---

## 4. Conduct

Unchanged in principle from project brief §3.1b, and still binding:

- **Serialise.** One request at a time, ≤1/second, slower preferred. 1,280 pages
  plus region requests is a real crawl of a public research library. Run it
  overnight; there is no deadline.
- **Backoff and stop** on 429 or 5xx. Never retry through a rate limit.
- **Cache permanently.** Every image fetched exactly once, ever.
- **Identify yourself** in the `User-Agent` with a working contact address.
- **Do one 冊 first**, verify the pipeline, then continue.
- **Do not redistribute the images.** The extracted genealogical data is family
  history; the page scans remain the library's to publish.
- Note that the IIIF host is plain `http`. Do not assume the absence of TLS
  implies the absence of policy — check the terms of use.

**Still worth emailing them.** Shanghai Library runs a crowdsourced 在线识谱
transcription programme precisely because these documents resist OCR. A
descendant producing a careful transcription of `STJP014805` is doing something
they have publicly said they want. Using the public IIIF API is legitimate;
telling them what you are doing is better, and may get you the volume structure,
existing partial transcriptions, or collaboration.

---

## 5. Cost, at the now-known page count

1,280 pages. Opus 5 at \$5/\$25 per MTok, Batch API 50% discount, four column
strips per page.

| stage | cost |
|---|---|
| Pass A — transcription | ~\$45 |
| Pass B — structural parse | ~\$40 |
| Pass C — targeted re-reads (~20%) | ~\$9 |
| **one full run** | **~\$94** |
| **three full runs** (realistic, with prompt iteration) | **~\$283** |

A Haiku 4.5 triage pass classifying page type first costs ~\$5 and keeps the
expensive passes off covers, blanks, and front matter.

Human review remains the real constraint: ~1,280 pages at a minute each is over
20 hours. Prioritise by confidence and validation failure.

---

## 6. Build order (revised)

1. **Read `info.json`.** Record native dimensions. Apply the acceptance gate.
2. Manifest harvester. Parse 1,280 canvases; check `structures` for 冊 divisions.
3. Image fetcher: serial, rate-limited, resumable, cached. Fetch 20 pages only.
4. Verify those 20 visually. Confirm resolution on a 世系 chart page.
5. Hand-transcribe 3 pages fully. Still non-negotiable — it is the only way to
   evaluate any later change.
6. Haiku triage pass for page type over the 20.
7. Column detection + IIIF region requests for strips.
8. Pass A. Compare against ground truth. Iterate on strip geometry before prompt
   wording.
9. Pass B, schema from the project brief.
10. Full 第1册, then remaining volumes.
11. Entity resolution — project brief §5.

---

## 7. Gotchas

| Problem | Handling |
|---|---|
| `full/max` returns 400 or a small image | Level 0 server. Use the largest entry in `sizes` from `info.json`. |
| Region requests rejected | Also Level 0. Fetch full pages, crop locally with Pillow. |
| `info.json` differs per page | Do not assume uniform dimensions. Fetch and cache per image; store `native_w`/`native_h` per page row. |
| Mixed `http`/`https` | The IIIF host is `http`. Do not silently upgrade — it may not have a valid certificate. Handle explicitly. |
| Manifest is IIIF Presentation 2 vs 3 | Structure differs (`sequences`/`canvases` vs `items`). Detect from `@context` and handle both. |
| 1,280 pages ≠ 1,280 leaves | Covers, blanks, and inserts are included. Triage before spending on extraction. |
| Rate limiting mid-run | Backoff and stop. Resume later from cache; never hammer. |
