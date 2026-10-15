"""
utils.py — Shared helpers: logging, retry, file ops, ID generation.

Logging redacts secrets: every line written to the console and to the daily
log file is scrubbed of access tokens, refresh tokens, client secrets and
API keys. Python's `requests` library puts the full URL (including
?access_token=...) into its exception messages, so without this a failed API
call writes your live token into the log.
"""

import logging
import re
import sys
import time
import uuid
import json
import shutil
import functools
from datetime import datetime
from pathlib import Path
from typing import Callable, Any

from config import LOG_DIR


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------

_REDACT_PATTERNS = [
    # key=value pairs in URLs / form bodies
    (re.compile(
        r"(?i)\b(access_token|refresh_token|client_secret|fb_exchange_token|"
        r"input_token|code_verifier)=([^&\s\)\"']+)"),
     r"\1=[REDACTED]"),
    # bare tokens, recognised by their well-known prefixes
    (re.compile(r"\b(?:EAA|IGAA|IGQ)[A-Za-z0-9_\-]{20,}"), "[REDACTED_TOKEN]"),
    (re.compile(r"\b(?:act|rft|clt)\.[A-Za-z0-9_\-\.~]{20,}"), "[REDACTED_TOKEN]"),
    (re.compile(r"\bya29\.[A-Za-z0-9_\-\.]{20,}"), "[REDACTED_TOKEN]"),
    (re.compile(r"\bgsk_[A-Za-z0-9]{20,}"), "[REDACTED_TOKEN]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), "[REDACTED_TOKEN]"),
]


def redact(text: Any) -> str:
    """Return text with anything that looks like a secret replaced."""
    out = str(text)
    for pattern, repl in _REDACT_PATTERNS:
        out = pattern.sub(repl, out)
    return out


class _RedactingFormatter(logging.Formatter):
    """Formats a record, then scrubs the finished text (message AND traceback)."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logger(name: str = "darkmind") -> logging.Logger:
    """Build a logger that writes to both stdout and a daily logfile."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = _RedactingFormatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    today = datetime.now().strftime("%Y-%m-%d")
    fh = logging.FileHandler(LOG_DIR / f"daily_log_{today}.txt", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger


log = setup_logger()


def new_id() -> str:
    """Generate a short unique post id."""
    return uuid.uuid4().hex[:12]


def retry(times: int = 3, delay: float = 2.0, backoff: float = 2.0,
          exceptions: tuple = (Exception,)) -> Callable:
    """Decorator: retry on exception with exponential backoff."""
    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> Any:
            current_delay = delay
            last_exc: Exception | None = None
            for attempt in range(1, times + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    log.warning(
                        "%s attempt %d/%d failed: %s",
                        fn.__name__, attempt, times, e,
                    )
                    if attempt < times:
                        time.sleep(current_delay)
                        current_delay *= backoff
            assert last_exc is not None
            raise last_exc
        return wrapper
    return deco


def safe_json_loads(s: str) -> Any:
    """Parse JSON, tolerating code-fenced model output."""
    s = s.strip()
    if s.startswith("```"):
        # strip first fence line and trailing fence
        lines = s.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    return json.loads(s)


def cleanup_temp(path: Path) -> None:
    """Remove a path silently."""
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    except Exception as e:
        log.warning("cleanup failed for %s: %s", path, e)


def truncate(s: str, n: int) -> str:
    """Truncate a string to n chars, no ellipsis."""
    return s if len(s) <= n else s[:n]
