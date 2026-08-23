"""
Video Agent
-----------
Takes the script JSON from script_agent and produces a finished
9:16 vertical video with:
  - full-screen gameplay/satisfying background (no cropping — shown at
    "contain" scale so nothing gets cut off)
  - free Microsoft TTS narration (edge-tts), normalized louder, with
    leading/trailing silence trimmed so scenes play back-to-back with
    no dead gaps between sentences
  - large, bold, centered word-by-word captions
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

_im_binary = os.getenv("IMAGEMAGICK_BINARY", "")
if _im_binary:
    os.environ["IMAGEMAGICK_BINARY"] = _im_binary

from PIL import Image as _PILImage
if not hasattr(_PILImage, "ANTIALIAS"):
    _PILImage.ANTIALIAS = _PILImage.LANCZOS

from moviepy.editor import (
    AudioFileClip, ColorClip, CompositeAudioClip, CompositeVideoClip,
    ImageClip, TextClip, VideoFileClip, concatenate_videoclips, afx, vfx,
)

W, H = 1080, 1920
WORKDIR = os.path.join(os.path.dirname(__file__), "..", "data", "work")

CAPTION_Y = H * 0.46


def _detect_caption_font():
    override = os.getenv("CAPTION_FONT", "").strip()
    if override:
        return override
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


CAPTION_FONT = _detect_caption_font()


def _text_clip_kwargs(**kwargs):
    if CAPTION_FONT:
        kwargs["font"] = CAPTION_FONT
    return kwargs


def _contain_fit(clip, target_w, target_h):
    scale = min(target_w / clip.w, target_h / clip.h)
    new_w, new_h = int(clip.w * scale), int(clip.h * scale)
    resized = clip.fx(vfx.resize, newsize=(new_w, new_h))
    bg = ColorClip(size=(target_w, target_h), color=(0, 0, 0)).set_duration(clip.duration)
    return CompositeVideoClip([bg, resized.set_position("center")], size=(target_w, target_h))


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
    voice = voice or os.getenv("VOICE_NAME", "en-US-AriaNeural")
    pitch = pitch or os.getenv("VOICE_PITCH", "+25Hz")
    communicate = edge_tts.Communicate(text, voice, pitch=pitch)
    await communicate.save(out_path)


def _trim_silence(clip, threshold: float = 0.02, pad: float = 0.03):
    try:
        import numpy as np
        fps = 22050
        arr = clip.to_soundarray(fps=fps)
        mono = np.abs(arr).mean(axis=1) if arr.ndim > 1 else np.abs(arr)
        above = np.where(mono > threshold)[0]
        if len(above) == 0:
            return clip
        start = max(above[0] / fps - pad, 0)
        end = min(above[-1] / fps + pad, clip.duration)
        if end <= start:
            return clip
        return clip.subclip(start, end)
    except Exception:
        return clip


def _whisper_captions(audio_path: str):
    from faster_whisper import WhisperModel
    model = WhisperModel("tiny.en", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio_path, word_timestamps=True)
    words = []
    for seg in segments:
        for w in seg.words:
            words.append((w.word.strip(), w.start, w.end))
    return words


def _get_satisfying_background(pexels_key: str):
    local_dir = os.path.join(os.path.dirname(__file__), "..", "data", "backgrounds")
    if os.path.isdir(local_dir):
        local_clips = [f for f in os.listdir(local_dir) if f.lower().endswith((".mp4", ".mov", ".m4v"))]
        if local_clips:
            path = os.path.join(local_dir, random.choice(local_clips))
            clip = VideoFileClip(path).without_audio()
            return _contain_fit(clip, W, H)

    query = os.getenv("SATISFYING_QUERY", "soap cutting satisfying asmr")
    video_url = _pexels_search_video(query, pexels_key) if pexels_key else None
    if video_url:
        path = os.path.join(WORKDIR, f"satisfying_{uuid.uuid4().hex[:6]}.mp4")
        _download(video_url, path)
        clip = VideoFileClip(path).without_audio()
        return _contain_fit(clip, W, H)

    from PIL import Image
    import numpy as np
    color = tuple(random.randint(30, 70) for _ in range(3))
    img_path = os.path.join(WORKDIR, f"satisfying_fallback_{uuid.uuid4().hex[:6]}.png")
    Image.fromarray(np.full((H, W, 3), color, dtype=np.uint8)).save(img_path)
    return ImageClip(img_path).set_duration(9999)


def _bg_slice(satisfying_src, cursor: float, duration: float):
    src_dur = satisfying_src.duration
    if duration >= src_dur:
        return satisfying_src.fx(vfx.loop, duration=duration)
    start = cursor % max(src_dur - duration, 0.01)
    return satisfying_src.subclip(start, start + duration)


def _build_scene_clip(scene: dict, freesound_key: str, idx: int, satisfying_src, cursor: float):
    os.makedirs(WORKDIR, exist_ok=True)
    scene_id = f"scene_{idx}_{uuid.uuid4().hex[:6]}"

    audio_path = os.path.join(WORKDIR, f"{scene_id}_voice.mp3")
    asyncio.run(_tts(scene["text"], audio_path))
    voice_clip = AudioFileClip(audio_path)
    voice_clip = voice_clip.fx(afx.audio_normalize)
    voice_clip = _trim_silence(voice_clip)
    duration = voice_clip.duration

    bg = _bg_slice(satisfying_src, cursor, duration).set_duration(duration)

    caption_clips = []
    try:
        words = _whisper_captions(audio_path)
        for word, start, end in words:
            txt = TextClip(word, **_text_clip_kwargs(
                fontsize=130, color="white", stroke_color="black",
                stroke_width=6, method="label",
            ))
            txt = txt.set_start(start).set_end(min(end, duration)).set_position(("center", CAPTION_Y))
            caption_clips.append(txt)
    except Exception:
        txt = TextClip(scene["text"], **_text_clip_kwargs(
            fontsize=90, color="white", stroke_color="black",
            stroke_width=5, method="caption", size=(W * 0.85, None),
        ))
        caption_clips = [txt.set_duration(duration).set_position(("center", CAPTION_Y))]

    sfx_path = os.path.join(WORKDIR, f"{scene_id}_sfx.mp3")
    sfx_downloaded = _freesound_download(scene.get("sfx", "whoosh"), freesound_key, sfx_path)
    audio_tracks = [voice_clip]
    if sfx_downloaded:
        audio_tracks.append(AudioFileClip(sfx_downloaded).volumex(0.35).set_start(0))
    combined_audio = CompositeAudioClip(audio_tracks).set_duration(duration)

    scene_clip = CompositeVideoClip([bg, *caption_clips], size=(W, H)).set_duration(duration)
    scene_clip = scene_clip.set_audio(combined_audio)
    return scene_clip, duration


def build_video(script: dict, pexels_key: str = "", freesound_key: str = "") -> str:
    set_status("video_agent", "running", "assembling scenes")
    try:
        os.makedirs(WORKDIR, exist_ok=True)
        satisfying_src = _get_satisfying_background(pexels_key)

        clips = []
        src_dur = satisfying_src.duration
        cursor = random.uniform(0, max(src_dur - 1, 0)) if src_dur and src_dur > 1 else 0.0
        for i, scene in enumerate(script["scenes"]):
            clip, duration = _build_scene_clip(scene, freesound_key, i, satisfying_src, cursor)
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