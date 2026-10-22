"""
watchdog.py — Makes sure each day's post actually goes out, even if the machine
was off, a run failed, or one platform hiccuped.

Why it exists: the scheduler only fires while the program is running, and a
run that misses its slot is simply skipped (an in-memory scheduler has no
memory of what it missed). Without this, a day with the PC off — or one failed
run — meant no post that day.

How it works: every few minutes (and once at startup) main.py asks decide():

  "Given the time now, how many posts should exist today, and how many do?"

  - Fewer than expected, the scheduled time (+ a grace period) has passed, and
    we haven't already tried too many times today  -> RUN the pipeline now.
  - As many as expected, but some platform is missing from one of today's posts
    -> RETRY just the missing platforms (never re-posts where it already worked).
  - Otherwise -> do nothing.

Guard rails so it can never spam or loop:
  - MAX_RUNS_PER_DAY full attempts per day (default 3), spaced RETRY_GAP apart.
  - MAX_PLATFORM_RETRIES per post (default 3), spaced RETRY_GAP apart.
  - No catch-up after CATCHUP_UNTIL (default 23:00 local) — a post at 2am helps
    nobody; the next day's normal slot takes over.
  - A post only counts as "done" if its status is 'posted' (at least one
    platform succeeded), judged by the LOCAL calendar day in TIMEZONE.

Everything here is a pure function of (now, database) so it can be tested with
a fake clock. Attempt counters live in the database's rotation_state table, so
they survive restarts.
"""

import os
from datetime import datetime, time as dtime, timedelta
from typing import Dict, List, Optional, Tuple

import pytz

from config import TIMEZONE, RUN_TIMES, DAILY_SCRIPT_COUNT
from database import conn

TZ = pytz.timezone(TIMEZONE)
UTC = pytz.utc

GRACE = timedelta(minutes=int(os.getenv("CATCHUP_GRACE_MINUTES", "20")))
RETRY_GAP = timedelta(minutes=int(os.getenv("RETRY_GAP_MINUTES", "30")))
MAX_RUNS_PER_DAY = int(os.getenv("MAX_RUNS_PER_DAY", "3"))
MAX_PLATFORM_RETRIES = int(os.getenv("MAX_PLATFORM_RETRIES", "3"))
CATCHUP_UNTIL = os.getenv("CATCHUP_UNTIL", "23:00")


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def now_local() -> datetime:
    return datetime.now(TZ)


def _utc_str_to_local(ts: str) -> datetime:
    """'2026-10-20 16:01:00' or '2026-10-20T16:01:00.123' (UTC, naive) -> local aware."""
    naive = datetime.fromisoformat(str(ts).replace(" ", "T"))
    return UTC.localize(naive).astimezone(TZ)


def _local_to_utc_str(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(tzinfo=None).isoformat()


def _slots(day) -> List[datetime]:
    """Today's scheduled run times as aware local datetimes."""
    out = []
    for t in RUN_TIMES:
        h, m = (int(x) for x in t.split(":"))
        out.append(TZ.localize(datetime.combine(day, dtime(h, m))))
    return out


def expected_posts(now: datetime) -> int:
    """How many posts should exist by `now`: one run's worth per scheduled slot
    that has passed (plus the grace period)."""
    passed = sum(1 for s in _slots(now.date()) if s + GRACE <= now)
    return passed * DAILY_SCRIPT_COUNT


def _past_cutoff(now: datetime) -> bool:
    h, m = (int(x) for x in CATCHUP_UNTIL.split(":"))
    return now.time() > dtime(h, m)


# ---------------------------------------------------------------------------
# Small key/value store (rotation_state table) for attempt counters
# ---------------------------------------------------------------------------

def _kv_get(key: str) -> Optional[str]:
    with conn() as c:
        row = c.execute("SELECT value FROM rotation_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def _kv_set(key: str, value: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO rotation_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def _counter(key: str) -> Tuple[int, Optional[datetime]]:
    raw = _kv_get(key)
    if not raw:
        return 0, None
    count, ts = raw.split("|", 1)
    return int(count), _utc_str_to_local(ts)


def _bump(key: str, now: datetime) -> None:
    count, _ = _counter(key)
    _kv_set(key, f"{count + 1}|{_local_to_utc_str(now)}")


def note_run(now: Optional[datetime] = None) -> None:
    """Record that a full pipeline run was started today (scheduled or catch-up)."""
    now = now or now_local()
    _bump(f"runs:{now.date().isoformat()}", now)


def note_platform_retry(post_id: str, now: Optional[datetime] = None) -> None:
    _bump(f"retry:{post_id}", now or now_local())


def gave_up_already_logged(now: datetime) -> bool:
    """True if the 'gave up for today' message was already shown (log it once)."""
    key = f"gaveup:{now.date().isoformat()}"
    if _kv_get(key):
        return True
    _kv_set(key, "1")
    return False


# ---------------------------------------------------------------------------
# What does the database say about today?
# ---------------------------------------------------------------------------

def posted_today(now: datetime) -> List[dict]:
    """Posts with status 'posted' whose posting time falls on today's LOCAL date."""
    with conn() as c:
        rows = c.execute(
            "SELECT *, COALESCE(posted_at, created_at) AS _when FROM posts WHERE status = 'posted'"
        ).fetchall()
    out = []
    for r in rows:
        if _utc_str_to_local(r["_when"]).date() == now.date():
            out.append(dict(r))
    return out


def status(now: Optional[datetime] = None) -> Dict:
    """Small summary for the /health endpoint."""
    now = now or now_local()
    runs, last = _counter(f"runs:{now.date().isoformat()}")
    return {
        "local_time": now.strftime("%Y-%m-%d %H:%M %Z"),
        "expected_posts_by_now": expected_posts(now),
        "posted_today": len(posted_today(now)),
        "runs_attempted_today": runs,
        "last_run_attempt": last.strftime("%H:%M") if last else None,
    }


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------

def decide(now: datetime, platform_fields: List[str]) -> Dict:
    """Return {"action": "none" | "run" | "retry", "reason": str, ...}.
    For "run": also "need" (how many posts to make). For "retry": "post_ids"."""
    if _past_cutoff(now):
        return {"action": "none", "reason": f"past the {CATCHUP_UNTIL} catch-up cut-off"}

    expected = expected_posts(now)
    if expected == 0:
        return {"action": "none", "reason": "today's scheduled time hasn't passed yet"}

    done_posts = posted_today(now)
    done = len(done_posts)

    if done < expected:
        runs, last = _counter(f"runs:{now.date().isoformat()}")
        if runs >= MAX_RUNS_PER_DAY:
            return {"action": "none", "gave_up": True,
                    "reason": f"already tried {runs} time(s) today; giving up until tomorrow's slot"}
        if last is not None and now - last < RETRY_GAP:
            return {"action": "none", "reason": "waiting before the next attempt"}
        return {"action": "run", "need": expected - done,
                "reason": f"{done} of {expected} expected post(s) are out"}

    # Today's posts exist — make sure no platform was left behind.
    to_retry = []
    for p in done_posts:
        missing = [f for f in platform_fields if not p.get(f)]
        if not missing:
            continue
        count, last = _counter(f"retry:{p['id']}")
        if count >= MAX_PLATFORM_RETRIES:
            continue
        if last is not None and now - last < RETRY_GAP:
            continue
        to_retry.append((p["id"], missing))
    if to_retry:
        return {"action": "retry", "post_ids": [pid for pid, _ in to_retry],
                "missing": {pid: m for pid, m in to_retry},
                "reason": "some platforms are missing from today's post"}
    return {"action": "none", "reason": "today's post is out everywhere"}
