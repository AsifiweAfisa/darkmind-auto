"""
dedupe.py — The system's memory of what it has already published.

Two jobs:

  1. pick_themes(n)
       Chooses which themes to write about next: never-published themes first
       (in list order), then whichever theme was published LONGEST AGO. It reads
       the real publishing history from the database, so test runs and runs you
       stopped part-way (which never reached an audience) don't use a theme up.

  2. similarity checks
       Even when a theme legitimately comes round again, the new script must not
       repeat an old one. `similarity_ratio()` compares a candidate against
       everything already published and returns a score; >= 1.0 means "too
       similar, write it again". The comparison is deliberately cheap and offline
       (word overlap, no extra API calls):
         - 3-word phrase overlap  -> catches reused sentences
         - key-word overlap       -> catches the same ideas in the same words
         - hook overlap           -> catches the same opening claim

"Published" means a post whose status is 'posted' (at least one platform
succeeded). Scripts that were generated but never went out are ignored.
"""

import re
from typing import Dict, List, Tuple

from config import THEMES
from database import conn

# Overlap at/above these is "too similar". Calibrated on real scripts — see
# test_dedupe.py. Each metric is divided by its limit, so a ratio >= 1.0 on
# ANY metric means too similar.
SHINGLE_LIMIT = 0.10     # Jaccard of 3-word phrases
CONTENT_LIMIT = 0.30     # Jaccard of meaningful (non-filler) words
HOOK_LIMIT = 0.50        # Jaccard of the hook's words

_STOP = frozenset("""
a an the and or but if so as of to in on at by for with from into onto about than then that this
these those is are was were be been being am do does did done have has had having it its it's
you your yours we our they them their he she him her his hers i me my not no yes can could will
would should may might must just very more most some any all each every one two than too also
even only own same such what which who whom when where why how there here out up down over under
again further once now never ever still much many few other another because while during before
after above below between through
""".split())


# ---------------------------------------------------------------------------
# Text comparison
# ---------------------------------------------------------------------------

def _words(text: str) -> List[str]:
    text = (text or "").lower().replace("’", "'").replace("‑", "-")
    return re.findall(r"[a-z0-9']+", text)


def _content_words(text: str) -> set:
    return {w for w in _words(text) if w not in _STOP and len(w) > 2}


def _shingles(words: List[str], n: int = 3) -> set:
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def pair_similarity(hook_a: str, script_a: str, hook_b: str, script_b: str) -> Tuple[float, str]:
    """Return (ratio, reason) for two pieces of content. ratio >= 1.0 means too similar."""
    wa, wb = _words(script_a), _words(script_b)
    scores = {
        "reuses the same phrases": _jaccard(_shingles(wa), _shingles(wb)) / SHINGLE_LIMIT,
        "covers the same ideas in the same words": _jaccard(_content_words(script_a), _content_words(script_b)) / CONTENT_LIMIT,
        "has almost the same hook": _jaccard(_content_words(hook_a), _content_words(hook_b)) / HOOK_LIMIT,
    }
    reason = max(scores, key=scores.get)
    return scores[reason], reason


def similarity_ratio(hook: str, script: str, past: List[dict]) -> Tuple[float, str, str]:
    """Compare a candidate with every past item.
    Returns (worst_ratio, reason, the_past_hook_it_resembles)."""
    worst, why, which = 0.0, "", ""
    for p in past:
        ratio, reason = pair_similarity(hook, script, p.get("hook_text", ""), p.get("script", ""))
        if ratio > worst:
            worst, why, which = ratio, reason, p.get("hook_text", "")
    return worst, why, which


# ---------------------------------------------------------------------------
# History (from the database)
# ---------------------------------------------------------------------------

def published_content() -> List[dict]:
    """Every published post, newest first: theme, hook_text, script."""
    with conn() as c:
        rows = c.execute(
            "SELECT theme, hook_text, script, created_at FROM posts "
            "WHERE status = 'posted' ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def theme_last_published() -> Dict[str, str]:
    """theme -> timestamp of its most recent published post."""
    with conn() as c:
        rows = c.execute(
            "SELECT theme, MAX(created_at) AS last FROM posts "
            "WHERE status = 'posted' GROUP BY theme"
        ).fetchall()
    return {r["theme"]: r["last"] for r in rows}


def pick_themes(n: int) -> List[str]:
    """The next n themes: never-published first (list order), then the one
    published longest ago. Never returns the same theme twice in one call."""
    last = theme_last_published()
    order = sorted(
        range(len(THEMES)),
        key=lambda i: (last.get(THEMES[i]) is not None, last.get(THEMES[i]) or "", i),
    )
    return [THEMES[i] for i in order[:max(0, min(n, len(THEMES)))]]


def avoid_list(theme: str, past: List[dict], same_theme_limit: int = 12, other_limit: int = 8) -> List[str]:
    """Hooks the writer must not reuse: every published hook for this theme
    (up to a cap), plus the most recent hooks from other themes."""
    same = [p["hook_text"] for p in past if p["theme"] == theme and p.get("hook_text")]
    other = [p["hook_text"] for p in past if p["theme"] != theme and p.get("hook_text")]
    seen, out = set(), []
    for h in same[:same_theme_limit] + other[:other_limit]:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out
