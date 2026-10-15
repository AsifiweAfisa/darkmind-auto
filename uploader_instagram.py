"""
uploader_instagram.py — Post a Reel via the Instagram Graph API.

Flow (Content Publishing API for Reels):
  1. Upload video to a publicly reachable URL (Cloudinary/S3/GDrive via
     cloud_upload). The cover image is staged the same way (branding.py).
  2. POST /{ig-user-id}/media with media_type=REELS, the public video URL and
     (when available) cover_url. Returns a creation_id (a "container").
  3. Poll GET /{creation_id}?fields=status_code until it reports FINISHED.
  4. POST /{ig-user-id}/media_publish with creation_id to publish.
  5. Delete the temporary cloud uploads.

The cover is optional and never blocks a post: if it can't be built, hosted,
or Instagram rejects it, the Reel is published without a custom cover and
Instagram picks a frame itself.

The access token comes from token_manager.get_instagram_access_token(),
which refreshes the long-lived token automatically before its 60-day expiry.
"""

import json
import time
from typing import Optional

import requests

from config import IG_USER_ID
from branding import make_cover, host_image, delete_image
from cloud_upload import CloudUploader
from token_manager import get_instagram_access_token
from utils import log, retry


GRAPH = "https://graph.facebook.com/v20.0"


@retry(times=2, delay=5.0, exceptions=(requests.RequestException,))
def _create_container(video_url: str, caption: str, token: str,
                      cover_url: Optional[str] = None) -> str:
    data = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": token,
    }
    if cover_url:
        data["cover_url"] = cover_url
    r = requests.post(f"{GRAPH}/{IG_USER_ID}/media", data=data, timeout=60)
    r.raise_for_status()
    return r.json()["id"]


def _wait_until_finished(container_id: str, token: str, timeout_s: int = 600) -> None:
    """Poll status_code until FINISHED, or raise."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = requests.get(
            f"{GRAPH}/{container_id}",
            params={"fields": "status_code,status", "access_token": token},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        code = data.get("status_code")
        log.info("IG container %s status=%s", container_id, code)
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"IG container failed: {data}")
        time.sleep(8)
    raise TimeoutError(f"IG container {container_id} did not finish in time")


@retry(times=2, delay=5.0, exceptions=(requests.RequestException,))
def _publish(container_id: str, token: str) -> str:
    r = requests.post(
        f"{GRAPH}/{IG_USER_ID}/media_publish",
        data={"creation_id": container_id, "access_token": token},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["id"]


def _stage_cover(post: dict, video_path: str):
    """Build and host the cover. Returns (cover_url, public_id) or (None, None)."""
    try:
        cover_path = make_cover(post, video_path)
        if cover_path:
            hosted = host_image(cover_path)
            if hosted:
                return hosted
    except Exception as e:
        log.warning("Instagram cover skipped: %s", e)
    return None, None


def upload_to_instagram(post: dict, video_path: str) -> str:
    """Publish as a Reel. Returns the IG media id."""
    token = get_instagram_access_token()

    hashtags = post["hashtags"]
    if isinstance(hashtags, str):
        hashtags = json.loads(hashtags)
    caption = post["caption"] + "\n\n" + " ".join(f"#{h}" for h in hashtags)

    cover_url, cover_id = _stage_cover(post, video_path)
    cloud = CloudUploader()
    public_url = cloud.upload(video_path)
    try:
        try:
            container_id = _create_container(public_url, caption, token, cover_url)
        except Exception as e:
            if not cover_url:
                raise
            log.warning("Instagram rejected the cover (%s) — retrying without it", e)
            container_id = _create_container(public_url, caption, token, None)
        _wait_until_finished(container_id, token)
        media_id = _publish(container_id, token)
        log.info("Instagram Reel published: id=%s (custom cover: %s)",
                 media_id, "yes" if cover_url else "no")
        return media_id
    finally:
        cloud.delete()
        if cover_id:
            delete_image(cover_id)
