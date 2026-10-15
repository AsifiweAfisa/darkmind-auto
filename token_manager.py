"""
token_manager.py — Single source of truth for every platform's access token.

Why this exists: tokens that refresh themselves at runtime (YouTube's OAuth
token, TikTok's access token) used to be written back to local files
(token.json, .env). That works on a developer's machine but breaks on an
ephemeral filesystem like Railway's — a redeploy wipes any file written at
runtime, silently reverting to a stale token.

This module stores every platform's token in the `tokens` SQLite table
instead (see database.py). NOTE: on Railway the database file itself must
live on a persistent Volume (DB_PATH=/data/darkmind.db), or it is wiped on
redeploy just like token.json was.

Each platform gets its own refresh cadence, matched to how fast its token
actually goes stale — they are NOT all treated the same:

  - TikTok:     access token lives ~24h. Checked on every call and refreshed
                if within TIKTOK_REFRESH_BUFFER of expiring.
  - YouTube:    Google's library handles refresh; we persist the result.
  - Instagram:  Facebook-Login user token (starts "EAA"), ~60 days. Its real
                expiry is read from Meta's debug_token endpoint, and it is
                renewed (re-exchanged) when within INSTAGRAM_REFRESH_BUFFER
                of expiring. Needs META_APP_ID / META_APP_SECRET. An
                Instagram-Login token (starts "IG") is renewed via
                graph.instagram.com instead — no app secret needed.
  - Threads:    ~60 days; refreshed via graph.threads.net.
  - Facebook:   System User-derived Page token does not expire. No refresh.

Secrets never appear in logs: every Meta call goes through _safe_get, which
builds error messages without the request URL (requests puts the full URL,
including ?access_token=..., into its exceptions), and utils.py additionally
redacts anything token-shaped from all log output.

Before first use, run seed_tokens.py once to populate the `tokens` table
from your existing .env / token.json values.
"""

from datetime import datetime, timedelta
from typing import Optional

import requests

from config import (
    TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET,
    META_APP_ID, META_APP_SECRET,
)
from database import get_token, set_token
from utils import log, retry, redact


# How long before expiry each platform proactively refreshes.
TIKTOK_REFRESH_BUFFER = timedelta(minutes=30)     # checked every call — cheap, token lives ~24h
INSTAGRAM_REFRESH_BUFFER = timedelta(days=7)      # token lives ~60 days
THREADS_REFRESH_BUFFER = timedelta(days=5)        # token lives ~60 days
WARN_WITHIN = timedelta(days=10)                  # log an ERROR if a token this close to expiry can't be renewed

# Stored in expires_at for tokens Meta reports as non-expiring.
NEVER_EXPIRES = "9999-12-31T00:00:00"

GRAPH_FB = "https://graph.facebook.com/v20.0"
GRAPH_IG = "https://graph.instagram.com"
GRAPH_THREADS = "https://graph.threads.net"


