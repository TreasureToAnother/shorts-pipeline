"""
Frame-by-frame renderer for the long-form videos.

Built for retention rather than for moviepy's compositing model:
  - shots: each illustration is shown as 2-3 hard-cut shots (the full
    frame, then closer crops on different parts), so the picture
    changes every ~4-5 seconds
  - popups: sticker-style images and text callouts that pop in at the
    exact word the narration says them, in the two upper corners
  - subtitles: 1-3 words at a time near the bottom, the word being
    spoken highlighted
  - chapter titles: a banner at the top as each chapter starts

Frames are composed with numpy and piped straight into ffmpeg together
with the narration track.
"""
import math
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1920, 1080
FPS = 30

SUB_Y = int(H * 0.80)          # subtitle line's vertical center
SUB_SIZE = 86
SUB_HIGHLIGHT = (255, 212, 0)
POPUP_H = int(H * 0.36)        # sticker height
POP_IN_FRAMES = 6              # quick scale-in with a little overshoot
POP_OUT_FRAMES = 4
SLOTS = {"left": (int(W * 0.04), int(H * 0.05)), "right": (int(W * 0.96), int(H * 0.05))}  # top-left anchors (right is right-aligned)


def _font(path: str, size: int):
    return ImageFont.truetype(path, size)


def sticker(img: Image.Image, height: int = POPUP_H) -> Image.Image:
    """An RGBA cut-out (transparent background) -> scaled sticker with a
    thick white outline and a soft shadow, like a YouTube explainer popup."""
    scale = height / img.height
    img = img.resize((max(1, int(img.width * scale)), height), Image.LANCZOS)
    pad = 24
    canvas = Image.new("RGBA", (img.width + pad * 2, img.height + pad * 2), (0, 0, 0, 0))
    alpha = Image.new("L", canvas.size, 0)
    alpha.paste(img.split()[3], (pad, pad))
    outline = alpha.filter(ImageFilter.MaxFilter(17))
    shadow = outline.filter(ImageFilter.GaussianBlur(8)).point(lambda a: int(a * 0.45))
    shadow_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow_layer.putalpha(shadow)
    shadow_layer = shadow_layer.transform(canvas.size, Image.AFFINE, (1, 0, -6, 0, 1, -8))
    white = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
    white.putalpha(outline)
    out = Image.alpha_composite(shadow_layer, white)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    layer.paste(img, (pad, pad), img)
    return Image.alpha_composite(out, layer)


def cut_out(img: Image.Image, threshold: int = 232) -> Image.Image:
    """Removes a plain light background: flood-fills near-white from the
    edges, so white inside the drawing (eyes, highlights) stays."""
    rgb = img.convert("RGB")
    marker = (255, 0, 255)
    work = rgb.copy()
    w, h = work.size
    px = work.load()
    for x, y in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]:
        if min(px[x, y]) >= threshold:
            ImageDraw.floodfill(work, (x, y), marker, thresh=255 - threshold + 20)
    arr = np.asarray(work)
    mask = ~((arr[:, :, 0] == 255) & (arr[:, :, 1] == 0) & (arr[:, :, 2] == 255))
    rgba = np.dstack([np.asarray(rgb), (mask * 255).astype(np.uint8)])
    out = Image.fromarray(rgba, "RGBA")
    bbox = out.getbbox()
    return out.crop(bbox) if bbox else out


