"""
Long-Form Script Agent
-----------------------
Generates a 60-110 minute self-improvement/motivation script, written
entirely by a local Ollama model — the "motivational speech over
Minecraft parkour" genre. Unlike the short-form script_agent.py (one
tight story, one Ollama call), this builds the script as an outline
of chapters under a single theme, then generates each chapter with
its own Ollama call — small local models lose coherence over very
long single completions, and per-chapter calls also make this
resumable/inspectable.

No external API dependency. Variety comes from randomly picking a
theme; the theme is tracked in data/longform_used_topics.json (local
only, gitignored — this pipeline never runs in CI) so the same theme
doesn't repeat until the pool is exhausted.
"""
import json
import os
import random
import re

USED_TOPICS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "longform_used_topics.json")

WORDS_PER_MINUTE = 150   # normal (non-sped-up) narration pace for this genre
WORDS_PER_CHAPTER = 650  # target length per chapter, before the intro/outro
MIN_TARGET_MINUTES = 60
MAX_TARGET_MINUTES = 100

THEMES = [
    "building unbreakable discipline",
    "why motivation always fades and what actually works instead",
    "overcoming procrastination for good",
    "developing genuine, unshakeable self-confidence",
    "the power of extreme consistency",
    "letting go of your past mistakes and regrets",
    "handling failure and rejection without breaking",
    "building real mental toughness",
    "escaping comparison and jealousy",
    "finding your purpose when you feel lost",
    "mastering your mindset instead of being ruled by it",
    "the art of patience and long-term thinking",
    "becoming disciplined with how you spend your time",
    "silencing self-doubt for good",
    "embracing hard work when no one is watching",
    "breaking bad habits that are holding you back",
    "getting back up after you fall",
    "why comfort is quietly destroying your potential",
    "training yourself to follow through on what you start",
    "becoming the person your future depends on",
]


def _load_used():
    if not os.path.exists(USED_TOPICS_PATH):
        return []
    try:
        with open(USED_TOPICS_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return []


def _save_used(used):
    os.makedirs(os.path.dirname(USED_TOPICS_PATH), exist_ok=True)
    with open(USED_TOPICS_PATH, "w") as f:
        json.dump(used, f, indent=2)


def _pick_theme():
    used = _load_used()
    remaining = [t for t in THEMES if t not in used]
    if not remaining:
        used = []
        remaining = THEMES
    theme = random.choice(remaining)
    used.append(theme)
    _save_used(used)
    return theme


def _generate_outline(theme: str, chapter_count: int, model: str) -> list:
    import ollama
    prompt = (
        f"You're outlining a long-form motivational YouTube video on the "
        f"theme: \"{theme}\".\n\n"
        f"List exactly {chapter_count} distinct chapter topics that build "
        f"on each other and together fully explore this theme — each one a "
        f"specific angle, lesson, or turning point, not a vague restatement "
        f"of the theme. Order them so the video builds naturally from start "
        f"to finish.\n\n"
        f"Return ONLY a numbered list, one line per chapter, no other text:\n"
        f"1. ...\n2. ...\n"
    )
    result = ollama.generate(model=model, prompt=prompt)
    lines = result.get("response", "").strip().splitlines()
    chapters = []
    for line in lines:
        cleaned = re.sub(r"^\s*\d+[\.\):]\s*", "", line).strip()
        cleaned = cleaned.strip('"\'')
        if cleaned:
            chapters.append(cleaned)
    return chapters[:chapter_count] if chapters else [theme] * chapter_count


def _generate_chapter(theme: str, chapter_topic: str, chapter_num: int,
                       total_chapters: int, target_words: int, model: str) -> str:
    import ollama
    position = (
        "This is the OPENING chapter — hook the listener immediately and "
        "set up why this theme matters." if chapter_num == 1 else
        "This is the CLOSING chapter — bring things to a resolute, "
        "motivating close, tying back to the overall theme." if chapter_num == total_chapters else
        f"This is chapter {chapter_num} of {total_chapters} — it should flow "
        f"naturally from the chapter before it."
    )
    prompt = (
        f"Write one chapter of a long-form motivational speech video. "
        f"Overall theme: \"{theme}\". This chapter's specific focus: "
        f"\"{chapter_topic}\".\n\n"
        f"{position}\n\n"
        f"Requirements:\n"
        f"- Second person direct address (\"you\"), spoken monologue style, "
        f"meant to be read aloud — no headers, no bullet points, no markdown\n"
        f"- Roughly {target_words} words\n"
        f"- Direct, punchy, emotionally resonant sentences; short sentences "
        f"and repetition for emphasis are welcome, this is a speech not an essay\n"
        f"- No preamble, no chapter title, no quotes — return ONLY the "
        f"spoken text itself"
    )
    result = ollama.generate(model=model, prompt=prompt)
    text = result.get("response", "").strip().strip('"')
    if not text:
        raise RuntimeError(f"Ollama returned an empty chapter for '{chapter_topic}'")
    return text


def _generate_title_with_ollama(theme: str, model: str) -> str:
    try:
        import ollama
        prompt = (
            f"Write ONE YouTube title for a 60-90 minute motivational "
            f"speech video on the theme: \"{theme}\".\n\n"
            f"Style: direct, second-person, the kind of title that works "
            f"for long-form motivational content over Minecraft parkour "
            f"background footage (e.g. \"Discipline Will Change Your Life "
            f"— Motivational Speech\", \"Stop Waiting For Motivation\").\n"
            f"Under 90 characters, no quotes, no emoji, return ONLY the title."
        )
        result = ollama.generate(model=model, prompt=prompt)
        title = result.get("response", "").strip().strip('"').strip("'")
        if title and len(title) <= 100:
            return title
    except Exception:
        pass
    return theme[:1].upper() + theme[1:]


def generate_longform_script(ollama_model: str = "llama3.2", target_minutes: int = None) -> dict:
    target_minutes = target_minutes or random.randint(MIN_TARGET_MINUTES, MAX_TARGET_MINUTES)
    target_words = target_minutes * WORDS_PER_MINUTE
    chapter_count = max(4, round(target_words / WORDS_PER_CHAPTER))

    theme = _pick_theme()
    outline = _generate_outline(theme, chapter_count, ollama_model)
    words_per_chapter = max(150, target_words // len(outline))

    chapters = []
    for i, topic in enumerate(outline, start=1):
        text = _generate_chapter(theme, topic, i, len(outline), words_per_chapter, ollama_model)
        chapters.append({"topic": topic, "text": text})

    title = _generate_title_with_ollama(theme, ollama_model)
    return {"title": title, "theme": theme, "chapters": chapters}


if __name__ == "__main__":
    script = generate_longform_script(target_minutes=8)  # short run for a quick local test
    print(json.dumps(script, indent=2))
    total_words = sum(len(c["text"].split()) for c in script["chapters"])
    print(f"\n{len(script['chapters'])} chapters, ~{total_words} words, "
          f"~{total_words / WORDS_PER_MINUTE:.1f} min narration")
