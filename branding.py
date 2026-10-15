"""
branding.py — Everything the viewer sees as TEXT or as a COVER, in one place so
it always looks the same.

  1. burn_hook(post, video_path)
       Draws, in ONE encoding pass:
         - the hook_text, large, in the first HOOK_SECONDS
         - the three timed phrases (text_overlays), each for OVERLAY_SECONDS
       All text is word-wrapped and size-fitted to a safe area, so nothing can be
       cut off at the screen edges (ffmpeg's own drawtext cannot wrap, which is
       why long phrases used to run off the frame). Never fatal: on any error it
       logs a warning and returns the original video.

  2. make_cover(post, video_path)
       Builds the cover/thumbnail from ONE fixed template (TEMPLATE_VERSION):
       the same colour grade, vignette, typography, text position, accent bar and
       wordmark every time — only the hook wording and the background frame
       change. Used as the Instagram cover_url and the YouTube thumbnail.

  3. host_image(path) / delete_image(public_id)
       Stages the cover on Cloudinary so Instagram can fetch it, then removes it.

Look and feel is controlled by the constants just below. Font: set FONT_PATH in
.env to force a specific .ttf/.otf; otherwise a bold system font is found
automatically. Set BRAND_TEXT= (empty) in .env to drop the cover wordmark.
"""

import glob
import json
import os
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from config import (
    VIDEO_DIR, TEMP_DIR, CLOUD_PROVIDER,
    CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET,
)
from utils import log


# ---- Look and feel (edit these) --------------------------------------------
TEMPLATE_VERSION = 1                      # bump only if you deliberately redesign
W, H = 1080, 1920
HOOK_SECONDS = float(os.getenv("HOOK_SECONDS", "3.0"))   # hook on screen at the start
OVERLAY_SECONDS = 3.0                     # each timed phrase stays this long
OVERLAY_GAP = 0.2                         # pause between the hook and the first phrase
SAFE_WIDTH = 0.84                         # text never wider than 84% of the frame

TEXT_COLOR = (255, 255, 255)
STROKE_COLOR = (0, 0, 0)
ACCENT_COLOR = (224, 17, 58)              # the small bar under the text

# One typography for hook + cover (sizes only shrink if a hook is very long);
# timed phrases are a step smaller so the hook always leads.
HOOK_STYLE = dict(start=104, floor=72, max_lines=4, bar=True)
COVER_STYLE = HOOK_STYLE
OVERLAY_STYLE = dict(start=80, floor=54, max_lines=3, bar=False)
HOOK_Y_FRAC = 0.30                        # where the hook sits in the video
OVERLAY_Y_FRAC = 0.52                     # where the timed phrases sit
COVER_Y_FRAC = 0.50                       # where the hook sits on the cover

# Cover grade — makes very different stock footage read as one consistent set.
COVER_SATURATION = 0.55
COVER_CONTRAST = 1.08
COVER_TINT = (10, 16, 30)                 # cold, dark blue-black
COVER_TINT_AMOUNT = 0.30
COVER_DARKEN = 0.30
COVER_VIGNETTE = 0.55
BRAND_TEXT = os.getenv("BRAND_TEXT", "DARKMIND")
BRAND_Y_FRAC = 0.80


# ---- Fonts -----------------------------------------------------------------
_font_path_cache: Optional[str] = None
_font_searched = False


