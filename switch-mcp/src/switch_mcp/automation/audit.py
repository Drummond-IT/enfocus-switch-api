"""Append-only audit trail for anything automation changes (or refuses to change).

One JSON object per line in ``AUDIT_LOG`` (e.g. ``~/.enfocus-switch-mcp/audit.jsonl``):
time, actor, action, target and result. Passwords, tokens and file contents are never
written. Rotate it with the OS tools (logrotate / a scheduled task); it is not trimmed here.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("switch_mcp.audit")
_lock = threading.Lock()


class AuditLog:
    def __init__(self, path: str | Path | None, actor: str = "switch-mcp"):
        self.path = Path(path).expanduser() if path else None
        self.actor = actor

    def __call__(self, event: dict[str, Any]) -> None:
        self.write(event)

    def write(self, event: dict[str, Any]) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "actor": self.actor, **event}
        line = json.dumps(record, default=str, ensure_ascii=False)
        log.info("audit %s", line)
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with _lock:
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except OSError as exc:  # never let auditing break the action itself, but say so loudly
            log.error("Could not write audit log %s: %s", self.path, exc)
