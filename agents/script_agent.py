"""
Script Agent
------------
Generates an original 30-60s "story time" script written entirely by
a local Ollama model — girl's-POV romance/suspense stories (crushes,
betrayal, love triangles, red flags) built for comments/engagement,
each ending with a comment-bait question. No external API dependency
at all; variety comes from randomly combining a genre, occupation,
setting, and twist type on every generation.

Requires Ollama running locally with a model available — there's no
meaningful non-LLM fallback for original fiction, so if Ollama is
unavailable, a very basic templated story is used as a last resort
just to keep the pipeline from hard-failing (quality will be much
lower in that case).

The randomized element combination used for each video is tracked in
data/used_topics.json so the same exact combo doesn't repeat until
the space is exhausted.
"""
import json
import os
import random
import re
import sys

sys.path.append("..")
from status_store import set_status

USED_TOPICS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "used_topics.json")

MIN_TARGET_WORDS = 90   # ~30s of narration
MAX_TARGET_WORDS = 170  # ~60s of narration
FIXED_LINE_WORD_COUNT = 22  # rough budget used by the hook line + comment-bait cta

GENRES = [
    "a secret crush confession story, from a girl's first-person point of view",
    "a story about being betrayed by a best friend, from a girl's first-person point of view",
    "a suspenseful red-flag first date story, from a girl's first-person point of view",
    "a love-triangle dilemma story, from a girl's first-person point of view",
    "a story about finding out a partner was cheating, from a girl's first-person point of view",
    "a story about falling for the wrong person, from a girl's first-person point of view",
    "a story about a text message that changed everything, from a girl's first-person point of view",
    "a story about a crush turning out to be someone unexpected, from a girl's first-person point of view",
    "a story about an ex showing up unannounced, from a girl's first-person point of view",
    "a suspenseful story about a secret she wasn't supposed to find out, from a girl's first-person point of view",
]

OCCUPATIONS = [
    "a college student", "a barista", "a waitress", "an intern",
    "a nursing student", "a hostess", "a retail worker", "a photography student",
]

SETTINGS = [
    "a first date", "a college dorm", "a group chat", "a wedding",
    "a road trip with friends", "a shared apartment", "a party",
    "a coffee shop", "summer camp", "a long-distance relationship",
    "a group project", "a mutual friend's birthday",
]

TWISTS = [
    "a shocking text message discovery", "a best friend's betrayal being revealed",
    "an unexpected confession", "a public breakup", "a surprise reunion",
    "finding out the truth at the worst possible moment",
    "an ex showing up unannounced", "a secret admirer being revealed",
    "catching someone in a lie",
]

