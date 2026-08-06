"""Local capture server for the 族譜 pipeline.

Milestone 2 of `zupu-capture-spec.md` §4: `POST /capture`, dedup, SQLite, image
storage. The quality gate (§3.2), extraction worker (§3.4), and operator UI
(§3.5) are deliberately not implemented yet.
"""

from server.app import create_app

__all__ = ["create_app"]
