"""Logging configuration for JARVIS.

The whole project logs through the standard :mod:`logging` module; there are no
``print`` calls outside of the explicit TTS console fallback, which is a
user-facing feature rather than a debugging aid.

Two output modes are supported:

- **Human**: coloured, aligned, easy to read in a terminal.
- **JSON**: newline-delimited JSON, one object per event, for log shipping.

Both always go to stderr so that stdout stays free for assistant speech and
transcripts that other tooling may want to consume.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any, Optional

from .config import Settings

#: Colour codes used by the human formatter, keyed by level.
_COLOURS = {
    "DEBUG": "\033[38;5;244m",
    "INFO": "\033[38;5;39m",
    "WARNING": "\033[38;5;214m",
    "ERROR": "\033[38;5;203m",
    "CRITICAL": "\033[1;38;5;199m",
}
_RESET = "\033[0m"
_DIM = "\033[38;5;240m"

#: Attributes present on every LogRecord; anything else was passed by the caller.
_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
) | {"asctime", "message", "taskName"}


def _supports_colour(stream: Any) -> bool:
    """True when ANSI colour is safe to emit on this stream."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return bool(getattr(stream, "isatty", lambda: False)())


class HumanFormatter(logging.Formatter):
    """Compact, aligned, optionally coloured single-line formatter."""

    def __init__(self, colour: bool = True) -> None:
        super().__init__()
        self.colour = colour

    def format(self, record: logging.LogRecord) -> str:
        """Render one record as ``LEVEL  logger.message  key=value``."""
        level = record.levelname
        prefix = f"{_COLOURS.get(level, '')}{level:<8}{_RESET}" if self.colour else f"{level:<8}"
        stamp = _DIM + self.formatTime(record, "%H:%M:%S.%f")[:-3] + _RESET if self.colour else self.formatTime(record, "%H:%M:%S")

        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_ATTRS and not key.startswith("_")
        }
        tail = " ".join(f"{key}={value}" for key, value in extras.items())

        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"

        line = f"{stamp} {prefix} {_DIM}{record.name}{_RESET} {message}"
        if tail:
            line = f"{line} {tail}"
        return line


class JsonFormatter(logging.Formatter):
    """One JSON object per log line, with caller extras inlined."""

    def format(self, record: logging.LogRecord) -> str:
        """Render one record as a JSON object."""
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(settings: Optional[Settings] = None) -> None:
    """Install the root logging handlers.

    Safe to call more than once: existing handlers are replaced rather than
    duplicated, so a reload never doubles every line.

    Args:
        settings: Configuration to read the level and file target from. When
            omitted the process singleton is loaded.

    Returns:
        The configured root logger.
    """
    cfg = settings or Settings()
    root = logging.getLogger()
    root.setLevel(cfg.log_level.upper())

    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter: logging.Formatter
    if cfg.log_json:
        formatter = JsonFormatter()
    else:
        formatter = HumanFormatter(colour=_supports_colour(sys.stderr))

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if cfg.log_file:
        path = Path(cfg.log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        rotating.setFormatter(JsonFormatter())
        root.addHandler(rotating)

    # These libraries are extremely chatty at INFO.
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "edge_tts"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger("jarvis").debug(
        "logging configured level=%s json=%s file=%s",
        cfg.log_level,
        cfg.log_json,
        cfg.log_file or "-",
    )
    return root


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced child logger.

    Args:
        name: Usually ``__name__``. A ``jarvis.`` prefix is added when absent.

    Returns:
        A configured logger.
    """
    if name == "jarvis" or name.startswith("jarvis."):
        return logging.getLogger(name)
    return logging.getLogger(f"jarvis.{name}")
