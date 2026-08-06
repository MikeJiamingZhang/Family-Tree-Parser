# Project Brief: 族谱 (Zupu) Extraction and Genealogy Reconstruction

## 1. Goal

Build a system that reconstructs a machine-readable family tree from a Chinese
genealogy (族谱) that exists only as page-scan images inside a web viewer with no
download facility.

The operator browses the viewer manually. As pages are displayed, the system
captures each page image, extracts the genealogical content using a vision LLM,
resolves the extracted people against those already known, and incrementally
builds a persistent genealogy graph.

**Success criterion:** a queryable genealogy graph where every assertion traces
back to a specific page image and column, and where conflicts and low-confidence
readings are surfaced for human review rather than silently resolved.

**Explicit non-goal:** a fully automatic, hands-off pipeline. Zupu transcription
is a human-in-the-loop task. The system's job is to do 90% of the labour and be
honest about the remaining 10%.

---

## 2. Domain primer

This section is the most important part of the brief. An implementation that
treats this as generic OCR will produce a corrupt genealogy that looks correct.

### 2.1 Document layout

- **Vertical text, right-to-left columns.** Reading order runs top-to-bottom
  within a column, then right to left across columns. Vision models frequently
  get this backwards. It must be verified, not assumed.
- **Double-leaf binding (線裝).** A single captured image may contain two logical
  pages. Page numbers usually sit in the centre fold (版心), along with the
  volume (卷) and section name.
- **Two dominant content formats**, often both present in one volume:
  - **世系圖 / 世系表** — generation charts. Commonly *five generations per
    chart* (五世一圖). Two classical layouts exist: the 歐式 (Ouyang Xiu) tabular
    style and the 蘇式 (Su Xun) style. Determine which is in use; the parsing
    logic differs.
  - **齒錄 / 行傳** — sequential prose biographies. Denser and more informative
    than the charts, and the primary source of dates, spouses, burial sites,
    and notable deeds.

### 2.1b CONFIRMED for this volume (from sample page, 卷一 世系)

The volume in scope uses the **歐式 五世一圖** layout. Verified from a sample:

- Each page is divided by **horizontal rules into five bands**. **One band = one
  generation.** Within a band, entries are read **right to left**.
- **Two-tier typography carries the semantics, and this is the single most
  important parsing fact in the document:**
  - **Large characters** = the person's 諱 (given name), including the
    generation character.
  - **Small characters set below and beside the name** = the 字/號, plus
    biographical annotation. Spouses, dates, birth rank, adoption notes, and
    line-termination markers all live in this small-character tier.
  - A parser that reads only the large tier will produce a skeleton with names
    and generations but **no dates, no spouses, and no adoption edges** — that
    is, it will look successful while omitting most of the genealogy.
- The **版心** (centre-fold strip) carries, top to bottom: title (`◻氏宗譜`),
  volume (`卷一`), section (`世系`), page number, and the 堂號 (hall name) at the
  foot. Use the page number here as the authoritative sequence key, not the
  viewer's own paging.
- The **outer margin** of each page carries small vertical branch markers of the
  form `◻◻公宗` / `◻◻公居` / `◻◻公裔` — naming the apical ancestor whose
  descendants that page covers. These are high-value linking keys and should be
  extracted as first-class fields, not lumped into free-text notes.
- **Bleed-through from the reverse leaf is visible** on blank areas of the
  sample, as are faint red seal impressions. Both will confuse a naive binarising
  preprocessor.

**Partial 字輩 recovered from the sample**, in consecutive generations:

```
… 錫 → 式 → 仁 → 厚 → 其 …
```

(A parallel `之` character appears in the same position as `其` in some entries;
determine whether this is a branch variant, a 字 rather than a 諱, or a
misreading.) The full poem should be transcribed by hand from the 凡例/譜例 in
the front matter before bulk extraction begins.
- **Front matter matters.** 譜序, 凡例, 譜例, and especially 字輩/派語/行輩 (the
  generation poem) usually appear before the lineage data. Process it first.

