"""
Long-Form Video Agent
----------------------
Takes the chaptered script from longform_script_agent.py and produces a
1920x1080 narrated, illustrated video built for retention:
  - narration (edge-tts, deep documentary voice) at ~180 words a minute,
    with the timing of every word, which drives everything below
  - the narration is split into ~TARGET_IMAGES beats; Ollama describes
    one scene per beat, drawn by Cloudflare Workers AI in the crude
    cartoon style of the thumbnails (thumbnail_agent.STYLE), the general
    always in his hand-written costume
  - each picture is shown as 2-3 hard-cut shots (full frame, then closer
    crops), cutting at sentence breaks — a new view every ~4-5 seconds
  - popups at the exact word they're about: text callouts for numbers,
    dates and names; sticker images of the general, each side's soldiers
    and objects the narration mentions
  - word-by-word subtitles, and a banner as each chapter starts
  - rendered frame by frame (longform_renderer.py); no music, no SFX

Budget: ~35 pictures (~156 neurons each) plus small 512x512 stickers
(~26 each) stays inside Cloudflare's 10,000 free neurons per 24 hours.
"""
import asyncio
import json
import os
import random
import re
import subprocess
import sys
import uuid
import wave
import concurrent.futures as cf

import numpy as np

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from status_store import set_status
from script_agent import _sentences
from thumbnail_agent import FONT_PATH, STYLE, _generate_image, _text_in_image
import longform_renderer as R

from PIL import Image as _PILImage

W, H = R.W, R.H
WORKDIR = os.path.join(os.path.dirname(__file__), "..", "data", "work")

TARGET_IMAGES = 35          # pictures per video — the free-tier budget
BEAT_GAP_SECONDS = 0.15     # breath between beats, after silence trimming
IMAGE_WORKERS = 4
NARRATOR_VOICE = os.getenv("LONGFORM_VOICE_NAME", "en-US-ChristopherNeural")
NARRATOR_RATE = os.getenv("LONGFORM_VOICE_RATE", "+20%")  # ~180 wpm; +0% measured 149
SAMPLE_RATE = 24000

MIN_SHOT_SECONDS = 2.5      # never cut faster than this
TARGET_SHOT_SECONDS = 4.5   # aim for a new view about this often
SHOT_CROPS = [(0.32, 0.5, 1.5), (0.68, 0.5, 1.5), (0.5, 0.42, 1.35), (0.35, 0.38, 1.7), (0.65, 0.38, 1.7)]
TEXT_POPUP_SECONDS = 2.4
STICKER_SECONDS = 2.8
BANNER_SECONDS = 3.0
MAX_TEXT_POPUPS_PER_BEAT = 2
PORTRAIT_MIN_GAP = 25.0     # seconds between general-portrait popups

# Ollama writes scenes with placeholders instead of people: THE GENERAL,
# OUR MEN and ENEMY MEN, swapped afterwards for fixed costumes. That
# keeps the two sides looking different and consistent, and keeps real
# names (which the image API's safety filter refuses) out of prompts.
SCENE_PROMPT = """You are planning pictures for part of a narrated history video about {general}.
This part is about: {topic}

Write one picture description for each numbered narration beat below. Every description must:
- be one plain sentence of 10 to 25 words, saying who is visible, doing what, and where
- name people ONLY with these exact words: THE GENERAL (that is {general}), OUR MEN (= {ours}, his side, drawn as a crowd of tiny figures), ENEMY MEN (= {theirs}, the side he fought against, drawn as a crowd of tiny figures)
- keep the sides straight: {general} commands OUR MEN, never ENEMY MEN
- never contain any other name of a person, people, nation, city, river or place; describe the landscape instead
- only use these things: {objects}
- show danger with worried faces, charging crowds, arrows flying in the sky, or smoke, never anyone getting hurt
- describe only things a camera could see — never ideas, lessons, words, maps, signs or labels (for a lesson, show THE GENERAL doing something)
- be different from the picture before it

Beats:
{beats}

Return ONLY JSON: {{"scenes": ["...", "..."]}} with exactly {n} descriptions, in order."""

SINGLE_SCENE_PROMPT = """Describe ONE simple picture for this moment in a narrated history video about {general}:
"{beat}"

The description must:
- be one plain sentence of 10 to 25 words, saying who is visible, doing what, and where
- name people ONLY with these exact words: THE GENERAL (that is {general}), OUR MEN (= {ours}, his side), ENEMY MEN (= {theirs}, the side he fought against)
- never contain any name of a person, people, nation, city, river or place
- only use these things: {objects}
- never show anyone getting hurt
- describe only things a camera could see — never ideas, lessons, words, maps, signs or labels

Return ONLY JSON: {{"scene": "..."}}"""

