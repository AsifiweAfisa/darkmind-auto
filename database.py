"""
database.py — SQLite persistence for posts, run logs, and theme rotation pointer.
"""

import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Optional

from config import DB_PATH
from utils import log


SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    theme TEXT,
    hook_text TEXT,
    script TEXT,
    visual_directions TEXT,
    caption TEXT,
    hashtags TEXT,
    text_overlays TEXT,
    audio_path TEXT,
    video_path TEXT,
    status TEXT DEFAULT 'pending',
    youtube_post_id TEXT,
    instagram_post_id TEXT,
    facebook_post_id TEXT,
    threads_post_id TEXT,
    tiktok_post_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    posted_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS run_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    scripts_generated INTEGER DEFAULT 0,
    youtube_success INTEGER DEFAULT 0,
    instagram_success INTEGER DEFAULT 0,
    facebook_success INTEGER DEFAULT 0,
    threads_success INTEGER DEFAULT 0,
    tiktok_success INTEGER DEFAULT 0,
    errors TEXT
);

CREATE TABLE IF NOT EXISTS rotation_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    """Yield a connection with row factory and auto-close."""
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _migrate(c: sqlite3.Connection) -> None:
    """Add columns that may be missing from a pre-existing DB file."""
    existing = {row["name"] for row in c.execute("PRAGMA table_info(posts)")}
    if "facebook_post_id" not in existing:
        c.execute("ALTER TABLE posts ADD COLUMN facebook_post_id TEXT")
    if "threads_post_id" not in existing:
        c.execute("ALTER TABLE posts ADD COLUMN threads_post_id TEXT")

    existing_logs = {row["name"] for row in c.execute("PRAGMA table_info(run_logs)")}
    if "facebook_success" not in existing_logs:
        c.execute("ALTER TABLE run_logs ADD COLUMN facebook_success INTEGER DEFAULT 0")
    if "threads_success" not in existing_logs:
        c.execute("ALTER TABLE run_logs ADD COLUMN threads_success INTEGER DEFAULT 0")


def init_db() -> None:
    """Create tables if missing, and migrate older DB files in place."""
    with conn() as c:
        c.executescript(SCHEMA)
        _migrate(c)
    log.info("Database ready at %s", DB_PATH)


def insert_post(post: dict) -> None:
    """Insert a freshly generated script."""
    with conn() as c:
        c.execute(
            """INSERT INTO posts
               (id, theme, hook_text, script, visual_directions, caption,
                hashtags, text_overlays, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (
                post["id"], post["theme"], post["hook_text"], post["script"],
                json.dumps(post["visual_directions"]),
                post["caption"],
                json.dumps(post["hashtags"]),
                json.dumps(post["text_overlays"]),
            ),
        )


def update_post(post_id: str, **fields) -> None:
    """Generic field update (whitelist enforced)."""
    allowed = {
        "audio_path", "video_path", "status",
        "youtube_post_id",
        "instagram_post_id", "facebook_post_id", "threads_post_id",
        "tiktok_post_id",
        "posted_at",
    }
    sets = []
    vals = []
    for k, v in fields.items():
        if k not in allowed:
            raise ValueError(f"Field {k} not updatable")
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return
    vals.append(post_id)
    with conn() as c:
        c.execute(f"UPDATE posts SET {', '.join(sets)} WHERE id = ?", vals)


def get_post(post_id: str) -> Optional[dict]:
    with conn() as c:
        row = c.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        return dict(row) if row else None


def insert_run_log(scripts_generated: int, yt: int, ig: int, fb: int, th: int,
                   tt: int, errors: str) -> None:
    with conn() as c:
        c.execute(
            """INSERT INTO run_logs
               (scripts_generated, youtube_success,
                instagram_success, facebook_success, threads_success,
                tiktok_success, errors)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (scripts_generated, yt, ig, fb, th, tt, errors),
        )


def get_rotation_index() -> int:
    """Return current theme rotation pointer (0..len(THEMES)-1)."""
    with conn() as c:
        row = c.execute(
            "SELECT value FROM rotation_state WHERE key = 'theme_index'"
        ).fetchone()
        return int(row["value"]) if row else 0


def set_rotation_index(idx: int) -> None:
    with conn() as c:
        c.execute(
            """INSERT INTO rotation_state (key, value) VALUES ('theme_index', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (str(idx),),
        )


def get_recent_status() -> dict:
    """Aggregate stats for /health endpoint."""
    with conn() as c:
        total = c.execute("SELECT COUNT(*) AS n FROM posts").fetchone()["n"]
        posted = c.execute(
            "SELECT COUNT(*) AS n FROM posts WHERE status = 'posted'"
        ).fetchone()["n"]
        failed = c.execute(
            "SELECT COUNT(*) AS n FROM posts WHERE status = 'failed'"
        ).fetchone()["n"]
        last_run = c.execute(
            "SELECT * FROM run_logs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_run = dict(last_run) if last_run else None
    return {
        "posts_total": total,
        "posts_posted": posted,
        "posts_failed": failed,
        "last_run": last_run,
        "now": datetime.utcnow().isoformat() + "Z",
    }