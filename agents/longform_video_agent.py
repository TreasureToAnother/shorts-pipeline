"""
Long-Form Video Agent
----------------------
Takes the chaptered script from longform_script_agent.py and produces
a 1920x1080 narrated, illustrated video:
  - the narration is split into ~TARGET_IMAGES beats (a few sentences,
    ~15-20s each), and each beat gets its own picture
  - Ollama describes one simple scene per beat; the pictures are drawn
    by Cloudflare Workers AI in the same crude, childish cartoon style
    as the thumbnails (thumbnail_agent.STYLE), with the general always
    in his hand-written costume so he looks the same throughout
  - each picture slowly zooms in or out while its beat is read (ffmpeg's
    zoompan), so the screen never sits completely still
  - free Microsoft TTS narration per beat (edge-tts) in a deep
    documentary voice, so every picture changes exactly when the
    narration moves on
  - a title card at the start of each chapter, in the thumbnail font
  - no music, no SFX

~35 pictures at ~150 neurons each stays well inside Cloudflare's
10,000 free daily neurons, with room for the thumbnail and retries.
"""
import asyncio
import json
import os
import random
import re
import subprocess
import sys
import uuid
import concurrent.futures as cf

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from status_store import set_status
from script_agent import _sentences
from video_agent import _trim_silence, _tts
from thumbnail_agent import FONT_PATH, STYLE, _generate_image

from PIL import Image as _PILImage, ImageDraw, ImageFont
if not hasattr(_PILImage, "ANTIALIAS"):
    _PILImage.ANTIALIAS = _PILImage.LANCZOS

from moviepy.editor import (
    AudioFileClip, CompositeAudioClip, CompositeVideoClip, ImageClip,
    VideoFileClip, concatenate_videoclips,
)
from moviepy.audio.fx.all import audio_normalize

W, H = 1920, 1080
FPS = 30
WORKDIR = os.path.join(os.path.dirname(__file__), "..", "data", "work")

TARGET_IMAGES = 35          # pictures per video — the free-tier budget
BEAT_GAP_SECONDS = 0.35     # breath between beats, after silence trimming
ZOOM_AMOUNT = 0.08          # each picture zooms 8% in (or out) over its beat
IMAGE_WORKERS = 4
CHAPTER_CARD_SECONDS = 4.0
NARRATOR_VOICE = os.getenv("LONGFORM_VOICE_NAME", "en-US-ChristopherNeural")

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
    """Words the narration capitalizes mid-sentence — i.e. names."""
    nouns = set()
    for chapter in script["chapters"]:
        for sentence in _sentences(chapter["text"]):
            for word in re.findall(r"[A-Za-z][A-Za-z'-]*", sentence)[1:]:
                if word[0].isupper():
                    nouns.add(word.lower().removesuffix("'s"))
    return nouns


def _looks_like_name(word: str, proper_nouns: set) -> bool:
    w = word.lower().strip(",.;:'").removesuffix("'s")
    if w in STARTERS:
        return False
    return w in proper_nouns or bool(re.search(r"(ian|ians|ean|eans|ese|ish|ans|ic)$", w))


def _valid_scene(scene, general_pattern, side_names: dict, allow_elephants: bool, proper_nouns: set = frozenset(),
                 era: str = ""):
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
    allow_elephants = script["general"] in ELEPHANT_GENERALS
    if allow_elephants:
        objects = "elephants, " + objects
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
        scene = _valid_scene(raw[i], pattern, cast["names"], allow_elephants, proper_nouns, era) if i < len(raw) else None
        for _ in range(2):
            if scene:
                break
            try:
                data = _ollama_json(SINGLE_SCENE_PROMPT.format(general=script["general"], objects=objects,
                                                               beat=_normalize_scene(beat, pattern, cast["names"]),
                                                               ours=sides, theirs=enemies), model, 0.9)
                scene = _valid_scene(data.get("scene"), pattern, cast["names"], allow_elephants, proper_nouns, era)
            except Exception:
                pass
        if not scene:
            scene = fallbacks[used_fallbacks % len(fallbacks)].format(land=ERA_LAND.get(era, "plains"))
            used_fallbacks += 1
        scenes.append(_fill_placeholders(scene, cast["fill"]))
    return scenes


