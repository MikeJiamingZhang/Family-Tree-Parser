# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## ⚠ Unresolved: `zupu-capture-spec-v2.md`

A v2 capture spec appeared in the repo on 2026-08-06 declaring that it **supersedes `zupu-capture-spec.md` entirely** — the collection is reachable through a plain, unauthenticated IIIF API (`fullimg.library.sh.cn`, 1,280 pages), so the browser extension, `chrome.debugger` capture, settle detection, and the tainted-canvas workaround are obsolete and "must not be built".

**This has not been confirmed with the user, and the code below was written against v1.** Do not build on either spec until that is settled. What v2 changes:

- `extension/` is obsolete in full.
- `server/`'s `captures` table and `POST /capture` are obsolete; v2 §3 replaces them with `pages` (keyed by manifest `imageId`) and `regions`. `extractions` is explicitly unchanged.
- `calibration.md` is obsolete — v2 §1 replaces canvas measurement with one `info.json` request, and the viewport/DPR/tainted-canvas constants no longer mean anything.
- `zupu-project-brief.md` is **unaffected and still authoritative** — everything downstream of image acquisition stands.

## Current state

**Milestones 2–3.** Built: the local capture server (`server/` — `POST /capture` with dedup, the §3.3 SQLite schema, PNG storage under `data/images/`) and the MV3 extension (`extension/`). Deliberately not built: the quality gate (§3.2), the extraction worker (§3.4), the operator UI (§3.5).

**The extension has never run against the live viewer.** Its viewer-UI selectors and clip coordinate math are unverified guesses; `extension/README.md` §1 and §4 list what to confirm, in order. Do not treat a green test suite as evidence that capture works — the tests cover only what is checkable without a browser.

The repo's three authoritative specs:

- `zupu-project-brief.md` — domain model, data model, extraction passes, entity resolution, validation invariants, build order. **Read this before writing anything.**
- `zupu-capture-spec.md` — the capture half only (browser extension + local server + queue). Companion to the brief; explicitly defers passes/prompts/schema to it.
- `calibration.md` — measured Milestone 0 results and the constants derived from them.

All three are authoritative. **Where `calibration.md` conflicts with the capture spec, calibration wins** — it records what was actually measured against the live viewer. Capture-spec §2.2 and §2.3 have since been rewritten to match calibration, so the two no longer disagree on the capture path; §3.1's signature-based dedup is the one place they still do (see "Server design notes").

Python (FastAPI + stdlib `sqlite3`) for the server and worker; the extension is plain JS (Manifest V3), no build step.

## Commands

```bash
python -m venv .venv                                   # once
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt

.venv/Scripts/pytest.exe -q                            # all tests
.venv/Scripts/pytest.exe tests/test_capture_smoke.py::test_post_twice_dedups -q   # one test
.venv/Scripts/python.exe -m server                     # run the server
```

Paths above are the Windows venv layout (`.venv/bin/` on POSIX). `pyproject.toml` puts the repo root on `sys.path`, so bare `pytest` works.

Env overrides, used by the tests to redirect writes to a tmpdir: `ZUPU_DATA_DIR`, `ZUPU_DB_PATH`, `ZUPU_HOST`, `ZUPU_PORT`, `ZUPU_CALIBRATION`.

## Server design notes

- **Dedup keys on `sha256` of the PNG bytes, not on `signature`.** Capture-spec §3.1 step 1 deduped on the client's signature; calibration killed that (a tainted canvas means the extension cannot compute a pixel signature at all). The `signature` column is retained per §3.3 and stays nullable. A unique index on `sha256` is the only addition to the §3.3 schema, and it also makes a concurrent double-post safe — the `IntegrityError` is caught and adopted as a dedup.
- **Images are content-addressed** at `data/images/{sha256}.png`, written atomically via a tmp file plus `os.replace`, so a repeat write is a no-op rather than a torn file.
- **`/capture` is a sync `def`** on purpose: FastAPI runs it in a threadpool, so the blocking file write and SQLite call cannot stall the event loop. Measured median 6.4 ms, p95 9.1 ms against the ~20 ms budget in §3.1.
- **Viewport mismatch is advisory.** `canvas_w`/`canvas_h`/`dpr` are checked against calibration and returned in `warnings`, never rejected — a wrongly sized page still beats a missing one (§5, "everything captured at the wrong size"). The exact relationship between the emulated viewport and what `Page.captureScreenshot` reports needs confirming when the extension lands.
- `extractions` is created but unused; its `UNIQUE(capture_id, pass, prompt_version)` is what makes re-extraction cheap later.

