"""
Script Agent
------------
Produces a 60-120s script about the surprising origin story of a
genuinely popular, eye-catching topic (dinosaurs, viral foods, iconic
tech/brands, everyday objects with a good story) as structured JSON:

{
  "title": "...",
  "scenes": [
    {"text": "...", "duration": 4.5, "visual_query": "vintage zipper factory", "sfx": "whoosh"},
    ...
  ]
}

Fully free: pulls the full Wikipedia intro section for the topic (no
key needed) and builds several fact-based beats from real sentences in
it. Only the connective hook/transition/cta lines are optionally
polished by a local Ollama model for variety — factual beats are never
rewritten by the LLM, and a guardrail rejects any rewrite that appears
to introduce a new, unsourced claim.

Topics are tracked in data/used_topics.json so the same subject never
repeats until the full list has been used once.
"""
import json
import os
import random
import re
import sys

import requests

sys.path.append("..")
from status_store import set_status

WIKI_EXTRACT_API = "https://en.wikipedia.org/w/api.php"
SFX_POOL = ["whoosh", "impact", "ding", "dramatic_sting", "swipe"]

USED_TOPICS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "used_topics.json")

MIN_TARGET_WORDS = 170
MAX_TARGET_WORDS = 280
FIXED_LINE_WORD_COUNT = 45  # rough budget used by hook/transition/cliff/cta