def _fallback_scene(script: dict) -> str:
    return (f"a round-headed cartoon man wearing {script['look']} standing on a hill, "
            f"looking thoughtfully into the distance, open landscape behind him")


def _draw_scene(scene: str, script: dict):
    """Returns a PIL image, or None if every attempt was refused/failed.
    A refused scene (the API's safety filter) falls back to a calm shot
    of the general."""
    for prompt_scene in (scene, scene, _fallback_scene(script)):
        if not prompt_scene:
            continue
        try:
            return _generate_image(STYLE + "Wide view. " + prompt_scene + " Flat simple background.")
        except Exception:
            continue
    return None


def _zoom_segment(image_path: str, duration: float, out_path: str, zoom_in: bool):
    """Renders a still image as a slowly zooming 1920x1080 clip. Each
    frame is an affine warp with sub-pixel precision (OpenCV), piped
    into ffmpeg — ffmpeg's own zoompan crops on whole pixels, which makes
    a slow zoom visibly stutter on alternate frames."""
    import cv2
    import numpy as np
    frames = max(1, int(round(duration * FPS)))
    src = np.asarray(_PILImage.open(image_path).convert("RGB"))
    sh, sw = src.shape[:2]
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", out_path],
        stdin=subprocess.PIPE,
    )
    try:
        for n in range(frames):
            t = n / max(1, frames - 1)
            # Ease in-out, so the motion starts and ends gently instead of lurching.
            t = t * t * (3 - 2 * t)
            zoom = 1 + ZOOM_AMOUNT * (t if zoom_in else 1 - t)
            sx, sy = W * zoom / sw, H * zoom / sh  # output pixels per source pixel
            m = np.float32([[sx, 0, W / 2 - sx * sw / 2], [0, sy, H / 2 - sy * sh / 2]])
            frame = cv2.warpAffine(src, m, (W, H), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
            proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed rendering {out_path}")


def _chapter_card(text: str) -> _PILImage.Image:
    """Chapter title in the thumbnail font: white, thick black outline."""
    text = text.upper()  # the font's lowercase reads as mismatched small caps
    size = int(H * 0.095)
    font = ImageFont.truetype(FONT_PATH, size)
    stroke = max(4, size // 10)
    probe = ImageDraw.Draw(_PILImage.new("RGBA", (1, 1)))
    while size > 30:
        font = ImageFont.truetype(FONT_PATH, size)
        stroke = max(4, size // 10)
        b = probe.textbbox((0, 0), text, font=font, stroke_width=stroke)
        if b[2] - b[0] <= W * 0.85:
            break
        size -= 4
    card = _PILImage.new("RGBA", (b[2] - b[0] + 40, b[3] - b[1] + 40), (0, 0, 0, 0))
    ImageDraw.Draw(card).text((20 - b[0], 20 - b[1]), text, font=font, fill="white",
                              stroke_width=stroke, stroke_fill="black")
    return card


def _build_beat_audio(text: str, path: str):
    asyncio.run(_tts(text, path, voice=NARRATOR_VOICE, pitch="-5Hz", rate="+0%"))
    clip = AudioFileClip(path).fx(audio_normalize)
    return _trim_silence(clip)


def build_longform_video(script: dict, ollama_model: str = "qwen2.5:7b") -> str:
    set_status("longform_video_agent", "running", "planning illustrations")
    try:
        run_dir = os.path.join(WORKDIR, f"longform_{uuid.uuid4().hex[:6]}")
        os.makedirs(run_dir, exist_ok=True)

        total_words = sum(len(c["text"].split()) for c in script["chapters"])
        # Beats close only once they pass this length, so they run ~15%
        # long on average; aiming a little short lands near TARGET_IMAGES.
        words_per_beat = max(25, int(total_words / (TARGET_IMAGES * 1.15)))

        # 1. Beats, their narration, and a scene for each.
        beats = []  # dicts: text, scene, chapter index
        for ci, chapter in enumerate(script["chapters"]):
            texts = _split_beats(chapter["text"], words_per_beat)
            scenes = _scene_descriptions(script, ci, texts, ollama_model, fallback_start=len(beats))
            for text, scene in zip(texts, scenes):
                beats.append({"text": text, "scene": scene, "chapter": ci})
        # Kept next to the pictures, to trace any odd illustration back to its prompt.
        with open(os.path.join(run_dir, "beats.json"), "w") as f:
            json.dump([{k: b[k] for k in ("chapter", "text", "scene")} for b in beats], f, indent=1)

        set_status("longform_video_agent", "running", f"narrating {len(beats)} beats")
        cursor = 0.0
        for i, beat in enumerate(beats):
            beat["audio"] = _build_beat_audio(beat["text"], os.path.join(run_dir, f"beat{i:03d}.mp3"))
            beat["offset"] = cursor
            # Snapped to whole frames: each picture's clip is a whole number
            # of frames, so unsnapped durations would let the pictures drift
            # behind the narration a little more with every beat.
            beat["duration"] = round((beat["audio"].duration + BEAT_GAP_SECONDS) * FPS) / FPS
            cursor += beat["duration"]
        total_duration = cursor

        # 2. Pictures, drawn in parallel. A beat whose picture failed
        # reuses the previous one, so one refusal never sinks the video.
        set_status("longform_video_agent", "running", f"drawing {len(beats)} illustrations")
        with cf.ThreadPoolExecutor(IMAGE_WORKERS) as pool:
            images = list(pool.map(lambda b: _draw_scene(b["scene"], script), beats))
        if not any(images):
            raise RuntimeError("Every illustration failed — check Cloudflare credentials and the daily neuron allowance")
        last = next(img for img in images if img)
        for i, img in enumerate(images):
            last = img or last
            beats[i]["image_path"] = os.path.join(run_dir, f"beat{i:03d}.png")
            last.save(beats[i]["image_path"])
        failed = sum(1 for img in images if img is None)

        # 3. Zooming clips, alternating in and out.
        set_status("longform_video_agent", "running", "rendering")
        segment_paths = []
        for i, beat in enumerate(beats):
            seg = os.path.join(run_dir, f"beat{i:03d}.mp4")
            _zoom_segment(beat["image_path"], beat["duration"], seg, zoom_in=(i % 2 == 0))
            segment_paths.append(seg)
        video = concatenate_videoclips([VideoFileClip(p) for p in segment_paths], method="chain")
        video = video.set_duration(total_duration)

        # 4. Chapter title cards over the first beat of each chapter.
        cards = []
        for ci, chapter in enumerate(script["chapters"]):
            first = next(b for b in beats if b["chapter"] == ci)
            card_path = os.path.join(run_dir, f"card{ci}.png")
            _chapter_card(chapter["topic"]).save(card_path)
            card = (ImageClip(card_path).set_start(first["offset"])
                    .set_duration(min(CHAPTER_CARD_SECONDS, first["duration"]))
                    .crossfadein(0.5).crossfadeout(0.5))
            cards.append(card.set_position(("center", H * 0.80)))

        audio = CompositeAudioClip([b["audio"].set_start(b["offset"]) for b in beats]).set_duration(total_duration)
        final = CompositeVideoClip([video, *cards], size=(W, H)).set_duration(total_duration).set_audio(audio)

        out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "output")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"longform_{uuid.uuid4().hex[:8]}.mp4")
        final.write_videofile(
            out_path, fps=FPS, codec="libx264", audio_codec="aac", threads=os.cpu_count() or 4,
            preset="veryfast", logger=None, ffmpeg_params=["-movflags", "+faststart"],
        )
        note = f" ({failed} of {len(beats)} illustrations reused the previous picture)" if failed else ""
        set_status("longform_video_agent", "done", out_path + note)
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
