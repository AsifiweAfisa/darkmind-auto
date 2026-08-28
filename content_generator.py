"""
content_generator.py — Generates scripts from the locked theme rotation,
using OpenAI, Anthropic, Gemini, or Groq.
"""

import json
from typing import List

from config import (
    LLM_PROVIDER, OPENAI_API_KEY, OPENAI_MODEL,
    ANTHROPIC_API_KEY, ANTHROPIC_MODEL,
    GEMINI_API_KEY, GEMINI_MODEL,
    GROQ_API_KEY, GROQ_MODEL, THEMES,
)
from database import (
    insert_post, get_rotation_index, set_rotation_index,
)
from utils import log, new_id, retry, safe_json_loads


SYSTEM_PROMPT = (
    "You are a script writer for short-form faceless videos in the niche of "
    "Human Psychology and Dark Truths. Tone: calm, intellectual, dark, "
    "mysterious, slightly cinematic. "
    "CRITICAL RULES:\n"
    "1. Output strict JSON only, no prose, no markdown fences.\n"
    "2. Never use markdown formatting (no asterisks, no underscores, no bold, "
    "no italics) anywhere in any field — all text must be plain.\n"
    "3. The 'script' field MUST be between 75 and 130 words — count "
    "carefully and do not undershoot this.\n"
    "4. RETENTION IS THE TOP PRIORITY. The first sentence of 'script' must "
    "be a scroll-stopping hook that creates an open loop, a provocative "
    "claim, or a question the viewer needs answered — written specifically "
    "so a viewer who hears only the first 3 seconds feels compelled to keep "
    "watching instead of skipping. Never open with a generic statement, a "
    "greeting, or a slow windup.\n"
    "5. 'hook_text', 'caption', and 'text_overlays' must all match the exact "
    "emotional energy and specific subject matter of THIS script — never "
    "generic, interchangeable, or reusable across different scripts. They "
    "should read like they were written by someone who just watched this "
    "exact video, referencing its specific claim or twist."
)


USER_PROMPT_TEMPLATE = """Generate ONE 30-50 second voiceover script on the theme:
"{theme}"

Constraints:
- script: 75-130 words, smooth spoken cadence, no stage directions. The
  opening sentence is the hook — it must stop a scroller cold (a bold claim,
  an uncomfortable truth, or a direct question), not a slow introduction.
- hook_text: max 8 words, on-screen text for the opening 2 seconds. Must
  echo the specific hook/claim of THIS script's opening line, not a generic
  niche tagline.
- visual_directions: exactly 5 short scene descriptions (each suitable as a
  stock-footage search query — concrete nouns, 3-6 words each, e.g.
  "rain on dark window", "candle in empty room")
- caption: max 150 chars, curiosity gap tied specifically to this script's
  claim, NO hashtags inside it
- hashtags: exactly 5, lowercase, no '#' prefix, relevant to both the niche
  AND the specific subject of this script
- text_overlays: exactly 3 entries with {{time_seconds, text}} where text is
  4-7 words, each pulled from or paraphrasing a specific line in the script
  (not generic filler); times must be ascending and within 0-50

Return ONLY this JSON (no fences):
{{
  "hook_text": "...",
  "script": "...",
  "visual_directions": ["...","...","...","...","..."],
  "caption": "...",
  "hashtags": ["...","...","...","...","..."],
  "text_overlays": [
    {{"time_seconds": 2, "text": "..."}},
    {{"time_seconds": 15, "text": "..."}},
    {{"time_seconds": 35, "text": "..."}}
  ]
}}"""


@retry(times=2, delay=3.0)
def _call_openai(theme: str) -> dict:
    from openai import OpenAI
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.85,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(theme=theme)},
        ],
    )
    return safe_json_loads(resp.choices[0].message.content)


@retry(times=2, delay=3.0)
def _call_anthropic(theme: str) -> dict:
    import anthropic
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY missing")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        temperature=0.85,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(theme=theme)},
        ],
    )
    text = "".join(b.text for b in msg.content if hasattr(b, "text"))
    return safe_json_loads(text)


@retry(times=2, delay=3.0)
def _call_gemini(theme: str) -> dict:
    import google.generativeai as genai
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY missing")
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT,
        generation_config={
            "temperature": 0.85,
            "response_mime_type": "application/json",
        },
    )
    resp = model.generate_content(USER_PROMPT_TEMPLATE.format(theme=theme))
    return safe_json_loads(resp.text)


@retry(times=2, delay=3.0)
def _call_groq(theme: str) -> dict:
    """Call Groq (free tier, no regional restrictions)."""
    from groq import Groq
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY missing")
    client = Groq(api_key=GROQ_API_KEY)
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.85,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(theme=theme)},
        ],
    )
    return safe_json_loads(resp.choices[0].message.content)


def _generate_one(theme: str) -> dict:
    if LLM_PROVIDER == "openai":
        raw = _call_openai(theme)
    elif LLM_PROVIDER == "anthropic":
        raw = _call_anthropic(theme)
    elif LLM_PROVIDER == "gemini":
        raw = _call_gemini(theme)
    else:
        raw = _call_groq(theme)
    required = {
        "hook_text", "script", "visual_directions",
        "caption", "hashtags", "text_overlays",
    }
    if not required.issubset(raw.keys()):
        raise ValueError(f"LLM response missing keys: {required - raw.keys()}")
    if len(raw["visual_directions"]) != 5:
        raise ValueError("visual_directions must have exactly 5 items")
    if len(raw["text_overlays"]) != 3:
        raise ValueError("text_overlays must have exactly 3 items")
    word_count = len(raw["script"].split())
    if word_count < 60:
        raise ValueError(f"script too short ({word_count} words) — likely weak hook/pacing")
    raw["hashtags"] = [h.lstrip("#").lower() for h in raw["hashtags"]][:5]
    raw["caption"] = raw["caption"][:150]
    raw["hook_text"] = raw["hook_text"][:60]
    return raw


def _next_themes(n: int) -> List[str]:
    idx = get_rotation_index()
    out = []
    for i in range(n):
        out.append(THEMES[(idx + i) % len(THEMES)])
    set_rotation_index((idx + n) % len(THEMES))
    return out


def generate_daily_scripts(num: int = 1) -> List[dict]:
    """Generate `num` scripts, persist each, and return them."""
    themes = _next_themes(num)
    out: List[dict] = []
    for theme in themes:
        try:
            data = _generate_one(theme)
        except Exception as e:
            log.error("Script generation failed for theme '%s': %s", theme, e)
            continue
        post = {
            "id": new_id(),
            "theme": theme,
            **data,
        }
        try:
            insert_post(post)
            log.info("Generated script %s | theme=%s", post["id"], theme)
            out.append(post)
        except Exception as e:
            log.error("DB insert failed for %s: %s", post["id"], e)
    return out