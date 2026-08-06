// Service worker — capture-spec §2.2 and §2.5.
//
// Capture goes through chrome.debugger + Page.captureScreenshot, which reads
// composited pixels so canvas tainting is irrelevant. There is deliberately no
// toDataURL path: calibration.md confirmed the canvas is permanently tainted.
//
// MV3 terminates this worker whenever it likes (capture-spec §5), so it keeps
// no authoritative state. Session facts live in chrome.storage.session, the
// retry buffer in chrome.storage.local, and everything that matters lives in
// the local server.

const SERVER = "http://localhost:8787";
const CDP_VERSION = "1.3";
const BUFFER_CAP = 50; // capture-spec §2.5: "cap ~50 images"

// ---------------------------------------------------------------------------
// Settings. Calibration constants are fetched from the server, which reads them
// from calibration.md — never hardcoded here. See CLAUDE.md, "Calibration
// constants — never hardcode".
// ---------------------------------------------------------------------------

const FALLBACK_SETTINGS = {
  viewportW: null,
  viewportH: null,
  dpr: null,
  captureMethod: "chrome.debugger:Page.captureScreenshot",
  settleMs: 700,
  clipToCanvas: true,
  serverOnline: false,
};

async function getSettings() {
  const stored = await chrome.storage.session.get("settings");
  return { ...FALLBACK_SETTINGS, ...(stored.settings || {}) };
}

async function patchSettings(patch) {
  const settings = { ...(await getSettings()), ...patch };
  await chrome.storage.session.set({ settings });
  return settings;
}

async function refreshCalibration() {
  try {
    const res = await fetch(`${SERVER}/health`, { cache: "no-store" });
    if (!res.ok) throw new Error(`health returned ${res.status}`);
    const health = await res.json();
    const [w, h] = health.capture_viewport || [null, null];
    return await patchSettings({
      viewportW: w,
      viewportH: h,
      dpr: health.capture_dpr,
      captureMethod: health.capture_method || FALLBACK_SETTINGS.captureMethod,
      serverOnline: true,
      queueDepth: health.queue_depth,
    });
  } catch (err) {
    await note(`server unreachable: ${err.message}`);
    return await patchSettings({ serverOnline: false });
  }
}

// ---------------------------------------------------------------------------
// Session state for the popup
// ---------------------------------------------------------------------------

async function getState() {
  const s = await chrome.storage.session.get(["frames", "stats", "events", "attached"]);
  return {
    frames: s.frames || {},
    stats: s.stats || { stored: 0, dedup: 0, failed: 0, buffered: 0 },
    events: s.events || [],
    attached: s.attached || {},
    settings: await getSettings(),
  };
}

async function note(message, level = "info") {
  const { events = [] } = await chrome.storage.session.get("events");
  events.unshift({ at: new Date().toISOString(), message, level });
  await chrome.storage.session.set({ events: events.slice(0, 25) });
  if (level === "error") {
    chrome.action.setBadgeBackgroundColor({ color: "#c00" });
    chrome.action.setBadgeText({ text: "!" });
  }
  console[level === "error" ? "error" : "log"]("[zupu-capture]", message);
}

async function bumpStat(key, by = 1) {
  const { stats = { stored: 0, dedup: 0, failed: 0, buffered: 0 } } =
    await chrome.storage.session.get("stats");
  stats[key] = (stats[key] || 0) + by;
  await chrome.storage.session.set({ stats });
  if (key === "stored" || key === "dedup") {
    chrome.action.setBadgeBackgroundColor({ color: "#282" });
    chrome.action.setBadgeText({ text: String(stats.stored) });
  }
  return stats;
}

// ---------------------------------------------------------------------------
// Debugger attach / metrics override
// ---------------------------------------------------------------------------

function cdp(tabId, method, params = {}) {
  return chrome.debugger.sendCommand({ tabId }, method, params);
}

