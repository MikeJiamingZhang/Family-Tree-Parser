// Settle detection — capture-spec §2.3.
//
// The canvas is tainted (calibration.md, "Canvas is tainted — CONFIRMED"), so
// getImageData and drawImage-then-read are both unavailable and pixel
// signatures cannot be computed here. This uses the three signals the spec
// prescribes instead: network quiescence, page identity from the viewer UI, and
// a minimum tile count.
//
// Everything in this file must stay cheap. The operator must never wait on it.

(() => {
  // Tunables. SETTLE_MS is calibration's starting value; the service worker
  // overwrites it from the server's /health, which reads calibration.md.
  let SETTLE_MS = 700;
  const MIN_TILES = 1;

  // A capture is only worth taking in the frame that owns the viewer.
  const viewerEl = () => document.querySelector(".openseadragon-canvas canvas");

  let tilesSinceCapture = 0;
  let quietTimer = null;
  let lastKey = null;
  let paused = false;

  chrome.runtime
    .sendMessage({ type: "get-settings" })
    .then((settings) => {
      if (settings?.settleMs) SETTLE_MS = settings.settleMs;
    })
    .catch(() => {});

  // 1. Network quiescence — observe tile requests without touching pixels.
  const observer = new PerformanceObserver((list) => {
    for (const e of list.getEntries()) {
      if (
        e.initiatorType === "img" ||
        e.initiatorType === "fetch" ||
        e.initiatorType === "xmlhttprequest"
      ) {
        tilesSinceCapture++;
        clearTimeout(quietTimer);
        quietTimer = setTimeout(maybeCapture, SETTLE_MS);
      }
    }
  });
  observer.observe({ type: "resource", buffered: true });

  // 2. Page identity — from the viewer UI, not the pixels.
  //
  // These selectors are a best guess at the dhapi viewer's markup and are the
  // most likely thing in this file to need correcting against the real site.
  // A null key is not fatal: it disables only the cheap client-side dedup, and
  // the server still dedups on the image bytes.
  function textOf(selectors) {
    for (const sel of selectors) {
      const el = document.querySelector(sel);
      const value = (el?.value ?? el?.textContent ?? "").trim();
      if (value) return value;
    }
    return "";
  }

  function pageLabel() {
    return textOf(['input[value*="/"]', ".page-indicator", "[class*='page'] input", "[class*='pageNum']"]);
  }

  function volumeLabel() {
    return textOf(["[class*='volume'] .active", "[class*='ce'] .active", "select[class*='volume']"]);
  }

  function pageKey() {
    return `${pageLabel()}|${volumeLabel()}`;
  }

  function maybeCapture() {
    if (paused) return;
    const canvas = viewerEl();
    if (!canvas) return; // not the viewer frame, or OSD has not mounted

    const key = pageKey();
    if (tilesSinceCapture < MIN_TILES) return;
    // 3. Dedup on page identity. Only skips when the key is non-empty — an
    // unreadable indicator must not suppress every capture in the session.
    if (key && key !== "|" && key === lastKey) return;

    lastKey = key;
    tilesSinceCapture = 0;
    requestCapture(key);
  }

  function requestCapture(key, opts = {}) {
    const canvas = viewerEl();
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();

    // Fire and forget. No awaiting, no network, nothing that blocks browsing.
    chrome.runtime
      .sendMessage({
        type: "settled",
        pageKey: key,
        viewerPage: pageLabel() || null,
        viewerVolume: volumeLabel() || null,
        manual: !!opts.manual,
        // Frame-local CSS pixels. The service worker converts to top-document
        // coordinates before clipping.
        rect: {
          x: rect.x + window.scrollX,
          y: rect.y + window.scrollY,
          width: rect.width,
          height: rect.height,
        },
      })
      .catch(() => {
        /* worker asleep or reloading; the next settle will retry */
      });
  }

  // The popup's manual capture button, for when settle detection misses a page.
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.type === "force-capture") {
      const canvas = viewerEl();
      if (!canvas) {
        sendResponse({ ok: false, reason: "no viewer canvas in this frame" });
        return;
      }
      lastKey = null; // bypass the identity dedup
      tilesSinceCapture = MIN_TILES;
      requestCapture(pageKey(), { manual: true });
      sendResponse({ ok: true });
    } else if (msg.type === "set-paused") {
      paused = !!msg.paused;
      sendResponse({ ok: true, paused });
    }
  });
})();
