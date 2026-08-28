"""
uploader_facebook.py — Publish the assembled video to a Facebook Page.

Requires a PAGE access token (not a User token, not a System User token —
see README note below). Get it via:

    GET https://graph.facebook.com/v20.0/me/accounts?fields=name,id,access_token
        &access_token=<your System User or User token>

The `access_token` field in that response, scoped to your specific Page, is
the FB_PAGE_TOKEN this module needs.
"""

import json

import requests

from config import FB_PAGE_ID, FB_PAGE_TOKEN
from cloud_upload import CloudUploader
from utils import log, retry


GRAPH = "https://graph.facebook.com/v20.0"


@retry(times=2, delay=5.0, exceptions=(requests.RequestException,))
def _publish(video_url: str, caption: str) -> str:
    r = requests.post(
        f"{GRAPH}/{FB_PAGE_ID}/videos",
        data={
            "file_url": video_url,
            "description": caption,
            "access_token": FB_PAGE_TOKEN,
        },
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    if "id" not in data:
        raise RuntimeError(f"Facebook publish failed: {data}")
    return data["id"]


def upload_to_facebook(post: dict, video_path: str) -> str:
    """Publish the video to the configured Facebook Page. Returns the video id."""
    if not FB_PAGE_ID or not FB_PAGE_TOKEN:
        raise RuntimeError("FB_PAGE_ID / FB_PAGE_TOKEN not configured")

    hashtags = post["hashtags"]
    if isinstance(hashtags, str):
        hashtags = json.loads(hashtags)
    caption = post["caption"] + "\n\n" + " ".join(f"#{h}" for h in hashtags)

    cloud = CloudUploader()
    public_url = cloud.upload(video_path)
    try:
        video_id = _publish(public_url, caption)
        log.info("Facebook video published: id=%s", video_id)
        return video_id
    finally:
        cloud.delete()