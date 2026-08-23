"""
Script Agent
------------
Produces a short-form (40-60s) script about the surprising origin story
of an everyday retail item (food, clothing, material, or household
supply) as structured JSON:

{
  "title": "...",
  "scenes": [
    {"text": "...", "duration": 4.5, "visual_query": "vintage zipper factory", "sfx": "whoosh"},
    ...
  ]
}

Fully free: pulls the real intro summary of a Wikipedia article about
the item (no key needed), then reshapes it into a punchy hook/beats/
cliffhanger structure. If a local Ollama server is running, it's used
to punch up the wording; otherwise a template-based fallback runs so
the pipeline never blocks on a missing local model.
"""
import json
import random
import re
import sys
import textwrap

import requests

sys.path.append("..")
from status_store import set_status

WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
SFX_POOL = ["whoosh", "impact", "ding", "dramatic_sting", "swipe"]

# Curated list of everyday items with a genuinely interesting origin
# story, spanning food, clothing, materials, and household supplies.
# Titles match real Wikipedia article names.
ITEMS = [
    # Food
    "Ketchup", "Sliced bread", "Popsicle", "Chewing gum", "Potato chip",
    "Corn flakes", "Ice cream cone", "Coca-Cola", "Chocolate chip cookie",
    "Sandwich", "Pretzel", "Doughnut", "Champagne", "Margarine",
    # Clothing / textiles
    "Zipper", "Velcro", "Denim", "Blue jeans", "Bra", "Necktie",
    "High-heeled footwear", "Raincoat", "Nylon", "Safety pin",
    "Button (clothing)", "T-shirt",
    # Materials
    "Bubble wrap", "Plastic", "Rubber band", "Post-it note",
    "Superglue", "WD-40", "Styrofoam", "Aluminium foil", "Cellophane",
    "Concrete", "Glass", "Porcelain",
    # Household / supplies
    "Toothbrush", "Vacuum cleaner", "Ballpoint pen", "Paper clip",
    "Matches", "Umbrella", "Toilet paper", "Sewing machine",
    "Refrigerator", "Lightbulb", "Frisbee", "Slinky", "Rubik's Cube",
]


def _fetch_item_origin():
    item = random.choice(ITEMS)
    url = WIKI_SUMMARY.format(title=item.replace(" ", "_"))
    resp = requests.get(url, timeout=15, headers={"User-Agent": "faceless-origins-bot/1.0"})
    resp.raise_for_status()
    data = resp.json()
    extract = data.get("extract", "")
    if not extract:
        raise RuntimeError(f"No summary found for {item}")
    return {"item": item, "extract": extract}


def _first_sentences(text: str, max_chars: int) -> str:
    """
    Grabs whole sentences up to a length budget instead of chopping text
    off mid-sentence at a fixed character count (which is what produced
    the broken-grammar "..." truncations before). Always returns at
    least one complete sentence, even if it runs a bit over budget.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    result = ""
    for sentence in sentences:
        if result and len(result) + len(sentence) > max_chars:
            break
        result = (result + " " + sentence).strip()
    return result or text


def _template_script(fact: dict) -> dict:
    """No-LLM fallback: turns a raw Wikipedia summary into a punchy beat structure."""
    item = fact["item"]
    extract = fact["extract"]

    hook = f"You use {item.lower()} all the time. You have no idea where it actually came from."
    beat1 = _first_sentences(extract, 160)
    beat2 = f"Almost nobody knows the real story behind {item.lower()} — until now."
    cliff = "And the twist at the end is the part nobody expects."
    cta = "Follow for the hidden origin story of something you use every day."

    scenes = [
        {"text": hook, "duration": 4.0, "visual_query": f"{item} closeup product", "sfx": "dramatic_sting"},
        {"text": beat1, "duration": 6.0, "visual_query": f"{item} vintage history", "sfx": "whoosh"},
        {"text": beat2, "duration": 5.0, "visual_query": f"{item} factory old", "sfx": "impact"},
        {"text": cliff, "duration": 3.0, "visual_query": "mystery vintage invention", "sfx": "swipe"},
        {"text": cta, "duration": 3.0, "visual_query": f"{item} modern day", "sfx": "ding"},
    ]
    return {"title": f"The Real Origin of {item}", "scenes": scenes}


def _try_ollama_polish(script: dict, model: str) -> dict:
    """Optional: rewrite scene text with a local free LLM for punchier phrasing."""
    try:
        import ollama
    except ImportError:
        return script

    try:
        prompt = (
            "Rewrite each 'text' field below to be punchier, shorter, and more "
            "retention-optimized for a YouTube Short about the surprising origin "
            "of an everyday item, in the same JSON structure. Keep durations and "
            "visual_query and sfx unchanged. Return ONLY JSON.\n\n"
            + json.dumps(script)
        )
        result = ollama.generate(model=model, prompt=prompt)
        raw = result.get("response", "")
        raw = re.sub(r"^```json|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        polished = json.loads(raw)
        if "scenes" in polished and len(polished["scenes"]) == len(script["scenes"]):
            return polished
    except Exception:
        pass  # silently fall back to template script — pipeline must not break
    return script


def generate_script(ollama_model: str = "llama3.2") -> dict:
    set_status("script_agent", "running", "fetching item origin story")
    try:
        fact = _fetch_item_origin()
        script = _template_script(fact)
        script = _try_ollama_polish(script, ollama_model)
        for s in script["scenes"]:
            s.setdefault("sfx", random.choice(SFX_POOL))
        set_status("script_agent", "done", f"generated: {script['title']}")
        return script
    except Exception as e:
        set_status("script_agent", "error", str(e))
        raise


if __name__ == "__main__":
    print(json.dumps(generate_script(), indent=2))