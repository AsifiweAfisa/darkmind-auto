"""
main.py — Entry point.

Responsibilities:
  - Initialize DB
  - Start a Flask /health endpoint in a background thread
  - Schedule pipeline runs at the configured times (default: once a day)
  - On each run: generate scripts, build videos, upload to all platforms,
    log everything, never let one failure halt the rest.
  - Skip re-uploading to a platform a post has already succeeded on, unless
    explicitly forced via retry_upload().

Run with:  python main.py
"""

import threading
import time
import traceback
from datetime import datetime
from typing import List, Optional

from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from config import TIMEZONE, RUN_TIMES, HEALTH_PORT, DAILY_SCRIPT_COUNT
from database import init_db, insert_run_log, update_post, get_post, get_recent_status
from content_generator import generate_daily_scripts
from voiceover import generate_voiceover
from video_assembler import assemble_video
from uploader_youtube import upload_to_youtube
from uploader_instagram import upload_to_instagram
from uploader_facebook import upload_to_facebook
from uploader_threads import upload_to_threads
from uploader_tiktok import upload_to_tiktok
from utils import log


# Platform registry: (name, upload fn, DB field that stores its post id)
# X is out of scope — its API requires a paid tier for video uploads.
UPLOADERS = [
    ("youtube", upload_to_youtube, "youtube_post_id"),
    ("instagram", upload_to_instagram, "instagram_post_id"),
    ("facebook", upload_to_facebook, "facebook_post_id"),
    ("threads", upload_to_threads, "threads_post_id"),
    # ("tiktok", upload_to_tiktok, "tiktok_post_id"),  # enable once TikTok app is approved
]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _process_one(post: dict) -> dict:
    """Generate audio + video and upload to all configured platforms.

    Skips any platform for which this post already has a stored post id
    (i.e. it already succeeded there) — prevents accidental duplicate posts
    on manual re-runs. Use retry_upload(..., force=True) to override.

    Returns a dict of which platforms succeeded, plus errors collected.
    """
    result = {"id": post["id"], "errors": []}
    for name, _, _ in UPLOADERS:
        result[name] = False

    update_post(post["id"], status="processing")

    # Voiceover
    try:
        audio_path = generate_voiceover(post["script"], post["id"])
        update_post(post["id"], audio_path=audio_path)
    except Exception as e:
        msg = f"voiceover failed for {post['id']}: {e}"
        log.error(msg)
        result["errors"].append(msg)
        update_post(post["id"], status="failed")
        return result

    # Video
    try:
        video_path = assemble_video(post, audio_path)
        update_post(post["id"], video_path=video_path)
    except Exception as e:
        msg = f"video assembly failed for {post['id']}: {e}\n{traceback.format_exc()}"
        log.error(msg)
        result["errors"].append(msg)
        update_post(post["id"], status="failed")
        return result

    # Uploads — sequential, isolated, duplicate-safe
    for name, fn, field in UPLOADERS:
        current = get_post(post["id"]) or {}
        if current.get(field):
            log.info(
                "%s: post %s already uploaded (id=%s) — skipping to avoid "
                "duplicate. Use retry_upload(force=True) to re-post anyway.",
                name, post["id"], current[field],
            )
            result[name] = True
            continue
        try:
            remote_id = fn(post, video_path)
            update_post(post["id"], **{field: str(remote_id)})
            result[name] = True
            time.sleep(2)  # gentle spacing between platforms
        except Exception as e:
            msg = f"{name} upload failed for {post['id']}: {e}"
            log.error(msg)
            result["errors"].append(msg)

    update_post(
        post["id"],
        status="posted" if any(result[name] for name, _, _ in UPLOADERS) else "failed",
        posted_at=datetime.utcnow().isoformat(),
    )
    return result


