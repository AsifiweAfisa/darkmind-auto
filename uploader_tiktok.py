"""
uploader_tiktok.py — Send the video to the TikTok INBOX (draft) via the
Content Posting API's inbox endpoint.

Why inbox and not Direct Post: Direct Post (/video/init/) requires an app
that has passed TikTok's audit, and this app was not approved for personal
use. The inbox endpoint (/inbox/video/init/) is available to any
authenticated app — it stages the video in your TikTok inbox/drafts, and you
finish the post with one tap in the app. The inbox endpoint accepts no
caption, so the caption is logged for you to paste.

Tokens: TikTok access tokens live ~24h. token_manager.get_tiktok_access_token()
refreshes proactively and persists the new pair to the database (not .env),
so this keeps working on ephemeral filesystems like Railway's.

Callable two ways:
  - main.py:    upload_to_tiktok(post: dict, video_path: str)
  - standalone: upload_to_tiktok(video_path: str, caption: str)
"""

import os

import requests

from token_manager import get_tiktok_access_token
from utils import log

INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"


def _init_upload(payload: dict, access_token: str) -> dict:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    r = requests.post(INIT_URL, json=payload, headers=headers, timeout=60)
    return r.json()


def upload_to_tiktok(post_or_video_path, video_path_or_caption: str = None) -> str:
    """Stage the video in the TikTok inbox. Returns the publish_id."""
    if isinstance(post_or_video_path, dict):
        post = post_or_video_path
        video_path = video_path_or_caption or post.get("video_path")
        caption = post.get("caption") or ""
    else:
        video_path = post_or_video_path
        caption = video_path_or_caption or ""

    if not video_path or not os.path.exists(video_path):
        raise ValueError(f"Invalid video file path: {video_path}")

    file_size = os.path.getsize(video_path)
    payload = {
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": file_size,
            "total_chunk_count": 1,
        }
    }

    if caption:
        log.info("TikTok caption to paste when you publish the draft: %s", caption)

    log.info("TikTok: initializing inbox upload")
    init_res = _init_upload(payload, get_tiktok_access_token())

    # If TikTok rejects the token even though our clock said it was fine
    # (revoked early, clock skew), force one refresh and retry once.
    if init_res.get("error", {}).get("code") == "access_token_invalid":
        log.warning("TikTok rejected the token — forcing a refresh and retrying")
        init_res = _init_upload(payload, get_tiktok_access_token(force=True))

    err = init_res.get("error", {})
    if err and err.get("code") != "ok":
        raise RuntimeError(f"TikTok inbox init failed: {init_res}")

    upload_url = init_res["data"]["upload_url"]
    publish_id = init_res["data"]["publish_id"]

    log.info("TikTok: pushing video file")
    with open(video_path, "rb") as f:
        video_data = f.read()

    upload_res = requests.put(
        upload_url,
        data=video_data,
        headers={
            "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
            "Content-Type": "video/mp4",
        },
        timeout=300,
    )
    if upload_res.status_code not in (200, 201, 206):
        raise RuntimeError(f"TikTok video upload failed: {upload_res.status_code} {upload_res.text[:300]}")

    log.info("TikTok: video staged in inbox (publish_id=%s) — open the TikTok "
             "app to publish it", publish_id)
    return publish_id
