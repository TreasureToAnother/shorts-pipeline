"""
Video Agent
-----------
Takes the script JSON from script_agent and produces a finished
9:16 vertical video with:
  - a continuous "satisfying texture" background (soap/wax cutting style
    loop) pulled from free Pexels footage
  - the actual topic footage as a smaller framed inset near the top
  - free Microsoft TTS narration (edge-tts), normalized louder
  - word-level auto captions (faster-whisper)
  - free transition/impact SFX (Freesound)

Everything here is free-tier: Pexels API key is free, Freesound API key
is free, edge-tts and faster-whisper are open-source/local.
"""
import asyncio
import os
import random
import sys
import uuid

import requests

sys.path.append("..")
from status_store import set_status

# Point moviepy at the real ImageMagick binary before it's imported below.
_im_binary = os.getenv("IMAGEMAGICK_BINARY", "")
if _im_binary:
    os.environ["IMAGEMAGICK_BINARY"] = _im_binary

# moviepy 1.0.3 calls the old Pillow constant Image.ANTIALIAS, removed in
# Pillow 10. Restore it rather than downgrading Pillow.
from PIL import Image as _PILImage
if not hasattr(_PILImage, "ANTIALIAS"):
    _PILImage.ANTIALIAS = _PILImage.LANCZOS

from moviepy.editor import (
    AudioFileClip, ColorClip, CompositeAudioClip, CompositeVideoClip,
    ImageClip, TextClip, VideoFileClip, concatenate_videoclips, afx, vfx,
)

W, H = 1080, 1920  # vertical short format
WORKDIR = os.path.join(os.path.dirname(__file__), "..", "data", "work")

# Inset window where the actual topic footage plays, positioned near
# the top so the satisfying background stays visible around it.
_INSET_SCALE = 0.75  # 25% smaller than the original size, same center point
_orig_w, _orig_h, _orig_y = W * 0.86, H * 0.34, H * 0.05
_center_y = _orig_y + _orig_h / 2
INSET_W = int(_orig_w * _INSET_SCALE)
INSET_H = int(_orig_h * _INSET_SCALE)
INSET_Y = int(_center_y - INSET_H / 2)
INSET_BORDER = 10

CAPTION_Y = H * 0.62  # below the inset, so captions never overlap it


def _detect_caption_font():
    """
    Picks a real, installed bold font for ImageMagick to use, instead of
    hardcoding a font name that only exists on some operating systems.
    Checks common font file paths directly. Returns None if nothing is
    found — callers must handle that by omitting the font argument.
    """
    override = os.getenv("CAPTION_FONT", "").strip()
    if override:
        return override

    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",   # macOS
        "/Library/Fonts/Arial Bold.ttf",                        # macOS (older)
        "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",  # macOS fallback
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", # Linux / GitHub Actions
        "C:\\Windows\\Fonts\\arialbd.ttf",                      # Windows
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


CAPTION_FONT = _detect_caption_font()


def _text_clip_kwargs(**kwargs):
    """Only includes the 'font' key when a real font was found — passing
    font=None straight into moviepy/ImageMagick crashes instead of
    falling back cleanly."""
    if CAPTION_FONT:
        kwargs["font"] = CAPTION_FONT
    return kwargs


def _cover_fit(clip, target_w, target_h):
    """
    Resizes + crops a clip to fill an exact target size without
    distortion, choosing whichever dimension needs to grow based on the
    clip's real aspect ratio. This is what was missing before: blindly
    resizing to match height first (regardless of the source's aspect
    ratio) blew landscape clips up huge and then cropped ~70% of the
    width away, which is what caused the "too zoomed in" look.
    """
    clip_ratio = clip.w / clip.h
    target_ratio = target_w / target_h
    if clip_ratio > target_ratio:
        clip = clip.fx(vfx.resize, height=target_h)
    else:
        clip = clip.fx(vfx.resize, width=target_w)
    clip = clip.fx(vfx.crop, x_center=clip.w / 2, y_center=clip.h / 2,
                    width=target_w, height=target_h)
    return clip


def _pexels_search_video(query: str, api_key: str):
    url = "https://api.pexels.com/videos/search"
    r = requests.get(url, headers={"Authorization": api_key}, params={"query": query, "per_page": 5, "orientation": "portrait"}, timeout=20)
    r.raise_for_status()
    videos = r.json().get("videos", [])
    if not videos:
        return None
    video = random.choice(videos)
    files = sorted(video["video_files"], key=lambda f: f.get("height", 0), reverse=True)
    return files[0]["link"] if files else None


