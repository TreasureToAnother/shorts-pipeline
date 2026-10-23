"""
Thumbnail Agent
----------------
Builds a 1280x720 YouTube thumbnail for a long-form video, in a crude,
childish cartoon style (round bald heads, dot eyes, nub noses, flat
colors, wobbly black outlines):
  1. The scene is assembled from fixed parts, not invented by a model:
     the general's costume, the featured battle's background visual,
     and the hook text all come from GENERALS in longform_script_agent.py
     (so they're always historically right), and the pose is picked
     from POSES. The general is drawn big on one side, with open sky
     on the other for the text.
  2. Cloudflare Workers AI (Flux 2 Klein 4B, ~150 of the 10,000 free
     daily neurons per image) draws IMAGE_CANDIDATES versions at
     1280x720 — with NO text, since image models misspell words.
  3. The candidate and side (left/right) with the emptiest text area
     win — the model doesn't always put the general where it's asked.
  4. Pillow draws the hook there in a chunky cartoon font (white, last
     line yellow, slight tilt, thick outline, drop shadow). A general
     with no hand-written hook gets one from Ollama instead.

Shorts don't get one: custom thumbnails set through the API don't
reliably show in the Shorts feed.

Needs CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN (a "Workers AI"
token). Without them, or if anything here fails, it returns None and
the video uploads with YouTube's auto-picked frame instead — a missing
thumbnail should never cost a week's video.
"""
import base64
import io
import json
import os
import random
import re
import sys

import requests

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from status_store import set_status

from PIL import Image, ImageDraw, ImageFilter, ImageFont

THUMB_W, THUMB_H = 1280, 720
IMAGE_MODEL = "@cf/black-forest-labs/flux-2-klein-4b"
IMAGE_ATTEMPTS = 3      # per candidate, for when the API flags a prompt
IMAGE_CANDIDATES = 2    # drawn per video; the one with the emptiest text area wins
FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "fonts", "LuckiestGuy-Regular.ttf")

# The art style. Kept short on purpose: longer style prompts drift
# toward polished, detailed comic art, and naming anything (even to
# forbid it, e.g. "no beards") tends to make it appear.
STYLE = (
    "Very simple childish cartoon drawing, like a kid drew it on a computer. Wobbly uneven "
    "thick black outlines, flat solid soft muted colors, no shading. Round bald heads with "
    "tiny dot eyes, nub noses and simple curved mouths, plain colored shirts. "
)
# The general is always big on the right, facing the viewer; "open
# ground under a wide sky" on the left is what keeps room for the text
# (asking for "empty space" directly makes the model paste a blank panel).
SCENE = (
    "Wide view. On the right side, big in the foreground and facing the viewer, a "
    "round-headed cartoon man wearing {look}, {pose}. Behind him on the right, small in the background: "
    "{visual}. The left side is open {ground} under a wide flat sky."
)
POSES = [
    "smirking with his arms crossed",
    "grinning slyly and rubbing his hands together",
    "smiling proudly with one finger raised",
    "pointing forward with a big confident grin",
    "laughing with his hands on his hips",
    "raising one eyebrow with a sly little smile",
]

HOOK_PROMPT = """Write the big text for a YouTube thumbnail. The video is about how {general} won the {battle} by {tactic}.

Rules:
- 2 to 4 words, all capital letters
- It must make a curious viewer want to know HOW it happened, without explaining it
- Base it on what is surprising or bold about this specific tactic (the odds, the trick, the risk, the speed, the result)
- Do not use the general's name, the battle's name, or the words "battle", "war", "tactic" or "genius"
- Plain everyday words a 12 year old knows

Return ONLY JSON: {{"options": ["...", "...", "...", "...", "..."], "best": "..."}} with five different options and the strongest one as best."""
HOOK_BANNED = {"BATTLE", "WAR", "TACTIC", "TACTICS", "GENIUS"}

# Where the hook can go: left, top, right, bottom as fractions of the frame.
TEXT_BOXES = {"left": (0.04, 0.10, 0.50, 0.86), "right": (0.50, 0.10, 0.96, 0.86)}
TEXT_COLORS = ("white", "#FFD400")    # every line but the last, then the last (payoff) line
TEXT_TILT_DEGREES = 4
TEXT_STROKE_FRAC = 0.10


