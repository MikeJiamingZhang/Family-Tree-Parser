// Popup — live status (capture-spec §2.1).
//
// The Injection block is first on purpose. Capture-spec §2.1 calls a content
// script that never reaches the iframe "the most likely first bug", and it is
// silent by nature, so the operator gets to see the answer without opening
// DevTools on a cross-origin frame.

const $ = (id) => document.getElementById(id);

function frameLine(frame) {
  const li = document.createElement("li");
  const where = frame.isTop ? "top frame" : "subframe";
  const viewer = frame.hasViewer
    ? '<span class="ok">viewer ✓</span>'
    : '<span class="muted">no viewer</span>';
  li.innerHTML = `${viewer} <span class="muted">${where}</span><br><code>${frame.href}</code>`;
  return li;
}

function render(state) {
  // --- injection -----------------------------------------------------------
  const frames = Object.values(state.frames || {});
  const box = $("frames");
  box.innerHTML = "";

  if (!frames.length) {
    box.innerHTML =
      '<span class="bad">No frames reported.</span> ' +
      '<span class="muted">Open the viewer and reload the page. If this stays ' +
      "empty, the content script is not matching the iframe origin.</span>";
  } else {
    const dhapi = frames.filter((f) => f.origin?.includes("dhapi.library.sh.cn"));
    const header = document.createElement("div");
    header.innerHTML = dhapi.some((f) => !f.isTop)
      ? '<span class="ok">✓ injecting into the dhapi.library.sh.cn iframe</span>'
      : '<span class="bad">⚠ no dhapi SUBFRAME seen</span> <span class="muted">' +
        "(fine if you opened the viewer in its own tab)</span>";
    box.appendChild(header);

    const list = document.createElement("ul");
    for (const frame of frames) list.appendChild(frameLine(frame));
    box.appendChild(list);
  }

  // --- server --------------------------------------------------------------
  const s = state.settings || {};
  $("server").innerHTML = s.serverOnline
    ? '<span class="ok">online</span>'
    : '<span class="bad">offline</span>';
  $("viewport").textContent =
    s.viewportW && s.viewportH ? `${s.viewportW}×${s.viewportH} @ DPR ${s.dpr}` : "unknown";
  $("queue").textContent = s.queueDepth ?? "—";

  // --- capture -------------------------------------------------------------
  const attachedCount = Object.keys(state.attached || {}).length;
  $("debugger").innerHTML = attachedCount
    ? `<span class="ok">attached (${attachedCount} tab)</span>`
    : '<span class="muted">not attached yet</span>';

  const stats = state.stats || {};
  $("stored").textContent = stats.stored || 0;
  $("dedup").textContent = stats.dedup || 0;
  $("buffered").textContent = `${stats.buffered || 0} / ${stats.failed || 0}`;
  $("clip").checked = s.clipToCanvas !== false;

  chrome.storage.session.get("lastPage").then(({ lastPage }) => {
    $("last").textContent = lastPage
      ? `${lastPage.volume || "?"} ${lastPage.page || "?"}` +
        (lastPage.size?.width ? ` (${lastPage.size.width}×${lastPage.size.height})` : "")
      : "—";
  });

  // --- events --------------------------------------------------------------
  const events = $("events");
  events.innerHTML = "";
  for (const event of state.events || []) {
    const li = document.createElement("li");
    li.className = event.level === "error" ? "bad" : "muted";
    li.textContent = `${event.at.slice(11, 19)}  ${event.message}`;
    events.appendChild(li);
  }
}

async function refresh(hard = false) {
  const state = await chrome.runtime.sendMessage({ type: hard ? "refresh" : "get-state" });
  if (state) render(state);
}

$("refresh").addEventListener("click", () => refresh(true));

$("clip").addEventListener("change", async (event) => {
  await chrome.runtime.sendMessage({ type: "set-clip", value: event.target.checked });
  refresh();
});

$("capture").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;
  // Broadcast to every frame; only the one holding the viewer canvas acts.
  chrome.tabs.sendMessage(tab.id, { type: "force-capture" }).catch(() => {});
  setTimeout(refresh, 1200);
});

chrome.runtime.sendMessage({ type: "clear-badge" }).catch(() => {});
refresh(true);
setInterval(refresh, 2000);
