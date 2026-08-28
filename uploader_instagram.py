"""
uploader_instagram.py — Post a Reel via the Instagram Graph API.

Flow (Content Publishing API for Reels):
  1. Upload video to a publicly reachable URL (S3 / GDrive via cloud_upload).
  2. POST /{ig-user-id}/media with media_type=REELS and the public URL.
     This returns a creation_id (a "container").
  3. Poll GET /{creation_id}?fields=status_code until it reports FINISHED.
  4. POST /{ig-user-id}/media_publish with creation_id to publish.
  5. Delete the temporary cloud upload.

Requires:
  - An Instagram Professional (Business or Creator) account
  - That account linked to a Facebook Page
  - A long-lived User Access Token with instagram_content_publish,
    instagram_basic, pages_read_engagement, pages_show_list scopes.
"""

import json
import time

import requests

from config import IG_USER_ID, IG_ACCESS_TOKEN
from cloud_upload import CloudUploader
from utils import log, retry


GRAPH = "https://graph.facebook.com/v20.0"


@retry(times=2, delay=5.0, exceptions=(requests.RequestException,))
def _create_container(video_url: str, caption: str) -> str:
    r = requests.post(
        f"{GRAPH}/{IG_USER_ID}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
            "access_token": IG_ACCESS_TOKEN,
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["id"]


def _wait_until_finished(container_id: str, timeout_s: int = 600) -> None:
    """Poll status_code until FINISHED, or raise."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = requests.get(
            f"{GRAPH}/{container_id}",
            params={"fields": "status_code,status",
                    "access_token": IG_ACCESS_TOKEN},
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
def _publish(container_id: str) -> str:
    r = requests.post(
        f"{GRAPH}/{IG_USER_ID}/media_publish",
        data={"creation_id": container_id, "access_token": IG_ACCESS_TOKEN},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["id"]


def upload_to_instagram(post: dict, video_path: str) -> str:
    """Publish as a Reel. Returns the IG media id."""
    hashtags = post["hashtags"]
    if isinstance(hashtags, str):
        hashtags = json.loads(hashtags)
    caption = post["caption"] + "\n\n" + " ".join(f"#{h}" for h in hashtags)

    cloud = CloudUploader()
    public_url = cloud.upload(video_path)
    try:
        container_id = _create_container(public_url, caption)
        _wait_until_finished(container_id)
        media_id = _publish(container_id)
        log.info("Instagram Reel published: id=%s", media_id)
        return media_id
    finally:
        cloud.delete()
