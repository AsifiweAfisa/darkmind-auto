"""
utils.py — Shared helpers: logging, retry, file ops, ID generation.
"""

import logging
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


def setup_logger(name: str = "darkmind") -> logging.Logger:
    """Build a logger that writes to both stdout and a daily logfile."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
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
