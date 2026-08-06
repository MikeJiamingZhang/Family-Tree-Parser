# Calibration results — Milestone 0

Measured constants. **Never hardcode these values anywhere else in the codebase;
read them from here or from config.**

Source: 《經鏗黃繼述堂支譜》, item `STJP014805`, 8 冊, 第1册 = 237 pages.
Viewer: `dhapi.library.sh.cn/pdfview/`, OpenSeadragon tile pyramid, served inside
a cross-origin iframe from the `jiapu.library.sh.cn` catalogue page.

---

## Canvas scaling

The OpenSeadragon canvas backing store scales linearly with viewport × DPR. No
plateau was reached by viewport size alone:

| viewport | DPR | canvas backing store |
|---|---|---|
| default window | 1 | 896 × 1262 |
| 2388 × 3154 | 1 | 2388 × 3154 |
| 2388 × 3154 | 2 | 4776 × 6308 |

## DPR 2 carries no real detail — REJECTED

Tested by upscaling the DPR 1 capture with bicubic interpolation (which by
construction adds zero information) and comparing high-frequency energy against
the native DPR 2 capture:

| | Laplacian variance | mean gradient |
|---|---|---|
| DPR 1, bicubic-upscaled | 2.2 | 9.35 |
| DPR 2, native | 1.6 | 7.46 |

DPR 2 scores **lower** than a naive upscale — the viewer is interpolating, and
more softly than bicubic. DPR 2 costs 3.4× the file size and ~3× the vision
tokens for no additional information.

**Decision: DPR 1.**

## Letterboxing — auto-crop is mandatory

Cover page capture at DPR 1:

- full capture: 3588 × 3154
- actual book area: 1935 × 2928 (**54% of width**)
- remaining 46% is black letterbox

Sending that to a vision model wastes ~46% of every image's tokens. **Auto-crop
to the page boundary by ink projection before extraction is required, not
optional** (capture spec §3.2).

Two mitigations, use both: zoom the viewer so the page fills the frame at
capture time, and crop server-side as a safety net.

## Resolution verdict — PASSES

Projected onto a text page at ~10 columns, using the measured book area:

```
px per column      ≈ 194
px per small glyph ≈ 68        (floor = 28)
```

Roughly 2.4× above the acceptance floor. **Proceed.**

⚠️ Measured on the **cover**, which is calligraphic brush on textured paper and
genuinely soft in the source scan. Woodblock-printed text pages should be
sharper. **Re-confirm on a 世系 chart page and update this file.**

## Canvas is tainted — CONFIRMED

`canvas.toDataURL()` throws `SecurityError`. Tiles are served without
`Access-Control-Allow-Origin`, so the canvas is permanently tainted and
JavaScript can never read its pixels. Ordinary CORS behaviour, not an access
control measure.

Consequences, both already reflected in capture spec §2.2 and §2.3:

- Capture must use `chrome.debugger` + `Page.captureScreenshot`, which reads
  composited pixels. **Do not implement a `toDataURL` path.**
- Settle detection cannot use pixel signatures. Use `PerformanceObserver` tile
  quiescence plus page identity from the viewer UI.
- Deduplication moves server-side, where PNG bytes can be hashed freely.

---

## Constants for config

```
CAPTURE_VIEWPORT_W = 2388
CAPTURE_VIEWPORT_H = 3154
CAPTURE_DPR        = 1
CANVAS_TAINTED     = true
CAPTURE_METHOD     = "chrome.debugger:Page.captureScreenshot"
AUTOCROP_REQUIRED  = true
SETTLE_MS          = 700      # starting value, tune against the live viewer
```

## Still to confirm

- [ ] Re-run on a 世系 chart page with the page zoomed to fill the frame.
- [ ] Record px-per-small-glyph measured directly, not projected.
- [ ] Page counts for 第2册–第8册.
- [ ] Hand-transcribe the 字輩 poem from the 凡例 in early 第1册.
