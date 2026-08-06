"""Runtime configuration.

Calibration constants are **parsed from `calibration.md`**, never hardcoded
here. That file's own header says so, and CLAUDE.md makes it a rule: the values
are empirical, several are explicitly provisional (`SETTLE_MS` is a starting
value to tune; the resolution verdict was measured on the cover and still needs
re-confirming on a 世系 chart page), so a copy pasted into source would silently
diverge the next time the calibration is redone.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

CONSTANTS_HEADING = "## Constants for config"

# Keys the server refuses to start without. Anything else in the block is kept
# in `Calibration.raw` for later milestones (the worker will want SETTLE_MS).
REQUIRED_KEYS = ("CAPTURE_VIEWPORT_W", "CAPTURE_VIEWPORT_H", "CAPTURE_DPR")


class CalibrationError(RuntimeError):
    """`calibration.md` is missing, unparseable, or lacks a required constant."""


def _strip_comment(value: str) -> str:
    """Drop a trailing `#` comment, ignoring `#` inside a quoted string."""
    quote = None
    for i, ch in enumerate(value):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return value[:i]
    return value


def _coerce(value: str) -> Any:
    value = _strip_comment(value).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    lowered = value.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def parse_constants(text: str) -> dict[str, Any]:
    """Pull the `Constants for config` fenced block out of calibration.md."""
    start = text.find(CONSTANTS_HEADING)
    if start == -1:
        raise CalibrationError(f"no {CONSTANTS_HEADING!r} section found")

    fence = re.search(r"```[^\n]*\n(.*?)^```", text[start:], re.S | re.M)
    if fence is None:
        raise CalibrationError(f"no fenced code block under {CONSTANTS_HEADING!r}")

    constants: dict[str, Any] = {}
    for lineno, line in enumerate(fence.group(1).splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^([A-Z][A-Z0-9_]*)\s*=\s*(.+)$", line.strip())
        if match is None:
            raise CalibrationError(
                f"line {lineno} of the constants block is not KEY = VALUE: {line!r}"
            )
        constants[match.group(1)] = _coerce(match.group(2))
    return constants


@dataclass(frozen=True)
class Calibration:
    """Measured constants, as read from `calibration.md`."""

    source_path: Path
    raw: dict[str, Any] = field(repr=False)

    @property
    def viewport_w(self) -> int:
        return int(self.raw["CAPTURE_VIEWPORT_W"])

    @property
    def viewport_h(self) -> int:
        return int(self.raw["CAPTURE_VIEWPORT_H"])

    @property
    def dpr(self) -> float:
        return float(self.raw["CAPTURE_DPR"])

    @property
    def capture_method(self) -> str | None:
        value = self.raw.get("CAPTURE_METHOD")
        return str(value) if value is not None else None

    @classmethod
    def load(cls, path: Path | None = None) -> "Calibration":
        path = path or Path(os.environ.get("ZUPU_CALIBRATION", REPO_ROOT / "calibration.md"))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CalibrationError(f"cannot read calibration at {path}: {exc}") from exc

        constants = parse_constants(text)
        missing = [key for key in REQUIRED_KEYS if key not in constants]
        if missing:
            raise CalibrationError(f"{path} is missing required constants: {', '.join(missing)}")
        return cls(source_path=path, raw=constants)


@dataclass(frozen=True)
class ServerConfig:
    db_path: Path
    data_dir: Path
    calibration: Calibration
    host: str = "127.0.0.1"
    port: int = 8787

    @property
    def image_dir(self) -> Path:
        return self.data_dir / "images"

    @classmethod
    def from_env(cls) -> "ServerConfig":
        data_dir = Path(os.environ.get("ZUPU_DATA_DIR", REPO_ROOT / "data"))
        return cls(
            db_path=Path(os.environ.get("ZUPU_DB_PATH", data_dir / "zupu.db")),
            data_dir=data_dir,
            calibration=Calibration.load(),
            host=os.environ.get("ZUPU_HOST", "127.0.0.1"),
            # Port 8787 is fixed by capture-spec §2.5; the extension posts there.
            port=int(os.environ.get("ZUPU_PORT", "8787")),
        )
