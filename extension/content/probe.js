// Injection probe — capture-spec §2.1.
//
// "Content script must match the iframe origin and run with all_frames: true.
//  Matching only the parent catalogue page will silently capture nothing —
//  this is the most likely first bug."
//
// This file exists to make that bug loud instead of silent. It runs at
// document_start in every frame the manifest matches, logs where it landed, and
// reports the frame to the service worker so the popup can show it without the
// operator opening DevTools.
//
// VERIFY THIS BEFORE TRUSTING ANYTHING ELSE IN THE EXTENSION. See
// extension/README.md §1.

(() => {
  const isTop = window.top === window;

  const frame = {
    href: location.href,
    origin: location.origin,
    isTop,
    // A frame is the viewer only if OpenSeadragon's container is in it. Checked
    // later too — at document_start the DOM is usually still empty.
    hasViewerAtStart: !!document.querySelector(".openseadragon-container, .openseadragon-canvas"),
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    devicePixelRatio: window.devicePixelRatio,
    at: new Date().toISOString(),
  };

  console.log(
    `%c[zupu-capture] content script injected into ${isTop ? "TOP frame" : "SUBFRAME"}`,
    "color:#0a0;font-weight:bold",
    frame
  );

  // The service worker may be asleep; this wakes it. Failure here is not fatal
  // to capture, so it is logged and swallowed.
  chrome.runtime
    .sendMessage({ type: "frame-hello", frame })
    .catch((err) => console.warn("[zupu-capture] could not reach service worker:", err));

  // Re-report once the DOM exists, so `hasViewer` reflects reality rather than
  // an empty document_start snapshot.
  const reportWhenReady = () => {
    const viewer = document.querySelector(".openseadragon-container, .openseadragon-canvas");
    if (!viewer) return false;
    console.log("[zupu-capture] OpenSeadragon container found in", location.href);
    chrome.runtime
      .sendMessage({ type: "frame-hello", frame: { ...frame, hasViewer: true } })
      .catch(() => {});
    return true;
  };

  if (!reportWhenReady()) {
    // OSD mounts asynchronously (bundled React). Poll briefly, then give up
    // quietly rather than spinning for the life of the tab.
    let tries = 0;
    const timer = setInterval(() => {
      if (reportWhenReady() || ++tries > 60) clearInterval(timer);
    }, 500);
  }
})();