ERA_OBJECTS = {
    "ancient": "horses, spears, shields, swords held upright, bows, tents, wooden walls, stone walls, ships with oars, flags, campfires",
    "eastern": "horses, spears, bows, swords held upright, flags, tents, wooden walls, stone walls, wooden boats, campfires",
    "gunpowder": "horses, muskets, cannons, flags, drums, tents, forts, stone walls, sailing ships, campfires, smoke",
    "naval": "wooden sailing ships, cannons, waves, flags, rowing boats, cliffs, harbors, smoke",
    "modern": "tanks, trucks, airplanes, rifles held upright, helmets, trenches, sandbags, flags, binoculars, smoke",
}
# Things that would be out of their time in each era's pictures.
ANACHRONISMS = {
    "ancient": r"gun|guns|cannon\w*|musket\w*|rifle\w*|pistol\w*|tank\w*|binocular\w*|telescope\w*|gunpowder|airplane\w*|truck\w*",
    "eastern": r"tank\w*|binocular\w*|telescope\w*|airplane\w*|truck\w*|rifle\w*",
    "gunpowder": r"tank\w*|binocular\w*|airplane\w*|truck\w*|machine gun\w*|bows drawn|arrows?|archers?|chariot\w*|catapult\w*",
    "naval": r"tank\w*|binocular\w*|airplane\w*|truck\w*|submarine\w*|chariot\w*|catapult\w*",
    "modern": r"chariot\w*|catapult\w*|musket\w*|arrows?|archers?|bows drawn|swords?|spears?",
}
LANDSCAPES = "rivers, hills, mountains, forests, plains, deserts, snowy fields, sea, coast, marsh, valleys, camps"
# Elephants only where history puts them.
ELEPHANT_GENERALS = {"Hannibal Barca", "Scipio Africanus", "Alexander the Great"}

GORE_WORDS = re.compile(
    r"\b(blood\w*|bleed\w*|bod(y|ies)|corpse\w*|dead|death\w*|die[sd]?|dying|kill\w*|execut\w*|"
    r"wound\w*|slaughter\w*|massacre\w*|fallen|stab\w*|behead\w*|murder\w*|gore|injur\w*|"
    r"trampl\w*|crush\w*|casualt\w*|ablaze|motionless|no sign of (life|movement)|"
    r"(lie|lies|lying)(?! low| in wait| hidden| in ambush)|burn(ing|ed|s)? alive|captive\w*|prisoner\w*|hang(ed|ing))\b", re.IGNORECASE)
PLACEHOLDERS = ("THE GENERAL", "OUR MEN", "ENEMY MEN")
# Common words a description may start with in capitals. Any other
# capitalized first word is treated as a name if the script uses it as
# one, or if it looks like a nationality ("Korean ships...").
STARTERS = set("""a an the on in at under as while with from across near behind inside by beside along
before after during above below over through into onto around among between beyond against toward towards
two three four five several many some few all both each every one tiny small little big huge large long
tall wide smoke fog mist snow rain dust fire flames arrows waves wind storm clouds night morning dawn dusk
evening sunset sunrise tents ships boats horses cannons tanks flags hills mountains rivers fields trees
there here then now soon finally suddenly meanwhile later far deep dark bright""".split())
GROUP_NOUNS = (r"troops|soldiers|army|armies|forces|force|men|columns|column|cavalry|infantry|horsemen|"
               r"archers|warriors|legions|legion|lines|line|ranks|side|sailors|crews|fleet|fleets|navy|"
               r"riders|spearmen|musketeers|regiments|regiment|divisions|division|units|camp|ships")

# Used when Ollama can't produce a valid scene for a beat — rotated, so
# a run of failures still gives varied pictures rather than one repeat.
FALLBACK_SCENES = {
    "land": [
        "THE GENERAL standing on a hill, watching OUR MEN march past in a long line below",
        "OUR MEN marching in a long line across open {land}",
        "THE GENERAL pointing forward while OUR MEN cheer behind him",
        "ENEMY MEN gathered in a big crowd on the far side of open {land}",
        "OUR MEN sitting around campfires in front of their tents at night",
        "THE GENERAL thinking hard with one hand on his chin, inside a big tent",
        "OUR MEN and ENEMY MEN facing each other across open {land}, both sides looking nervous",
        "THE GENERAL smiling proudly while OUR MEN wave flags and cheer",
    ],
    "naval": [
        "THE GENERAL standing on the deck of a wooden ship, looking out to sea",
        "OUR MEN on wooden sailing ships sailing in a line across the sea",
        "THE GENERAL pointing ahead from the deck of his ship while OUR MEN cheer",
        "ENEMY MEN on a big crowd of ships on the far side of the sea",
        "OUR MEN rowing small boats past tall cliffs",
        "THE GENERAL studying the sea from the top of a cliff",
        "ships of OUR MEN and ENEMY MEN facing each other across the sea, both sides looking nervous",
        "OUR MEN raising flags on their ships and cheering",
    ],
}
ERA_LAND = {"ancient": "plains", "eastern": "grassland", "gunpowder": "fields", "modern": "plains"}


def _split_beats(text: str, target_words: int) -> list:
    """Groups sentences into beats of roughly target_words each."""
    beats, current = [], []
    for sentence in _sentences(text):
        current.append(sentence)
        if len(" ".join(current).split()) >= target_words:
            beats.append(" ".join(current))
            current = []
    if current:
        # A short tail joins the previous beat rather than getting a picture of its own.
        if beats and len(" ".join(current).split()) < target_words / 2:
            beats[-1] += " " + " ".join(current)
        else:
            beats.append(" ".join(current))
    return beats or [text]


def _ollama_json(prompt: str, model: str, temperature: float = 0.7) -> dict:
    import ollama
    result = ollama.generate(model=model, prompt=prompt, format="json",
                             options={"temperature": temperature, "num_ctx": 8192})
    data = json.loads(result.get("response", "{}"))
    return data if isinstance(data, dict) else {}