def _clean_hook(text: str, general: dict, battle: str):
    if not isinstance(text, str):
        return None
    hook = re.sub(r"[^A-Z0-9' ]", " ", text.upper())
    hook = " ".join(hook.split())
    words = hook.split()
    if not 2 <= len(words) <= 4 or len(hook) > 24:
        return None
    name_words = {w for w in re.sub(r"[^A-Z ]", " ", general["name"].upper()).split() if len(w) > 3}
    battle_words = {w for w in re.sub(r"[^A-Z ]", " ", battle.upper()).split() if len(w) > 3} - {"BATTLE"}
    forms = set(words) | {re.sub(r"'?S$", "", w) for w in words}  # "CAESAR'S" -> "CAESAR"
    if forms & (HOOK_BANNED | name_words | battle_words):
        return None
    return hook


def _hook(script: dict, battle: str, tactic: str, model: str) -> str:
    general = {"name": script["general"]}
    try:
        import ollama
        result = ollama.generate(
            model=model, format="json", options={"temperature": 0.8},
            prompt=HOOK_PROMPT.format(general=script["general"], battle=battle, tactic=tactic),
        )
        data = json.loads(result.get("response", "{}"))
        options = data.get("options", [])
        # Some models return [{"text": ...}] instead of plain strings.
        options = [o.get("text") if isinstance(o, dict) else o for o in options]
        for candidate in [data.get("best")] + options:
            hook = _clean_hook(candidate, general, battle)
            if hook:
                return hook
    except Exception:
        pass
    return "HOW HE WON"


def _generate_image(prompt: str) -> Image.Image:
    account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    token = os.environ["CLOUDFLARE_API_TOKEN"]
    r = requests.post(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{IMAGE_MODEL}",
        headers={"Authorization": f"Bearer {token}"},
        # Flux 2 takes multipart form fields, not a JSON body.
        files={"prompt": (None, prompt), "width": (None, str(THUMB_W)), "height": (None, str(THUMB_H))},
        timeout=180,
    )
    data = r.json()
    image = data.get("result", {}).get("image") if isinstance(data.get("result"), dict) else None
    if not image:
        raise RuntimeError(f"Image generation failed: {str(data.get('errors') or data)[:300]}")
    img = Image.open(io.BytesIO(base64.b64decode(image))).convert("RGB")
    if img.size != (THUMB_W, THUMB_H):
        img = img.resize((THUMB_W, THUMB_H), Image.LANCZOS)
    return img


def _scene_image(script: dict, pose: str) -> Image.Image:
    """Tries the scene a few times. The image API sometimes refuses a
    prompt as flagged; the last attempt drops the background scene,
    which is the usual trigger."""
    thumb = script["thumb"]
    last_error = None
    for attempt in range(IMAGE_ATTEMPTS):
        visual = thumb["visual"] if attempt < IMAGE_ATTEMPTS - 1 else "a few tiny round-headed cartoon men cheering"
        prompt = STYLE + SCENE.format(look=script["look"], pose=pose, visual=visual, ground=thumb["ground"])
        try:
            return _generate_image(prompt)
        except Exception as e:
            last_error = e
    raise last_error


def _busyness(img: Image.Image, box) -> float:
    """Share of pixels in box that sit on an outline — low means open
    sky/ground, where text reads cleanly."""
    x0, y0, x1, y1 = (int(box[0] * THUMB_W), int(box[1] * THUMB_H), int(box[2] * THUMB_W), int(box[3] * THUMB_H))
    edges = img.crop((x0, y0, x1, y1)).convert("L").filter(ImageFilter.FIND_EDGES)
    return sum(1 for v in edges.getdata() if v > 40) / ((x1 - x0) * (y1 - y0))


def _best_scene(script: dict):
    """Draws IMAGE_CANDIDATES images (different poses) and returns the
    (image, text side) with the emptiest text area."""
    poses = random.sample(POSES, IMAGE_CANDIDATES)
    images, last_error = [], None
    for pose in poses:
        try:
            images.append(_scene_image(script, pose))
        except Exception as e:
            last_error = e
    if not images:
        raise last_error
    scored = [(_busyness(img, box), side, img) for img in images for side, box in TEXT_BOXES.items()]
    _, side, img = min(scored, key=lambda t: t[0])
    return img, side