## Calibration constants — never hardcode

`calibration.md` ends with a `Constants for config` block: `CAPTURE_VIEWPORT_W`, `CAPTURE_VIEWPORT_H`, `CAPTURE_DPR`, `CANVAS_TAINTED`, `CAPTURE_METHOD`, `AUTOCROP_REQUIRED`, `SETTLE_MS`. Read them from `calibration.md`, or from a config file generated from it — never inline them into extension, server, or worker source.

They are empirical and several are explicitly provisional: `SETTLE_MS` is a starting value to be tuned against the live viewer, and the resolution verdict was measured on the calligraphic cover, with re-confirmation on a 世系 chart page still outstanding. A value pasted into code will silently diverge when the file is updated. Captures taken at a different viewport/DPR produce drifting crops and coordinates, so a mismatch must be asserted on and surfaced loudly rather than tolerated.

## Consequences of the tainted canvas

Capture-spec §2.2/§2.3 now state these directly, but they are the constraints most likely to be "helpfully" undone by someone who has not read calibration:

- **The canvas is permanently tainted.** Tiles are served without `Access-Control-Allow-Origin` (ordinary CORS, not an access control), so `toDataURL()` throws `SecurityError` and JS can never read those pixels. **There is no `toDataURL` path and there must never be one** — `tests/test_extension_contract.py` fails the build if one appears.
- **Pixel-signature settle detection is impossible** for the same reason. Settle detection is `PerformanceObserver` tile quiescence plus page identity scraped from the viewer UI; `getImageData`/`drawImage` are likewise banned by test.
- **Deduplication is server-side**, on the PNG bytes. The page identity check in the content script is only a cheap first filter.
- **DPR 2 is rejected.** It scored *below* a bicubic upscale of the DPR 1 capture on both Laplacian variance and mean gradient — the viewer is interpolating — at 3.4× the file size and ~3× the vision tokens for zero added information.
- **Auto-crop is mandatory, not optional.** The measured cover capture was 54% book, 46% black letterbox. Zoom the viewer to fill the frame *and* crop server-side by ink projection.

## Extension design notes

- **Capture is `chrome.debugger` + `Page.captureScreenshot`** with `Emulation.setDeviceMetricsOverride` applied **once per attach** (§2.2 — re-applying forces OSD to re-render and re-fetch every tile, adding host load). `captureVisibleTab` exists behind the same interface as the §2.2 fallback, selected by the `captureMethod` setting.
- **The extension hardcodes no calibration values.** It fetches viewport, DPR, capture method, and `SETTLE_MS` from the server's `/health`, which reads `calibration.md`. A test asserts the measured viewport literals appear nowhere in `extension/`.
- **Host permissions stay at `dhapi.library.sh.cn` plus `localhost:8787`**, per §2.1's "nothing broader". The catalogue page is never injected into: §2.4's parent-DOM metadata route would need a second host permission, so metadata comes from the iframe and the iframe's offset comes from CDP (`DOM.getBoxModel`), which the debugger already grants.
- **Clipping is best-effort.** An implausible clip falls back to an unclipped capture, and the operator can turn clipping off entirely; server-side auto-crop is the backstop either way.
- **The service worker holds no authoritative state** (§5, MV3 sleeps): session facts in `chrome.storage.session`, the ≤50-image retry buffer in `chrome.storage.local`, everything durable in the server.

## Intended architecture

Four decoupled layers, decoupled so the operator's browsing pace is never gated on LLM latency and so re-extraction from cache is cheap:

```
[1 Capture]  MV3 browser extension  ->  raw page images + viewer metadata
[2 Extract]  worker queue           ->  per-page structured JSON (assertions)
[3 Resolve]  entity resolution      ->  person records + typed relationship edges
[4 Review]   local web UI           ->  human adjudication, corrections, export
```

Capture posts to `http://localhost:8787/capture`; the local server (FastAPI or Express) writes PNGs to `data/images/{sha256}.png` and rows to SQLite; a separate worker process drains the queue. Schemas for `captures` and `extractions` are in capture-spec §3.3; assertion/person/relationship schemas are in the brief §4.

`data/` holds page scans that belong to Shanghai Library and is gitignored. It must never be committed or redistributed — only the extracted genealogical data is ours to publish.