# Curated list of genuinely popular, high-interest topics — the kind
# people actually stop scrolling for — spanning dinosaurs, viral foods,
# iconic tech/brands, and the best (most interesting) everyday objects.
# Titles match real Wikipedia article names.
ITEMS = [
    # Dinosaurs / prehistoric (huge built-in interest)
    "Tyrannosaurus", "Velociraptor", "Triceratops", "Stegosaurus",
    "Brachiosaurus", "Spinosaurus", "Ankylosaurus", "Pterosaur",
    "Woolly mammoth", "Megalodon", "Sabertooth", "Archaeopteryx",

    # Viral / iconic foods
    "Pizza", "Sushi", "Hamburger", "Ramen", "Taco", "French fries",
    "Ice cream", "Chocolate", "Coffee", "Bubble tea", "Oreo",
    "Coca-Cola", "Ketchup", "Sliced bread", "Popsicle", "Chewing gum",
    "Potato chip", "Champagne",

    # Tech & iconic brands
    "IPhone", "Nintendo Entertainment System", "Bitcoin", "Internet",
    "Wi-Fi", "Video game", "YouTube", "Television", "LEGO", "Barbie",
    "Pokémon", "McDonald's", "Rubik's Cube",

    # Best of everyday objects (kept only the genuinely interesting ones)
    "Zipper", "Velcro", "Blue jeans", "Bubble wrap", "Post-it note",
    "Superglue", "WD-40", "Slinky", "Frisbee",
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


def _pick_unused_item():
    """Never repeats a topic until every topic in the list has been
    used once, at which point the tracking list resets and the cycle
    starts over."""
    used = _load_used()
    available = [i for i in ITEMS if i not in used]
    if not available:
        used = []
        available = ITEMS[:]
    item = random.choice(available)
    used.append(item)
    _save_used(used)
    return item


def _fetch_item_origin():
    item = _pick_unused_item()
    params = {
        "action": "query",
        "prop": "extracts",
        "exintro": True,
        "explaintext": True,
        "format": "json",
        "titles": item,
    }
    resp = requests.get(WIKI_EXTRACT_API, params=params, timeout=15,
                         headers={"User-Agent": "faceless-origins-bot/1.0"})
    resp.raise_for_status()
    pages = resp.json().get("query", {}).get("pages", {})
    page = next(iter(pages.values()), {})
    extract = page.get("extract", "")
    if not extract:
        raise RuntimeError(f"No extract found for {item}")
    return {"item": item, "extract": extract}


def _sentences(text: str):
    text = re.sub(r"\s+", " ", text).strip()
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _build_fact_beats(extract: str, word_budget: int):
    """
    Groups real Wikipedia sentences into a handful of fact beats (1-2
    sentences each) until the running word count hits the budget. Stops
    BEFORE adding a sentence that would blow way past the budget
    (unless no fact beat exists yet).
    """
    sentences = _sentences(extract)
    beats = []
    current = []
    current_words = 0
    total_words = 0

    for sentence in sentences:
        sentence_words = len(sentence.split())
        would_exceed = total_words + sentence_words > word_budget
        have_content = beats or current
        if would_exceed and have_content:
            break
        current.append(sentence)
        current_words += sentence_words
        total_words += sentence_words
        if current_words >= 20 or len(current) >= 2:
            beats.append(" ".join(current))
            current, current_words = [], 0
        if total_words >= word_budget:
            break

    if current:
        beats.append(" ".join(current))

    if not beats:
        beats = [extract[:200]]
    return beats


def _template_script(fact: dict) -> dict:
    item = fact["item"]
    extract = fact["extract"]

    target_words = random.randint(MIN_TARGET_WORDS, MAX_TARGET_WORDS)
    fact_word_budget = target_words - FIXED_LINE_WORD_COUNT

    hook = f"You know {item.lower()}. You have no idea where it actually came from."
    fact_beats = _build_fact_beats(extract, fact_word_budget)
    transition = f"Here's what almost nobody knows about {item.lower()}."
    cliff = "And the part right before this is where it gets really strange."
    cta = "Follow for the hidden origin story of something you already know."

    visual_pool = [
        f"{item} closeup", f"{item} vintage history", f"{item} old photo",
        f"{item} historical", f"{item} origin", f"{item} early history",
    ]

    scenes = [{"text": hook, "duration": 4.0, "visual_query": visual_pool[0], "sfx": "dramatic_sting"}]
    for i, beat in enumerate(fact_beats):
        scenes.append({
            "text": beat,
            "duration": max(3.0, len(beat.split()) / 2.5),
            "visual_query": visual_pool[(i + 1) % len(visual_pool)],
            "sfx": random.choice(SFX_POOL),
        })
    scenes.insert(1, {"text": transition, "duration": 3.0, "visual_query": f"{item} mystery", "sfx": "whoosh"})
    scenes.append({"text": cliff, "duration": 3.0, "visual_query": "mystery vintage invention", "sfx": "swipe"})
    scenes.append({"text": cta, "duration": 3.0, "visual_query": f"{item} today", "sfx": "ding"})

    return {"title": f"The Real Origin of {item}", "scenes": scenes}


def _introduces_new_claim(original: str, rewritten: str, item: str) -> bool:
    """Rough guardrail against LLM hallucination: flags a rewrite if it
    contains a capitalized word/phrase (a likely proper noun) that
    wasn't in the original line."""
    def proper_nouns(text):
        words = text.split()
        found = set()
        for i, w in enumerate(words):
            clean = w.strip(".,!?\"'").strip()
            if i > 0 and clean and clean[0].isupper() and clean.lower() != item.lower():
                found.add(clean.lower())
        return found

    new_nouns = proper_nouns(rewritten) - proper_nouns(original)
    return len(new_nouns) > 0


def _polish_connective_lines(script: dict, model: str) -> dict:
    """Rewrites ONLY the non-factual connective lines (hook, transition,
    cliffhanger, cta) for variety/punch. Fact beats are never touched."""
    try:
        import ollama
    except ImportError:
        return script

    item = script["title"].replace("The Real Origin of ", "")
    connective_indices = [0, 1, len(script["scenes"]) - 2, len(script["scenes"]) - 1]
    try:
        for idx in connective_indices:
            original = script["scenes"][idx]["text"]
            prompt = (
                "Rewrite this single line to be punchier and more retention-optimized "
                "for a YouTube Short. Do not add any names, places, dates, or facts "
                "not already in the line. Same meaning, similar length, no quotes. "
                "Return ONLY the rewritten line:\n\n" + original
            )
            result = ollama.generate(model=model, prompt=prompt)
            rewritten = result.get("response", "").strip().strip('"')
            if not rewritten or len(rewritten) >= len(original) * 2:
                continue
            if _introduces_new_claim(original, rewritten, item):
                continue
            script["scenes"][idx]["text"] = rewritten
    except Exception:
        pass
    return script


def generate_script(ollama_model: str = "llama3.2") -> dict:
    set_status("script_agent", "running", "fetching topic origin story")
    try:
        fact = _fetch_item_origin()
        script = _template_script(fact)
        script = _polish_connective_lines(script, ollama_model)
        for s in script["scenes"]:
            s.setdefault("sfx", random.choice(SFX_POOL))
        set_status("script_agent", "done", f"generated: {script['title']}")
        return script
    except Exception as e:
        set_status("script_agent", "error", str(e))
        raise


if __name__ == "__main__":
    print(json.dumps(generate_script(), indent=2))