### 2.2 Entity conventions

- **One person, many names.** 諱 (taboo/given name), 字 (courtesy name), 號 (art
  name), 行第 (birth rank, e.g. 長子/次子, or ranked forms like 千一, 百二), and
  posthumous titles. The charts may use one name and the 齒錄 another. Name
  alone is never a sufficient identity key.
- **Generation characters.** Under a 字輩 scheme, every member of a generation
  shares a character from a fixed poem. This is a strong signal and a strong
  validator.
- **Spouses.** Notation includes 配 (first wife), 繼/續 (successor wife after
  death or divorce), 副室/側室/庶 (secondary). Women are usually recorded as
  surname + 氏 (張氏, 王氏) with no given name. Occasionally father's name or
  native place is given (某地某公之女), which is the only reliable disambiguator.
- **Adoption and succession.** 出繼, 入嗣, 承祧, 兼祧 — transferring a son to a
  sonless brother or cousin to continue that line. **This is common, not
  exceptional.** The person has both a biological father (生父) and a ritual
  father (嗣父). 兼祧 means one man carries on *two* lines simultaneously.
- **Line termination.** 無嗣 / 止 (no heirs), 殤 / 夭 (died young), 出家, 失考
  (record lost). These are meaningful data, not gaps.
- **Migration.** 遷居某地, 徙 — branch relocations, which often explain why a
  lineage stops appearing in later charts.

### 2.3 Dates

Dates appear as reign era + year, usually with the sexagenary cycle year:
e.g. 康熙三十二年癸酉. The reign-year and the 干支 year **independently encode
the same year** — this redundancy is a free checksum on the OCR. Store the raw
string always; store a converted Gregorian year only as a derived field with its
own confidence.

Do not hand-roll calendar conversion. Use an established mapping table for
reign eras and the 60-year cycle.

---

## 3. Architecture

Four decoupled layers. Decoupling matters because the operator's browsing pace
must not be limited by LLM latency, and because re-running extraction on already
captured pages must be cheap.

```
[1 Capture]  browser extension  ->  raw page images + viewer metadata
                                        |
[2 Extract]  worker queue       ->  per-page structured JSON (assertions)
                                        |
[3 Resolve]  entity resolution  ->  person records + typed relationship edges
                                        |
[4 Review]   local web UI       ->  human adjudication, corrections, export
```

### 3.1 Capture layer

**CONFIRMED:** the viewer is `dhapi.library.sh.cn/pdfview/`, running
**OpenSeadragon** over a **tile pyramid**. Volume selection is a sidebar
(`第1册`–`第8册`); paging is `n/237` within a volume.

#### Why screenshotting is off the table

The DOM shows `<canvas width="685" height="847">` inside
`.openseadragon-canvas`. That canvas is the **viewport**, not the image — it is
sized to the browser window and holds only the currently visible composite.
Screenshotting or calling `toDataURL()` on it yields at most ~685 px of page
width, well under the resolution floor in §3.1a. **The full-resolution data
exists only as individual tile JPEGs behind the canvas.**

#### The capture method: ask OpenSeadragon for its own tile URLs

Do not reverse-engineer the URL scheme, and do not probe for endpoints. The
viewer already knows how to build every tile URL, and it will tell you. Run this
in the browser console on a loaded page:

```js
// Locate the viewer instance. It is often on window; otherwise walk from the container.
const viewer = window.viewer
  ?? OpenSeadragon.getViewer(document.querySelector('.openseadragon-container'));

const src = viewer.world.getItemAt(0).source;

console.log({
  nativeWidth:  src.width,        // <-- TRUE resolution of the scan
  nativeHeight: src.height,
  tileSize:     src.tileSize,
  tileOverlap:  src.tileOverlap,  // usually 1 px; must be trimmed when stitching
  maxLevel:     src.maxLevel,
  sampleUrl:    src.getTileUrl(src.maxLevel, 0, 0),
});
```

