"""Logging setup.

The old scrapers had no logging module usage at all -- 17 broad
`except Exception as e: print(f"...{e}")` blocks across the codebase, and
subprocess.run(capture_output=True) in scraper_runner.py discarded stdout
entirely (only stderr was surfaced), so a failing scraper that printed its
error to stdout reported an empty message. Everything here goes through
the `logging` module so tracebacks and levels survive, and callers choose
plain console output or line-delimited JSON.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from rich.logging import RichHandler

_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging(*, verbose: bool = False, json_output: bool = False) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger("dfs")
    root.setLevel(level)

    if json_output:
        handler: logging.Handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(_JsonFormatter())
    else:
        handler = RichHandler(
            show_time=False,
            show_path=verbose,
            rich_tracebacks=True,
            markup=False,
        )
    root.addHandler(handler)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"dfs.{name}")