def _name_pattern(general: str):
    """Matches the general's name, its distinctive words ("Hannibal",
    "Barca") and possessives, so a slipped name becomes a placeholder."""
    name = re.sub(r"^the ", "", general)
    words = [w for w in re.findall(r"[A-Za-z][\w-]+", name)
             if len(w) > 3 and w.lower() not in {"admiral", "great", "duke"}]
    return re.compile(
        r"(?:\bthe\s+)?(?:" + "|".join([re.escape(name)] + [rf"\b{re.escape(w)}\b" for w in words]) + r")(?:\s+(?:" +
        "|".join(rf"{re.escape(w)}\b" for w in words) + r"))*('s)?", re.IGNORECASE)


def _side_cast(script: dict, chapter_index: int) -> dict:
    """Which enemy a chapter is about: chapters are [intro, battle 1,
    battle 2, battle 3, outro]; intro and outro use the featured battle's."""
    n_chapters = len(script["chapters"])
    battle = chapter_index - 1 if 0 < chapter_index < n_chapters - 1 else script["thumb"]["battle"]
    battle = min(max(battle, 0), len(script["enemies"]) - 1)
    enemy = script["enemies"][battle]
    return {
        "names": {"OUR MEN": script["army"]["names"], "ENEMY MEN": enemy["names"]},
        "fill": {
            "THE GENERAL": f"a round-headed cartoon man wearing {script['look']}",
            "OUR MEN": f"tiny round-headed cartoon men in {script['army']['look']}",
            "ENEMY MEN": f"tiny round-headed cartoon men in {enemy['look']}",
        },
    }


def _normalize_scene(scene: str, general_pattern, side_names: dict) -> str:
    """Rewrites what the model naturally writes into placeholders: the
    general's name, any casing of the placeholders, and the two sides'
    names ("Austrian columns", "the French") -> OUR MEN / ENEMY MEN."""
    scene = general_pattern.sub(lambda m: "his" if m.group(1) else "THE GENERAL", scene.strip())
    for placeholder in PLACEHOLDERS:
        scene = re.sub(re.escape(placeholder).replace(r"\ ", r"\s+"), placeholder, scene, flags=re.IGNORECASE)
    for placeholder, names in side_names.items():
        for name in sorted(names, key=len, reverse=True):
            # "the Austrian columns" / "Austrians" -> placeholder
            scene = re.sub(rf"(?:\b[Tt]he\s+)?\b{re.escape(name)}(?:'s)?(?:\s+(?:{GROUP_NOUNS})\b)?", placeholder, scene)
    # "OUR MEN's ships" -> "OUR MEN with their ships"
    scene = re.sub(r"\b(OUR MEN|ENEMY MEN)'s\b", r"\1 with their", scene)
    scene = re.sub(r"\bTHE GENERAL's\b", "THE GENERAL with his", scene)
    scene = re.sub(r"(THE GENERAL|OUR MEN|ENEMY MEN)", r" \1 ", scene)  # the model sometimes glues them to a word
    # The general twice ("THE GENERAL surveys the map, THE GENERAL ponders")
    # would draw him twice — later mentions become "he".
    first = scene.find("THE GENERAL")
    if first != -1:
        scene = scene[:first + 11] + scene[first + 11:].replace("THE GENERAL", "he")
    # Stray all-caps words ("ENEMY lines crumble") read as names to the checker.
    scene = re.sub(r"\b(?!(?:THE|GENERAL|OUR|ENEMY|MEN)\b)[A-Z]{2,}\b", lambda m: m.group(0).lower(), scene)
    scene = re.sub(r"\bENEMY\b(?! MEN)", "enemy", scene)
    return re.sub(r"\s+([,.;:])", r"\1", re.sub(r"\s+", " ", scene)).strip().rstrip(".")


def _script_proper_nouns(script: dict) -> set:
    """Names in the narration: words it capitalizes mid-sentence, plus
    words it only ever writes capitalized ("Capua" opening a sentence,
    never "capua")."""
    nouns, capitalized, lowercase = set(), set(), set()
    for chapter in script["chapters"]:
        for sentence in _sentences(chapter["text"]):
            for i, word in enumerate(re.findall(r"[A-Za-z][A-Za-z'-]*", sentence)):
                key = word.lower().removesuffix("'s")
                if word[0].isupper():
                    capitalized.add(key)
                    if i:
                        nouns.add(key)
                else:
                    lowercase.add(key)
    return nouns | {w for w in capitalized - lowercase if w not in STARTERS and len(w) > 2}


def _looks_like_name(word: str, proper_nouns: set) -> bool:
    w = word.lower().strip(",.;:'").removesuffix("'s")
    if w in STARTERS:
        return False
    return w in proper_nouns or bool(re.search(r"(ian|ians|ean|eans|ese|ish|ans|ic)$", w))


