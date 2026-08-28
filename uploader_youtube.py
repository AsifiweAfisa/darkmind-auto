"""
uploader_youtube.py — Upload the assembled MP4 to YouTube as a Short.

Auth: OAuth 2.0 desktop flow. On first run, opens a browser to authorize;
the resulting token is cached in YOUTUBE_TOKEN_FILE and refreshed
automatically afterwards.
"""

import os
from pathlib import Path
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from config import YOUTUBE_CLIENT_SECRETS_FILE, YOUTUBE_TOKEN_FILE
from utils import log, retry, truncate


SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _get_credentials() -> Credentials:
    """Load cached creds or run the OAuth flow."""
    creds: Optional[Credentials] = None
    if os.path.exists(YOUTUBE_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(YOUTUBE_TOKEN_FILE, SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(
            YOUTUBE_CLIENT_SECRETS_FILE, SCOPES
        )
        creds = flow.run_local_server(port=0)
    with open(YOUTUBE_TOKEN_FILE, "w") as f:
        f.write(creds.to_json())
    return creds


@retry(times=2, delay=5.0)
def upload_to_youtube(post: dict, video_path: str) -> str:
    """Upload as a public Short; return the YouTube video id."""
    creds = _get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    title = truncate(post["hook_text"], 60).strip()
    if not title:
        title = truncate(post["caption"], 60)
    hashtags = post["hashtags"]
    if isinstance(hashtags, str):
        import json
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
    video_id = response["id"]
    log.info("YouTube upload complete: https://youtu.be/%s", video_id)
    return video_id
