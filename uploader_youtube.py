"""
uploader_youtube.py — Upload the assembled MP4 to YouTube as a Short, then
try to set a custom thumbnail.

Credentials come from token_manager.get_youtube_credentials(), which keeps
the OAuth token in the database and refreshes it automatically — no
token.json is read or written at runtime, so this works on ephemeral
filesystems (e.g. Railway). Run seed_tokens.py once to bootstrap.

Thumbnail: attempted after the upload via thumbnails.set, and NEVER fatal.
YouTube only allows custom Shorts thumbnails for eligible channels (Partner
Program members; the channel must also be verified), so on a newer channel
this is expected to be refused. The failure is logged and the upload still
counts as successful. Once the channel qualifies, it starts working with no
code changes.
"""

import json

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from branding import make_cover
from token_manager import get_youtube_credentials
from utils import log, retry, truncate


def _set_thumbnail(youtube, video_id: str, post: dict, video_path: str) -> None:
    """Best-effort custom thumbnail. Logs and returns on any failure."""
    try:
        cover = make_cover(post, video_path)
        if not cover:
            return
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(cover, mimetype="image/jpeg"),
        ).execute()
        log.info("YouTube custom thumbnail set for %s", video_id)
    except Exception as e:
        log.warning(
            "YouTube thumbnail not set (%s). Expected until the channel is "
            "verified and eligible for custom Shorts thumbnails — the video "
            "itself uploaded fine.", e)


@retry(times=2, delay=5.0)
def _upload(youtube, post: dict, video_path: str) -> str:
    title = truncate(post["hook_text"], 60).strip()
    if not title:
        title = truncate(post["caption"], 60)
    hashtags = post["hashtags"]
    if isinstance(hashtags, str):
        hashtags = json.loads(hashtags)
    description = (
        f"{post['caption']}\n\n"
        + " ".join(f"#{h}" for h in hashtags)
        + "\n\n#shorts"
    )

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": hashtags,
            "categoryId": "27",  # Education
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True,
                             mimetype="video/mp4")
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media,
    )
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log.info("YouTube upload progress: %d%%",
                     int(status.progress() * 100))
    return response["id"]


def upload_to_youtube(post: dict, video_path: str) -> str:
    """Upload as a public Short; return the YouTube video id.

    The retry wraps only the upload itself, so a thumbnail problem can never
    cause the video to be uploaded twice."""
    youtube = build("youtube", "v3", credentials=get_youtube_credentials())
    video_id = _upload(youtube, post, video_path)
    log.info("YouTube upload complete: https://youtu.be/%s", video_id)
    _set_thumbnail(youtube, video_id, post, video_path)
    return video_id
