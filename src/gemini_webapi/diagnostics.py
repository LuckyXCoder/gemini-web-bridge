"""Allowlisted diagnostic events: no payloads, credentials or exception messages."""

import json
import logging
import os
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

_context = ContextVar("bridge_diagnostics", default=None)
_log = logging.getLogger("gemini_bridge_diagnostics")
_log.propagate = False
_FIELDS = {
    "stage",
    "reason",
    "error_type",
    "http_status",
    "duration_ms",
    "interval_seconds",
    "cookie_received",
    "cookie_changed",
    "cookie_present",
    "cache_saved",
    "was_running",
    "refresh_active",
    "status",
}


class PrivateRotatingHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, self.mode, encoding=self.encoding)


def configure():
    """Called by the optional server, not by SDK imports."""
    if _log.handlers:
        return
    _log.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    _log.addHandler(handler)
    directory = os.environ.get("GEMINI_BRIDGE_LOG_DIR")
    if directory:
        path = Path(directory).expanduser()
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        file = path / "diagnostics.jsonl"
        fd = os.open(file, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.close(fd)
        file.chmod(0o600)
        handler = PrivateRotatingHandler(file, maxBytes=5 * 1024 * 1024, backupCount=3)
        handler.setFormatter(logging.Formatter("%(message)s"))
        _log.addHandler(handler)


@contextmanager
def scope(account_id=None, request_id=None):
    data = dict(_context.get() or {})
    if account_id is not None:
        data["account_id"] = account_id
    data["request_id"] = request_id or data.get("request_id") or uuid.uuid4().hex
    token = _context.set(data)
    try:
        yield data["request_id"]
    finally:
        _context.reset(token)


def event(name, **fields):
    # Exclude every unrecognized field, even when a caller accidentally passes secrets.
    data = {"time": datetime.now(UTC).isoformat(), "event": name, **(_context.get() or {})}
    for key, value in fields.items():
        if key not in _FIELDS:
            continue
        if (
            isinstance(value, (bool, int, float))
            or value is None
            or (isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value))
        ):
            data[key] = value
    _log.info(json.dumps(data, ensure_ascii=True))
