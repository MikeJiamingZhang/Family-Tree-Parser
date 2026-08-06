# Family Tree Parser — Operator Guide

This project reads a scanned Chinese genealogy (族譜) page by page as you browse
it in your web browser, and turns the photographed pages into a family tree you
can search and export. You do the browsing; the software does the transcribing,
and flags anything it is unsure about for you to check.

> **Note:** the server and the extension are both built, but the extension has
> **never been run against the real viewer**. Before a real session, work
> through `extension/README.md` §1 and §4 — a few selectors in it are educated
> guesses and may need correcting. Step 8's progress page is not built yet.
> See `CLAUDE.md` for build status.

---

## 1. What you need

| Thing | Why | Where |
|---|---|---|
| Python 3.11 or newer | runs the local server | https://www.python.org/downloads/ |
| Microsoft Edge | runs the capture extension | already on Windows |
| An Anthropic API key | reads the page images | https://console.anthropic.com |

When installing Python, **tick "Add Python to PATH"** on the first screen of the
installer. If you miss it, uninstall and reinstall — it is easier than fixing it.

Check it worked. Open a terminal and run:

```
python --version
```

You should see `Python 3.11.x` or higher.

## 2. One-time setup

In the terminal, go to the project folder and install what it needs:

```
cd "D:\Coursework\Side Projects\Family-Tree-Parser"
python -m pip install -r requirements.txt
```

## 3. Your API key

The key is a long password that lets the software read pages. It goes in a file
called `.env` in the project folder — never in a message, a document, or the
code.

Create the file with this command, replacing the `sk-ant-...` part with your
real key:

```
echo ANTHROPIC_API_KEY=sk-ant-your-key-here > .env
```

`.env` is ignored by Git, so it will never be committed or shared.

## 4. Start the server

Leave this running for the whole session. In the terminal:

```
python -m server
```

You should see something like `listening on http://localhost:8787`, followed by
the capture settings it read from `calibration.md`. You do not need to do
anything with those numbers — the extension fetches them from the server and
sets the browser up itself. They are printed so you can see what is in force.

**Leave this terminal window open.** Closing it stops the capture.

## 5. Load the extension in Edge

You only do this once. Edge will keep it loaded afterwards.

1. Open Edge and go to `edge://extensions`.
2. Turn on **Developer mode** — the switch is on the left-hand side.
3. Click **Load unpacked**.
4. Select the `extension` folder inside the project folder, and click Select.
5. "Zupu Capture" now appears in your extension list. Pin it (the puzzle-piece
   icon in the toolbar) so you can see its counter while you work.

## 6. The "debugging this browser" banner

When capture starts, Edge shows a yellow bar reading something like
**"Zupu Capture is debugging this browser"**.

**This is expected and it is not a problem.** The library's viewer protects its
page images in a way that stops ordinary screenshots from working, so the
extension uses Edge's built-in debugging tool to photograph the page instead. It
is the only method that works, and it only ever reads pages you have opened
yourself.

⚠️ **Do not click "Cancel" on that banner.** Cancelling disconnects the
extension and capture stops silently — you will keep browsing and nothing will
be saved. Just ignore the bar. If you do click it by accident, reload the viewer
page to reconnect.

## 7. A normal session

1. Start the server (§4).
2. Open the genealogy in Edge. Click the Zupu Capture icon and check the
   **Injection** block says `✓ injecting into the dhapi.library.sh.cn iframe`.
   If it does not, stop — see `extension/README.md` §1.
3. Open the volume you are working on, and zoom the viewer so the page **fills
   the frame** with as little black border as possible. This matters — black
   border is wasted cost on every single page. The page may look oddly scaled
   once capture starts; that is the extension setting the picture size, and it
   is normal.
4. Click through the pages at comfortable reading speed. Pause a beat on each
   page so it finishes loading sharply before you move on.
5. Watch the counter on the extension icon go up. If it stops going up, stop and
   check §9.
6. When you are done, close Edge. Stop the server with `Ctrl+C` in the terminal.

If a page gets missed, open the popup and click **Capture this page now**.

You never have to wait for the software. It works through the pages in the
background, often finishing long after you have stopped browsing.

## 8. Checking progress

Open `http://localhost:8787` in a browser tab while the server is running. It
shows:

- **Counters** — pages captured, waiting, read, and flagged for your attention.
- **The coverage strip** — a grid with one square per page of the volume. Green
  means captured, grey means missed. This is the important one: any grey square
  is a page you skipped, and going back for it now is far easier than
  discovering the hole weeks later.
- **The flagged queue** — pages the software could not read confidently, shown
  next to what it thought it saw, for you to correct.

## 9. If something looks wrong

| What you see | What to do |
|---|---|
| Counter is not going up | Reload the viewer page in Edge. If the yellow debugging bar is gone, it was cancelled — reloading brings it back. |
| Popup says "No frames reported" | The extension is not seeing the viewer at all. Stop and read `extension/README.md` §1. |
| Popup says the server is offline | The terminal from §4 was closed. Restart it; buffered pages are sent automatically. |
| Popup says "BUFFER FULL" | The server was down too long and pages were lost. Restart it and revisit those pages. |
| "Cannot attach: DevTools is open" | Close DevTools on that tab and reload it. |
| Lots of grey squares | You were clicking faster than pages finished loading. Go back and revisit them more slowly. |
| Pages flagged as blurry | Wait a moment longer on each page before clicking onward. |
| Terminal shows a red error | Copy the whole message before closing anything. It is the only record of what went wrong. |

## 10. Please do not

- Speed up browsing with any automatic page-turner. The library serves this for
  free; browsing at human speed is part of the deal.
- Share or upload the page images. They belong to Shanghai Library. The family
  data the software extracts is yours; the scans are not.
