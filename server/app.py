"""`POST /capture` — capture-spec §3.1.

Must return in ~20ms, so this does the minimum inline: hash, dedup, write the
PNG, insert one row, return the queue depth. Quality gating (blur, dHash,
auto-crop) and all model calls belong to the worker and are not implemented at
this milestone.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from server import db
from server.config import ServerConfig

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Guards against a runaway extension filling the disk. A full-frame DPR 1
# capture is a few MB; 64 MB is far above any legitimate single page.
MAX_IMAGE_BYTES = 64 * 1024 * 1024


class CapturePayload(BaseModel):
    """The extension's POST body, capture-spec §2.5.

    Every field except the image itself is optional. A capture that arrives
    without viewer metadata is still worth keeping — the authoritative page
    number is the 版心 printed on the leaf, which is read from the image later.
    """

    image_b64: str
    signature: str | None = None
    captured_at: str | None = None
    viewer_page: str | None = None
    viewer_volume: str | None = None
    canvas_w: int | None = None
    canvas_h: int | None = None
    dpr: float | None = None
    capture_method: str | None = None


class CaptureResponse(BaseModel):
    ok: bool = True
    status: str = Field(description="stored | dedup")
    capture_id: int
    sha256: str
    seen_count: int
    queue_depth: int
    warnings: list[str] = []


def _decode_png(image_b64: str) -> bytes:
    try:
        image = base64.b64decode(image_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"image_b64 is not valid base64: {exc}")
    if not image:
        raise HTTPException(status_code=400, detail="image_b64 decoded to zero bytes")
    if len(image) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413, detail=f"image exceeds {MAX_IMAGE_BYTES} byte limit"
        )
    if not image.startswith(PNG_MAGIC):
        raise HTTPException(status_code=400, detail="image_b64 is not a PNG")
    return image


def _write_image(image_dir: Path, sha256: str, image: bytes) -> Path:
    """Write `{sha256}.png` atomically. Content-addressed, so a rewrite is a no-op."""
    path = image_dir / f"{sha256}.png"
    if path.exists():
        return path
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_bytes(image)
    os.replace(tmp, path)
    return path


def _viewport_warnings(payload: CapturePayload, config: ServerConfig) -> list[str]:
    """Advisory only — capture-spec §5, "everything captured at the wrong size".

    Device emulation resets on reload, and captures taken at a different
    viewport produce crops and coordinates that drift against every other page.
    Never rejects: a wrongly sized page is still better than a missing one, and
    the operator needs to be told rather than stonewalled.
    """
    warnings: list[str] = []
    expected_w, expected_h = config.calibration.viewport_w, config.calibration.viewport_h
    if payload.canvas_w is not None and payload.canvas_w != expected_w:
        warnings.append(
            f"canvas_w={payload.canvas_w} but calibration expects {expected_w}"
        )
    if payload.canvas_h is not None and payload.canvas_h != expected_h:
        warnings.append(
            f"canvas_h={payload.canvas_h} but calibration expects {expected_h}"
        )
    if payload.dpr is not None and float(payload.dpr) != config.calibration.dpr:
        warnings.append(
            f"dpr={payload.dpr} but calibration expects {config.calibration.dpr} "
            "(DPR 2 was measured to add no real detail)"
        )
    return warnings


def create_app(config: ServerConfig | None = None) -> FastAPI:
    config = config or ServerConfig.from_env()
    config.image_dir.mkdir(parents=True, exist_ok=True)
    db.init_db(config.db_path)

    app = FastAPI(title="Zupu capture server", version="0.1.0")
    app.state.config = config

    @app.get("/health")
    def health() -> dict[str, object]:
        with db.transaction(config.db_path) as conn:
            depth = db.queue_depth(conn)
            total = conn.execute("SELECT COUNT(*) AS n FROM captures").fetchone()["n"]
        return {
            "ok": True,
            "queue_depth": depth,
            "captures": total,
            "calibration": str(config.calibration.source_path),
            "capture_viewport": [
                config.calibration.viewport_w,
                config.calibration.viewport_h,
            ],
            "capture_dpr": config.calibration.dpr,
            "capture_method": config.calibration.capture_method,
        }

    # Sync `def` on purpose: FastAPI runs it in a threadpool, so the blocking
    # file write and SQLite call cannot stall the event loop.
    @app.post("/capture", response_model=CaptureResponse)
    def capture(payload: CapturePayload) -> CaptureResponse:
        image = _decode_png(payload.image_b64)
        sha256 = hashlib.sha256(image).hexdigest()
        warnings = _viewport_warnings(payload, config)

        with db.transaction(config.db_path) as conn:
            existing = db.find_by_sha256(conn, sha256)
            if existing is not None:
                seen_count = db.bump_seen_count(conn, existing["id"])
                return CaptureResponse(
                    status="dedup",
                    capture_id=existing["id"],
                    sha256=sha256,
                    seen_count=seen_count,
                    queue_depth=db.queue_depth(conn),
                    warnings=warnings,
                )

            path = _write_image(config.image_dir, sha256, image)
            try:
                capture_id = db.insert_capture(
                    conn,
                    signature=payload.signature,
                    sha256=sha256,
                    path=str(path),
                    captured_at=payload.captured_at,
                    viewer_page=payload.viewer_page,
                    viewer_volume=payload.viewer_volume,
                    canvas_w=payload.canvas_w,
                    canvas_h=payload.canvas_h,
                    dpr=payload.dpr,
                    capture_method=payload.capture_method,
                    status="pending",
                )
            except sqlite3.IntegrityError:
                # Two identical captures raced. The image is content-addressed,
                # so the file on disk is correct either way; adopt the winner.
                existing = db.find_by_sha256(conn, sha256)
                if existing is None:
                    raise
                seen_count = db.bump_seen_count(conn, existing["id"])
                return CaptureResponse(
                    status="dedup",
                    capture_id=existing["id"],
                    sha256=sha256,
                    seen_count=seen_count,
                    queue_depth=db.queue_depth(conn),
                    warnings=warnings,
                )

            return CaptureResponse(
                status="stored",
                capture_id=capture_id,
                sha256=sha256,
                seen_count=1,
                queue_depth=db.queue_depth(conn),
                warnings=warnings,
            )

    return app