`src.width × src.height` is the answer to the project's gating question. Compare
it against the resolution floor below before building anything else.

To enumerate a page's tiles at full zoom:

```js
const L = src.maxLevel;
const { x: nx, y: ny } = src.getNumTiles(L);
const urls = [];
for (let x = 0; x < nx; x++)
  for (let y = 0; y < ny; y++)
    urls.push({ x, y, url: src.getTileUrl(L, x, y) });
```

This approach has three properties that matter:

- **It is not reverse engineering.** `getTileUrl()` is the viewer's own URL
  constructor. The URLs produced are byte-identical to what the viewer would
  request if the operator zoomed to full magnification.
- **It is tile-source agnostic.** OpenSeadragon supports DZI, IIIF, legacy
  pyramids, and custom sources. Third-party tooling reports that this particular
  endpoint is *not* standard early-IIIF, so **do not assume IIIF Image API
  syntax** (`/full/max/0/default.jpg`) will work. Going through `getTileUrl()`
  sidesteps the question entirely.
- **It surfaces `tileOverlap`.** Tiles overlap by a pixel or so; stitching
  without trimming the overlap produces subtly doubled strokes, which is exactly
  the kind of corruption that degrades CJK reading without looking obviously
  wrong.

#### Check for a single-request path first

Before building a stitcher, inspect `sampleUrl`. If the base turns out to expose
a size or region parameter that yields the whole page in one request, use it —
one request per page is far kinder to the host than forty, and avoids stitching
artifacts altogether. Only fall back to tile assembly if no such path exists.

#### Two viable operating modes

1. **Passive (browser extension / userscript).** Operator browses; the script
   collects tiles for each page visited and posts them to the local worker. Load
   on the host is identical to normal reading.
2. **Enumerated (headless).** Walk volumes and pages programmatically. Far more
   convenient, but see §3.1b — this is a crawl, and the conduct requirements are
   materially stricter.

Start with mode 1 for the spike. Move to mode 2 only after the pipeline is
proven on a few dozen pages and the rate-limiting is in place.

**Never photograph or screen-capture the monitor.** A phone photo of a screen
adds moiré (the sensor grid beating against the display grid), keystone
distortion, glare, and a second lossy compression pass — on top of whatever
downscaling the viewer already applied. It destroys exactly the fine stroke
detail that distinguishes similar characters.

**Hard resolution floor.** Dense CJK needs roughly **40–50 px per glyph** for
reliable reading; below ~25 px, variant forms and rare characters start failing.
Measure this on a candidate capture before building anything downstream:

```
px_per_glyph ≈ (page_width_px / columns_per_page) × 0.8   # large tier
                                                × 0.35    # small annotation tier
```

The small annotation tier is the binding constraint, because that is where the
dates and relationships are. A capture method that clears the floor for names
but not for annotations is **not sufficient** — it yields a nameplate list, not
a genealogy. If the viewer cannot serve enough resolution, that is a
project-blocking finding and must be resolved at the capture layer, not
compensated for with better prompting.

Capture must record, alongside the image: viewer URL, any visible volume/page
label, capture timestamp, and a content hash for deduplication (the operator
will revisit pages).

The extension posts to a local HTTP endpoint. It should never block the
operator's browsing on network or model latency.

### 3.1b Access conduct — binding constraints

This system is a **passive recorder of content the operator's browser has
already been served**. It must not become anything else. The following are hard
requirements, not preferences.

**Permitted:**

- Reading and saving image bytes the viewer already loaded into the page for a
  page the operator navigated to themselves.
- Rewriting documented public API parameters — e.g. IIIF `region`/`size`
  arguments — on endpoints the viewer itself is already calling.
- Reassembling tiles the viewer already fetched into a single image.

**Prohibited:**

