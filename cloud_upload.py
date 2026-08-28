"""
cloud_upload.py — Temporarily host the video on a publicly reachable URL,
which Instagram's and Facebook's Graph APIs require for video_url/file_url.
Three providers:

  - cloudinary: upload to Cloudinary, return its secure_url (recommended —
                this is the path already proven working for this project)
  - s3:         upload to S3, return a presigned URL (valid 1 hour)
  - gdrive:     upload to Drive folder, set anyone-with-link reader, return
                a direct-download URL.

After the platform finishes downloading, call delete() to clean up
(Cloudinary videos are deleted by public_id; S3/GDrive as before).
"""

from pathlib import Path
from typing import Optional

from config import (
    CLOUD_PROVIDER,
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_S3_BUCKET,
    GDRIVE_CREDENTIALS_FILE, GDRIVE_FOLDER_ID,
    CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET,
)
from utils import log


class CloudUploader:
    """Abstracts Cloudinary / S3 / Google Drive temporary hosting."""

    def __init__(self) -> None:
        self.provider = CLOUD_PROVIDER
        self._key: Optional[str] = None
        self._gdrive_id: Optional[str] = None
        self._cloudinary_public_id: Optional[str] = None

    # ----- Cloudinary -----
    def _cloudinary_upload(self, path: Path) -> str:
        import cloudinary
        import cloudinary.uploader

        cloudinary.config(
            cloud_name=CLOUDINARY_CLOUD_NAME,
            api_key=CLOUDINARY_API_KEY,
            api_secret=CLOUDINARY_API_SECRET,
            secure=True,
        )
        result = cloudinary.uploader.upload_large(
            str(path),
            resource_type="video",
            folder="darkmind_reels",
            chunk_size=6_000_000,
        )
        self._cloudinary_public_id = result.get("public_id")
        return result["secure_url"]

    def _cloudinary_delete(self) -> None:
        if not self._cloudinary_public_id:
            return
        import cloudinary
        import cloudinary.uploader

        cloudinary.config(
            cloud_name=CLOUDINARY_CLOUD_NAME,
            api_key=CLOUDINARY_API_KEY,
            api_secret=CLOUDINARY_API_SECRET,
            secure=True,
        )
        try:
            cloudinary.uploader.destroy(
                self._cloudinary_public_id, resource_type="video"
            )
        except Exception as e:
            log.warning("Cloudinary delete failed: %s", e)
        self._cloudinary_public_id = None

    # ----- S3 -----
    def _s3_upload(self, path: Path) -> str:
        import boto3
        s3 = boto3.client(
            "s3",
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION,
        )
        key = f"darkmind/{path.name}"
        s3.upload_file(
            str(path), AWS_S3_BUCKET, key,
            ExtraArgs={"ContentType": "video/mp4"},
        )
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": AWS_S3_BUCKET, "Key": key},
            ExpiresIn=3600,
        )
        self._key = key
        return url

    def _s3_delete(self) -> None:
        if not self._key:
            return
        import boto3
        s3 = boto3.client(
            "s3",
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION,
        )
        try:
            s3.delete_object(Bucket=AWS_S3_BUCKET, Key=self._key)
        except Exception as e:
            log.warning("S3 delete failed: %s", e)
        self._key = None

    # ----- Google Drive -----
    def _gdrive_upload(self, path: Path) -> str:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        creds = service_account.Credentials.from_service_account_file(
            GDRIVE_CREDENTIALS_FILE,
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        service = build("drive", "v3", credentials=creds)
        meta = {"name": path.name}
        if GDRIVE_FOLDER_ID:
            meta["parents"] = [GDRIVE_FOLDER_ID]
        media = MediaFileUpload(str(path), mimetype="video/mp4", resumable=True)
        f = service.files().create(
            body=meta, media_body=media, fields="id"
        ).execute()
        file_id = f["id"]
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()
        self._gdrive_id = file_id
        return f"https://drive.google.com/uc?export=download&id={file_id}"

    def _gdrive_delete(self) -> None:
        if not self._gdrive_id:
            return
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_file(
            GDRIVE_CREDENTIALS_FILE,
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        service = build("drive", "v3", credentials=creds)
        try:
            service.files().delete(fileId=self._gdrive_id).execute()
        except Exception as e:
            log.warning("GDrive delete failed: %s", e)
        self._gdrive_id = None

    # ----- public -----
    def upload(self, path: str) -> str:
        """Upload and return a public URL."""
        p = Path(path)
        log.info("Cloud upload (%s) -> %s", self.provider, p.name)
        if self.provider == "cloudinary":
            return self._cloudinary_upload(p)
        if self.provider == "s3":
            return self._s3_upload(p)
        if self.provider == "gdrive":
            return self._gdrive_upload(p)
        raise ValueError(f"Unknown CLOUD_PROVIDER: {self.provider}")

    def delete(self) -> None:
        """Remove the most recently uploaded file."""
        if self.provider == "cloudinary":
            self._cloudinary_delete()
        elif self.provider == "s3":
            self._s3_delete()
        elif self.provider == "gdrive":
            self._gdrive_delete()