def text_popup(text: str, font_path: str, size: int = 96) -> Image.Image:
    """A bold callout ("40,000 MEN") on a yellow card with a dark outline."""
    font = _font(font_path, size)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    b = probe.textbbox((0, 0), text, font=font)
    tw, th = b[2] - b[0], b[3] - b[1]
    padx, pady = 40, 26
    card = Image.new("RGBA", (tw + padx * 2 + 16, th + pady * 2 + 16), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((8, 12, card.width - 1, card.height - 1), radius=28, fill=(0, 0, 0, 110))   # shadow
    d.rounded_rectangle((0, 0, card.width - 9, card.height - 13), radius=28, fill=(255, 212, 0, 255),
                        outline=(20, 20, 20, 255), width=7)
    d.text((padx - b[0] + 4, pady - b[1] + 2), text, font=font, fill=(20, 20, 20, 255))
    return card


def banner(text: str, font_path: str) -> Image.Image:
    """Chapter title banner for the top of the frame."""
    size = 74
    font = _font(font_path, size)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    while size > 36:
        font = _font(font_path, size)
        b = probe.textbbox((0, 0), text, font=font, stroke_width=6)
        if b[2] - b[0] <= W * 0.8:
            break
        size -= 4
    b = probe.textbbox((0, 0), text, font=font, stroke_width=6)
    card = Image.new("RGBA", (b[2] - b[0] + 90, b[3] - b[1] + 50), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle((0, 0, card.width - 1, card.height - 1), radius=26, fill=(20, 20, 20, 205))
    d.text((45 - b[0], 25 - b[1]), text, font=font, fill="white", stroke_width=6, stroke_fill="black")
    return card


def subtitle_chunks(words: list, max_words: int = 3, max_chars: int = 20) -> list:
    """Groups timed words [(word, start, end)] into short on-screen
    chunks, breaking after punctuation so a chunk never spans sentences."""
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        text = " ".join(x[0] for x in cur)
        if len(cur) >= max_words or len(text) >= max_chars or w[0][-1:] in ".,;:!?—":
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def subtitle_image(chunk: list, highlight: int, font_path: str) -> Image.Image:
    font = _font(font_path, SUB_SIZE)
    stroke = 9
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    words = [w[0].upper() for w in chunk]
    space = probe.textlength(" ", font=font)
    widths = [probe.textbbox((0, 0), w, font=font, stroke_width=stroke)[2] for w in words]
    total = sum(widths) + space * (len(words) - 1)
    b = probe.textbbox((0, 0), "AGJ", font=font, stroke_width=stroke)
    img = Image.new("RGBA", (int(total) + 20, b[3] - b[1] + 30), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = 10
    for i, (w, wd) in enumerate(zip(words, widths)):
        d.text((x, 10 - b[1]), w, font=font, fill=SUB_HIGHLIGHT if i == highlight else "white",
               stroke_width=stroke, stroke_fill="black")
        x += wd + space
    return img


class _Overlay:
    """A pre-rendered RGBA image placed on the frame over [start, end)."""

    def __init__(self, img: Image.Image, start: float, end: float, anchor, align: str = "left", pop: bool = True):
        self.img, self.start, self.end, self.anchor, self.align, self.pop = img, start, end, anchor, align, pop
        self._scaled = {}

    def _scale_at(self, t: float) -> float:
        if not self.pop:
            return 1.0
        f_in = (t - self.start) * FPS
        f_out = (self.end - t) * FPS
        if f_in < POP_IN_FRAMES:
            x = f_in / POP_IN_FRAMES  # 0..1, overshoot to 1.08 then settle
            return 0.55 + 0.53 * math.sin(x * math.pi / 2) - 0.08 * max(0.0, x - 0.7) / 0.3
        if f_out < POP_OUT_FRAMES:
            return max(0.2, f_out / POP_OUT_FRAMES)
        return 1.0

    def rendered(self, t: float):
        scale = round(self._scale_at(t), 2)
        if scale not in self._scaled:
            if scale == 1.0:
                im = self.img
            else:
                im = self.img.resize((max(1, int(self.img.width * scale)), max(1, int(self.img.height * scale))),
                                     Image.BILINEAR)
            arr = np.asarray(im).astype(np.float32)
            self._scaled[scale] = (arr[:, :, :3], arr[:, :, 3:4] / 255.0)
        return self._scaled[scale]

    def position(self, w: int, h: int):
        ax, ay = self.anchor
        if self.align == "center":
            return ax - w // 2, ay - h // 2
        if self.align == "center_top":
            return ax - w // 2, ay
        if self.align == "right":
            return ax - w, ay
        return ax, ay


def _blend(frame: np.ndarray, rgb: np.ndarray, alpha: np.ndarray, x: int, y: int):
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x0 >= x1 or y0 >= y1:
        return
    sx, sy = x0 - x, y0 - y
    region = frame[y0:y1, x0:x1].astype(np.float32)
    a = alpha[sy:sy + (y1 - y0), sx:sx + (x1 - x0)]
    region = region * (1 - a) + rgb[sy:sy + (y1 - y0), sx:sx + (x1 - x0)] * a
    frame[y0:y1, x0:x1] = region.astype(np.uint8)


def shot_frame(img: Image.Image, crop) -> np.ndarray:
    """crop: (cx, cy, zoom) in fractions — the full frame is (0.5, 0.5, 1.0)."""
    cx, cy, zoom = crop
    sw, sh = img.size
    cw, ch = sw / zoom, sh / zoom
    left = min(max(0, cx * sw - cw / 2), sw - cw)
    top = min(max(0, cy * sh - ch / 2), sh - ch)
    return np.asarray(img.crop((int(left), int(top), int(left + cw), int(top + ch))).resize((W, H), Image.LANCZOS).convert("RGB"))


def render(shots: list, overlays: list, audio_path: str, duration: float, out_path: str):
    """shots: [(start, PIL image, crop)] in time order, covering the video.
    overlays: [_Overlay]. Writes an H.264/AAC mp4."""
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-i", audio_path,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out_path],
        stdin=subprocess.PIPE,
    )
    frames = int(math.ceil(duration * FPS))
    shot_i, base, base_key = 0, None, None
    overlays = sorted(overlays, key=lambda o: o.start)
    try:
        for n in range(frames):
            t = n / FPS
            while shot_i + 1 < len(shots) and shots[shot_i + 1][0] <= t:
                shot_i += 1
            if base_key != shot_i:
                base_key = shot_i
                base = shot_frame(shots[shot_i][1], shots[shot_i][2])
            frame = base.copy()
            for o in overlays:
                if o.start > t:
                    break
                if t < o.end:
                    rgb, a = o.rendered(t)
                    x, y = o.position(rgb.shape[1], rgb.shape[0])
                    _blend(frame, rgb, a, x, y)
            proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed rendering {out_path}")


Overlay = _Overlay