def _valid_scene(scene, general_pattern, side_names: dict, allow_elephants: bool, proper_nouns: set = frozenset(),
                 era: str = ""):
    """allow_elephants: whether this beat's narration mentions elephants."""
    """Returns the cleaned scene (still with placeholders) or None.
    Rejects anything that could get the image refused or wrong: non-ASCII
    text, gore, and any capitalized word other than the placeholders —
    which catches leftover names of people, nations and places."""
    if not isinstance(scene, str):
        return None
    scene = _normalize_scene(scene, general_pattern, side_names)
    if not scene.isascii() or GORE_WORDS.search(scene):
        return None
    if not allow_elephants and re.search(r"elephant", scene, re.IGNORECASE):
        return None
    if era in ANACHRONISMS and re.search(rf"\b({ANACHRONISMS[era]})\b", scene, re.IGNORECASE):
        return None
    words = scene.split()
    if not 4 <= len(words) <= 40:
        return None
    # "map, flexibility, terrain, victory" — a list of ideas, which the
    # image model draws as written words.
    if scene.count(",") >= 4 and scene.count(",") / len(words) > 0.15:
        return None
    rest = scene
    for p in PLACEHOLDERS:
        rest = rest.replace(p, " ")
    rest_words = rest.split()
    if rest_words and scene.split()[0] not in PLACEHOLDERS and rest_words[0][:1].isupper():
        if _looks_like_name(rest_words[0], proper_nouns):
            return None
        rest_words = rest_words[1:]
    if any(re.search(r"[A-Z]", w) for w in rest_words):
        return None
    return scene


def _fill_placeholders(scene: str, fill: dict) -> str:
    for placeholder, description in fill.items():
        scene = scene.replace(placeholder, description)
    return scene[:1].upper() + scene[1:]


def _scene_descriptions(script: dict, chapter_index: int, beats: list, model: str, fallback_start: int = 0) -> list:
    """One finished scene description per beat. Ollama plans the whole
    chapter in one call; beats whose scene fails validation are asked
    again on their own, then fall back to a rotating safe scene."""
    chapter = script["chapters"][chapter_index]
    era = script.get("era", "ancient")
    objects = ERA_OBJECTS.get(era, ERA_OBJECTS["ancient"]) + ", " + LANDSCAPES
    # Elephants only in beats whose narration mentions them: allowed
    # everywhere, the model put them in every other Hannibal picture.
    elephants_era = script["general"] in ELEPHANT_GENERALS
    pattern = _name_pattern(script["general"])
    cast = _side_cast(script, chapter_index)
    proper_nouns = _script_proper_nouns(script)
    # "the Carthaginians" — the plural form reads naturally in the prompt.
    label = lambda names: "the " + next((n for n in names if n.endswith("s")), names[0])
    sides, enemies = label(cast["names"]["OUR MEN"]), label(cast["names"]["ENEMY MEN"])

    raw = []
    try:
        # The narration the model reads already says OUR MEN / ENEMY MEN
        # instead of "the Romans" etc., so it can't mix up the sides.
        prompt_beats = [_normalize_scene(b, pattern, cast["names"]) for b in beats]
        numbered = "\n".join(f"{i}. {b}" for i, b in enumerate(prompt_beats, start=1))
        data = _ollama_json(SCENE_PROMPT.format(general=script["general"], topic=chapter["topic"], objects=objects,
                                                ours=sides, theirs=enemies, beats=numbered, n=len(beats)), model)
        raw = data.get("scenes") or next((v for v in data.values() if isinstance(v, list)), [])
        # Some models return [{"description": ...}] instead of plain strings.
        raw = [next((v for v in r.values() if isinstance(v, str)), None) if isinstance(r, dict) else r for r in raw]
    except Exception:
        pass

    fallbacks = FALLBACK_SCENES["naval" if era == "naval" else "land"]
    scenes, used_fallbacks = [], fallback_start
    for i, beat in enumerate(beats):
        scene = _valid_scene(raw[i], pattern, cast["names"], elephants_era and "elephant" in beats[i].lower(), proper_nouns, era) if i < len(raw) else None
        for _ in range(2):
            if scene:
                break
            try:
                data = _ollama_json(SINGLE_SCENE_PROMPT.format(general=script["general"], objects=objects,
                                                               beat=_normalize_scene(beat, pattern, cast["names"]),
                                                               ours=sides, theirs=enemies), model, 0.9)
                scene = _valid_scene(data.get("scene"), pattern, cast["names"], elephants_era and "elephant" in beat.lower(),
                                         proper_nouns, era)
            except Exception:
                pass
        if not scene:
            scene = fallbacks[used_fallbacks % len(fallbacks)].format(land=ERA_LAND.get(era, "plains"))
            used_fallbacks += 1
        scenes.append(_fill_placeholders(scene, cast["fill"]))
    return scenes


OBJECT_PROMPT = """These are the narration beats of part of a history video about {general}. For each beat, pick ONE physical object the beat mentions that would make a good small picture popping up on screen: an animal, a specific weapon (a spear, a sword, a bow, a catapult, a cannon, a musket, a tank — only what the beat's era had), a piece of armour, a ship or boat, a gate, wall or tower, a tent, a banner, a horse. Name it specifically: "a war elephant", not "an animal"; "a catapult", not "a war machine". Never a person, a group of people, a place, a landscape, a battle or an idea.

Beats:
{beats}

For each beat give:
- "n": the beat number
- "word": the exact word in that beat where the thing is mentioned (copied from the beat)
- "object": the thing as a short phrase, 2 to 6 words, e.g. "a war elephant", "a wooden sailing ship", "a bronze shield"
Skip beats that mention nothing concrete.

Return ONLY JSON: {{"objects": [{{"n": 1, "word": "...", "object": "..."}}]}}"""


