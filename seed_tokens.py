"""
seed_tokens.py — Run ONCE to move your current tokens into the database.

Reads from your existing .env (via config.py), token.json, and
client_secret.json, and writes each platform's credentials into the
`tokens` table. After this, token_manager.py handles every refresh and the
uploaders no longer touch token.json or write to .env.

Safe to re-run. By default it only seeds platforms that have NO row in the
database yet, so it can never overwrite a token that token_manager has since
refreshed (TikTok in particular rotates its refresh token on every refresh —
re-seeding it from an old .env value would break it).

To deliberately replace one platform's stored token — e.g. after you generate
a new Instagram token by hand and put it in .env — name it:

Usage:
    python seed_tokens.py                  # seed only what's missing
    python seed_tokens.py instagram        # force-replace just Instagram
    python seed_tokens.py instagram threads
Platforms: youtube, tiktok, instagram, threads, facebook
"""

import json
import os
import sys

from config import (
    BASE_DIR,
    YOUTUBE_CLIENT_SECRETS_FILE, YOUTUBE_TOKEN_FILE,
    IG_ACCESS_TOKEN, FB_PAGE_TOKEN, THREADS_ACCESS_TOKEN,
    TIKTOK_ACCESS_TOKEN, TIKTOK_REFRESH_TOKEN,
)
from database import init_db, set_token, get_token
from utils import log


def _resolve(path: str) -> str:
    return path if os.path.isabs(path) else str(BASE_DIR / path)


def seed_youtube() -> None:
    token_path = _resolve(YOUTUBE_TOKEN_FILE)
    secrets_path = _resolve(YOUTUBE_CLIENT_SECRETS_FILE)
    if not os.path.exists(token_path):
        log.warning("YouTube: %s not found — skipped. Run a manual YouTube "
                    "upload once locally to create it, then re-run this.", token_path)
        return

    with open(token_path, encoding="utf-8") as f:
        tok = json.load(f)

    client_id = tok.get("client_id")
    client_secret = tok.get("client_secret")
    if (not client_id or not client_secret) and os.path.exists(secrets_path):
        with open(secrets_path, encoding="utf-8") as f:
            cs = json.load(f)
        block = cs.get("installed") or cs.get("web") or {}
        client_id = client_id or block.get("client_id")
        client_secret = client_secret or block.get("client_secret")

    if not tok.get("refresh_token") or not client_id or not client_secret:
        log.error("YouTube: token.json is missing refresh_token or client "
                  "credentials — cannot seed.")
        return

    expiry = tok.get("expiry")  # e.g. "2026-08-26T18:36:28.123456Z" (UTC)
    if expiry:
        expiry = expiry.rstrip("Z")

    set_token(
        "youtube",
        access_token=tok.get("token"),
        refresh_token=tok["refresh_token"],
        client_id=client_id,
        client_secret=client_secret,
        token_uri=tok.get("token_uri") or "https://oauth2.googleapis.com/token",
        expires_at=expiry,
    )
    log.info("YouTube: seeded")


def seed_tiktok() -> None:
    if not TIKTOK_REFRESH_TOKEN:
        log.warning("TikTok: TIKTOK_REFRESH_TOKEN missing in .env — skipped.")
        return
    # expires_at left empty on purpose: the first real call refreshes
    # immediately, which also rotates us onto a known-fresh token pair.
    set_token(
        "tiktok",
        access_token=TIKTOK_ACCESS_TOKEN or None,
        refresh_token=TIKTOK_REFRESH_TOKEN,
        expires_at=None,
    )
    log.info("TikTok: seeded (will refresh on first use)")


def seed_instagram() -> None:
    if not IG_ACCESS_TOKEN:
        log.warning("Instagram: IG_ACCESS_TOKEN missing in .env — skipped.")
        return
    set_token("instagram", access_token=IG_ACCESS_TOKEN, expires_at=None)
    log.info("Instagram: seeded (will attempt refresh on first use)")


def seed_threads() -> None:
    if not THREADS_ACCESS_TOKEN:
        log.warning("Threads: THREADS_ACCESS_TOKEN missing in .env — skipped.")
        return
    set_token("threads", access_token=THREADS_ACCESS_TOKEN, expires_at=None)
    log.info("Threads: seeded (will attempt refresh on first use)")


def seed_facebook() -> None:
    if not FB_PAGE_TOKEN:
        log.warning("Facebook: FB_PAGE_TOKEN missing in .env — skipped.")
        return
    set_token("facebook", access_token=FB_PAGE_TOKEN, expires_at=None)
    log.info("Facebook: seeded (non-expiring, never refreshed)")


SEEDERS = {
    "youtube": seed_youtube,
    "tiktok": seed_tiktok,
    "instagram": seed_instagram,
    "threads": seed_threads,
    "facebook": seed_facebook,
}


if __name__ == "__main__":
    init_db()
    requested = [a.lower() for a in sys.argv[1:]]
    unknown = [a for a in requested if a not in SEEDERS]
    if unknown:
        sys.exit(f"Unknown platform(s): {', '.join(unknown)}. "
                 f"Choose from: {', '.join(SEEDERS)}")

    if requested:                                   # explicit = force-replace
        for name in requested:
            log.info("Replacing %s from .env / files (as requested)", name)
            SEEDERS[name]()
    else:                                           # default = only what's missing
        for name, fn in SEEDERS.items():
            if get_token(name):
                log.info("%s: already in the database — left untouched", name)
            else:
                fn()
    log.info("Done. Run `python check_tokens.py` to verify.")
