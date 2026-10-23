"""
Thumbnail Agent
----------------
Builds a 1280x720 YouTube thumbnail for a long-form video:
  1. Ollama reads the script and writes one visual scene description
     plus a short hook (2-4 words) for the thumbnail text
  2. Cloudflare Workers AI (Flux schnell, free daily allowance) draws
     the scene in a fixed stickman/cartoon style — with NO text, since
     image models misspell words
  3. Pillow crops it to 16:9 and draws the hook text on top, so the
     lettering is always clean and on-brand

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
import sys

import requests

sys.path.append("..")
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from status_store import set_status

from PIL import Image, ImageDraw, ImageFont

THUMB_W, THUMB_H = 1280, 720
FLUX_MODEL = "@cf/black-forest-labs/flux-1-schnell"
FLUX_STEPS = 8  # the model's max; still only ~a few dozen of the 10,000 free daily neurons

# Prepended to every scene so all thumbnails share one look. This is
# the main knob for the thumbnail's visual style.
STYLE_PREFIX = (
    "Simple cartoon illustration, stick figure characters with round heads, "
    "thick bold black outlines, flat bright colors, clean uncluttered background, "
    "high contrast, expressive and dramatic, YouTube thumbnail style, no text, "
    "no letters, no words. Scene: "
)
TEXT_COLOR = "white"
TEXT_STROKE_COLOR = "black"
TEXT_MAX_WIDTH_FRAC = 0.55   # hook text sits in the left part of the frame
TEXT_MAX_HEIGHT_FRAC = 0.45


def _scene_and_hook(script: dict, model: str) -> dict:
    """Asks Ollama for {"scene": ..., "hook": ...}. Falls back to a
    generic scene and the general's name if the model's JSON is unusable."""
    fallback = {
        "scene": f"a stick figure general in a cape on a hilltop, pointing at two armies clashing below",
        "hook": script.get("general", script["title"]).upper()[:24],
    }
    try:
        import ollama
        battles = "; ".join(c["topic"] for c in script["chapters"][1:-1])
        prompt = (
            f"You're designing a YouTube thumbnail for a video titled "
            f"\"{script['title']}\", about {script.get('general', '')} and "
            f"these battles: {battles}.\n\n"
            f"Return JSON with two fields:\n"
            f"- \"scene\": ONE simple, dramatic visual moment to illustrate, "
            f"in one sentence — characters, action, setting. Something "
            f"readable at a glance on a phone screen. No text or signs in the scene.\n"
            f"- \"hook\": 2 to 4 punchy words for big thumbnail text that "
            f"creates curiosity (e.g. \"THE PERFECT TRAP\", \"OUTNUMBERED 10 TO 1\"). "
            f"Don't repeat the title."
        )
        result = ollama.generate(model=model, prompt=prompt, format="json")
        data = json.loads(result.get("response", "{}"))
        scene = str(data.get("scene", "")).strip()
        hook = str(data.get("hook", "")).strip().strip('"').upper()
        if scene and hook and len(hook.split()) <= 5:
            return {"scene": scene, "hook": hook}
    except Exception:
        pass
    return fallback


def _generate_image(scene: str) -> Image.Image:
    account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    token = os.environ["CLOUDFLARE_API_TOKEN"]
    r = requests.post(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{FLUX_MODEL}",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": (STYLE_PREFIX + scene)[:2048], "steps": FLUX_STEPS},
        timeout=120,
    )
    r.raise_for_status()
    if r.headers.get("content-type", "").startswith("image/"):
        raw = r.content
    else:
        raw = base64.b64decode(r.json()["result"]["image"])
    return Image.open(io.BytesIO(raw)).convert("RGB")


def _crop_to_thumb(img: Image.Image) -> Image.Image:
    """Flux has no size option (it returns a square-ish image), so this
    center-crops to 16:9 and resizes to 1280x720."""
    target_ratio = THUMB_W / THUMB_H
    w, h = img.size
    if w / h > target_ratio:
        new_w = int(h * target_ratio)
        img = img.crop(((w - new_w) // 2, 0, (w - new_w) // 2 + new_w, h))
    else:
        new_h = int(w / target_ratio)
        img = img.crop((0, (h - new_h) // 2, w, (h - new_h) // 2 + new_h))
    return img.resize((THUMB_W, THUMB_H), Image.LANCZOS)


def _wrap_lines(hook: str):
    words = hook.split()
    if len(words) <= 2:
        return words  # one word per line: bigger text than one long line
    mid = (len(words) + 1) // 2
    return [" ".join(words[:mid]), " ".join(words[mid:])]


def _draw_hook(img: Image.Image, hook: str) -> Image.Image:
    from video_agent import CAPTION_FONT
    draw = ImageDraw.Draw(img)
    lines = _wrap_lines(hook)
    max_w, max_h = THUMB_W * TEXT_MAX_WIDTH_FRAC, THUMB_H * TEXT_MAX_HEIGHT_FRAC

    # Largest font size where the text block still fits its box.
    size = 160
    while size > 30:
        font = ImageFont.truetype(CAPTION_FONT, size) if CAPTION_FONT else ImageFont.load_default(size)
        stroke = max(4, size // 12)
        boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=stroke) for line in lines]
        block_w = max(b[2] - b[0] for b in boxes)
        line_h = max(b[3] - b[1] for b in boxes)
        block_h = line_h * len(lines) + int(line_h * 0.15) * (len(lines) - 1)
        if block_w <= max_w and block_h <= max_h:
            break
        size -= 6

    x = int(THUMB_W * 0.05)
    y = THUMB_H - int(THUMB_H * 0.07) - block_h
    for line in lines:
        draw.text((x, y), line, font=font, fill=TEXT_COLOR,
                  stroke_width=stroke, stroke_fill=TEXT_STROKE_COLOR)
        y += line_h + int(line_h * 0.15)
    return img


def generate_thumbnail(script: dict, video_path: str, ollama_model: str = "llama3.2"):
    if not (os.getenv("CLOUDFLARE_ACCOUNT_ID") and os.getenv("CLOUDFLARE_API_TOKEN")):
        print("No Cloudflare credentials — skipping thumbnail (YouTube will auto-pick a frame).")
        return None
    set_status("thumbnail_agent", "running", "generating thumbnail")
    try:
        idea = _scene_and_hook(script, ollama_model)
        img = _crop_to_thumb(_generate_image(idea["scene"]))
        img = _draw_hook(img, idea["hook"])
        out_path = os.path.splitext(video_path)[0] + "_thumb.jpg"
        img.save(out_path, "JPEG", quality=90)  # YouTube's limit is 2MB; this lands well under
        set_status("thumbnail_agent", "done", f"{idea['hook']} — {idea['scene'][:120]}")
        return out_path
    except Exception as e:
        set_status("thumbnail_agent", "error", str(e))
        print(f"Thumbnail failed, uploading without one: {e}")
        return None


if __name__ == "__main__":
    # Local preview: runs steps 1 and 3 against a plain placeholder
    # background if no Cloudflare credentials are set.
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", ".env"))
    demo = {"title": "The 3 Battles That Made Hannibal Barca a Legend", "general": "Hannibal Barca",
            "chapters": [{"topic": "intro"}, {"topic": "Battle of the Trebia (218 BC)"},
                         {"topic": "Battle of Lake Trasimene (217 BC)"},
                         {"topic": "Battle of Cannae (216 BC)"}, {"topic": "outro"}]}
    model = os.getenv("LONGFORM_OLLAMA_MODEL", "qwen2.5:7b")
    out = os.path.join(os.path.dirname(__file__), "..", "data", "output", "thumbnail_preview.jpg")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    idea = _scene_and_hook(demo, model)
    print(json.dumps(idea, indent=2))
    if os.getenv("CLOUDFLARE_ACCOUNT_ID") and os.getenv("CLOUDFLARE_API_TOKEN"):
        img = _crop_to_thumb(_generate_image(idea["scene"]))
    else:
        img = Image.new("RGB", (THUMB_W, THUMB_H), (70, 110, 160))
    _draw_hook(img, idea["hook"]).save(out, "JPEG", quality=90)
    print("Preview written to:", out)
