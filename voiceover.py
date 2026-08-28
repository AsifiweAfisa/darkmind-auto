"""
voiceover.py — Generates an MP3 voiceover for a script.

Primary: ElevenLabs. Fallback: OpenAI TTS. One retry on the primary, then
falls through to the fallback. If both fail, raises so the caller can skip.
"""

from pathlib import Path

from config import (
    AUDIO_DIR, TTS_PROVIDER,
    ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, ELEVENLABS_MODEL,
    OPENAI_API_KEY, OPENAI_TTS_VOICE,
)
from utils import log, retry


@retry(times=2, delay=2.0)
def _elevenlabs(text: str, out_path: Path) -> None:
    """Synthesize via ElevenLabs and write MP3."""
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY missing")
    from elevenlabs.client import ElevenLabs
    client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
    audio_iter = client.text_to_speech.convert(
        voice_id=ELEVENLABS_VOICE_ID,
        model_id=ELEVENLABS_MODEL,
        text=text,
        output_format="mp3_44100_128",
        voice_settings={
            "stability": 0.55,
            "similarity_boost": 0.8,
            "style": 0.2,
            "use_speaker_boost": True,
            "speed": 0.9,
        },
    )
    with open(out_path, "wb") as f:
        for chunk in audio_iter:
            if chunk:
                f.write(chunk)


@retry(times=2, delay=2.0)
def _openai_tts(text: str, out_path: Path) -> None:
    """Synthesize via OpenAI TTS and write MP3."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    with client.audio.speech.with_streaming_response.create(
        model="tts-1-hd",
        voice=OPENAI_TTS_VOICE,
        input=text,
        speed=0.9,
        response_format="mp3",
    ) as response:
        response.stream_to_file(str(out_path))


def generate_voiceover(script_text: str, script_id: str) -> str:
    """Produce /audio/{script_id}.mp3 and return its path string.

    Tries the configured primary provider, falls through to the other one.
    Raises RuntimeError if both fail.
    """
    out_path = AUDIO_DIR / f"{script_id}.mp3"
    primary, fallback = (
        (_elevenlabs, _openai_tts)
        if TTS_PROVIDER == "elevenlabs"
        else (_openai_tts, _elevenlabs)
    )
    try:
        primary(script_text, out_path)
        log.info("Voiceover saved (primary) -> %s", out_path)
        return str(out_path)
    except Exception as e:
        log.warning("Primary TTS failed: %s — falling back", e)
    try:
        fallback(script_text, out_path)
        log.info("Voiceover saved (fallback) -> %s", out_path)
        return str(out_path)
    except Exception as e:
        log.error("Both TTS providers failed: %s", e)
        raise RuntimeError("voiceover generation failed") from e