def retry_upload(post_id: str, platform: Optional[str] = None, force: bool = False) -> dict:
    """Manually (re)attempt upload(s) for an existing post.

    platform: one of the names in UPLOADERS (e.g. "instagram"), or None to
              attempt every configured platform.
    force:    if True, re-upload even if this post already has a stored id
              for that platform (creates a genuine duplicate post — only do
              this if you actually want two copies live).

    Example:
        retry_upload("1c3c65d84550")                       # retry whatever's missing
        retry_upload("1c3c65d84550", "threads")             # retry just Threads
        retry_upload("1c3c65d84550", "facebook", force=True) # force a duplicate
    """
    post = get_post(post_id)
    if not post:
        raise ValueError(f"No post found with id {post_id}")
    video_path = post.get("video_path")
    if not video_path:
        raise ValueError(f"Post {post_id} has no assembled video to upload")

    targets = [u for u in UPLOADERS if platform is None or u[0] == platform]
    if not targets:
        raise ValueError(f"Unknown platform '{platform}'")

    out = {}
    for name, fn, field in targets:
        if post.get(field) and not force:
            log.info("%s: post %s already uploaded (id=%s) — skipping. "
                     "Pass force=True to re-post.", name, post_id, post[field])
            out[name] = "skipped-already-posted"
            continue
        try:
            remote_id = fn(post, video_path)
            update_post(post_id, **{field: str(remote_id)})
            out[name] = f"success:{remote_id}"
            log.info("%s: retry succeeded for %s -> %s", name, post_id, remote_id)
        except Exception as e:
            out[name] = f"failed:{e}"
            log.error("%s: retry failed for %s: %s", name, post_id, e)
    return out


def run_pipeline() -> None:
    """One scheduled run: generate DAILY_SCRIPT_COUNT scripts, all platforms each."""
    log.info("=== Pipeline run start ===")
    posts = generate_daily_scripts(num=DAILY_SCRIPT_COUNT)
    if not posts:
        log.error("No scripts generated — aborting run.")
        insert_run_log(0, 0, 0, 0, 0, 0, "no scripts generated")
        return

    counts = {name: 0 for name, _, _ in UPLOADERS}
    all_errors: List[str] = []
    for post in posts:
        try:
            r = _process_one(post)
            for name in counts:
                counts[name] += int(r.get(name, False))
            all_errors.extend(r["errors"])
        except Exception as e:
            msg = f"pipeline crashed on {post.get('id')}: {e}"
            log.error(msg)
            all_errors.append(msg)

    insert_run_log(
        scripts_generated=len(posts),
        yt=counts.get("youtube", 0),
        ig=counts.get("instagram", 0),
        fb=counts.get("facebook", 0),
        th=counts.get("threads", 0),
        tt=counts.get("tiktok", 0),
        errors=" | ".join(all_errors)[:4000],
    )
    log.info("=== Pipeline run done | %s ===",
              " ".join(f"{k}={v}" for k, v in counts.items()))


# ---------------------------------------------------------------------------
# Flask health server
# ---------------------------------------------------------------------------

def _start_health_server() -> None:
    app = Flask("darkmind-health")

    @app.route("/health")
    def health():
        return jsonify(get_recent_status())

    @app.route("/")
    def root():
        return jsonify({"name": "DarkMind Auto",
                        "tagline": "It posts while you sleep. It grows while you live."})

    def run():
        app.run(host="0.0.0.0", port=HEALTH_PORT, threaded=True,
                use_reloader=False)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    log.info("Health server on :%d", HEALTH_PORT)


# ---------------------------------------------------------------------------
# Scheduler bootstrap
# ---------------------------------------------------------------------------

def main() -> None:
    init_db()
    _start_health_server()

    tz = pytz.timezone(TIMEZONE)
    scheduler = BackgroundScheduler(timezone=tz)
    for t in RUN_TIMES:
        hour, minute = t.split(":")
        scheduler.add_job(
            run_pipeline,
            trigger=CronTrigger(hour=int(hour), minute=int(minute),
                                 timezone=tz),
            id=f"run_{t}",
            replace_existing=True,
            misfire_grace_time=600,
        )
        log.info("Scheduled run at %s %s", t, TIMEZONE)
    scheduler.start()

    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutting down...")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()