async function ensureAttached(tabId) {
  const { attached = {} } = await chrome.storage.session.get("attached");
  if (attached[tabId]) return true;

  try {
    await chrome.debugger.attach({ tabId }, CDP_VERSION);
  } catch (err) {
    const message = String(err?.message || err);
    // Re-attaching after a service-worker restart is normal, not an error.
    if (!/already attached/i.test(message)) {
      if (/devtools/i.test(message)) {
        await note(
          "Cannot attach: DevTools is open on this tab. Close DevTools and reload.",
          "error"
        );
      } else {
        await note(`debugger attach failed: ${message}`, "error");
      }
      return false;
    }
  }

  const settings = await getSettings();
  try {
    await cdp(tabId, "Page.enable");
    // Set the metrics override ONCE per attach (capture-spec §2.2): changing it
    // forces OSD to re-render and re-fetch every tile, which is slow and adds
    // load to the host.
    if (settings.viewportW && settings.viewportH && settings.dpr) {
      await cdp(tabId, "Emulation.setDeviceMetricsOverride", {
        width: settings.viewportW,
        height: settings.viewportH,
        deviceScaleFactor: settings.dpr,
        mobile: false,
      });
      await note(
        `viewport override ${settings.viewportW}x${settings.viewportH} @ DPR ${settings.dpr}`
      );
    } else {
      await note(
        "no calibration from server — capturing at the window's own size, which " +
          "will not match other pages",
        "error"
      );
    }
  } catch (err) {
    await note(`metrics override failed: ${err.message}`, "error");
  }

  attached[tabId] = { since: new Date().toISOString() };
  await chrome.storage.session.set({ attached });
  return true;
}

chrome.debugger.onDetach.addListener(async (source, reason) => {
  const { attached = {} } = await chrome.storage.session.get("attached");
  delete attached[source.tabId];
  await chrome.storage.session.set({ attached });
  await note(
    reason === "canceled_by_user"
      ? 'You clicked "Cancel" on the debugging banner — capture has STOPPED. ' +
          "Reload the viewer page to resume."
      : `debugger detached (${reason})`,
    "error"
  );
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const { attached = {} } = await chrome.storage.session.get("attached");
  if (attached[tabId]) {
    delete attached[tabId];
    await chrome.storage.session.set({ attached });
  }
});

// ---------------------------------------------------------------------------
// Coordinate resolution
//
// The content script reports the canvas rect in ITS OWN frame's coordinates.
// When the viewer sits in a cross-origin iframe those are not the top
// document's coordinates, so the iframe's offset has to be added. This is done
// over CDP rather than by injecting into the catalogue page, which keeps host
// permissions limited to dhapi.library.sh.cn as capture-spec §2.1 requires.
//
// UNVERIFIED against the live site — see extension/README.md §4. If the clip
// looks wrong, turn "clip to viewer" off in the popup; the server's auto-crop
// (calibration: AUTOCROP_REQUIRED) will trim the letterbox anyway.
// ---------------------------------------------------------------------------

async function iframeOffset(tabId) {
  try {
    const { root } = await cdp(tabId, "DOM.getDocument", { depth: 1 });
    const { nodeId } = await cdp(tabId, "DOM.querySelector", {
      nodeId: root.nodeId,
      selector: 'iframe[src*="dhapi.library.sh.cn"]',
    });
    if (!nodeId) return { x: 0, y: 0 }; // viewer is the top document
    const { model } = await cdp(tabId, "DOM.getBoxModel", { nodeId });
    return { x: model.content[0], y: model.content[1] };
  } catch {
    return { x: 0, y: 0 };
  }
}

async function computeClip(tabId, rect) {
  const settings = await getSettings();
  if (!settings.clipToCanvas || !rect?.width || !rect?.height) return null;

  const offset = await iframeOffset(tabId);
  const clip = {
    x: Math.max(0, Math.round(rect.x + offset.x)),
    y: Math.max(0, Math.round(rect.y + offset.y)),
    width: Math.round(rect.width),
    height: Math.round(rect.height),
    scale: 1,
  };
  // Implausible clip: fall back to the full frame rather than saving a sliver.
  if (clip.width < 200 || clip.height < 200) {
    await note(`clip looked wrong (${clip.width}x${clip.height}); capturing unclipped`);
    return null;
  }
  return clip;
}

// ---------------------------------------------------------------------------
// Capture
// ---------------------------------------------------------------------------

async function screenshotViaDebugger(tabId, clip) {
  const params = { format: "png", captureBeyondViewport: true };
  if (clip) params.clip = clip;
  const { data } = await cdp(tabId, "Page.captureScreenshot", params);
  return data;
}

async function screenshotViaVisibleTab(windowId) {
  // Fallback path (capture-spec §2.2). Capped at the physical viewport times
  // the real DPR, so it is only viable if the operator's own screen clears the
  // Milestone 0 resolution gate. Same interface, swappable via settings.
  const dataUrl = await chrome.tabs.captureVisibleTab(windowId, { format: "png" });
  return dataUrl.split(",")[1];
}

async function imageSize(base64) {
  try {
    const blob = await (await fetch(`data:image/png;base64,${base64}`)).blob();
    const bitmap = await createImageBitmap(blob);
    const size = { width: bitmap.width, height: bitmap.height };
    bitmap.close();
    return size;
  } catch {
    return { width: null, height: null };
  }
}