def find_font() -> Optional[str]:
    """Locate a bold font. FONT_PATH in .env wins; then common system paths."""
    global _font_path_cache, _font_searched
    if _font_searched:
        return _font_path_cache
    _font_searched = True

    candidates = []
    if os.getenv("FONT_PATH"):
        candidates.append(os.getenv("FONT_PATH"))
    candidates += [
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\impact.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for p in candidates:
        if p and os.path.exists(p):
            _font_path_cache = p
            return p

    for pattern in ("/nix/store/*/share/fonts/**/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/**/*Bold*.ttf"):
        hits = glob.glob(pattern, recursive=True)
        if hits:
            _font_path_cache = hits[0]
            return hits[0]

    log.warning("No bold font found — text will use a tiny default font. "
                "Set FONT_PATH in .env to a .ttf file.")
    return None


def _load_font(size: int) -> ImageFont.ImageFont:
    path = find_font()
    if path:
        return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                      # older Pillow without size support
        return ImageFont.load_default()


# ---- Text layout: wrap + fit so nothing can leave the frame ------------------

def _chunk_word(draw: ImageDraw.ImageDraw, word: str, font, max_w: int) -> List[str]:
    """Split one over-long word into pieces that each fit max_w."""
    pieces, cur = [], ""
    for ch in word:
        if cur and draw.textlength(cur + ch, font=font) > max_w:
            pieces.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        pieces.append(cur)
    return pieces


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int,
          hard_break: bool = False) -> List[str]:
    lines: List[str] = []
    cur = ""
    for word in text.split():
        pieces = [word]
        if hard_break and draw.textlength(word, font=font) > max_w:
            pieces = _chunk_word(draw, word, font, max_w)
        for piece in pieces:
            trial = f"{cur} {piece}".strip() if cur else piece
            if cur and draw.textlength(trial, font=font) > max_w:
                lines.append(cur)
                cur = piece
            else:
                cur = trial
    if cur:
        lines.append(cur)
    return lines


def _fit(draw: ImageDraw.ImageDraw, text: str, max_w: int, start: int,
         floor: int, max_lines: int):
    """Largest font size where the wrapped text fits the width and line count.
    Falls back to breaking over-long words, then to smaller-than-floor sizes —
    the text always ends up inside the frame."""
    for size in range(start, floor - 1, -4):
        font = _load_font(size)
        lines = _wrap(draw, text, font, max_w)
        if len(lines) <= max_lines and all(
                draw.textlength(ln, font=font) <= max_w for ln in lines):
            return font, lines, size
    for size in range(floor, 35, -4):
        font = _load_font(size)
        lines = _wrap(draw, text, font, max_w, hard_break=True)
        if len(lines) <= max_lines:
            return font, lines, size
    font = _load_font(36)
    return font, _wrap(draw, text, font, max_w, hard_break=True), 36


