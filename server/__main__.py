"""`python -m server` — start the local capture server."""

from __future__ import annotations

import sys

import uvicorn

from server.app import create_app
from server.config import CalibrationError, ServerConfig


def main() -> int:
    try:
        config = ServerConfig.from_env()
    except CalibrationError as exc:
        print(f"calibration error: {exc}", file=sys.stderr)
        print(
            "The server reads its capture constants from calibration.md. "
            "Fix that file (or set ZUPU_CALIBRATION) and try again.",
            file=sys.stderr,
        )
        return 1

    cal = config.calibration
    # The operator guide (README §4) tells the reader to take the window setup
    # off this banner rather than memorising numbers that calibration may revise.
    banner = "\n".join(
        [
            f"zupu capture server  ->  http://{config.host}:{config.port}",
            f"  calibration : {cal.source_path}",
            f"  window size : {cal.viewport_w} x {cal.viewport_h} at DPR {cal.dpr:g}",
            f"  method      : {cal.capture_method}",
            f"  images      : {config.image_dir}",
            f"  database    : {config.db_path}",
            "Leave this window open. Ctrl+C to stop.",
        ]
    )
    # flush: uvicorn logs to stderr unbuffered, so a buffered banner would
    # surface out of order, or not until shutdown.
    print(banner, flush=True)

    uvicorn.run(create_app(config), host=config.host, port=config.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