async function handleSettled(msg, sender) {
  const tabId = sender.tab?.id;
  if (tabId == null) return;

  const settings = await getSettings();
  if (!settings.serverOnline) await refreshCalibration();

  let base64;
  try {
    if (settings.captureMethod.startsWith("chrome.debugger")) {
      if (!(await ensureAttached(tabId))) return;
      const clip = await computeClip(tabId, msg.rect);
      base64 = await screenshotViaDebugger(tabId, clip);
    } else {
      base64 = await screenshotViaVisibleTab(sender.tab.windowId);
    }
  } catch (err) {
    await note(`capture failed: ${err.message}`, "error");
    await bumpStat("failed");
    return;
  }

  const size = await imageSize(base64);
  const body = {
    image_b64: base64,
    captured_at: new Date().toISOString(),
    viewer_page: msg.viewerPage,
    viewer_volume: msg.viewerVolume,
    // Actual decoded pixel dimensions, not the requested viewport — this is the
    // number the server compares against calibration.
    canvas_w: size.width,
    canvas_h: size.height,
    dpr: settings.dpr,
    capture_method: settings.captureMethod,
  };

  await chrome.storage.session.set({
    lastPage: { page: msg.viewerPage, volume: msg.viewerVolume, at: body.captured_at, size },
  });

  await postCapture(body);
}

// ---------------------------------------------------------------------------
// Transport — fire and forget, buffer on failure (capture-spec §2.5)
// ---------------------------------------------------------------------------

async function postCapture(body) {
  try {
    const res = await fetch(`${SERVER}/capture`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`server returned ${res.status}`);

    const result = await res.json();
    await bumpStat(result.status === "dedup" ? "dedup" : "stored");
    await patchSettings({ serverOnline: true, queueDepth: result.queue_depth });
    if (result.warnings?.length) {
      for (const warning of result.warnings) await note(`server: ${warning}`);
    }
    await flushBuffer();
    return true;
  } catch (err) {
    await note(`POST failed (${err.message}) — buffering`, "error");
    await bufferCapture(body);
    await patchSettings({ serverOnline: false });
    return false;
  }
}

async function bufferCapture(body) {
  const { buffer = [] } = await chrome.storage.local.get("buffer");
  buffer.push(body);
  let dropped = 0;
  while (buffer.length > BUFFER_CAP) {
    buffer.shift();
    dropped++;
  }
  await chrome.storage.local.set({ buffer });
  await bumpStat("buffered");
  if (dropped) {
    // A dropped capture is a silent hole in the genealogy, which capture-spec
    // §7 calls out as the failure mode that costs a whole session.
    await note(
      `BUFFER FULL — dropped ${dropped} capture(s). Start the server, then ` +
        "revisit those pages. Check the coverage strip.",
      "error"
    );
  }
}

async function flushBuffer() {
  const { buffer = [] } = await chrome.storage.local.get("buffer");
  if (!buffer.length) return;

  const remaining = [...buffer];
  while (remaining.length) {
    const body = remaining[0];
    try {
      const res = await fetch(`${SERVER}/capture`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`server returned ${res.status}`);
      remaining.shift();
      await bumpStat("stored");
    } catch {
      break; // still down; keep what is left for the next attempt
    }
  }
  await chrome.storage.local.set({ buffer: remaining });
  if (!remaining.length) await note("buffered captures flushed");
}

// ---------------------------------------------------------------------------
// Messaging
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  switch (msg.type) {
    case "frame-hello": {
      // Respond immediately; the content script must never wait on us.
      sendResponse({ ok: true });
      (async () => {
        const { frames = {} } = await chrome.storage.session.get("frames");
        const key = `${sender.tab?.id ?? "?"}:${sender.frameId ?? "?"}`;
        frames[key] = {
          ...msg.frame,
          tabId: sender.tab?.id ?? null,
          frameId: sender.frameId ?? null,
          hasViewer: msg.frame.hasViewer || frames[key]?.hasViewer || false,
        };
        await chrome.storage.session.set({ frames });
      })();
      return false;
    }

    case "get-settings":
      getSettings().then(sendResponse);
      return true;

    case "settled":
      sendResponse({ ok: true });
      handleSettled(msg, sender);
      return false;

    case "get-state":
      getState().then(sendResponse);
      return true;

    case "refresh":
      refreshCalibration().then(() => getState().then(sendResponse));
      return true;

    case "set-clip":
      patchSettings({ clipToCanvas: !!msg.value }).then(sendResponse);
      return true;

    case "clear-badge":
      chrome.action.setBadgeText({ text: "" });
      sendResponse({ ok: true });
      return false;

    default:
      return false;
  }
});

chrome.runtime.onStartup.addListener(refreshCalibration);
chrome.runtime.onInstalled.addListener(refreshCalibration);
refreshCalibration();