def _split_lines(hook: str):
    words = hook.split()
    if len(words) <= 2:
        return words
    # Two lines, as even as possible by character count.
    best = min(range(1, len(words)),
               key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))))
    return [" ".join(words[:best]), " ".join(words[best:])]


def _draw_hook(img: Image.Image, hook: str, side: str = "left") -> Image.Image:
    box = TEXT_BOXES[side]
    lines = _split_lines(hook)
    box_w = (box[2] - box[0]) * THUMB_W
    box_h = (box[3] - box[1]) * THUMB_H
    measure = ImageDraw.Draw(img)

    # Largest font size where the text block still fits its box.
    size = 260
    while True:
        font = ImageFont.truetype(FONT_PATH, size)
        stroke = max(4, int(size * TEXT_STROKE_FRAC))
        gap = int(size * 0.05)
        boxes = [measure.textbbox((0, 0), line, font=font, stroke_width=stroke) for line in lines]
        heights = [b[3] - b[1] for b in boxes]
        block_h = sum(heights) + gap * (len(lines) - 1)
        if (max(b[2] - b[0] for b in boxes) <= box_w and block_h <= box_h) or size <= 40:
            break
        size -= 6

    shadow = max(4, size // 18)
    layer = Image.new("RGBA", (int(box_w) + 60, block_h + 60), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    y = 20
    for i, (line, b, h) in enumerate(zip(lines, boxes, heights)):
        color = TEXT_COLORS[1] if i == len(lines) - 1 and len(lines) > 1 else TEXT_COLORS[0]
        x = 20 - b[0]
        draw.text((x + shadow, y - b[1] + shadow), line, font=font, fill="black",
                  stroke_width=stroke, stroke_fill="black")
        draw.text((x, y - b[1]), line, font=font, fill=color, stroke_width=stroke, stroke_fill="black")
        y += h + gap
    layer = layer.rotate(TEXT_TILT_DEGREES, expand=True, resample=Image.BICUBIC)

    out = img.convert("RGBA")
    top = int(box[1] * THUMB_H + (box_h - layer.height) / 2)
    left = int(box[0] * THUMB_W) - 20 if side == "left" else int(box[2] * THUMB_W) - layer.width + 20
    out.alpha_composite(layer, (max(0, left), max(0, top)))
    return out.convert("RGB")


def build_thumbnail(script: dict, ollama_model: str):
    """Returns (image, hook) — no file written, no status updates."""
    battle, tactic = script["battles"][script["thumb"]["battle"]]
    img, side = _best_scene(script)
    hook = script["thumb"].get("hook") or _hook(script, battle, tactic, ollama_model)
    return _draw_hook(img, hook, side), hook


def generate_thumbnail(script: dict, video_path: str, ollama_model: str = "llama3.2"):
    if not (os.getenv("CLOUDFLARE_ACCOUNT_ID") and os.getenv("CLOUDFLARE_API_TOKEN")):
        print("No Cloudflare credentials — skipping thumbnail (YouTube will auto-pick a frame).")
        return None
    set_status("thumbnail_agent", "running", "generating thumbnail")
    try:
        img, hook = build_thumbnail(script, ollama_model)
        out_path = os.path.splitext(video_path)[0] + "_thumb.jpg"
        img.save(out_path, "JPEG", quality=90)  # YouTube's limit is 2MB; this lands well under
        set_status("thumbnail_agent", "done", hook)
        return out_path
    except Exception as e:
        set_status("thumbnail_agent", "error", str(e))
        print(f"Thumbnail failed, uploading without one: {e}")
        return None


if __name__ == "__main__":
    # Local preview for one general (no video needed):
    #   python agents/thumbnail_agent.py "Julius Caesar"
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", ".env"))
    from longform_script_agent import GENERALS
    name = sys.argv[1] if len(sys.argv) > 1 else random.choice(GENERALS)["name"]
    g = next(g for g in GENERALS if g["name"] == name)
    script = {"general": g["name"], "look": g["look"], "thumb": g["thumb"], "battles": g["battles"]}
    img, hook = build_thumbnail(script, os.getenv("LONGFORM_OLLAMA_MODEL", "qwen2.5:7b"))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "output", "thumbnail_preview.jpg")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.save(out, "JPEG", quality=90)
    print(f"{name}: {hook}\nPreview written to: {out}")
