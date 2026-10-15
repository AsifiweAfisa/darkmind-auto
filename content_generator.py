"""
content_generator.py — Writes the day's script(s) with OpenAI, Anthropic,
Gemini or Groq, and makes sure they are NEW.

What keeps content from repeating (see dedupe.py):
  - Theme choice comes from what was actually PUBLISHED: never-published
    themes first, then the one published longest ago. Test runs and runs you
    stopped part-way don't use a theme up.
  - The prompt includes the hooks already published for the theme (and the most
    recent hooks overall) with an instruction not to reuse or reword them.
  - Every candidate script is compared with everything already published. If it
    resembles an earlier post it is rewritten (up to MAX_ATTEMPTS), each retry
    also told what was just rejected. If every attempt resembles something, the
    least similar one is used and a warning is logged — a day is never skipped
    over this.
"""

from typing import List

from config import (
    LLM_PROVIDER, OPENAI_API_KEY, OPENAI_MODEL,
    ANTHROPIC_API_KEY, ANTHROPIC_MODEL,
    GEMINI_API_KEY, GEMINI_MODEL,
    GROQ_API_KEY, GROQ_MODEL,
)
from database import insert_post
from dedupe import pick_themes, published_content, avoid_list, similarity_ratio
from utils import log, new_id, retry, safe_json_loads

MAX_ATTEMPTS = 3


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
    "exact video, referencing its specific claim or twist.\n"
    "6. Every script must be genuinely NEW. When told which hooks have "
    "already been published, choose a different angle, example, claim and "
    "opening line — never repeat or lightly reword them."
)