## Load-bearing design decisions

These are already settled in the docs. Do not quietly revisit them.

- **The Milestone 0 gate has been passed** — projected ~68 px per small glyph against a floor of 28. The pending re-confirmation on a chart page is a refinement, not a re-opening. The *small annotation tier*, not the large 諱, remains the binding resolution constraint, because that is where dates, spouses, and adoption edges live; a capture that reads names but not annotations produces a nameplate list, not a genealogy.
- **Three extraction passes stay separate** (brief §3.2): A transcription (verbatim, damage markers, no correction), B structural parsing (text-only input, forced tool use), C targeted re-reads of low-confidence regions. Fusing them lets interpretation contaminate transcription — the documented top failure mode.
- **Assertions are immutable; the graph is derived.** Never mutate person records as pages arrive. `UNIQUE(capture_id, pass, prompt_version)` plus prompt versioning on every assertion is what makes re-runs cheap and idempotent.
- **Relationships are edges, not fields**, and `biological_father` / `ritual_father` are distinct edge types. 出繼/入嗣/兼祧 are common, not exceptional.
- **Entity resolution is the hard part.** A false merge grafts one branch onto another and is near-undetectable; a false split is obvious and cheap. Threshold accordingly, never merge on name alone, and make every merge recorded and reversible.
- **Capture is a passive recorder.** The extension must never navigate, follow links, prefetch, page-advance, or issue requests the page wasn't already making. Access-conduct constraints in brief §3.1b are hard requirements — identifying `User-Agent`, serialized requests, and stopping to contact the site operator rather than engineering around any access control.

## Domain facts already established

Treating this as generic OCR produces a corrupt genealogy that looks correct. The confirmed specifics (brief §2.1b, §10; `calibration.md`):

- Work: 《經鏗黃繼述堂支譜》, item `STJP014805`, surname 黃, 堂號 繼述堂, 8 冊, 第1册 = 237 pages. Printed block-cut face — a general vision model suffices, no HTR model needed.
- Viewer: `dhapi.library.sh.cn/pdfview/`, OpenSeadragon tile pyramid, in a **cross-origin iframe** inside the `jiapu.library.sh.cn` catalogue page. Content scripts must target the iframe origin with `all_frames: true`; matching only the catalogue page silently captures nothing.
- Layout: **歐式 五世一圖**. Five horizontal bands per page, one band = one generation, entries read right-to-left; vertical text, right-to-left columns; double-leaf binding, so one image may hold two logical pages.
- **Two-tier typography carries the semantics** — large characters are the 諱; small characters below/beside hold 字/號, spouses, dates, birth rank, adoption notes, line termination. This is the single most important parsing fact.
- The 版心 centre-fold page number is the authoritative sequence key, not the viewer's paging. Outer-margin branch markers (`◻◻公宗`/`公居`/`公裔`) are first-class fields, not free-text notes.
- 字輩 partially recovered: `錫 → 式 → 仁 → 厚 → 其`. Full poem still needs hand-transcription from the 凡例/譜例 in 第1册's front matter.
- Dates carry reign-era year *and* 干支 year encoding the same year — a free checksum. Store raw strings always; Gregorian is a derived field with its own confidence. Do not hand-roll calendar conversion.

## Working order

Build order is in capture-spec §4 and brief §8, sequenced so the riskiest assumptions fail cheapest. With Milestone 0 done, the live path is: server (`POST /capture`, dedup, SQLite, image storage) → extension → quality gate and auto-crop → **hand-transcribe 3 ground-truth pages** → Pass A. Those steps are where the project succeeds or fails; everything after is ordinary engineering. The ground-truth set is non-negotiable: without it no later change can be evaluated. When Pass A accuracy is poor, the lever is cropping, not prompt wording.

Remaining open items are the unchecked boxes at the foot of `calibration.md` plus brief §10 — chiefly whether Shanghai Library's open data platform or crowdsourced transcription programme already covers this volume.

## Model/API notes

Cost is not the constraint (~$140 per full-corpus run at Opus 5 batch pricing); human review time is (~30 hours at 1 min/page). Consequences: use the Batch API (nothing here is latency-sensitive), prompt-cache the large identical system prompt, run a cheap Haiku triage pass to classify page types before the expensive passes, and prioritize the review queue by confidence and validation-failure severity rather than reviewing linearly.
