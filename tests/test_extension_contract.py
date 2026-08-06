"""Checks on the extension that do not need a browser.

The extension itself can only be verified against the live viewer (see
`extension/README.md` §1). These tests cover the parts that can be checked
statically or against the local server, and they encode the two findings that
would be most expensive to regress: the canvas is tainted, and the content
script must reach the iframe.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.config import Calibration, ServerConfig
from tests.test_capture_smoke import png_bytes

EXTENSION = Path(__file__).resolve().parent.parent / "extension"
VIEWER_ORIGIN = "https://dhapi.library.sh.cn/*"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))


def extension_sources() -> list[Path]:
    return sorted(EXTENSION.rglob("*.js"))


def strip_js_comments(source: str) -> str:
    """Drop `//` and `/* */` comments, leaving string literals intact.

    These files discuss `toDataURL` and `getImageData` in comments precisely
    because they must not be called, so the bans below have to scan code only.
    """
    out: list[str] = []
    quote: str | None = None
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if quote:
            out.append(ch)
            if ch == "\\":
                if i + 1 < n:
                    out.append(nxt)
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
            out.append(ch)
        elif ch == "/" and nxt == "/":
            while i < n and source[i] != "\n":
                i += 1
            continue
        elif ch == "/" and nxt == "*":
            end = source.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def test_manifest_is_mv3_with_debugger(manifest: dict) -> None:
    assert manifest["manifest_version"] == 3
    # Page.captureScreenshot is unreachable without it.
    assert "debugger" in manifest["permissions"]


def test_content_script_reaches_the_iframe(manifest: dict) -> None:
    """capture-spec §2.1: matching only the parent page captures nothing."""
    entries = manifest["content_scripts"]
    viewer = [e for e in entries if VIEWER_ORIGIN in e["matches"]]
    assert viewer, f"no content script matches {VIEWER_ORIGIN}"
    for entry in viewer:
        assert entry["all_frames"] is True, "all_frames must be true to reach the iframe"


def test_host_permissions_stay_narrow(manifest: dict) -> None:
    """capture-spec §2.1: 'Host permissions limited to dhapi. Nothing broader.'"""
    allowed = {VIEWER_ORIGIN, "http://localhost:8787/*"}
    assert set(manifest["host_permissions"]) <= allowed


def test_no_todataurl_path_anywhere() -> None:
    """calibration.md: the canvas is permanently tainted.

    `toDataURL` would throw `SecurityError` at runtime on the real viewer, and
    the failure is easy to mistake for a transient bug. Keep it out of the tree.
    """
    offenders = [
        path.relative_to(EXTENSION).as_posix()
        for path in extension_sources()
        if "toDataURL" in strip_js_comments(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"toDataURL must not be used: {offenders}"


def test_settle_detection_does_not_read_pixels() -> None:
    """The tainted canvas also rules out getImageData-based settle detection."""
    raw = (EXTENSION / "content" / "settle.js").read_text(encoding="utf-8")
    code = strip_js_comments(raw)
    assert "PerformanceObserver" in code
    for banned in ("getImageData", "drawImage"):
        assert banned not in code, f"{banned} cannot work on a tainted canvas"


def test_calibration_constants_are_not_hardcoded() -> None:
    """The extension must take viewport/DPR from the server, not inline them."""
    calibration = Calibration.load()
    literals = (str(calibration.viewport_w), str(calibration.viewport_h))
    for path in extension_sources():
        source = path.read_text(encoding="utf-8")
        for literal in literals:
            assert literal not in source, (
                f"{path.name} hardcodes {literal}; fetch it from /health instead"
            )


def test_extension_payload_is_accepted_by_the_server(tmp_path: Path) -> None:
    """The exact body shape background.js posts must satisfy CapturePayload."""
    config = ServerConfig(
        db_path=tmp_path / "zupu.db",
        data_dir=tmp_path / "data",
        calibration=Calibration.load(),
    )
    client = TestClient(create_app(config))

    import base64

    image = png_bytes()
    body = {
        "image_b64": base64.b64encode(image).decode("ascii"),
        "captured_at": "2026-08-06T15:12:03.000Z",
        "viewer_page": "13/237",
        "viewer_volume": "第1册",
        "canvas_w": config.calibration.viewport_w,
        "canvas_h": config.calibration.viewport_h,
        "dpr": config.calibration.dpr,
        "capture_method": config.calibration.capture_method,
    }

    response = client.post("/capture", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "stored"
    # A correctly calibrated capture must not trip the viewport warning.
    assert payload["warnings"] == []


def test_null_viewer_metadata_is_accepted(tmp_path: Path) -> None:
    """The viewer UI selectors may not match; a capture must survive that."""
    config = ServerConfig(
        db_path=tmp_path / "zupu.db",
        data_dir=tmp_path / "data",
        calibration=Calibration.load(),
    )
    client = TestClient(create_app(config))

    import base64

    body = {
        "image_b64": base64.b64encode(png_bytes()).decode("ascii"),
        "captured_at": "2026-08-06T15:12:03.000Z",
        "viewer_page": None,
        "viewer_volume": None,
        "canvas_w": None,
        "canvas_h": None,
        "dpr": config.calibration.dpr,
        "capture_method": config.calibration.capture_method,
    }
    assert client.post("/capture", json=body).json()["status"] == "stored"