# What the object planner still picks despite being told not to: places,
# landscapes, ideas, people. Landscapes make poor stickers ("a lake" is a
# blue oval) and maps attract the image model's gibberish writing.
NOT_OBJECTS = re.compile(
    r"\b(battle\w*|field|plain|lake|river|sea|ocean|fog|mist|snow|valley|hill|mountain|pass|terrain|land\w*|"
    r"map|plan|strateg\w*|tactic\w*|envelopment|victory|defeat|army|armies|legion\w*|soldier\w*|men|man|"
    r"people|crowd|troops|camp|city|town|weather|winter|night|day|machine\w*|equipment|weapon\w*|vehicle\w*)\b", re.IGNORECASE)


def _object_popups(script: dict, chapter_index: int, beats: list, model: str) -> list:
    """Per beat, (trigger word, object phrase) for a sticker popup, or None."""
    era = script.get("era", "ancient")
    pattern = _name_pattern(script["general"])
    cast = _side_cast(script, chapter_index)
    proper_nouns = _script_proper_nouns(script)
    elephants_era = script["general"] in ELEPHANT_GENERALS
    out = [None] * len(beats)
    try:
        numbered = "\n".join(f"{i}. {b}" for i, b in enumerate(beats, start=1))
        data = _ollama_json(OBJECT_PROMPT.format(general=script["general"], beats=numbered), model, 0.3)
    except Exception:
        return out
    for item in data.get("objects", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        try:
            n = int(item.get("n")) - 1
        except (TypeError, ValueError):
            continue
        word, obj = str(item.get("word", "")).strip().strip(".,"), str(item.get("object", "")).strip().rstrip(".")
        if not (0 <= n < len(beats)) or not word or not obj or not 2 <= len(obj.split()) <= 6:
            continue
        if not re.search(rf"\b{re.escape(word)}\b", beats[n], re.IGNORECASE):
            continue
        # The object must be the thing the word names ("war elephants" ->
        # "a war elephant"); the model also tied "a wooden sailing ship"
        # to "Cannae" in a beat with no ship in it.
        stems = lambda text: {w[:5] for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2} - {"the", "and"}
        if not stems(word) & stems(re.sub(r"^(a|an|the)\s+", "", obj, flags=re.I)):
            continue
        # Same rules as scenes: no names, no gore, nothing out of its era.
        clean = _valid_scene(obj + " standing alone", pattern, cast["names"], elephants_era and "elephant" in beats[n].lower(),
                             proper_nouns, era)
        if not clean or any(p in clean for p in PLACEHOLDERS) or NOT_OBJECTS.search(obj):
            continue
        out[n] = (word, obj)
    return out


def _fallback_scene(script: dict) -> str:
    return (f"a round-headed cartoon man wearing {script['look']} standing on a hill, "
            f"looking thoughtfully into the distance, open landscape behind him")


def _draw_scene(scene: str, script: dict):
    """Returns a PIL image, or None if every attempt was refused/failed.
    A refused scene (the API's safety filter) falls back to a calm shot
    of the general. A picture with writing in it is redrawn once, keeping
    whichever version has less."""
    for prompt_scene in (scene, scene, _fallback_scene(script)):
        if not prompt_scene:
            continue
        prompt = STYLE + "Wide view. " + prompt_scene + " Flat simple background."
        try:
            img = _generate_image(prompt)
        except Exception:
            continue
        if _text_in_image(img) >= 2:
            try:
                retry = _generate_image(prompt)
                if _text_in_image(retry) < _text_in_image(img):
                    img = retry
            except Exception:
                pass
        return img
    return None


# ---------------------------------------------------------------- stickers

STICKER_PROMPT = "{style}{subject}, alone in the middle of a plain pure white background, nothing else in the picture."
# The main STYLE describes round-headed people, so with it every object
# sticker came out as a person ("a battlefield" was a bald man).
OBJECT_STYLE = ("Very simple childish cartoon drawing, like a kid drew it on a computer. Wobbly uneven thick black "
                "outlines, flat solid soft muted colors, no shading. ")


def _draw_sticker(subject: str, is_object: bool = False):
    """A cut-out sticker (RGBA) of one subject, or None."""
    prompt = STICKER_PROMPT.format(style=OBJECT_STYLE if is_object else STYLE, subject=subject)
    for _ in range(2):
        try:
            img = _generate_image(prompt, 512, 512)
        except Exception:
            continue
        if _text_in_image(img) >= 2:
            continue
        cut = R.cut_out(img)
        # A failed cut-out leaves (nearly) the whole square: not a sticker.
        if cut.width * cut.height < 0.92 * 512 * 512:
            return cut
    return None


# --------------------------------------------------------------- narration

async def _speak(text: str, path: str) -> list:
    """Saves the narration mp3 and returns [(word, start, end)] in seconds."""
    import edge_tts
    words = []
    communicate = edge_tts.Communicate(text, NARRATOR_VOICE, rate=NARRATOR_RATE, pitch="-5Hz",
                                       boundary="WordBoundary")
    with open(path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append((chunk["text"], start, start + chunk["duration"] / 1e7))
    return words


def _decode(path: str) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SAMPLE_RATE),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16)