def _download(url: str, dest: str):
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 16):
            f.write(chunk)
    return dest


def _freesound_download(query: str, api_key: str, dest: str):
    if not api_key:
        return None
    try:
        r = requests.get(
            "https://freesound.org/apiv2/search/text/",
            params={"query": query, "token": api_key, "fields": "id,previews", "filter": "duration:[0 TO 3]"},
            timeout=20,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        preview_url = random.choice(results)["previews"]["preview-hq-mp3"]
        return _download(preview_url, dest)
    except Exception:
        return None


async def _tts(text: str, out_path: str, voice: str = None, pitch: str = None):
    import edge_tts
    # en-US-AriaNeural is a bright, expressive female voice; pushing the
    # pitch up further gives it that higher, more "anime girl" energy.
    voice = voice or os.getenv("VOICE_NAME", "en-US-AriaNeural")
    pitch = pitch or os.getenv("VOICE_PITCH", "+25Hz")
    communicate = edge_tts.Communicate(text, voice, pitch=pitch)
    await communicate.save(out_path)


def _whisper_captions(audio_path: str):
    """Returns list of (word, start, end) using faster-whisper (free, local, CPU)."""
    from faster_whisper import WhisperModel
    model = WhisperModel("tiny.en", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio_path, word_timestamps=True)
    words = []
    for seg in segments:
        for w in seg.words:
            words.append((w.word.strip(), w.start, w.end))
    return words


def _get_satisfying_background(pexels_key: str):
    """
    Background source priority:
    1. A local video file in data/backgrounds/ (your own footage — e.g.
       your own recorded gameplay, or properly licensed clips). Drop
       any .mp4 files in there and one is picked at random each run.
    2. Free Pexels search (SATISFYING_QUERY), for generic satisfying
       textures Pexels actually has in its stock library.
    3. A plain animated color card fallback, so the pipeline never
       breaks even with nothing configured.
    """
    local_dir = os.path.join(os.path.dirname(__file__), "..", "data", "backgrounds")
    if os.path.isdir(local_dir):
        local_clips = [f for f in os.listdir(local_dir) if f.lower().endswith((".mp4", ".mov", ".m4v"))]
        if local_clips:
            path = os.path.join(local_dir, random.choice(local_clips))
            clip = VideoFileClip(path).without_audio()
            return _cover_fit(clip, W, H)

    query = os.getenv("SATISFYING_QUERY", "soap cutting satisfying asmr")
    video_url = _pexels_search_video(query, pexels_key) if pexels_key else None
    if video_url:
        path = os.path.join(WORKDIR, f"satisfying_{uuid.uuid4().hex[:6]}.mp4")
        _download(video_url, path)
        clip = VideoFileClip(path).without_audio()
        return _cover_fit(clip, W, H)

    from PIL import Image
    import numpy as np
    color = tuple(random.randint(30, 70) for _ in range(3))
    img_path = os.path.join(WORKDIR, f"satisfying_fallback_{uuid.uuid4().hex[:6]}.png")
    Image.fromarray(np.full((H, W, 3), color, dtype=np.uint8)).save(img_path)
    return ImageClip(img_path).set_duration(9999)  # trimmed to real length later


def _bg_slice(satisfying_src, cursor: float, duration: float):
    """Pulls a slice of the shared satisfying background starting at a
    rolling cursor so it progresses continuously across scenes instead
    of restarting/jumping every scene."""
    src_dur = satisfying_src.duration
    if duration >= src_dur:
        return satisfying_src.fx(vfx.loop, duration=duration)
    start = cursor % max(src_dur - duration, 0.01)
    return satisfying_src.subclip(start, start + duration)


def _build_scene_clip(scene: dict, pexels_key: str, freesound_key: str, idx: int, satisfying_src, cursor: float):
    os.makedirs(WORKDIR, exist_ok=True)
    scene_id = f"scene_{idx}_{uuid.uuid4().hex[:6]}"

    # 1. narration
    audio_path = os.path.join(WORKDIR, f"{scene_id}_voice.mp3")
    asyncio.run(_tts(scene["text"], audio_path))
    voice_clip = AudioFileClip(audio_path)
    voice_clip = voice_clip.fx(afx.audio_normalize)
    duration = voice_clip.duration

    # 2. continuous satisfying-texture background
    bg = _bg_slice(satisfying_src, cursor, duration).set_duration(duration)

    # 3. topic footage as a smaller framed inset near the top
    video_url = _pexels_search_video(scene["visual_query"], pexels_key) if pexels_key else None
    if video_url:
        raw_path = os.path.join(WORKDIR, f"{scene_id}_bg.mp4")
        _download(video_url, raw_path)
        inset = VideoFileClip(raw_path).without_audio()
        inset = _cover_fit(inset, INSET_W, INSET_H)
        inset = inset.loop(duration=duration) if inset.duration < duration else inset.subclip(0, duration)
    else:
        from PIL import Image
        import numpy as np
        color = tuple(random.randint(20, 60) for _ in range(3))
        img_path = os.path.join(WORKDIR, f"{scene_id}_inset.png")
        Image.fromarray(np.full((INSET_H, INSET_W, 3), color, dtype=np.uint8)).save(img_path)
        inset = ImageClip(img_path).set_duration(duration)

    inset = inset.set_duration(duration).set_position(("center", INSET_Y))

    border = ColorClip(size=(INSET_W + INSET_BORDER * 2, INSET_H + INSET_BORDER * 2),
                        color=(15, 15, 15)).set_duration(duration).set_position(("center", INSET_Y - INSET_BORDER))

    caption_clips = []
    try:
        words = _whisper_captions(audio_path)
        for word, start, end in words:
            txt = TextClip(word, **_text_clip_kwargs(
                fontsize=80, color="white", stroke_color="black",
                stroke_width=3, method="label",
            ))
            txt = txt.set_start(start).set_end(min(end, duration)).set_position(("center", CAPTION_Y))
            caption_clips.append(txt)
    except Exception:
        txt = TextClip(scene["text"], **_text_clip_kwargs(
            fontsize=60, color="white", stroke_color="black",
            stroke_width=2, method="caption", size=(W * 0.85, None),
        ))
        caption_clips = [txt.set_duration(duration).set_position(("center", CAPTION_Y))]

    sfx_path = os.path.join(WORKDIR, f"{scene_id}_sfx.mp3")
    sfx_downloaded = _freesound_download(scene.get("sfx", "whoosh"), freesound_key, sfx_path)
    audio_tracks = [voice_clip]
    if sfx_downloaded:
        audio_tracks.append(AudioFileClip(sfx_downloaded).volumex(0.35).set_start(0))
    combined_audio = CompositeAudioClip(audio_tracks).set_duration(duration)

    scene_clip = CompositeVideoClip([bg, border, inset, *caption_clips], size=(W, H)).set_duration(duration)
    scene_clip = scene_clip.set_audio(combined_audio)
    return scene_clip, duration


def build_video(script: dict, pexels_key: str = "", freesound_key: str = "") -> str:
    set_status("video_agent", "running", "assembling scenes")
    try:
        os.makedirs(WORKDIR, exist_ok=True)
        satisfying_src = _get_satisfying_background(pexels_key)

        clips = []
        # Start at a random point in the background clip each video,
        # instead of always the beginning — otherwise a long source
        # clip (e.g. a 10-minute gameplay recording) would only ever
        # show its first couple of minutes, no matter how long it is.
        src_dur = satisfying_src.duration
        cursor = random.uniform(0, max(src_dur - 1, 0)) if src_dur and src_dur > 1 else 0.0
        for i, scene in enumerate(script["scenes"]):
            clip, duration = _build_scene_clip(scene, pexels_key, freesound_key, i, satisfying_src, cursor)
            clips.append(clip)
            cursor += duration

        final = concatenate_videoclips(clips, method="compose")
        out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "output")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{uuid.uuid4().hex[:8]}.mp4")
        final.write_videofile(out_path, fps=30, codec="libx264", audio_codec="aac", threads=4, logger=None)
        set_status("video_agent", "done", out_path)
        return out_path
    except Exception as e:
        set_status("video_agent", "error", str(e))
        raise


if __name__ == "__main__":
    from script_agent import generate_script
    s = generate_script()
    path = build_video(s, pexels_key=os.getenv("PEXELS_API_KEY", ""), freesound_key=os.getenv("FREESOUND_API_KEY", ""))
    print("Video written to:", path)