def _parse_ts(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _needs_refresh(expires_at, buffer: timedelta) -> bool:
    """True if expires_at is missing, or within `buffer` of now (or past)."""
    dt = _parse_ts(expires_at)
    if dt is None:
        return True
    return datetime.utcnow() >= (dt - buffer)


# ---------------------------------------------------------------------------
# Leak-proof HTTP for Meta endpoints
# ---------------------------------------------------------------------------

class _Transient(Exception):
    """Network blip or 5xx — worth retrying. Message is already scrubbed."""


def _safe_get(url: str, params: dict, timeout: int = 30) -> dict:
    """GET that can never put the request URL (and so the token) in an error.

    4xx responses raise RuntimeError immediately (retrying a rejected token
    is pointless); network errors and 5xx raise _Transient (retryable)."""
    try:
        r = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as e:
        raise _Transient(f"{type(e).__name__} calling {url.split('?')[0]}") from None
    if r.status_code >= 500:
        raise _Transient(f"HTTP {r.status_code} from {url.split('?')[0]}")
    if r.status_code >= 400:
        raise RuntimeError(
            f"HTTP {r.status_code} from {url.split('?')[0]}: {redact(r.text[:300])}")
    return r.json()


_safe_get_retrying = retry(times=2, delay=3.0, exceptions=(_Transient,))(_safe_get)


# ---------------------------------------------------------------------------
# TikTok — refreshed proactively on (almost) every call; token lives ~24h.
# ---------------------------------------------------------------------------

@retry(times=2, delay=3.0, exceptions=(requests.RequestException,))
def _tiktok_refresh_call(refresh_token: str) -> dict:
    r = requests.post(
        "https://open.tiktokapis.com/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": TIKTOK_CLIENT_KEY,
            "client_secret": TIKTOK_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"TikTok token refresh failed: {data}")
    return data


def get_tiktok_access_token(force: bool = False) -> str:
    """Return a valid TikTok access token, refreshing first if it's due.

    force=True refreshes regardless of the stored expiry — used when TikTok
    itself rejects the token (e.g. revoked early) even though our clock
    says it should still be good.
    """
    row = get_token("tiktok")
    if not row or not row.get("refresh_token"):
        raise RuntimeError(
            "No TikTok token in the database — run seed_tokens.py first."
        )

    if force or _needs_refresh(row.get("expires_at"), TIKTOK_REFRESH_BUFFER):
        log.info("TikTok: access token due for refresh — refreshing now")
        data = _tiktok_refresh_call(row["refresh_token"])
        expires_at = datetime.utcnow() + timedelta(
            seconds=int(data.get("expires_in", 86400))
        )
        set_token(
            "tiktok",
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", row["refresh_token"]),
            expires_at=expires_at.isoformat(),
        )
        log.info("TikTok: token refreshed, valid until %s", expires_at)
        return data["access_token"]

    return row["access_token"]


# ---------------------------------------------------------------------------
# YouTube — Google's library drives the refresh; we persist the result.
# ---------------------------------------------------------------------------

def get_youtube_credentials():
    """Build (and refresh if needed) a google.oauth2 Credentials object,
    persisting any refresh back to the DB instead of token.json."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    row = get_token("youtube")
    if not row or not row.get("refresh_token"):
        raise RuntimeError(
            "No YouTube token in the database — run seed_tokens.py first."
        )

    expiry = _parse_ts(row.get("expires_at"))
    creds = Credentials(
        # With no known expiry, drop the access token so google-auth treats
        # the credentials as invalid and refreshes — never trust a token
        # whose lifetime we can't verify.
        token=row.get("access_token") if expiry else None,
        refresh_token=row["refresh_token"],
        token_uri=row.get("token_uri") or "https://oauth2.googleapis.com/token",
        client_id=row["client_id"],
        client_secret=row["client_secret"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
        expiry=expiry,
    )

    if not creds.valid:
        log.info("YouTube: credentials invalid/expired — refreshing")
        creds.refresh(Request())
        set_token(
            "youtube",
            access_token=creds.token,
            refresh_token=creds.refresh_token or row["refresh_token"],
            expires_at=creds.expiry.isoformat() if creds.expiry else None,
        )
        log.info("YouTube: token refreshed, valid until %s", creds.expiry)

    return creds


# ---------------------------------------------------------------------------
# Meta helpers — expiry lookup and renewal
# ---------------------------------------------------------------------------

def _debug_token(token: str) -> Optional[dict]:
    """Ask Meta about a token (validity, type, real expiry). Needs the app id
    and secret; returns None if unavailable or the call fails."""
    if not (META_APP_ID and META_APP_SECRET):
        return None
    try:
        data = _safe_get_retrying(
            f"{GRAPH_FB}/debug_token",
            {"input_token": token, "access_token": f"{META_APP_ID}|{META_APP_SECRET}"},
        )
        return data.get("data")
    except Exception as e:
        log.warning("Meta debug_token lookup failed: %s", e)
        return None


def _expiry_from_debug(info: dict) -> str:
    """expires_at (epoch seconds; 0 means 'never') -> stored timestamp."""
    exp = int(info.get("expires_at") or 0)
    return datetime.utcfromtimestamp(exp).isoformat() if exp else NEVER_EXPIRES


def _renew_instagram_token(token: str) -> dict:
    """Renew an Instagram token using the mechanism that matches its type."""
    if token.startswith("IG"):
        # Instagram-Login token -> refreshed on graph.instagram.com
        return _safe_get_retrying(
            f"{GRAPH_IG}/refresh_access_token",
            {"grant_type": "ig_refresh_token", "access_token": token},
        )
    # Facebook-Login user token ("EAA...") -> re-exchange for a fresh long-lived one
    if not (META_APP_ID and META_APP_SECRET):
        raise RuntimeError(
            "this is a Facebook-Login token; renewing it needs META_APP_ID and "
            "META_APP_SECRET in .env")
    return _safe_get_retrying(
        f"{GRAPH_FB}/oauth/access_token",
        {"grant_type": "fb_exchange_token", "client_id": META_APP_ID,
         "client_secret": META_APP_SECRET, "fb_exchange_token": token},
    )


# ---------------------------------------------------------------------------
# Instagram
# ---------------------------------------------------------------------------

def get_instagram_access_token() -> str:
    """Return the Instagram access token, renewing it first if it's within
    INSTAGRAM_REFRESH_BUFFER of expiring. Never blocks a post: if renewal
    isn't possible the current token is used and a warning is logged."""
    row = get_token("instagram")
    if not row or not row.get("access_token"):
        raise RuntimeError(
            "No Instagram token in the database — run seed_tokens.py first."
        )
    token = row["access_token"]
    expires_at = row.get("expires_at")

    # Learn the real expiry once (needs the app id/secret).
    if not expires_at:
        info = _debug_token(token)
        if info is not None:
            expires_at = _expiry_from_debug(info)
            set_token("instagram", expires_at=expires_at)
            log.info("Instagram: token %s",
                     "does not expire" if expires_at == NEVER_EXPIRES
                     else f"expires {expires_at} (UTC)")
    if not expires_at:
        log.warning(
            "Instagram: expiry unknown, so it can't be tracked or renewed. Add "
            "META_APP_ID and META_APP_SECRET to .env. Using the current token.")
        return token

    if not _needs_refresh(expires_at, INSTAGRAM_REFRESH_BUFFER):
        return token

    log.info("Instagram: token expires %s — renewing now", expires_at)
    try:
        data = _renew_instagram_token(token)
        new_token = data["access_token"]
        info = _debug_token(new_token)             # trust Meta's own number
        if info is not None:
            new_expiry = _expiry_from_debug(info)
        elif data.get("expires_in"):
            new_expiry = (datetime.utcnow()
                          + timedelta(seconds=int(data["expires_in"]))).isoformat()
        else:
            new_expiry = (datetime.utcnow() + timedelta(days=60)).isoformat()

        if _parse_ts(new_expiry) <= _parse_ts(expires_at):
            raise RuntimeError(
                f"renewal did not extend the token (still expires {new_expiry}); "
                "it must be regenerated by hand")
        set_token("instagram", access_token=new_token, expires_at=new_expiry)
        log.info("Instagram: token renewed, now valid until %s (UTC)", new_expiry)
        return new_token
    except Exception as e:
        left = _parse_ts(expires_at) - datetime.utcnow()
        emit = log.error if left <= WARN_WITHIN else log.warning
        emit("Instagram: could not renew token (%s). It expires %s (UTC) — "
             "%d day(s) left. Using the current token.",
             e, expires_at, max(left.days, 0))
        return token


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------

def get_threads_access_token() -> str:
    """Return a valid Threads access token, refreshing if within 5 days of
    its ~60-day expiry."""
    row = get_token("threads")
    if not row or not row.get("access_token"):
        raise RuntimeError(
            "No Threads token in the database — run seed_tokens.py first."
        )

    if _needs_refresh(row.get("expires_at"), THREADS_REFRESH_BUFFER):
        log.info("Threads: token due for refresh — refreshing now")
        try:
            data = _safe_get_retrying(
                f"{GRAPH_THREADS}/refresh_access_token",
                {"grant_type": "th_refresh_token", "access_token": row["access_token"]},
            )
        except Exception as e:
            # Meta refuses to refresh tokens younger than 24h, and a refresh
            # blip shouldn't block today's post if the current token still
            # works. Use it; we'll try the refresh again next run.
            log.warning("Threads: refresh failed (%s) — using current token", e)
            return row["access_token"]
        expires_at = datetime.utcnow() + timedelta(
            seconds=int(data.get("expires_in", 5_184_000))
        )
        set_token(
            "threads",
            access_token=data["access_token"],
            expires_at=expires_at.isoformat(),
        )
        log.info("Threads: token refreshed, valid until %s", expires_at)
        return data["access_token"]

    return row["access_token"]


# ---------------------------------------------------------------------------
# Facebook — does not expire. No refresh logic at all.
# ---------------------------------------------------------------------------

def get_facebook_access_token() -> str:
    """Return the stored Facebook Page token. Never refreshed — the
    System User-derived Page token this project uses does not expire."""
    row = get_token("facebook")
    if not row or not row.get("access_token"):
        raise RuntimeError(
            "No Facebook token in the database — run seed_tokens.py first."
        )
    return row["access_token"]