- Any attempt to bypass authentication, paywalls, or access controls; any use of
  credentials that are not the operator's own.
- Probing for unpublished endpoints, directory traversal, parameter fuzzing, or
  enumerating document/page IDs the operator has not been granted access to.
- Automated crawling, page-advancing, prefetching, or any request pattern faster
  than a human reading the book.
- Ignoring `robots.txt`, or evading rate limits, CAPTCHAs, or bot detection.
- Redistributing the scans. The extracted *genealogical data* is family history;
  the *page images* remain the host's to publish.

**Also required:**

- Read the site's terms of use before writing capture code, and record the
  conclusion in the repo.
- Set a real, identifying `User-Agent` with a contact address. Do not disguise
  the tool as a browser.
- Serialise all requests; never parallelise against the host.
- Prefer asking. Many 族谱 sites are run by clan associations or local archives
  who will supply a copy to a descendant on request. This is faster, higher
  quality, and unambiguous.

If the viewer turns out to require circumvention to capture usable resolution,
**stop and contact the site operator** rather than engineering around it.

#### Additional rules once capture is enumerated rather than passive

Tile-level enumeration changes the scale of this materially. Eight volumes at
~237 pages, at tens of tiles per page, is on the order of **40,000–100,000
requests** against a public research library. That is a crawl, and it must be
treated as one:

- **Serialise everything.** One request at a time, with a delay — target ≤1
  request/second, and prefer slower. Run it overnight rather than compressing it.
- **Exponential backoff on any 429/5xx, and stop entirely on repeated failures.**
  Never retry through a rate limit.
- **Do one volume first.** Prove the pipeline end to end on 第1册 before touching
  the other seven. Most bugs will be found in the first fifty pages.
- **Cache aggressively and permanently.** Every tile is fetched exactly once,
  ever. Re-running extraction must never re-fetch.
- **Identify yourself** in the `User-Agent` with a working contact address.

#### Prefer the sanctioned route

Shanghai Library is not a hostile host — it is a research institution that
actively wants this material transcribed:

- It operates an **open data platform** (`data.library.sh.cn`) publishing
  genealogy knowledge bases as Linked Data, with REST API and SPARQL endpoints,
  gated behind free API-key registration and a developer guide. Check whether
  this volume's metadata — or even partial transcription — is already available
  there before scraping anything.
- Its own published research acknowledges that older genealogies cannot be fully
  OCR'd, and it built a **crowdsourced 在线识谱 (online transcription)**
  programme in response.

A descendant offering a careful machine-assisted transcription of a specific
支譜 is aligned with that programme, not adversarial to it. **Email them before
building the enumerated crawler.** The likely outcomes are a straight copy of
the files, a documented API route, or a collaboration — all strictly better than
scraping, and all faster.

**Politeness:** the operator is browsing at human speed, so load is naturally
low. Do not add automated page-advancing or prefetching.

### 3.2 Extraction layer

A worker consuming the capture queue. **Three passes, kept separate.** Fusing
them is the single most common way this kind of project goes wrong, because it
lets interpretation contaminate transcription.

**Pass A — Transcription.** Input: page image (optionally split into halves and
column strips). Output: verbatim text per column, in reading order, with damage
markers and per-column confidence. No interpretation. The model must be
instructed to preserve variant characters, note uncertain glyphs, and never
"correct" what it sees into standard modern forms.

**Pass B — Structural parsing.** Input: transcribed text (not the image) plus
the extracted 字輩 poem and any known conventions. Output: typed assertions —
person mentions, name components, generation numbers, relationship claims,
dates, notes. Use forced tool-use / structured output so the schema is
guaranteed.

**Pass C — Targeted re-reading.** For assertions flagged low-confidence or
failing validation, crop the relevant region and re-read at higher magnification
with the surrounding context supplied. This is where most accuracy gains come
from.

