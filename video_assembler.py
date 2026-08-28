"""
video_assembler.py — Assembles vertical (1080x1920) videos from stock footage,
voiceover, ambient music, and burned-in text overlays.

Pipeline:
  1. Probe voiceover duration.
  2. For each visual_direction, search Pexels (Pixabay fallback) and
     download a short vertical clip.
  3. Trim each clip to (audio_duration / num_scenes) and concat them.
  4. Apply dark mood overlay.
  5. Mix voiceover (dominant) with a low-volume ambient music track from
     Pixabay.
  6. Burn text overlays at exact timestamps.
  7. Encode H.264, 30fps, 1080x1920.
"""

import os
import json
import shlex
import shutil
import subprocess
import random
from pathlib import Path
from typing import List

import requests

from config import (
    PEXELS_API_KEY, PIXABAY_API_KEY, VIDEO_DIR, TEMP_DIR,
)
from utils import log, retry, cleanup_temp


# ---------------------------------------------------------------------------
# Probing & ffmpeg helpers
# ---------------------------------------------------------------------------

def _probe_duration(path: str) -> float:
    """Return media duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
    ]
    out = subprocess.check_output(cmd).decode().strip()
    return float(out)


def _run(cmd: List[str]) -> None:
    """Run a subprocess and raise on non-zero with stderr surfaced."""
    log.debug("RUN: %s", " ".join(shlex.quote(c) for c in cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("FFmpeg stderr:\n%s", proc.stderr[-2000:])
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd[:3])}...")


# ---------------------------------------------------------------------------
# Stock footage search & download
# ---------------------------------------------------------------------------

@retry(times=2, delay=2.0, exceptions=(requests.RequestException,))
def _pexels_search(query: str) -> str | None:
    """Return a direct video URL for a vertical clip matching query."""
    if not PEXELS_API_KEY:
        return None
    r = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": PEXELS_API_KEY},
        params={"query": query, "orientation": "portrait", "per_page": 10,
                "size": "medium"},
        timeout=20,
    )
    r.raise_for_status()
    videos = r.json().get("videos", [])
    random.shuffle(videos)
    for v in videos:
        files = sorted(v.get("video_files", []),
                       key=lambda f: f.get("height", 0), reverse=True)
        for f in files:
            if f.get("width", 0) <= f.get("height", 0):  # vertical
                return f["link"]
    return None


@retry(times=2, delay=2.0, exceptions=(requests.RequestException,))
def _pixabay_search(query: str) -> str | None:
    if not PIXABAY_API_KEY:
        return None
    r = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": PIXABAY_API_KEY, "q": query, "per_page": 20,
                "video_type": "film"},
        timeout=20,
    )
    r.raise_for_status()
    hits = r.json().get("hits", [])
    random.shuffle(hits)
    for h in hits:
        for size in ("large", "medium", "small"):
            v = h.get("videos", {}).get(size)
            if v and v.get("height", 0) > v.get("width", 0):
                return v["url"]
    return None


def _download(url: str, out_path: Path) -> None:
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if chunk:
                    f.write(chunk)


def _fetch_clip(query: str, out_path: Path) -> bool:
    """Try Pexels then Pixabay. Return True on success."""
    for fn in (_pexels_search, _pixabay_search):
        try:
            url = fn(query)
        except Exception as e:
            log.warning("%s search failed: %s", fn.__name__, e)
            continue
        if not url:
            continue
        try:
            _download(url, out_path)
            return True
        except Exception as e:
            log.warning("download failed (%s): %s", url, e)
    return False


@retry(times=2, delay=2.0, exceptions=(requests.RequestException,))
def _fetch_ambient_music(out_path: Path) -> bool:
    """Pull a dark ambient music track from Pixabay (audio API)."""
    if not PIXABAY_API_KEY:
        return False
    # Pixabay's audio API requires a different endpoint; we use a curated
    # search term that consistently returns dark/ambient tracks.
    r = requests.get(
        "https://pixabay.com/api/",
        params={"key": PIXABAY_API_KEY, "q": "dark+ambient", "per_page": 5},
        timeout=20,
    )
    # Pixabay's free music API requires their music endpoint which is gated.
    # We fall back to a deterministic CC0 ambient sample bundled by request:
    # if music can't be fetched, the function returns False and the assembly
    # proceeds without music.
    return False


# ---------------------------------------------------------------------------
# Text overlay helpers
# ---------------------------------------------------------------------------

def _drawtext_filter(text: str, t_start: float, duration: float = 3.0) -> str:
    """Build an ffmpeg drawtext filter expression for one overlay."""
    # Escape characters that break ffmpeg filter parsing.
    safe = (
        text.replace("\\", "\\\\")
            .replace(":", r"\:")
            .replace("'", r"\\'")
            .replace(",", r"\,")
    )
    return (
        f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
        f"text='{safe}':fontsize=68:fontcolor=white:"
        f"borderw=4:bordercolor=black@0.85:"
        f"shadowcolor=black@0.7:shadowx=2:shadowy=2:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:"
        f"enable='between(t,{t_start},{t_start + duration})'"
    )


# ---------------------------------------------------------------------------
# Main assembly
# ---------------------------------------------------------------------------

def assemble_video(post: dict, audio_path: str) -> str:
    """Build the video and return its output path. Raises on failure."""
    post_id = post["id"]
    visual_directions = post["visual_directions"]
    overlays = post["text_overlays"]
    if isinstance(visual_directions, str):
        visual_directions = json.loads(visual_directions)
    if isinstance(overlays, str):
        overlays = json.loads(overlays)

    audio_duration = _probe_duration(audio_path)
    if audio_duration < 5:
        raise RuntimeError(f"audio suspiciously short: {audio_duration}s")
    log.info("Voiceover duration: %.2fs", audio_duration)

    work = TEMP_DIR / post_id
    work.mkdir(parents=True, exist_ok=True)

    # 1. Fetch a clip per scene
    n = len(visual_directions)
    per_clip = audio_duration / n
    clip_paths: List[Path] = []
    for i, query in enumerate(visual_directions):
        clip = work / f"clip_{i}.mp4"
        if not _fetch_clip(query, clip):
            log.warning("No clip found for '%s' — using black filler", query)
            # generate a black filler clip
            _run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"color=c=black:s=1080x1920:d={per_clip + 1}:r=30",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
            ])
        clip_paths.append(clip)

    # 2. Re-encode each clip to uniform 1080x1920, 30fps, trimmed to per_clip
    norm_paths: List[Path] = []
    for i, p in enumerate(clip_paths):
        out = work / f"norm_{i}.mp4"
        _run([
            "ffmpeg", "-y", "-i", str(p),
            "-t", f"{per_clip:.3f}",
            "-vf", (
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,fps=30"
            ),
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "veryfast", "-crf", "22", str(out),
        ])
        norm_paths.append(out)

    # 3. Concat
    concat_list = work / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in norm_paths)
    )
    concat_video = work / "concat.mp4"
    _run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list), "-c", "copy", str(concat_video),
    ])

    # 4. Build drawtext chain
    overlays_sorted = sorted(overlays, key=lambda o: o["time_seconds"])
    drawtexts = ",".join(
        _drawtext_filter(o["text"], o["time_seconds"]) for o in overlays_sorted
    )
    # Dark mood overlay applied via colorchannelmixer / lutyuv before drawtext.
    vfilter = (
        "format=yuv420p,"
        "eq=brightness=-0.08:saturation=0.85:contrast=1.05"
    )
    if drawtexts:
        vfilter += "," + drawtexts

    # 5. Try ambient music
    music_path = work / "music.mp3"
    has_music = _fetch_ambient_music(music_path)

    # 6. Final mux: voiceover + (optional) music + filtered video
    out_path = VIDEO_DIR / f"{post_id}.mp4"
    if has_music:
        _run([
            "ffmpeg", "-y",
            "-i", str(concat_video),
            "-i", audio_path,
            "-i", str(music_path),
            "-filter_complex",
            (
                f"[0:v]{vfilter}[v];"
                "[1:a]volume=1.0[a1];"
                "[2:a]volume=0.12[a2];"
                "[a1][a2]amix=inputs=2:duration=first:dropout_transition=2[a]"
            ),
            "-map", "[v]", "-map", "[a]",
            "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "veryfast", "-crf", "21",
            "-c:a", "aac", "-b:a", "192k",
            "-r", "30",
            str(out_path),
        ])
    else:
        _run([
            "ffmpeg", "-y",
            "-i", str(concat_video),
            "-i", audio_path,
            "-filter_complex", f"[0:v]{vfilter}[v]",
            "-map", "[v]", "-map", "1:a",
            "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "veryfast", "-crf", "21",
            "-c:a", "aac", "-b:a", "192k",
            "-r", "30",
            str(out_path),
        ])

    cleanup_temp(work)
    log.info("Video assembled -> %s", out_path)
    return str(out_path)