def _attach_punctuation(words: list, text: str) -> list:
    """edge-tts reports bare words; put the script's punctuation back on
    them (subtitle chunks break after it)."""
    out, pos = [], 0
    for word, start, end in words:
        m = re.search(re.escape(word), text[pos:])
        if not m:
            out.append((word, start, end))
            continue
        stop = pos + m.end()
        tail = re.match(r"[.,;:!?'’\"”—)]*", text[stop:]).group(0)
        out.append((word + tail.rstrip("'’\"”)"), start, end))
        pos = stop + len(tail)
    return out


def _narrate(beats: list, run_dir: str) -> tuple:
    """Speaks every beat, trims the silence around it, and joins them.
    Sets each beat's offset/duration/words (absolute times). Returns
    (wav path, total seconds)."""
    pieces, cursor = [], 0.0
    gap = np.zeros(int(BEAT_GAP_SECONDS * SAMPLE_RATE), np.int16)
    for i, beat in enumerate(beats):
        mp3 = os.path.join(run_dir, f"beat{i:03d}.mp3")
        words = asyncio.run(_speak(beat["text"], mp3))
        audio = _decode(mp3)
        if words:
            lead = max(0.0, words[0][1] - 0.04)
            tail = min(len(audio) / SAMPLE_RATE, words[-1][2] + 0.12)
        else:
            lead, tail = 0.0, len(audio) / SAMPLE_RATE
        clip = audio[int(lead * SAMPLE_RATE):int(tail * SAMPLE_RATE)]
        beat["offset"] = cursor
        beat["duration"] = len(clip) / SAMPLE_RATE + BEAT_GAP_SECONDS
        beat["words"] = [(w, cursor + s - lead, cursor + e - lead) for w, s, e in _attach_punctuation(words, beat["text"])]
        pieces += [clip, gap]
        cursor += beat["duration"]
    path = os.path.join(run_dir, "narration.wav")
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(np.concatenate(pieces).tobytes())
    return path, cursor


# ---------------------------------------------------------------- timeline

def _shot_starts(beat: dict) -> list:
    """Cut points inside a beat: at sentence starts closest to every
    TARGET_SHOT_SECONDS, never closer than MIN_SHOT_SECONDS."""
    start, end = beat["offset"], beat["offset"] + beat["duration"]
    n_shots = max(1, round(beat["duration"] / TARGET_SHOT_SECONDS))
    # Candidate cuts: the start of each word that follows a sentence or clause end.
    candidates = [w[1] for prev, w in zip(beat["words"], beat["words"][1:]) if prev[0][-1:] in ".!?;:,—"]
    cuts = [start]
    for k in range(1, n_shots):
        ideal = start + k * beat["duration"] / n_shots
        pool = [c for c in candidates if c - cuts[-1] >= MIN_SHOT_SECONDS and end - c >= MIN_SHOT_SECONDS]
        cut = min(pool, key=lambda c: abs(c - ideal)) if pool else None
        if cut is None or abs(cut - ideal) > 2.0:
            cut = ideal if ideal - cuts[-1] >= MIN_SHOT_SECONDS and end - ideal >= MIN_SHOT_SECONDS else None
        if cut:
            cuts.append(cut)
    return cuts


_NUMBER = re.compile(r"^\d[\d,]*$")
_MONTHS = set("January February March April May June July August September October November December".split())


_ORDINAL_WORDS = set("First Second Third Fourth Fifth Sixth Seventh Eighth Ninth Tenth".split())


def _text_popups(beat: dict, skip_names: set) -> list:
    """(time, TEXT) callouts for the numbers, dates and names spoken in a
    beat — copied from the (fact-checked) narration, never invented.
    Names are whole capitalized phrases ("SECOND PUNIC WAR", "LAKE
    TRASIMENE"), never a lone word like "Second"."""
    words, found = beat["words"], []
    bare = [w.strip(".,;:!?'’\"”—") for w, _, _ in words]
    i = 0
    while i < len(words):
        w, s = bare[i], words[i][1]
        nxt = bare[i + 1] if i + 1 < len(words) else ""
        prev = bare[i - 1] if i else ""
        # Dates: "22 December 218 BC", "December 218 BC", "June 21, 217 BC".
        date = re.match(r"(?:(\d{1,2}) )?(%s)(?: (\d{1,2}))?,? (\d{2,4})(?: (BC|AD|BCE|CE))?" % "|".join(_MONTHS),
                        " ".join(bare[i:i + 6]))
        if date and (w in _MONTHS or (_NUMBER.match(w) and nxt in _MONTHS)):
            day_first, month, day_after, year, era = date.groups()
            label = f"{day_first} {month} {year}" if day_first else (f"{month} {day_after}, {year}" if day_after else f"{month} {year}")
            found.append((s, (label + (f" {era}" if era else "")).upper()))
            i += len(date.group(0).split())
            continue
        if _NUMBER.match(w) and len(w.replace(",", "")) >= 2:
            if nxt in ("BC", "AD", "BCE", "CE"):
                label = f"{prev} {w} {nxt}" if prev in _MONTHS else f"{w} {nxt}"
            elif nxt.isalpha() and nxt.islower() and len(nxt) > 2:
                label = f"{w} {nxt}"
            else:
                label = w
            found.append((s, label.upper()))
            i += 1
            continue
        sentence_start = i == 0 or words[i - 1][0][-1:] in ".!?"
        if w[:1].isupper() and w.isalpha():
            # Take the whole run of capitalized words (plus "of"/"the" inside it).
            j = i
            while j + 1 < len(words) and words[j][0][-1:] not in ".,;:!?" and bare[j + 1] not in _MONTHS:
                if bare[j + 1][:1].isupper():
                    j += 1
                    continue
                # Linking words inside a name: "Battle of the Trebia".
                k = j + 1
                while k < len(words) and bare[k] in ("of", "the") and k - j <= 2:
                    k += 1
                if k > j + 1 and k < len(words) and bare[k][:1].isupper():
                    j = k
                    continue
                break
            phrase = bare[i:j + 1]
            if sentence_start and len(phrase) == 1:
                i += 1
                continue  # a lone capitalized sentence opener is just an ordinary word
            if sentence_start and phrase[0] in ("The", "In", "At", "On", "By", "As", "After", "Before"):
                phrase = phrase[1:]
            text = " ".join(phrase)
            if phrase and not (len(phrase) == 1 and (phrase[0] in skip_names or phrase[0] in _ORDINAL_WORDS
                                                    or phrase[0] in _MONTHS or len(phrase[0]) <= 3)) \
                    and not all(p in skip_names for p in phrase):
                found.append((s, text.upper()))
            i = j + 1
            continue
        i += 1
    # One callout per distinct label, spread out; numbers first.
    picked, seen = [], set()
    for s, label in sorted(found, key=lambda x: (not x[1][:1].isdigit(), x[0])):
        if label in seen or len(label) > 26 or any(abs(s - p[0]) < TEXT_POPUP_SECONDS for p in picked):
            continue
        picked.append((s, label))
        seen.add(label)
        if len(picked) >= MAX_TEXT_POPUPS_PER_BEAT:
            break
    return sorted(picked)