Every extraction is cached and keyed by image hash + prompt version, so
re-running after a prompt improvement only re-processes what changed.

### 3.3 Resolution layer

Converts per-page assertions into a coherent graph. See §5.

### 3.4 Review layer

A local web UI. Minimum viable feature set:

- Side-by-side page image and extracted assertions, with click-through from any
  field to the source region.
- A review queue sorted by confidence and by validation-failure severity.
- Merge/split controls for candidate duplicate people.
- Graph visualisation of the assembled lineage.
- Export to GEDCOM, and to JSON.

This UI is not optional polish. Without it the system produces a genealogy
nobody can trust.

---

## 4. Data model

Two principles:

**Assertions are immutable; the graph is derived.** Never mutate person records
in place as pages are scanned. Store what each page *claims*, with provenance.
Materialise the graph from the full assertion set. This makes the build
idempotent and re-runnable, lets you improve prompts and rebuild from cache, and
makes conflicting evidence visible instead of letting the last write win.

**Relationships are edges, not fields.** Adoption, 兼祧, and multiple marriages
make embedded `father` / `children` fields untenable.

### Suggested schema (adapt as needed)

```
PageCapture
  id, image_hash, image_path, source_url, volume_label, page_label,
  captured_at, dimensions

Assertion                      # one atomic claim from one page
  id, page_capture_id, region_bbox, pass_version,
  claim_type                   # person_mention | relationship | date | note | generation_poem
  payload                      # type-specific structured content
  raw_text                     # verbatim source text
  confidence                   # 0..1
  needs_review                 # bool

Person                         # materialised, not authored directly
  id
  names[]                      # {type: 諱|字|號|行第|posthumous, value, confidence}
  generation                   # integer, plus how it was determined
  sex
  birth  {raw_string, era, cyclical_year, gregorian_year, confidence}
  death  {…}
  status                       # living | deceased | died_young | no_heirs | unknown
  notes[]                      # {text, source_assertion_id}
  supporting_assertions[]
  confidence

Relationship
  id, type, from_person, to_person, confidence, supporting_assertions[]
  type ∈ {
    biological_father, ritual_father,     # 生父 / 嗣父 — both may exist
    mother,
    spouse,                               # with rank: 配 | 繼 | 側室
    sibling_order,                        # 行第 within a set of brothers
    adopted_out, adopted_in, dual_succession   # 出繼 / 入嗣 / 兼祧
  }
```

Note the split between `biological_father` and `ritual_father`. Rendering code
should let the user toggle which edge type defines the displayed tree — the
ritual line is what the zupu itself considers authoritative, but the biological
line is what a genetic genealogist wants.

---

## 5. Entity resolution

**This is the hardest part of the project. Budget accordingly.**

The problem: a person appears in the 世系圖 under their 諱, in the 齒錄 under
their 字, as a named son in their father's entry, and as a named father in each
of their sons' entries. Four mentions, one person. Meanwhile, two genuinely
different men in different branches may share an identical name.

### Signals available, roughly in descending order of strength

1. **Chart continuation.** In 五世一圖 layouts, the last generation of one chart
   is repeated as the first row of the next. This is an explicit join and should
   be exploited as the primary linking mechanism.
2. **Explicit cross-references.** 見某卷某頁, 接前, 詳見下.
3. **Generation character** from the 字輩 poem. Constrains which generation a
   name can belong to.
4. **Parent-child name agreement.** A son listed under father X, whose own entry
   names his father as X, at the correct generation.
5. **Sibling set consistency.** An entry stating 子三人 must yield exactly three
   sons.
6. **Date plausibility.** Father-son birth gaps outside roughly 15–60 years are
   suspicious. Generational birth years should trend monotonically.
7. **Spouse surname**, burial site, native place.

### Approach

Blocking on (generation, surname, name-character) to generate candidate pairs,
then scoring pairs against the signals above. **Do not auto-merge below a high
threshold.** Route ambiguous pairs to the review queue. A wrong merge silently
grafts one branch onto another and is very hard to detect later; an unmerged
duplicate is obvious and trivially fixed.

