# Zupu Capture — extension

Milestone 3 of `zupu-capture-spec.md`. Captures the viewer canvas as the
operator browses and posts it to the local server.

**None of this has been run against the live viewer yet.** It is written to
capture-spec §2.2/§2.3 and `calibration.md`, but every selector and coordinate
assumption below is unverified. Work through §1 and §4 in order before trusting
a capture run.

---

## 1. First check: is the content script reaching the iframe?

Capture-spec §2.1 calls this "the most likely first bug", and it fails
*silently* — the extension looks fine and captures nothing. Confirm it before
anything else.

1. Start the server: `.venv/Scripts/python.exe -m server`
2. Load the extension: `edge://extensions` → **Developer mode** on → **Load
   unpacked** → select this `extension/` folder.
3. Open the genealogy viewer in Edge.
4. Click the Zupu Capture toolbar icon.

The **Injection** block at the top of the popup is the answer. You want:

```
✓ injecting into the dhapi.library.sh.cn iframe
   viewer ✓   subframe
   https://dhapi.library.sh.cn/jp/full-img/...
```

- **"No frames reported"** — the content script is not matching. Check the
  viewer's real origin against `host_permissions` and `content_scripts.matches`
  in `manifest.json`. If the origin differs from `dhapi.library.sh.cn`, that is
  the bug.
- **Frames listed but none says `viewer ✓`** — the script is injecting, but
  `.openseadragon-canvas` is not matching. Find the real canvas selector and fix
  `viewerEl()` in `content/settle.js`.

For the raw log, open DevTools on the viewer tab, switch the console's frame
dropdown to the `dhapi.library.sh.cn` frame, and reload. `content/probe.js`
prints `location.href` and the frame's identity at `document_start`.

## 2. The "debugging this browser" banner

Expected. `Page.captureScreenshot` is the only capture path that works, because
the canvas is permanently tainted (`calibration.md`), and it needs the debugger
attached. Leave the banner alone.

Clicking **Cancel** detaches the debugger and capture stops. The extension
detects this and puts a red message in the popup, but the fix is to reload the
viewer page.

## 3. How it decides to capture

`content/settle.js`, per capture-spec §2.3 — no pixel reads anywhere, since the
canvas is tainted:

- a `PerformanceObserver` watches tile requests; a `SETTLE_MS` quiet period with
  no tile activity means the page has stopped rendering;
- at least one tile must have loaded since the last capture;
- the page identity scraped from the viewer UI is a cheap dedup filter.

Real dedup is server-side on the image bytes. `SETTLE_MS` comes from the server
(`/health` → `calibration.md`), so tune it there, not here.

## 4. What to verify on the first real run

In order, because each one invalidates the next if it is wrong:

1. **Injection** — §1 above.
2. **Page identity.** The selectors in `pageLabel()` / `volumeLabel()` are
   guesses at the viewer's markup. If the popup's "Last page" shows `? ?`, fix
   them. Captures still work without this; only the cheap dedup and the
   operator-facing labels degrade.
3. **The clip.** The canvas rect is reported in the iframe's own coordinates and
   offset by the iframe's box, fetched over CDP. If captures come out shifted or
   cropped wrong, untick **Clip to viewer canvas** in the popup — the server's
   auto-crop (`AUTOCROP_REQUIRED`) trims the letterbox anyway. Getting the clip
   right is an optimisation, not a correctness requirement.
4. **Capture size.** The popup shows the decoded pixel size of the last capture.
   It should be close to the calibrated viewport. If the server starts returning
   `canvas_w=… but calibration expects …` warnings on every page, the viewport
   override and the captured region disagree — reconcile them before a bulk run,
   or the warning becomes noise you learn to ignore.
5. **`SETTLE_MS`.** Blurry captures mean it is too short; a fast operator
   outrunning it means too long. Start at calibration's 700 ms.

## 5. Deviations from the spec, and why

- **`content.js` is split** into `content/probe.js` and `content/settle.js`, so
  the injection probe in §1 can be read and verified on its own.
- **`http://localhost:8787/*` is in `host_permissions`**, which §2.1 does not
  list. The service worker cannot POST captures or read `/health` without it.
  The viewer host stays exactly as narrow as the spec requires.
- **The catalogue page is never injected into.** §2.4 suggests reading the page
  and volume from the parent DOM; doing so would need host permission for the
  catalogue origin, which §2.1 forbids. Metadata is scraped from the iframe
  instead (§2.4's stated alternative), and the iframe's offset comes from CDP.
- **`Emulation.setDeviceMetricsOverride` is set on attach, once per tab**, per
  §2.2. The operator no longer sizes the window by hand — the extension applies
  the calibrated viewport itself. Expect the page to look oddly scaled while
  attached; that is the override doing its job.
