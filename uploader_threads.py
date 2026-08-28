"""
uploader_threads.py — Publish the assembled video to Threads.

Threads uses a separate API surface from Instagram/Facebook — different
base URL (graph.threads.net), different token, different user id — even
though it's part of the Meta family. Do not reuse IG_ACCESS_TOKEN or
FB_PAGE_TOKEN here.

Flow (container-based, same shape as Instagram Reels):
  1. Upload video to a public URL (Cloudinary, via CloudUploader).
  2. POST /{threads-user-id}/threads with media_type=VIDEO, video_url, text.
     Returns a creation_id (container).
  3. Poll GET /{creation_id}?fields=status until FINISHED.
  4. POST /{threads-user-id}/threads_publish with creation_id.
  5. Delete the temporary cloud upload.

Requires THREADS_ACCESS_TOKEN and THREADS_USER_ID in .env — see README for
how to generate them via the Threads API use case in the Meta Dashboard.
"""

import json
import time

import requests

from config import THREADS_USER_ID, THREADS_ACCESS_TOKEN
from cloud_upload import CloudUploader
from utils import log, retry


GRAPH = "https://graph.threads.net/v1.0"


@retry(times=2, delay=5.0, exceptions=(requests.RequestException,))
def _create_container(video_url: str, text: str) -> str:
    r = requests.post(
        f"{GRAPH}/{THREADS_USER_ID}/threads",
        data={
            "media_type": "VIDEO",
            "video_url": video_url,
            "text": text,
            "access_token": THREADS_ACCESS_TOKEN,
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if "id" not in data:
        raise RuntimeError(f"Threads container creation failed: {data}")
    return data["id"]


def _wait_until_finished(container_id: str, timeout_s: int = 600) -> None:
    """Poll status until FINISHED, or raise."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = requests.get(
            f"{GRAPH}/{container_id}",
            params={"fields": "status,error_message",
                    "access_token": THREADS_ACCESS_TOKEN},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        status = data.get("status")
        log.info("Threads container %s status=%s", container_id, status)
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Threads container failed: {data}")
        time.sleep(8)
    raise TimeoutError(f"Threads container {container_id} did not finish in time")


@retry(times=2, delay=5.0, exceptions=(requests.RequestException,))
def _publish(container_id: str) -> str:
    r = requests.post(
        f"{GRAPH}/{THREADS_USER_ID}/threads_publish",
        data={"creation_id": container_id, "access_token": THREADS_ACCESS_TOKEN},
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if "id" not in data:
        raise RuntimeError(f"Threads publish failed: {data}")
    return data["id"]


def upload_to_threads(post: dict, video_path: str) -> str:
    """Publish the video to Threads. Returns the Threads post id."""
    if not THREADS_USER_ID or not THREADS_ACCESS_TOKEN:
        raise RuntimeError("THREADS_USER_ID / THREADS_ACCESS_TOKEN not configured")

    hashtags = post["hashtags"]
    if isinstance(hashtags, str):
        hashtags = json.loads(hashtags)
    # Threads posts have a 500-char limit; keep it comfortably under that.
    text = (post["caption"] + "\n\n" + " ".join(f"#{h}" for h in hashtags))[:480]

    cloud = CloudUploader()
    public_url = cloud.upload(video_path)
    try:
        container_id = _create_container(public_url, text)
        _wait_until_finished(container_id)
        post_id = _publish(container_id)
        log.info("Threads post published: id=%s", post_id)
        return post_id
    finally:
        cloud.delete()