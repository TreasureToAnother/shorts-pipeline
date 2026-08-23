"""
Script Agent
------------
Generates an original 60-120s "story time" script written entirely by
a local Ollama model, in the general style of popular Reddit
storytelling genres (AITA-style dilemmas, TIFU-style mistakes, petty
revenge, malicious compliance, etc.) — with NO dependency on Reddit's
API at all. Variety comes from randomly combining a genre, occupation,
setting, and twist type on every generation, which is what keeps
stories fresh without needing any external live data source.

Requires Ollama running locally with a model available — there's no
meaningful non-LLM fallback for original fiction, so if Ollama is
unavailable, a very basic templated story is used as a last resort
just to keep the pipeline from hard-failing (quality will be much
lower in that case).

The randomized element combination used for each video is tracked in
data/used_topics.json so the same exact combo doesn't repeat until
the space is exhausted (which, given the combinatorics here, is a
very large number of unique combinations).
"""
import json
import os
import random
import re
import sys

sys.path.append("..")
from status_store import set_status

USED_TOPICS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "used_topics.json")
SFX_POOL = ["whoosh", "impact", "ding", "dramatic_sting", "swipe"]

MIN_TARGET_WORDS = 170
MAX_TARGET_WORDS = 280

GENRES = [
    "a moral dilemma story where the narrator isn't sure if they were wrong",
    "a hilarious mistake that spiraled completely out of control",
    "a petty revenge story",
    "a malicious compliance story, following instructions exactly to backfire on someone",
    "a story about an entitled person getting an unexpected reality check",
    "a family secret confession story",
    "a roommate or neighbor conflict story",
    "a workplace drama story",
]

OCCUPATIONS = [
    "a barista", "a flight attendant", "a teacher", "a software engineer",
    "a nurse", "a delivery driver", "a retail manager", "a college student",
    "a wedding photographer", "a landlord", "a waiter", "a mechanic",
]

SETTINGS = [
    "a small town diner", "a cross-country flight", "a family Thanksgiving dinner",
    "a cramped apartment building", "a corporate office", "a summer road trip",
    "a wedding reception", "a college dorm", "an Airbnb rental",
    "a neighborhood block party", "a grocery store", "a family reunion",
]

TWISTS = [
    "an unexpected betrayal", "a secret that had been hidden for years",
    "a hilarious misunderstanding", "instant karma", "a surprising act of kindness",
    "a shocking coincidence", "a plan that backfires spectacularly",
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
    """Randomly combines genre/occupation/setting/twist. Given the size
    of the combinatorial space (thousands of combos), a random retry
    loop is simpler and just as effective as pre-enumerating every
    possibility."""
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
    # extremely unlikely fallback: space nearly exhausted, reset and retry once
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
    """Splits into sentences, being careful not to break on abbreviations
    like 'Mrs.' or 'Dr.' — a naive split on every '. ' incorrectly cut
    sentences in half mid-name (e.g. 'Mrs. Johnson' became two separate
    sentences)."""
    text = re.sub(r"\s+", " ", text).strip()
    return [s.strip() for s in _SENTENCE_SPLIT_PATTERN.split(text) if s.strip()]


def _chunk_into_beats(text: str, target_beats: int = 7):
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
        f"read aloud by narration. Requirements:\n"
        f"- First person, dramatic, engaging from the very first line\n"
        f"- Clear beginning, escalation, and a satisfying resolution\n"
        f"- Roughly {target_words} words\n"
        f"- Plain, easy-to-follow spoken language, no jargon\n"
        f"- Return ONLY the story text, no title, no preamble, no quotes"
    )
    result = ollama.generate(model=model, prompt=prompt)
    story = result.get("response", "").strip().strip('"')
    if not story:
        raise RuntimeError("Ollama returned an empty story")
    return story


def _fallback_template_story(combo: dict) -> str:
    """Used only if Ollama itself is unavailable — much lower quality,
    but keeps the pipeline from hard-failing."""
    return (
        f"I work as {combo['occupation']}, and something happened at "
        f"{combo['setting']} that I still can't believe. It started as a normal "
        f"day, but things escalated fast. By the end, it all came down to "
        f"{combo['twist']}, and nobody saw it coming. This is one of those "
        f"stories you have to hear to believe."
    )


def _generate_title_with_ollama(story_text: str, model: str) -> str:
    try:
        import ollama
        prompt = (
            "Write a short, punchy YouTube Shorts title (under 60 characters) "
            "for this exact story below. The title must accurately reflect "
            "what actually happens in the story — do not invent or reference "
            "any detail that isn't in the text. No quotes, no 'Story Time:' "
            "prefix, just the title itself:\n\n" + story_text
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
    beats = _chunk_into_beats(story_text, target_beats=7)
    scenes = []
    for beat in beats:
        scenes.append({
            "text": beat,
            "duration": max(3.0, len(beat.split()) / 2.5),
            "sfx": random.choice(SFX_POOL),
        })
    scenes.append({
        "text": "Follow for more stories like this one.",
        "duration": 3.0,
        "sfx": "ding",
    })
    title = _generate_title_with_ollama(story_text, model)
    return {"title": title, "scenes": scenes}


def generate_script(ollama_model: str = "llama3.2") -> dict:
    set_status("script_agent", "running", "generating story time script")
    try:
        target_words = random.randint(MIN_TARGET_WORDS, MAX_TARGET_WORDS)
        combo = _pick_unused_combo()

        try:
            story_text = _generate_story_with_ollama(combo, target_words, ollama_model)
        except ImportError:
            story_text = _fallback_template_story(combo)
        except Exception:
            story_text = _fallback_template_story(combo)

        script = _build_script(story_text, ollama_model)
        for s in script["scenes"]:
            s.setdefault("sfx", random.choice(SFX_POOL))
        set_status("script_agent", "done", f"generated: {script['title']}")
        return script
    except Exception as e:
        set_status("script_agent", "error", str(e))
        raise


if __name__ == "__main__":
    print(json.dumps(generate_script(), indent=2))