Every merge decision must be recorded and reversible.

---

## 6. Validation invariants

Run these continuously; surface failures in the review queue.

- Generation character matches assigned generation under the 字輩 scheme.
- Reign-era year and 干支 cyclical year agree.
- Stated child count matches the number of child records.
- Birth year precedes death year; age at death is plausible.
- Father's birth precedes child's birth by a plausible margin.
- No cycles in the parent graph.
- Every person is reachable from the apical ancestor, or is explicitly flagged
  as an unattached fragment.
- Sibling 行第 ordering is a complete sequence with no gaps or duplicates.
- Page sequence is contiguous; flag gaps in captured page numbers.

---

## 7. Anticipated failure modes

| Risk | Mitigation |
|---|---|
| **Model hallucinates plausible names.** Vision models complete damaged text from priors, and a zupu is full of formulaic patterns that invite this. | Separate transcription from interpretation. Require explicit damage markers. Give the model explicit permission to return "illegible". Compare independent re-reads of the same crop and flag disagreement. |
| **Low-resolution captures.** Small characters lose the stroke detail that distinguishes similar glyphs. | Prefer tile-stitching or IIIF full-size requests over screenshots. Crop to column strips before sending — resolution per character is the dominant accuracy factor. Measure characters-per-100px on a sample before committing to a capture method. |
| **Variant and archaic character forms.** 異體字, 俗字, and 避諱缺筆 (deliberate stroke omission to avoid a taboo name). | Preserve variants verbatim; normalise only in a separate derived field. Build a project glossary of recurring variants and supply it as prompt context. Note that a missing stroke may be intentional and meaningful, not damage. |
| **Column reading order reversed.** | Test explicitly on a page with known content. Consider extracting columns as separate crops so order is imposed by the code rather than trusted to the model. |
| **Bleed-through and woodblock artifacts.** Reverse-side ink showing through thin paper. | Preprocess: autocontrast, and evaluate per-channel separation. Test with and without; enhancement helps some scans and hurts others. |
| **Identical names across branches.** | Never merge on name alone. Require corroborating generation and lineage-path evidence. |
| **Prompt drift over a long project.** Improving a prompt mid-run leaves earlier pages extracted under older logic. | Version prompts; store the version on every assertion; support selective re-extraction from the image cache. |
| **Silent capture failure.** Operator flips 200 pages, 30 didn't save. | Show live capture count and detected page numbers in the extension UI. Validate page-number contiguity and alert on gaps immediately. |

---

## 8. Build order

Sequenced so that the riskiest assumptions are tested cheapest and earliest.

1. **Capture spike.** Determine how the viewer serves images. Manually capture
   ten representative pages at the best available quality. *Stop here and assess
   whether the resolution is sufficient before building anything else.*
2. **Transcription spike.** Run those ten pages through Pass A with varied
   cropping strategies. Hand-check against the images. Establish a realistic
   character-level accuracy baseline. If accuracy is poor, resolution or
   cropping is the lever — revisit step 1 rather than elaborating the prompt.
3. **Ground truth set.** Hand-transcribe three pages completely. This is tedious
   and non-negotiable: without it, no later change can be evaluated.
4. Extraction schema and Pass B on the spike pages.
5. Persistence layer and assertion store.
6. Capture extension, wired to the queue.
7. Entity resolution over a single contiguous branch.
8. Review UI.
9. Validation invariants.
10. Full-volume run, with review.
11. GEDCOM export.

---

## 9. Technology notes

- **Vision model:** use a high-resolution-tier model for Pass A. Images are
  downsampled server-side past the model's long-edge limit, so resize
  client-side where the resampling filter is under your control.
- **Structured output:** forced tool-use or structured outputs for Pass B, so
  malformed JSON is impossible by construction.
