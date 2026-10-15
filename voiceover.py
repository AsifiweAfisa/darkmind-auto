"""
voiceover.py — Generates the narration MP3 for a script.

Providers, tried in this order (the configured TTS_PROVIDER first):
  1. ElevenLabs   — best quality; the free plan has a monthly character limit
  2. edge         — free Microsoft Edge voices via the `edge-tts` package
                    (unofficial endpoint: no key or limit, but it could change
                    without notice, so it is a fallback, not the main voice)
  3. OpenAI TTS   — only if OPENAI_API_KEY is set
A provider that isn't set up (no key / package not installed) is skipped.

Two kinds of failure are handled differently, because they need different cures:

  NETWORK errors (DNS failure, dropped connection, timeout):
      usually gone within a minute, so the SAME provider is retried several
      times with growing waits (NETWORK_BACKOFF) before giving up on it.
      The old behaviour — 2 tries, 2 seconds apart — meant a brief blip
      cost you the whole day's post.

  Everything else (quota used up, bad key, rejected request):
      retrying won't help, so the next provider is tried immediately.

If every provider fails, the error lists exactly why each one failed.
"""

import asyncio
import os
import time
from pathlib import Path
from typing import Callable, List, Tuple

from config import (
    AUDIO_DIR, TTS_PROVIDER,
    ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, ELEVENLABS_MODEL,
    OPENAI_API_KEY, OPENAI_TTS_VOICE,
)
from utils import log

# Seconds to wait before each retry of a network failure (5 tries in total,
# about 2.5 minutes of patience).
NETWORK_BACKOFF = (5, 15, 45, 90)

# Deep, calm, authoritative male voice. Any `edge-tts --list-voices` name works.
EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "en-US-ChristopherNeural")
EDGE_TTS_RATE = os.getenv("EDGE_TTS_RATE", "-10%")          # ~0.9x, reflective pacing


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

_NETWORK_CLASS_HINTS = (
    "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
    "RemoteProtocolError", "ReadError", "ConnectionError", "ConnectionReset",
    "ConnectionResetError", "ConnectionAborted", "TimeoutError", "Timeout",
    "ClientConnectorError", "ServerDisconnectedError", "ClientOSError",
    "gaierror", "WSServerHandshakeError",
)
_NETWORK_TEXT_HINTS = (
    "getaddrinfo failed", "name or service not known", "temporary failure in name resolution",
    "server disconnected", "connection reset", "connection aborted", "timed out",
    "network is unreachable", "cannot connect to host", "connection refused",
    "remote end closed", "unable to connect",
)


def is_network_error(exc: BaseException) -> bool:
    """True for failures that are about the connection, not about the request."""
    seen = set()
    while exc is not None and id(exc) not in seen:          # look through chained causes too
        seen.add(id(exc))
        if any(c.__name__ in _NETWORK_CLASS_HINTS for c in type(exc).__mro__):
            return True
        if any(h in str(exc).lower() for h in _NETWORK_TEXT_HINTS):
            return True
        if getattr(exc, "errno", None) in (11001, 11002, 11003, 11004, -2, -3, -5):   # DNS errors
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _describe(exc: BaseException) -> str:
    status = getattr(exc, "status_code", None)
    text = str(exc)
    if status in (401, 402, 429) and ("quota" in text.lower() or "limit" in text.lower()):
        return f"quota exhausted (HTTP {status})"
    return f"{type(exc).__name__}: {text[:200]}"


# ---------------------------------------------------------------------------
# Providers — each writes an MP3 to out_path or raises
# ---------------------------------------------------------------------------

def _elevenlabs(text: str, out_path: Path) -> None:
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY not set")
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


def _edge(text: str, out_path: Path) -> None:
    try:
        import edge_tts
    except ImportError:
        raise RuntimeError("edge-tts not installed (pip install edge-tts)") from None

    async def _go() -> None:
        await edge_tts.Communicate(text, EDGE_TTS_VOICE, rate=EDGE_TTS_RATE).save(str(out_path))
    asyncio.run(_go())


def _openai(text: str, out_path: Path) -> None:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    with client.audio.speech.with_streaming_response.create(
        model="tts-1-hd", voice=OPENAI_TTS_VOICE, input=text,
        speed=0.9, response_format="mp3",
    ) as response:
        response.stream_to_file(str(out_path))


_PROVIDERS = {"elevenlabs": _elevenlabs, "edge": _edge, "openai": _openai}


def _provider_order() -> List[str]:
    first = "edge" if TTS_PROVIDER in ("edge", "edge-tts") else TTS_PROVIDER
    order = [first] if first in _PROVIDERS else []
    order += [p for p in ("elevenlabs", "edge", "openai") if p not in order]
    return order


# ---------------------------------------------------------------------------
# Running one provider patiently
# ---------------------------------------------------------------------------

def _run_provider(name: str, fn: Callable[[str, Path], None], text: str, out_path: Path) -> None:
    """Call a provider; retry only network failures, with growing waits."""
    attempts = len(NETWORK_BACKOFF) + 1
    for attempt in range(1, attempts + 1):
        out_path.unlink(missing_ok=True)
        try:
            fn(text, out_path)
            if not out_path.exists() or out_path.stat().st_size < 1000:
                raise RuntimeError("provider returned an empty/invalid audio file")
            return
        except Exception as e:
            if is_network_error(e) and attempt < attempts:
                wait = NETWORK_BACKOFF[attempt - 1]
                log.warning("%s: network problem (%s) — retry %d/%d in %ds",
                            name, _describe(e), attempt, attempts - 1, wait)
                time.sleep(wait)
                continue
            raise


def generate_voiceover(script_text: str, script_id: str) -> str:
    """Produce /audio/{script_id}.mp3 and return its path string.

    Raises RuntimeError (listing every provider's reason) if none succeed."""
    out_path = AUDIO_DIR / f"{script_id}.mp3"
    failures: List[Tuple[str, str]] = []

    for name in _provider_order():
        try:
            _run_provider(name, _PROVIDERS[name], script_text, out_path)
            log.info("Voiceover saved (%s) -> %s", name, out_path)
            return str(out_path)
        except Exception as e:
            reason = _describe(e)
            failures.append((name, reason))
            log.warning("TTS provider '%s' failed: %s", name, reason)

    summary = "; ".join(f"{n}: {r}" for n, r in failures)
    log.error("All TTS providers failed — %s", summary)
    raise RuntimeError(f"voiceover generation failed — {summary}")
