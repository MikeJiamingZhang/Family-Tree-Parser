"""Smoke test for `POST /capture` — capture-spec §4 step 2.

The load-bearing assertion is the second one: posting the same PNG twice must
store one image and report `dedup`. The operator revisits pages constantly, so
a dedup failure would duplicate rows and re-bill every revisited page.
"""

from __future__ import annotations

import base64
import struct
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.config import Calibration, ServerConfig
from server.db import transaction


def png_bytes(pixel: tuple[int, int, int] = (0, 0, 0)) -> bytes:
    """A valid 1x1 PNG, built by hand so the tests need no image library."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1, 8-bit truecolour
    idat = zlib.compress(b"\x00" + bytes(pixel))  # filter byte + one pixel
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def payload(image: bytes, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "image_b64": base64.b64encode(image).decode("ascii"),
        "captured_at": "2026-08-06T15:12:03Z",
        "viewer_page": "13/237",
        "viewer_volume": "第1册",
        "capture_method": "chrome.debugger:Page.captureScreenshot",
    }
    body.update(overrides)
    return body


@pytest.fixture
def config(tmp_path: Path) -> ServerConfig:
    return ServerConfig(
        db_path=tmp_path / "zupu.db",
        data_dir=tmp_path / "data",
        calibration=Calibration.load(),  # the real calibration.md
    )


@pytest.fixture
def client(config: ServerConfig) -> TestClient:
    return TestClient(create_app(config))


def test_post_twice_dedups(client: TestClient, config: ServerConfig) -> None:
    image = png_bytes()

    first = client.post("/capture", json=payload(image))
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["status"] == "stored"
    assert first_body["seen_count"] == 1
    assert first_body["queue_depth"] == 1

    second = client.post("/capture", json=payload(image))
    assert second.status_code == 200, second.text
    second_body = second.json()

    assert second_body["status"] == "dedup"
    assert second_body["seen_count"] == 2
    assert second_body["capture_id"] == first_body["capture_id"]
    assert second_body["sha256"] == first_body["sha256"]
    # The revisit must not deepen the queue: the page is already pending.
    assert second_body["queue_depth"] == 1

    stored = list(config.image_dir.glob("*.png"))
    assert len(stored) == 1
    assert stored[0].read_bytes() == image
    assert stored[0].stem == first_body["sha256"]

    with transaction(config.db_path) as conn:
        rows = conn.execute("SELECT sha256, seen_count, status FROM captures").fetchall()
    assert len(rows) == 1
    assert rows[0]["seen_count"] == 2
    assert rows[0]["status"] == "pending"


def test_different_images_both_stored(client: TestClient, config: ServerConfig) -> None:
    for pixel in ((0, 0, 0), (255, 255, 255)):
        response = client.post("/capture", json=payload(png_bytes(pixel)))
        assert response.json()["status"] == "stored"

    assert len(list(config.image_dir.glob("*.png"))) == 2
    assert client.get("/health").json()["queue_depth"] == 2


def test_rejects_non_png(client: TestClient) -> None:
    response = client.post("/capture", json=payload(b"GIF89a not a png"))
    assert response.status_code == 400
    assert "not a PNG" in response.json()["detail"]


def test_rejects_bad_base64(client: TestClient) -> None:
    response = client.post("/capture", json={"image_b64": "@@@not base64@@@"})
    assert response.status_code == 400


def test_viewport_mismatch_warns_but_stores(client: TestClient, config: ServerConfig) -> None:
    wrong_width = config.calibration.viewport_w + 100
    response = client.post("/capture", json=payload(png_bytes(), canvas_w=wrong_width))

    body = response.json()
    assert body["status"] == "stored"  # advisory only, never rejected
    assert any("canvas_w" in warning for warning in body["warnings"])
