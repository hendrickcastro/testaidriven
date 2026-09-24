"""Logging: rotating file + in-memory ring buffer for the Logs page.

Firestore reports missing composite indexes as `FailedPrecondition` with a console URL that creates the index.
`LogCapture` extracts those URLs so the Logs page can list them with a copy button (see specs/03-modules/logs.md).
"""

from __future__ import annotations

import logging
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Secrets that must never reach log files or the Logs page (P6).
_REDACT_PATTERNS = [
    re.compile(r"sk-(?:ant-|proj-|or-v1-)?[A-Za-z0-9_\-]{16,}"),  # Anthropic / OpenAI / OpenRouter keys
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),  # Google API keys
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(
        r"(?i)(\"?(?:api[_-]?key|authorization|x-api-key|private_key)\"?\s*[:=]\s*\"?)(Bearer\s+)?[^\s\",}]{8,}"
    ),
]


def redact(text: str) -> str:
    for i, pattern in enumerate(_REDACT_PATTERNS):
        text = pattern.sub((lambda m: m.group(1) + "[REDACTED]") if i == 3 else "[REDACTED]", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


INDEX_URL_RE = re.compile(
    r"https://console\.(?:firebase|cloud)\.google\.com/[^\s\"'<>)]*(?:create_composite|indexes)[^\s\"'<>)]*"
)


@dataclass
class LogEntry:
    ts: datetime
    level: str
    logger: str
    message: str


@dataclass
class IndexHint:
    url: str
    first_seen: datetime
    last_seen: datetime
    count: int = 1
    context: str = ""


@dataclass
class LogCapture:
    capacity: int = 3000
    entries: deque[LogEntry] = field(default_factory=lambda: deque(maxlen=3000))
    index_hints: dict[str, IndexHint] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, entry: LogEntry) -> None:
        with self._lock:
            self.entries.append(entry)
            for url in INDEX_URL_RE.findall(entry.message):
                self._add_hint(url, entry)

    def _add_hint(self, url: str, entry: LogEntry) -> None:
        hint = self.index_hints.get(url)
        if hint:
            hint.count += 1
            hint.last_seen = entry.ts
        else:
            self.index_hints[url] = IndexHint(url, entry.ts, entry.ts, context=entry.message[:300])

    def snapshot(self) -> list[LogEntry]:
        with self._lock:
            return list(self.entries)

    def hints(self) -> list[IndexHint]:
        with self._lock:
            return sorted(self.index_hints.values(), key=lambda h: h.last_seen, reverse=True)

    def dismiss_hint(self, url: str) -> None:
        with self._lock:
            self.index_hints.pop(url, None)

    def clear(self) -> None:
        with self._lock:
            self.entries.clear()


class _CaptureHandler(logging.Handler):
    def __init__(self, capture: LogCapture) -> None:
        super().__init__()
        self.capture = capture

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg += "\n" + (self.formatter or logging.Formatter()).formatException(record.exc_info)
            self.capture.add(LogEntry(datetime.fromtimestamp(record.created, UTC), record.levelname, record.name, msg))
        except Exception:
            self.handleError(record)


CAPTURE = LogCapture()
_CONFIGURED = False


def setup_logging(logs_dir: Path, level: int = logging.INFO) -> LogCapture:
    global _CONFIGURED
    if _CONFIGURED:
        return CAPTURE
    logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(logs_dir / "aidriven.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(fmt)
    capture_handler = _CaptureHandler(CAPTURE)
    capture_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(level)
    for h in (file_handler, capture_handler, console):
        h.addFilter(RedactingFilter())
        root.addHandler(h)
    for noisy in ("httpx", "httpcore", "urllib3", "google.auth", "grpc", "docker"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True
    return CAPTURE