def render_text_layer(text: str, center_y_frac: float, style: dict,
                      size: Tuple[int, int] = (W, H)) -> Image.Image:
    """Transparent RGBA layer: wrapped, centred text on a soft dark band."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    max_w = int(size[0] * SAFE_WIDTH)
    font, lines, fsize = _fit(draw, text, max_w, style["start"], style["floor"],
                              style["max_lines"])

    line_h = int(fsize * 1.18)
    block_h = line_h * len(lines)
    top = int(size[1] * center_y_frac) - block_h // 2

    # soft dark band behind the text so it stays legible on bright footage
    band = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(band).rectangle(
        [0, top - 70, size[0], top + block_h + 100], fill=(0, 0, 0, 120))
    band = band.filter(ImageFilter.GaussianBlur(40))
    layer = Image.alpha_composite(layer, band)
    draw = ImageDraw.Draw(layer)

    y = top
    stroke = max(4, fsize // 16)
    for line in lines:
        x = (size[0] - draw.textlength(line, font=font)) / 2
        draw.text((x + 4, y + 5), line, font=font, fill=(0, 0, 0, 170))   # shadow
        draw.text((x, y), line, font=font, fill=TEXT_COLOR + (255,),
                  stroke_width=stroke, stroke_fill=STROKE_COLOR + (255,))
        y += line_h

    if style.get("bar"):
        bar_w, bar_y = 160, top + block_h + 14
        draw.rounded_rectangle(
            [(size[0] - bar_w) // 2, bar_y, (size[0] + bar_w) // 2, bar_y + 10],
            radius=5, fill=ACCENT_COLOR + (255,))
    return layer


# ---- Timing of the timed phrases ---------------------------------------------

def _overlay_windows(post: dict, duration: Optional[float]) -> List[Tuple[float, float, str]]:
    """(start, end, text) for each timed phrase: never colliding with the hook or
    with each other, and never past the end of the video."""
    overlays = post.get("text_overlays") or []
    if isinstance(overlays, str):
        overlays = json.loads(overlays)
    wins: List[Tuple[float, float, str]] = []
    prev_end = HOOK_SECONDS + OVERLAY_GAP - 0.3
    for o in sorted(overlays, key=lambda o: float(o["time_seconds"])):
        text = (o.get("text") or "").strip()
        if not text:
            continue
        start = max(float(o["time_seconds"]), prev_end + 0.3)
        if duration and start >= duration - 1.0:
            continue
        end = start + OVERLAY_SECONDS
        if duration:
            end = min(end, duration - 0.2)
        wins.append((start, end, text))
        prev_end = end
    return wins


# ---- 1. Hook + timed phrases -------------------------------------------------

def _duration(video_path: str) -> Optional[float]:
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            text=True).strip()
        return float(out)
    except Exception:
        return None


def burn_hook(post: dict, video_path: str) -> str:
    """Burn the hook and the timed phrases into the video (one encode pass).
    Returns the new path (<id>_hook.mp4), or the original path on any problem."""
    pngs: List[Path] = []
    try:
        hook = (post.get("hook_text") or "").strip()
        src = Path(video_path)
        if src.stem.endswith("_hook"):           # already done — don't double-burn
            return video_path

        wins = _overlay_windows(post, _duration(video_path))
        if not hook and not wins:
            return video_path

        out = src.with_name(src.stem + "_hook.mp4")
        specs: List[Tuple[Path, float, float]] = []   # (png, start, end)
        if hook:
            p = TEMP_DIR / f"{src.stem}_hook.png"
            render_text_layer(hook, HOOK_Y_FRAC, HOOK_STYLE).save(p)
            pngs.append(p)
            specs.append((p, 0.0, HOOK_SECONDS))
        for i, (s, e, text) in enumerate(wins):
            p = TEMP_DIR / f"{src.stem}_ov{i}.png"
            render_text_layer(text, OVERLAY_Y_FRAC, OVERLAY_STYLE).save(p)
            pngs.append(p)
            specs.append((p, s, e))

        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
        for p, _, _ in specs:
            cmd += ["-i", str(p)]
        chain, last = [], "0:v"
        for k, (_, s, e) in enumerate(specs):
            chain.append(f"[{last}][{k + 1}:v]overlay=0:0:"
                         f"enable='between(t,{s:.2f},{e:.2f})'[v{k}]")
            last = f"v{k}"
        cmd += ["-filter_complex", ";".join(chain),
                "-map", f"[{last}]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                "-c:a", "copy", str(out)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            raise RuntimeError(proc.stderr[-600:])

        log.info("Text burned in: hook (first %.1fs) + %d timed phrase(s) -> %s",
                 HOOK_SECONDS, len(wins), out)
        return str(out)
    except Exception as e:
        log.warning("Text burn-in failed (%s) — continuing with the plain video", e)
        return video_path
    finally:
        for p in pngs:
            p.unlink(missing_ok=True)


# ---- 2. Cover image (one fixed template) -------------------------------------

def _clean_frame_time(post: dict, duration: Optional[float]) -> float:
    """A timestamp with no burned-in text on screen: after the hook window and
    outside every timed phrase."""
    windows = [(0.0, HOOK_SECONDS + OVERLAY_GAP)]
    windows += [(s, e) for s, e, _ in _overlay_windows(post, duration)]
    for cand in (8.0, 10.0, 12.0, 20.0, 24.0, 28.0, 31.0, 6.0):
        if duration and cand > duration - 1.0:
            continue
        if all(not (a - 0.3 <= cand <= b + 0.3) for a, b in windows):
            return cand
    return min(8.0, (duration or 10.0) * 0.4)


def _vignette(size: Tuple[int, int], amount: float) -> Image.Image:
    """Mask that is bright in the middle and falls off toward the edges."""
    sw, sh = size[0] // 4, size[1] // 4
    m = Image.new("L", (sw, sh), 0)
    ImageDraw.Draw(m).ellipse([-sw * 0.25, sh * 0.05, sw * 1.25, sh * 0.95], fill=255)
    m = m.filter(ImageFilter.GaussianBlur(55)).resize(size, Image.BILINEAR)
    return m


def _grade(img: Image.Image) -> Image.Image:
    """The cover's fixed colour treatment: desaturated, cold-tinted, darkened,
    vignetted — so any stock frame ends up with the same look."""
    img = ImageEnhance.Color(img).enhance(COVER_SATURATION)
    img = ImageEnhance.Contrast(img).enhance(COVER_CONTRAST)
    img = Image.blend(img, Image.new("RGB", img.size, COVER_TINT), COVER_TINT_AMOUNT)
    img = Image.blend(img, Image.new("RGB", img.size, (0, 0, 0)), COVER_DARKEN)
    dark = Image.new("RGB", img.size, (0, 0, 0))
    vignetted = Image.composite(img, dark, _vignette(img.size, COVER_VIGNETTE))
    return Image.blend(img, vignetted, COVER_VIGNETTE)


def _wordmark_layer() -> Optional[Image.Image]:
    """Small letter-spaced brand name at a fixed spot."""
    if not BRAND_TEXT.strip():
        return None
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    font = _load_font(34)
    chars = list(BRAND_TEXT.upper())
    spacing = 12
    widths = [draw.textlength(c, font=font) for c in chars]
    x = (W - (sum(widths) + spacing * (len(chars) - 1))) / 2
    y = int(H * BRAND_Y_FRAC)
    for c, w in zip(chars, widths):
        draw.text((x, y), c, font=font, fill=(255, 255, 255, 215),
                  stroke_width=2, stroke_fill=(0, 0, 0, 200))
        x += w + spacing
    return layer


def make_cover(post: dict, video_path: str) -> Optional[str]:
    """Create (or reuse) <id>_cover.jpg — 1080x1920, under 1.9 MB — from the
    fixed template. Returns the path, or None if it couldn't be made (callers
    treat that as 'no cover')."""
    try:
        hook = (post.get("hook_text") or "").strip()
        if not hook:
            return None
        base = Path(video_path).stem.replace("_hook", "")
        out = VIDEO_DIR / f"{base}_cover.jpg"
        if out.exists():
            return str(out)

        t = _clean_frame_time(post, _duration(video_path))
        frame = TEMP_DIR / f"{base}_frame.jpg"
        proc = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-ss", f"{t:.2f}", "-i", video_path,
             "-frames:v", "1", "-q:v", "2", str(frame)],
            capture_output=True, text=True)
        if proc.returncode != 0 or not frame.exists():
            raise RuntimeError(proc.stderr[-400:])

        img = Image.open(frame).convert("RGB")
        scale = max(W / img.width, H / img.height)       # fill 1080x1920, no distortion
        img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1))
        left, top = (img.width - W) // 2, (img.height - H) // 2
        img = _grade(img.crop((left, top, left + W, top + H))).convert("RGBA")

        img = Image.alpha_composite(img, render_text_layer(hook, COVER_Y_FRAC, COVER_STYLE))
        wm = _wordmark_layer()
        if wm is not None:
            img = Image.alpha_composite(img, wm)
        img = img.convert("RGB")

        for quality in (92, 85, 75, 65):                 # YouTube caps thumbnails at 2 MB
            img.save(out, "JPEG", quality=quality, optimize=True)
            if out.stat().st_size < 1_900_000:
                break
        frame.unlink(missing_ok=True)
        log.info("Cover created (template v%d, frame at %.1fs) -> %s",
                 TEMPLATE_VERSION, t, out)
        return str(out)
    except Exception as e:
        log.warning("Cover image could not be created (%s)", e)
        return None


# ---- 3. Hosting the cover for Instagram -----------------------------------

def host_image(path: str) -> Optional[Tuple[str, str]]:
    """Stage an image on Cloudinary. Returns (public_url, public_id) or None."""
    if CLOUD_PROVIDER != "cloudinary":
        log.info("Cover hosting only implemented for Cloudinary — skipping cover")
        return None
    import cloudinary
    import cloudinary.uploader
    cloudinary.config(cloud_name=CLOUDINARY_CLOUD_NAME, api_key=CLOUDINARY_API_KEY,
                      api_secret=CLOUDINARY_API_SECRET, secure=True)
    r = cloudinary.uploader.upload(path, resource_type="image",
                                   folder="darkmind_covers")
    return r["secure_url"], r["public_id"]


def delete_image(public_id: str) -> None:
    """Remove a staged cover image. Failures are logged, never raised."""
    try:
        import cloudinary
        import cloudinary.uploader
        cloudinary.config(cloud_name=CLOUDINARY_CLOUD_NAME, api_key=CLOUDINARY_API_KEY,
                          api_secret=CLOUDINARY_API_SECRET, secure=True)
        cloudinary.uploader.destroy(public_id, resource_type="image")
    except Exception as e:
        log.warning("Cover image cleanup failed: %s", e)