def _first_word_time(beat: dict, pattern) -> float:
    for w, s, _ in beat["words"]:
        if re.fullmatch(pattern, w.strip(".,;:!?'’\"”—").lower().removesuffix("'s")):
            return s
    return None


class _Slots:
    """The two popup corners, each holding one popup at a time."""

    def __init__(self):
        self.busy = {"left": 0.0, "right": 0.0}
        self.last = "left"

    def take(self, start: float, end: float, prefer: str = None):
        order = [prefer] if prefer else []
        order += [s for s in ("right", "left") if s != self.last and s not in order]
        order += [s for s in ("left", "right") if s not in order]
        for side in order:
            if self.busy[side] <= start:
                self.busy[side] = end + 0.3
                self.last = side
                return side
        return None

    def block(self, start: float, end: float):
        for side in self.busy:
            self.busy[side] = max(self.busy[side], end)


def build_longform_video(script: dict, ollama_model: str = "qwen2.5:7b") -> str:
    set_status("longform_video_agent", "running", "planning illustrations")
    try:
        run_dir = os.path.join(WORKDIR, f"longform_{uuid.uuid4().hex[:6]}")
        os.makedirs(run_dir, exist_ok=True)

        total_words = sum(len(c["text"].split()) for c in script["chapters"])
        # Beats close only once they pass this length, so they run ~15%
        # long on average; aiming a little short lands near TARGET_IMAGES.
        words_per_beat = max(25, int(total_words / (TARGET_IMAGES * 1.15)))

        # 1. Beats, a scene for each, and an object popup where there's one.
        beats = []
        for ci, chapter in enumerate(script["chapters"]):
            texts = _split_beats(chapter["text"], words_per_beat)
            scenes = _scene_descriptions(script, ci, texts, ollama_model, fallback_start=len(beats))
            objects = _object_popups(script, ci, texts, ollama_model)
            for text, scene, obj in zip(texts, scenes, objects):
                beats.append({"text": text, "scene": scene, "object": obj, "chapter": ci})
        with open(os.path.join(run_dir, "beats.json"), "w") as f:
            json.dump([{k: b[k] for k in ("chapter", "text", "scene", "object")} for b in beats], f, indent=1)

        # 2. Narration, with the time of every word.
        set_status("longform_video_agent", "running", f"narrating {len(beats)} beats")
        audio_path, total_duration = _narrate(beats, run_dir)

        # 3. Pictures and stickers, drawn in parallel. A beat whose picture
        # failed reuses the previous one; a missing sticker just doesn't pop up.
        set_status("longform_video_agent", "running", f"drawing {len(beats)} illustrations")
        name = script["general"]
        sticker_subjects = {
            "general": f"A round-headed cartoon man wearing {script['look']}, seen from the waist up, smiling confidently",
            "ours": f"A single tiny round-headed cartoon man in {script['army']['look']}, standing with a spear, full body",
        }
        for enemy in script["enemies"]:
            sticker_subjects.setdefault(f"enemy:{enemy['look']}",
                                        f"A single tiny round-headed cartoon man in {enemy['look']}, standing with a spear, full body")
        for b in beats:
            if b["object"]:
                sticker_subjects.setdefault(f"obj:{b['object'][1].lower()}", b["object"][1][:1].upper() + b["object"][1][1:])
        with cf.ThreadPoolExecutor(IMAGE_WORKERS) as pool:
            image_jobs = [pool.submit(_draw_scene, b["scene"], script) for b in beats]
            sticker_jobs = {k: pool.submit(_draw_sticker, v, k.startswith("obj:")) for k, v in sticker_subjects.items()}
            images = [j.result() for j in image_jobs]
            stickers = {k: j.result() for k, j in sticker_jobs.items()}
        if not any(images):
            raise RuntimeError("Every illustration failed — check Cloudflare credentials and the daily neuron allowance")
        last = next(img for img in images if img)
        for i, img in enumerate(images):
            last = img or last
            beats[i]["image"] = last
            last.save(os.path.join(run_dir, f"beat{i:03d}.png"))
        failed = sum(1 for img in images if img is None)
        for key, st in stickers.items():
            if st is not None:
                st.save(os.path.join(run_dir, "sticker_" + re.sub(r"[^a-z0-9]+", "_", key.lower())[:40] + ".png"))

        # 4. Timeline: shots, popups, subtitles, chapter banners.
        set_status("longform_video_agent", "running", "rendering")
        shots, overlays, slots = [], [], _Slots()
        sticker_imgs = {k: R.sticker(v) for k, v in stickers.items() if v is not None}
        name_words = {w for w in re.findall(r"[A-Za-z][\w-]+", name) if len(w) > 3} - {"Admiral", "Great", "Duke"}
        general_re = "|".join(re.escape(w.lower()) for w in name_words) or "$^"
        last_portrait = -PORTRAIT_MIN_GAP
        chapter_seen = set()
        for i, beat in enumerate(beats):
            ci = beat["chapter"]
            if ci not in chapter_seen:
                chapter_seen.add(ci)
                overlays.append(R.Overlay(R.banner(script["chapters"][ci]["topic"].upper(), FONT_PATH),
                                          beat["offset"], beat["offset"] + BANNER_SECONDS, (W // 2, int(H * 0.04)), "center_top"))
                slots.block(beat["offset"], beat["offset"] + BANNER_SECONDS)
            # Shots: full frame first, then crops on different parts.
            crops = random.sample(SHOT_CROPS, len(SHOT_CROPS))
            for k, cut in enumerate(_shot_starts(beat)):
                shots.append((cut, beat["image"], (0.5, 0.5, 1.0) if k == 0 else crops[(k - 1) % len(crops)]))

            enemy_key = f"enemy:{script['enemies'][min(max(ci - 1, 0), len(script['enemies']) - 1)]['look']}" \
                if 0 < ci < len(script["chapters"]) - 1 else f"enemy:{script['enemies'][script['thumb']['battle']]['look']}"
            enemy_names = script["enemies"][min(max(ci - 1, 0), len(script["enemies"]) - 1)]["names"] \
                if 0 < ci < len(script["chapters"]) - 1 else script["enemies"][script["thumb"]["battle"]]["names"]
            wanted = []  # (time, kind, payload)
            t = _first_word_time(beat, general_re)
            if t is not None and t - last_portrait >= PORTRAIT_MIN_GAP and "general" in sticker_imgs:
                wanted.append((t, "sticker", "general"))
                last_portrait = t
            for key, names in (("ours", script["army"]["names"]), (enemy_key, enemy_names)):
                t = _first_word_time(beat, "|".join(re.escape(n.lower()) for n in names))
                if t is not None and key in sticker_imgs:
                    wanted.append((t, "sticker", key))
            if beat["object"]:
                t = _first_word_time(beat, re.escape(beat["object"][0].lower()))
                key = f"obj:{beat['object'][1].lower()}"
                if t is not None and key in sticker_imgs:
                    wanted.append((t, "sticker", key))
            for t, label in _text_popups(beat, name_words | set(script["army"]["names"]) | set(enemy_names)):
                wanted.append((t, "text", label))
            for t, kind, payload in sorted(wanted):
                length = STICKER_SECONDS if kind == "sticker" else TEXT_POPUP_SECONDS
                end = min(t + length, beat["offset"] + beat["duration"] + 0.5)
                side = slots.take(t, end)
                if not side:
                    continue
                img = sticker_imgs[payload] if kind == "sticker" else R.text_popup(payload, FONT_PATH)
                overlays.append(R.Overlay(img, t, end, R.SLOTS[side], side))

            # Subtitles: short chunks, the spoken word highlighted.
            for chunk in R.subtitle_chunks(beat["words"]):
                for j, (w, s, e) in enumerate(chunk):
                    until = chunk[j + 1][1] if j + 1 < len(chunk) else e + 0.12
                    overlays.append(R.Overlay(R.subtitle_image(chunk, j, FONT_PATH), s, until,
                                              (W // 2, R.SUB_Y), "center", pop=False))

        out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "output")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"longform_{uuid.uuid4().hex[:8]}.mp4")
        R.render(shots, overlays, audio_path, total_duration, out_path)
        popups = sum(1 for o in overlays if o.pop)
        note = f"; {failed} of {len(beats)} illustrations reused the previous picture" if failed else ""
        set_status("longform_video_agent", "done", f"{out_path} ({len(shots)} shots, {popups} popups{note})")
        return out_path
    except Exception as e:
        set_status("longform_video_agent", "error", str(e))
        raise


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", ".env"))
    from longform_script_agent import generate_longform_script
    model = os.getenv("LONGFORM_OLLAMA_MODEL", "qwen2.5:7b")
    s = generate_longform_script(ollama_model=model, target_minutes=2)  # short run for a quick local test
    path = build_longform_video(s, ollama_model=model)
    print("Video written to:", path)
