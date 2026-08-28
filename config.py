"""
config.py — Centralized configuration loaded from environment variables.

All other modules import constants from here. Never read os.getenv directly
elsewhere — keeps secrets surface auditable in one place.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent
AUDIO_DIR = BASE_DIR / "audio"
VIDEO_DIR = BASE_DIR / "videos"
TEMP_DIR = BASE_DIR / "temp"
LOG_DIR = BASE_DIR / "logs"
for d in (AUDIO_DIR, VIDEO_DIR, TEMP_DIR, LOG_DIR):
    d.mkdir(exist_ok=True)

# --- Scheduling ---
TIMEZONE = os.getenv("TIMEZONE", "America/New_York")
# Default: once a day. Add more comma-separated HH:MM times to post more often.
RUN_TIMES = [t.strip() for t in os.getenv("RUN_TIMES", "12:30").split(",")]
# How many scripts/videos to generate and post per scheduled run.
DAILY_SCRIPT_COUNT = int(os.getenv("DAILY_SCRIPT_COUNT", "1"))
HEALTH_PORT = int(os.getenv("HEALTH_PORT", "8080"))
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "darkmind.db"))

# --- LLM ---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# --- TTS ---
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "elevenlabs").lower()
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "onyx")

# --- Stock footage ---
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")

# --- YouTube ---
YOUTUBE_CLIENT_SECRETS_FILE = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secret.json")
YOUTUBE_TOKEN_FILE = os.getenv("YOUTUBE_TOKEN_FILE", "token.json")

# --- Instagram ---
IG_USER_ID = os.getenv("IG_USER_ID", "")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN", "")

# --- Facebook Page ---
FB_PAGE_ID = os.getenv("FB_PAGE_ID", "")
FB_PAGE_TOKEN = os.getenv("FB_PAGE_TOKEN", "")

# --- Threads ---
# Separate token/id from Instagram/Facebook — Threads uses graph.threads.net
# and its own auth flow. See README for how to generate these.
THREADS_USER_ID = os.getenv("THREADS_USER_ID", "")
THREADS_ACCESS_TOKEN = os.getenv("THREADS_ACCESS_TOKEN", "")

# --- TikTok ---
TIKTOK_ACCESS_TOKEN = os.getenv("TIKTOK_ACCESS_TOKEN", "")
TIKTOK_OPEN_ID = os.getenv("TIKTOK_OPEN_ID", "")

# --- Cloud hosting (used by Instagram + Facebook + Threads for public URLs) ---
CLOUD_PROVIDER = os.getenv("CLOUD_PROVIDER", "cloudinary").lower()
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET", "")
GDRIVE_CREDENTIALS_FILE = os.getenv("GDRIVE_CREDENTIALS_FILE", "gdrive_service_account.json")
GDRIVE_FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID", "")
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")

# --- Themes (locked rotation) ---
THEMES = [
    "Why you attract what you haven't healed",
    "Manipulation you don't notice until too late",
    "The psychology of silence and power",
    "Signs someone is secretly intimidated by you",
    "Why intelligent people struggle with love",
    "Hidden signs of high intelligence",
    "Why society punishes authenticity",
    "The dark side of being too nice",
    "How childhood wounds shape adult desires",
    "The truth about people who never apologize",
    "Why loneliness is increasing in a connected world",
    "Psychological tricks confident people use",
    "The danger of being self-aware but not healed",
    "Why most people never reach their potential",
    "How to read energy, not words",
]