USER_PROMPT_TEMPLATE = """Generate ONE 30-50 second voiceover script on the theme:
"{theme}"

Constraints:
- script: 75-130 words, smooth spoken cadence, no stage directions. The
  opening sentence is the hook — it must stop a scroller cold (a bold claim,
  an uncomfortable truth, or a direct question), not a slow introduction.
- hook_text: max 8 words. Must be its OWN complete, punchy phrase that
  stands alone grammatically — never a truncated fragment of the script's
  opening sentence. Rephrase and compress the core claim into a short
  standalone line (e.g. if the script opens "Most people die without ever
  touching their true potential", hook_text could be "You're wasting your
  potential" or "Most people never wake up" — a full thought, not a cut-off
  clip).
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
{avoid_block}
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


def _avoid_block(hooks: List[str]) -> str:
    if not hooks:
        return ""
    lines = "\n".join(f"- {h}" for h in hooks)
    return (
        "\nALREADY PUBLISHED — do NOT reuse, repeat or lightly reword any of "
        "these hooks or the angles they take. Find a genuinely different "
        "angle, example, claim and opening line for this theme:\n"
        f"{lines}\n"
    )


def _prompt(theme: str, avoid_block: str) -> str:
    return USER_PROMPT_TEMPLATE.format(theme=theme, avoid_block=avoid_block)


@retry(times=2, delay=3.0)
def _call_openai(theme: str, avoid_block: str = "") -> dict:
    from openai import OpenAI
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0.9,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _prompt(theme, avoid_block)},
        ],
    )
    return safe_json_loads(resp.choices[0].message.content)


@retry(times=2, delay=3.0)
def _call_anthropic(theme: str, avoid_block: str = "") -> dict:
    import anthropic
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY missing")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        temperature=0.9,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _prompt(theme, avoid_block)}],
    )
    text = "".join(b.text for b in msg.content if hasattr(b, "text"))
    return safe_json_loads(text)


@retry(times=2, delay=3.0)
def _call_gemini(theme: str, avoid_block: str = "") -> dict:
    import google.generativeai as genai
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY missing")
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT,
        generation_config={"temperature": 0.9, "response_mime_type": "application/json"},
    )
    resp = model.generate_content(_prompt(theme, avoid_block))
    return safe_json_loads(resp.text)


@retry(times=2, delay=3.0)
def _call_groq(theme: str, avoid_block: str = "") -> dict:
    """Call Groq (free tier, no regional restrictions)."""
    from groq import Groq
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY missing")
    client = Groq(api_key=GROQ_API_KEY)
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        temperature=0.9,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _prompt(theme, avoid_block)},
        ],
    )
    return safe_json_loads(resp.choices[0].message.content)


def _call_llm(theme: str, avoid_block: str) -> dict:
    """Route to the configured provider."""
    if LLM_PROVIDER == "openai":
        return _call_openai(theme, avoid_block)
    if LLM_PROVIDER == "anthropic":
        return _call_anthropic(theme, avoid_block)
    if LLM_PROVIDER == "gemini":
        return _call_gemini(theme, avoid_block)
    return _call_groq(theme, avoid_block)


def _validate(raw: dict) -> dict:
    """Check shape and clean up. Raises ValueError if the output is unusable."""
    required = {"hook_text", "script", "visual_directions",
                "caption", "hashtags", "text_overlays"}
    if not required.issubset(raw.keys()):
        raise ValueError(f"LLM response missing keys: {required - raw.keys()}")
    if len(raw["visual_directions"]) != 5:
        raise ValueError("visual_directions must have exactly 5 items")
    if len(raw["text_overlays"]) != 3:
        raise ValueError("text_overlays must have exactly 3 items")
    word_count = len(raw["script"].split())
    if word_count < 60:
        raise ValueError(f"script too short ({word_count} words) — likely weak hook/pacing")
    if not raw["hook_text"].strip():
        raise ValueError("hook_text is empty")
    if not raw["caption"].strip():
        raise ValueError("caption is empty")
    raw["hashtags"] = [h.lstrip("#").lower() for h in raw["hashtags"]][:5]
    raw["caption"] = raw["caption"][:150]
    raw["hook_text"] = raw["hook_text"][:60]
    return raw


def _generate_one(theme: str, past: List[dict]) -> dict:
    """Write one script for `theme` that doesn't resemble anything in `past`."""
    avoid = avoid_list(theme, past)
    rejected: List[str] = []
    best, best_ratio = None, float("inf")
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = _validate(_call_llm(theme, _avoid_block(avoid + rejected)))
        except ValueError as e:                       # unusable output: try again
            last_error = e
            log.warning("Attempt %d/%d for '%s' was unusable (%s) — retrying",
                        attempt, MAX_ATTEMPTS, theme, e)
            continue

        ratio, why, which = similarity_ratio(raw["hook_text"], raw["script"], past)
        if ratio < 1.0:
            if attempt > 1:
                log.info("Attempt %d/%d for '%s' is original enough — using it",
                         attempt, MAX_ATTEMPTS, theme)
            return raw

        log.warning("Attempt %d/%d for '%s' %s as an earlier post (\"%s\") — rewriting",
                    attempt, MAX_ATTEMPTS, theme, why, which)
        rejected.append(raw["hook_text"])
        if ratio < best_ratio:
            best, best_ratio = raw, ratio

    if best is None:
        raise ValueError(f"no usable script after {MAX_ATTEMPTS} attempts: {last_error}")
    log.warning("All %d attempts for '%s' resembled earlier posts; using the least "
                "similar one (similarity %.2f, 1.00 = reject line)", MAX_ATTEMPTS, theme, best_ratio)
    return best


def generate_daily_scripts(num: int = 1) -> List[dict]:
    """Generate `num` scripts on different themes, persist each, return them."""
    past = published_content()
    out: List[dict] = []
    for theme in pick_themes(num):
        try:
            data = _generate_one(theme, past)
        except Exception as e:
            log.error("Script generation failed for theme '%s': %s", theme, e)
            continue
        post = {"id": new_id(), "theme": theme, **data}
        try:
            insert_post(post)
            log.info("Generated script %s | theme=%s", post["id"], theme)
            out.append(post)
            # later scripts in this same run must not resemble this one either
            past.insert(0, {"theme": theme, "hook_text": data["hook_text"], "script": data["script"]})
        except Exception as e:
            log.error("DB insert failed for %s: %s", post["id"], e)
    return out