- **Batch processing:** Pass A over a full volume is a large, latency-tolerant
  workload — a good fit for a batch API if cost matters.
- **Storage:** SQLite is more than adequate and keeps the project a single
  portable file. Avoid a graph database unless the graph genuinely outgrows it.
- **Export:** GEDCOM 5.5.1 or 7.0 for interoperability with genealogy software.
  Note that GEDCOM models adoption awkwardly; retain the native JSON as the
  authoritative record.

---

## 10. Questions to resolve before building

**Resolved:**

- ~~Which layout style?~~ **歐式 五世一圖.** See §2.1b.
- ~~Printed or handwritten?~~ **Printed**, in a regular block-cut face. A general
  vision model is appropriate; a dedicated HTR model is not needed.
- ~~Is there a 字輩?~~ **Yes** — `錫 → 式 → 仁 → 厚 → 其` recovered partially.
  The full poem still needs hand-transcription from the front matter.
- ~~Is the viewer IIIF, tile-based, or plain images?~~ **OpenSeadragon over a
  tile pyramid**, and reportedly *not* standard IIIF. Capture via
  `getTileUrl()`; see §3.1.
- ~~What is the work?~~ **《經鏗黃繼述堂支譜》** (also catalogued as 經鏗黃氏支譜 /
  黃氏支譜 / 彬甫公宗譜). Surname 黃. **堂號 = 繼述堂**, which matches the 版心 foot
  on the sample page — a useful confirmation that page-level metadata is
  self-consistent. **8 冊**, 第1册 running to 237 pages.
- ~~Where is the front matter?~~ Early pages of 第1册 — the sidebar shows
  封面 / 正文, and p.13 is already into the 凡例. Capture pages 1–30 of 第1册
  first and hand-transcribe.

**Still open — resolve before building:**

1. **What is `src.width × src.height`?** Run the console snippet in §3.1. This is
   the true native resolution of the scan and it gates the whole project.
   Everything else is downstream of this number.
2. Does a single-request full-page path exist, or is tile stitching required?
3. Transcribe the full 字輩 poem and the 凡例/譜例 by hand from 第1册.
4. Page counts for 第2册–第8册 (only 第1册's 237 is known). Needed for cost and
   review-effort estimates.
5. Does the volume also contain a prose 齒錄/行傳 section alongside the 世系圖?
   If so it is the richer source for dates and spouses, and should be extracted
   with a separate parser and cross-linked to the chart entries.
6. Does the volume contain recent generations with living people? If so, decide
   the privacy policy for the export before, not after, building it.
7. Has Shanghai Library already transcribed part of this work through its
   crowdsourcing programme, or exposed it via `data.library.sh.cn`? Check before
   duplicating the effort.

---

## 11. Cost estimate

Assumes ~1,900 pages (8 × 237, actual volumes vary), pages split into four
vertical column strips fitted to a 2,576 px long edge (≈6,600 input visual
tokens/page), Opus 5 at \$5/\$25 per MTok, and the **Batch API's 50% discount**,
which applies cleanly since none of this is latency-sensitive.

| Stage | Full corpus |
|---|---|
| Pass A — transcription | ~\$67 |
| Pass B — structural parse (text only) | ~\$59 |
| Pass C — targeted re-reads (~20% of pages) | ~\$13 |
| **One full run** | **~\$140** |
| **Three full runs** (realistic, with prompt iteration) | **~\$420** |

A Haiku 4.5 triage pass classifying every page by type (封面 / 序 / 凡例 / 世系
/ 齒錄 / blank) costs roughly \$7 and is worth running first, so the expensive
passes only touch pages that carry genealogical data.

**The cost is not the constraint on this project.** Human review time is. Budget
accordingly: at even one minute of review per page, 1,900 pages is around 30
hours of attention. Design the review UI to make that time efficient, and use
the confidence scores and validation failures to prioritise it rather than
reviewing linearly.
