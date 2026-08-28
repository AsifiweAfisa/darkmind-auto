# DarkMind Auto

> *It posts while you sleep. It grows while you live.*

A private, fully autonomous content pipeline that writes, voices, assembles,
and publishes short-form vertical video content — end to end, no human
touch per post.

---

## What this is

DarkMind Auto is a faceless short-form content system built around a single
niche: **Human Psychology & Dark Truths**. Once a day, it:

1. **Writes** a 30–50 second voiceover script on a rotating theme from a
   fixed 15-topic list, engineered around a scroll-stopping opening hook.
2. **Voices** it with a calm, cinematic AI narration track.
3. **Assembles** a finished vertical (1080×1920) video — stock footage
   matched to the script's scenes, burned-in text overlays timed to key
   lines, dark color grading, mixed audio.
4. **Publishes** the same finished video, identically, to every connected
   platform.
5. **Logs** every step, tracks what succeeded and what didn't per platform,
   and never lets one platform's failure block the others.

It runs unattended on a daily schedule, with a `/health` endpoint for
remote monitoring.

## Why it exists

Producing consistent short-form content across multiple platforms by hand
is repetitive, time-consuming work: writing a hook-driven script, sourcing
footage, editing, exporting in the right aspect ratio, then manually
uploading the same file to several platforms with platform-specific
captions. This system removes that manual loop entirely while keeping full
creative control over the format (theme list, tone, video style) through
editable prompts and settings — not a black box.

## What it does, platform by platform