ENGAGEMENT_CTAS = [
    "Would you have forgiven him? Let me know in the comments.",
    "Was I wrong here? Tell me in the comments.",
    "What would you have done in my situation? Comment below.",
    "Drop a comment if this has ever happened to you.",
    "Team loyalty or team moving on? Let me know below.",
    "Should I post part two? Comment below if you want it.",
    "Be honest — was that a red flag? Tell me in the comments.",
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


def _pick_unused_combo(max_attempts: int = 30):
    used = _load_used()
    for _ in range(max_attempts):
        combo = {
            "genre": random.choice(GENRES),
            "occupation": random.choice(OCCUPATIONS),
            "setting": random.choice(SETTINGS),
            "twist": random.choice(TWISTS),
        }
        key = json.dumps(combo, sort_keys=True)
        if key not in used:
            used.append(key)
            _save_used(used)
            return combo
    _save_used([])
    combo = {
        "genre": random.choice(GENRES),
        "occupation": random.choice(OCCUPATIONS),
        "setting": random.choice(SETTINGS),
        "twist": random.choice(TWISTS),
    }
    _save_used([json.dumps(combo, sort_keys=True)])
    return combo


_ABBREVIATIONS = ["Mr", "Mrs", "Ms", "Dr", "St", "Jr", "Sr", "Prof"]
_SENTENCE_SPLIT_PATTERN = re.compile(
    r"(?<!\b" + r")(?<!\b".join(_ABBREVIATIONS) + r")(?<=[.!?])\s+"
)


def _sentences(text: str):
    text = re.sub(r"\s+", " ", text).strip()
    return [s.strip() for s in _SENTENCE_SPLIT_PATTERN.split(text) if s.strip()]


def _chunk_into_beats(text: str, target_beats: int = 5):
    sentences = _sentences(text)
    if not sentences:
        return [text]
    per_beat = max(1, len(sentences) // target_beats)
    beats = []
    for i in range(0, len(sentences), per_beat):
        beats.append(" ".join(sentences[i:i + per_beat]))
    return beats


def _generate_story_with_ollama(combo: dict, target_words: int, model: str) -> str:
    import ollama
    prompt = (
        f"Write {combo['genre']}. The main character is {combo['occupation']}. "
        f"The story is set in/at {combo['setting']}. The story should build to "
        f"{combo['twist']}. This is for a YouTube Shorts 'story time' video, "
        f"read aloud by narration, meant to spark comments and reactions. "
        f"Requirements:\n"
        f"- First person, from a girl's point of view, emotionally engaging "
        f"from the very first line\n"
        f"- A COMPLETE story arc (setup, escalation, twist/resolution) even "
        f"though it's short — don't leave it feeling unfinished\n"
        f"- Roughly {target_words} words — keep it tight and punchy, every "
        f"sentence earning its place\n"
        f"- Plain, easy-to-follow spoken language, no jargon\n"
        f"- Leave something relatable or debatable that would make someone "
        f"want to comment\n"
        f"- Return ONLY the story text, no title, no preamble, no quotes"
    )
    result = ollama.generate(model=model, prompt=prompt)
    story = result.get("response", "").strip().strip('"')
    if not story:
        raise RuntimeError("Ollama returned an empty story")
    return story


def _fallback_template_story(combo: dict) -> str:
    return (
        f"I was {combo['occupation']} when it happened, at {combo['setting']}. "
        f"I never saw it coming. Everything changed in an instant because of "
        f"{combo['twist']}. I still don't know how I feel about it."
    )


def _generate_title_with_ollama(story_text: str, model: str) -> str:
    try:
        import ollama
        prompt = (
            "You're writing a YouTube Shorts title for a girl's-POV romance/"
            "suspense 'story time' video. This niche gets clicks from curiosity "
            "gaps, relatable emotional keywords, and POV framing — not from "
            "flat, descriptive summaries.\n\n"
            "Study these proven high-performing title PATTERNS (don't copy "
            "them, just match the style):\n"
            "- \"POV: your best friend betrays you 💔\"\n"
            "- \"He said one thing and I knew it was over\"\n"
            "- \"The text I wasn't supposed to see 😳\"\n"
            "- \"I caught him red-handed and he didn't even lie\"\n"
            "- \"She confessed everything at the worst possible time\"\n"
            "- \"The note that changed everything\"\n\n"
            "Now write ONE title for the story below, in that style:\n"
            "- Under 55 characters (a hashtag block gets appended after, so "
            "leave room)\n"
            "- Use a real emotional/searchable keyword from the story itself "
            "(e.g. betrayed, red flag, cheated, crush, confession, secret) "
            "where it fits naturally — don't force one that isn't true to "
            "the story\n"
            "- A curiosity gap or POV framing works better than a flat summary\n"
            "- At most one well-placed emoji, only if it genuinely fits\n"
            "- The title MUST accurately reflect what actually happens in the "
            "story — never invent a detail, event, or setting that isn't in "
            "the text\n"
            "- No quotes, no 'Story Time:' prefix, just the title itself\n\n"
            "Story:\n" + story_text
        )
        result = ollama.generate(model=model, prompt=prompt)
        title = result.get("response", "").strip().strip('"').strip("'")
        if title and len(title) <= 100:
            return title
    except Exception:
        pass
    first_sentence = _sentences(story_text)[0] if _sentences(story_text) else story_text[:60]
    return first_sentence[:80]


def _build_script(story_text: str, model: str) -> dict:
    beats = _chunk_into_beats(story_text, target_beats=5)
    scenes = [{
        "text": "You won't believe what just happened.",
        "duration": 3.0,
    }]
    for beat in beats:
        scenes.append({
            "text": beat,
            "duration": max(3.0, len(beat.split()) / 2.5),
        })
    scenes.append({
        "text": random.choice(ENGAGEMENT_CTAS),
        "duration": 3.5,
    })
    title = _generate_title_with_ollama(story_text, model)
    return {"title": title, "scenes": scenes}


def generate_script(ollama_model: str = "llama3.2") -> dict:
    set_status("script_agent", "running", "generating story time script")
    try:
        target_words = random.randint(MIN_TARGET_WORDS, MAX_TARGET_WORDS)
        story_word_budget = max(50, target_words - FIXED_LINE_WORD_COUNT)
        combo = _pick_unused_combo()

        try:
            story_text = _generate_story_with_ollama(combo, story_word_budget, ollama_model)
        except ImportError:
            story_text = _fallback_template_story(combo)
        except Exception:
            story_text = _fallback_template_story(combo)

        script = _build_script(story_text, ollama_model)
        set_status("script_agent", "done", f"generated: {script['title']}")
        return script
    except Exception as e:
        set_status("script_agent", "error", str(e))
        raise


if __name__ == "__main__":
    print(json.dumps(generate_script(), indent=2))