| Platform | What happens |
|---|---|
| **YouTube** | Uploads as a public Short via the YouTube Data API v3, OAuth-authenticated (app published, no 7-day token expiry) |
| **Instagram** | Publishes as a Reel via the Instagram Graph API (container → poll → publish) |
| **Facebook** | Publishes to a connected Page via the Meta Graph API |
| **Threads** | Publishes via the Threads API (separate auth/token from Instagram/Facebook) |
| **TikTok** | Publishes via the TikTok Content Posting API *(pending TikTok's Direct Post app review — disabled until approved)* |

**X/Twitter was deliberately left out of scope.** Its API requires a paid
Basic tier (~$100/mo) for any video upload — there is no functional
free-tier path for this use case.

## A note on what this deliberately does NOT include

Mobile-app automation (e.g. Appium driving the actual TikTok/Instagram
apps) was considered and rejected. Both platforms' Terms of Service
explicitly prohibit automating their apps, and accounts caught doing it
tend to be banned outright — which defeats the entire purpose of a system
meant to build a channel's history over time. Every platform here is
posted to exclusively through its official, sanctioned API.

## How it works — the pipeline

```
Scheduler (once/day, configurable)
        │
        ▼
Script generation (LLM: Groq / OpenAI / Anthropic / Gemini — configurable)
        │   → hook-driven script, on-screen hook text, caption, hashtags,
        │     5 scene descriptions, 3 timed text overlays
        ▼
Voiceover synthesis (ElevenLabs, with OpenAI TTS fallback)
        │
        ▼
Video assembly (FFmpeg)
        │   → stock clips fetched per scene (Pexels, Pixabay fallback)
        │   → trimmed, concatenated, color-graded
        │   → text overlays burned in at exact timestamps
        │   → voiceover mixed in
        ▼
Cloud staging (Cloudinary) — gives Meta/Threads a public URL to fetch from
        │
        ▼
Sequential upload: YouTube → Instagram → Facebook → Threads → (TikTok)
        │   → each platform is isolated: one failing never blocks the rest
        │   → each platform's success is recorded per-post in SQLite,
        │     so a post already live somewhere is never re-uploaded there
        │     by accident on a re-run
        ▼
Run log written (per-platform success counts, errors) + /health endpoint
```

## Design choices worth knowing about

- **Duplicate-upload protection.** Every post's per-platform success is
  tracked in the database. If a post already succeeded on a platform, the
  pipeline skips re-uploading there — even on a manual retry — unless you
  explicitly force it via `retry_upload(post_id, platform, force=True)`.
- **Hook-first script generation.** The prompt explicitly requires the
  script's opening line to function as a 3-second retention hook — a claim,
  twist, or question — not a slow introduction, since surviving the first
  few seconds without being skipped is the whole game in short-form video.
- **Energy-matched metadata.** Captions, on-screen hook text, and hashtags
  are generated to reference the *specific* content of that script, not
  generic reusable niche filler.
- **One video, everywhere, identically.** The pipeline generates one video
  per run and publishes that exact file to every platform — never
  different content per platform.

## Project layout

```
darkmind-auto/
├── main.py                  # Scheduler, health server, pipeline orchestrator,
│                             # duplicate-upload guard, retry_upload()
├── config.py                # All env loading + constants
├── database.py              # SQLite schema + CRUD
├── content_generator.py     # Groq/OpenAI/Anthropic/Gemini script generation
├── voiceover.py             # ElevenLabs / OpenAI TTS
├── video_assembler.py       # FFmpeg + Pexels/Pixabay
├── uploader_youtube.py      # YouTube Data API v3
├── uploader_instagram.py    # Instagram Graph API (Reels)
├── uploader_facebook.py     # Meta Graph API (Page video)
├── uploader_threads.py      # Threads API
├── uploader_tiktok.py       # TikTok Content Posting API (pending approval)
├── cloud_upload.py          # Cloudinary / S3 / GDrive hosting for public video URLs
├── utils.py                 # Logging, retry, helpers
├── requirements.txt
├── .env.example
├── README.md  ← you are here
└── logs/
```

## Tech stack

- **Language:** Python 3.12
- **Scheduling:** APScheduler
- **LLM:** Groq (Llama 3.3 70B) by default — swappable for OpenAI, Anthropic, or Gemini via `LLM_PROVIDER`
- **TTS:** ElevenLabs, with OpenAI TTS as automatic fallback
- **Video:** FFmpeg
- **Stock footage:** Pexels API, Pixabay API fallback
- **Cloud video staging:** Cloudinary (primary), S3/GDrive supported
- **Database:** SQLite
- **Health monitoring:** Flask `/health` endpoint
- **Platform APIs:** YouTube Data API v3, Meta Graph API (Instagram/Facebook), Threads API, TikTok Content Posting API

---

## Setup

### 1. Server requirements

A small Ubuntu VPS (1 vCPU / 2 GB RAM minimum, 4 GB recommended for ffmpeg)
works. Railway and Render also work — see the deployment section.

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv ffmpeg fonts-dejavu-core
```

### 2. Clone and install

```bash
git clone https://github.com/asifiweafisa/darkmind-auto.git
cd darkmind-auto
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
copy .env.example .env       # Windows: copy | macOS/Linux: cp
```

Open `.env` and fill in keys as you collect them below.

### 3. Get the API keys

#### LLM (pick one — Groq recommended, genuinely free, no regional restrictions)
- **Groq**: https://console.groq.com/ → API Keys → `GROQ_API_KEY`. Set `LLM_PROVIDER=groq`.
- **OpenAI**: requires billing. https://platform.openai.com/api-keys → `OPENAI_API_KEY`.
- **Anthropic**: https://console.anthropic.com/ → `ANTHROPIC_API_KEY`, `LLM_PROVIDER=anthropic`.
- **Gemini**: free tier availability varies by region — not reliable everywhere.

#### ElevenLabs (voiceover)
- https://elevenlabs.io/ → Profile → API key → `ELEVENLABS_API_KEY`.
- Voice library → pick a deep calm voice → copy its ID into `ELEVENLABS_VOICE_ID`.

#### Pexels & Pixabay
- Pexels: https://www.pexels.com/api/ → free key → `PEXELS_API_KEY`.
- Pixabay: https://pixabay.com/api/docs/ → free key → `PIXABAY_API_KEY`.

#### YouTube Data API v3
1. https://console.cloud.google.com/ → create a project.
2. Enable **YouTube Data API v3**.
3. OAuth consent screen → **Publish the app** (not just Testing mode —
   Testing-mode refresh tokens silently expire every 7 days).
4. Credentials → Create credentials → **OAuth client ID** → Desktop app.
5. Download the JSON, save as `client_secret.json` next to `main.py`.
6. First run opens a browser to authorize; `token.json` is saved and
   refreshed automatically from then on.

#### Instagram + Facebook (Meta Graph API)
1. Convert the Instagram account to **Professional** and connect it to a
   Facebook Page you own.
2. Create a Meta Business Portfolio at business.facebook.com if you don't
   have one; add the Page as an asset there.
3. Create a **System User** in Business Settings → Users → System users.
   Assign it the Page with the **Content** task (not just "Full
   access/Everything" — that combination does not actually grant posting
   rights for System Users).
4. Generate a token for that System User with scopes: `pages_manage_posts`,
   `pages_read_engagement`, `pages_show_list`.
5. Exchange it for the **Page-specific** access token via:
   `GET /me/accounts?fields=name,id,access_token` — use the `access_token`
   from that response (not the System User's own token) as `FB_PAGE_TOKEN`.
6. Get the Instagram Business Account ID linked to the Page via:
   `GET /{page-id}?fields=instagram_business_account` → use that `id` as
   `IG_USER_ID`.

#### Threads
1. Meta App Dashboard → **Use cases** → **Add use cases** → **Access the
   Threads API**.
2. Inside that use case → Settings → add your Threads username as a
   Tester, then **accept the invite from the Threads mobile app**
   (Settings → Account → Website permissions → Invitations).
3. Settings → User Token Generator → Generate Token → approve
   `threads_basic`, `threads_content_publish` → set as `THREADS_ACCESS_TOKEN`.
4. That token's owning user id (visible via the Access Token Debugger) is
   `THREADS_USER_ID`.

#### TikTok Content Posting API
1. https://developers.tiktok.com/ → create app.
2. Requires a real, hosted Terms of Service and Privacy Policy URL — a
   free GitHub Pages site works fine for this.
3. Add **Login Kit** and **Content Posting API** products.
4. Apply for `video.publish`/`video.upload` scopes and Direct Post
   approval. This is an audited review — expect days to weeks.
5. Complete OAuth to get `TIKTOK_ACCESS_TOKEN` / `TIKTOK_OPEN_ID`.
6. TikTok stays commented out of `main.py`'s `UPLOADERS` list until
   approval comes through — uncomment the one line once it does.

#### Cloud hosting for public video URLs (Cloudinary — recommended)
1. https://cloudinary.com/ → free account.
2. Cloud name, API key, API secret from the dashboard →
   `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`.
3. `CLOUD_PROVIDER=cloudinary`.

S3 and Google Drive are also supported (`CLOUD_PROVIDER=s3` or `gdrive`) —
see `cloud_upload.py` for the required variables of each.

### 4. Run

```bash
venv\Scripts\activate
python main.py
```

Scheduled job(s) register on start. Health check:

```bash
curl http://localhost:8080/health
```

### 5. Manual test run (don't wait for the schedule)

```python
from database import init_db
init_db()
from main import run_pipeline
run_pipeline()
```

### 6. Retry a specific platform for an existing post

```python
from main import retry_upload
retry_upload("post_id_here")                          # retry whatever's missing
retry_upload("post_id_here", "threads")                # retry just one platform
retry_upload("post_id_here", "facebook", force=True)   # force a duplicate post
```

### 7. Run as a systemd service (VPS)

```ini
# /etc/systemd/system/darkmind.service
[Unit]
Description=DarkMind Auto
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/darkmind-auto
ExecStart=/home/ubuntu/darkmind-auto/venv/bin/python main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now darkmind
sudo journalctl -u darkmind -f
```

### 8. Deploy to Railway / Render

- **Railway**: import the repo, set env vars in the dashboard, start
  command `python main.py`. Health check path: `/health`. Needs a
  `nixpacks.toml` adding `ffmpeg` and fonts to the build. YouTube's
  `client_secret.json`/`token.json` need to be handled via env vars or a
  Railway Volume, since the filesystem is ephemeral.
- **Render**: same, as a Background Worker (or a Web Service bound to
  `$PORT` if you want `/health` publicly reachable).

---

## Database

SQLite at `darkmind.db`. Tables: `posts`, `run_logs`, `rotation_state`
(remembers where in the 15-theme rotation the system left off, so themes
never repeat back-to-back across restarts).

```bash
sqlite3 darkmind.db "SELECT id, theme, status, posted_at FROM posts ORDER BY created_at DESC LIMIT 10;"
sqlite3 darkmind.db "SELECT * FROM run_logs ORDER BY id DESC LIMIT 5;"
```

## Tuning notes

- **Posting cadence**: currently 1×/day, configurable via `RUN_TIMES` and
  `DAILY_SCRIPT_COUNT` in `.env`. New accounts across multiple platforms
  can get throttled or shadow-banned if ramped up too aggressively — raise
  frequency gradually once accounts have some history.
- **Voice pacing**: `speed: 0.9` in `voiceover.py`. Lower for a slower,
  more cinematic read.
- **Stock footage matching**: searches Pexels/Pixabay by the LLM's
  `visual_directions` strings. If clips feel mismatched, tighten the
  prompt in `content_generator.py` toward more concrete, literal nouns.
- **Music**: `_fetch_ambient_music` in `video_assembler.py` is currently a
  stub — the mixing pipeline supports it, but no track source is wired in
  yet.
- **Failure isolation**: each platform upload is independent; one failing
  never blocks the others, and failures are logged per-post.

## Stopping it

- Foreground: `Ctrl+C`.
- Systemd: `sudo systemctl stop darkmind`.
- Railway/Render: stop the service from the dashboard.

---

## Status

Actively developed. Live on YouTube, Instagram, Facebook, and Threads.
TikTok integration is built and tested, pending TikTok's Direct Post app
review. X was evaluated and excluded (paid-tier-only API).

---

*This is a personal, private automation project. Not intended for public
distribution or reuse as-is.*