"""
Video Agent
-----------
Takes the script JSON from script_agent and produces a finished
9:16 vertical video with:
  - a continuously varying background made of random 15s clips (each
    sped up 1.2x) chopped from whatever files are in data/backgrounds/,
    switching clips/scenes every ~12.5s regardless of sentence
    boundaries, for constant visual novelty
  - an optional short intro stinger sound from data/intro_sound/,
    played once at the very start
  - free Microsoft TTS narration (edge-tts), normalized louder, with
    leading/trailing silence trimmed for no dead gaps between sentences
  - large, bold, centered word-by-word captions
  - free transition/impact SFX (Freesound)

Everything here is free-tier: Pexels API key is free, Freesound API key
is free, edge-tts and faster-whisper are open-source/local.
"""
import asyncio
import os
import random
import re
import subprocess
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
BACKGROUNDS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "backgrounds")
INTRO_SOUND_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "intro_sound")

CLIP_SEGMENT_SECONDS = 15.0
CLIP_SPEED = 1.2
INTRO_MAX_SECONDS = 1.0


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
    """Scales DOWN to fit entirely within the frame, no cropping —
    letterboxes with black bars if the aspect ratio doesn't match."""
    scale = min(target_w / clip.w, target_h / clip.h)
    new_w, new_h = int(clip.w * scale), int(clip.h * scale)
    resized = clip.fx(vfx.resize, newsize=(new_w, new_h))
    bg = ColorClip(size=(target_w, target_h), color=(0, 0, 0)).set_duration(clip.duration)
    return CompositeVideoClip([bg, resized.set_position("center")], size=(target_w, target_h))


def _get_sample_aspect_ratio(path: str) -> float:
    """
    Some video files use non-square pixels ("anamorphic" encoding) —
    the raw stored pixel grid isn't the same shape as how the video is
    meant to be displayed. Players like QuickTime/Finder/VS Code apply
    this stretch automatically; ffmpeg/moviepy do NOT unless told to,
    which is what was silently distorting the aspect ratio. This reads
    the correction factor so it can be applied explicitly.
    """
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=sample_aspect_ratio",
             "-of", "csv=s=x:p=0", path],
            capture_output=True, text=True, timeout=15,
        )
        val = result.stdout.strip()
        if not val or val in ("0:1", "1:1", "N/A", ""):
            return 1.0
        num, den = val.split(":")
        num, den = int(num), int(den)
        return num / den if den else 1.0
    except Exception:
        return 1.0


def _load_background_clip(path: str):
    """Loads a background file and corrects for non-square pixels if
    present, so the resulting clip's dimensions match how the video
    actually looks when played normally, not the raw distorted
    pixel grid."""
    clip = VideoFileClip(path).without_audio()
    sar = _get_sample_aspect_ratio(path)
    if abs(sar - 1.0) > 0.01:
        corrected_w = max(2, int(round(clip.w * sar)))
        if corrected_w % 2:
            corrected_w += 1
        clip = clip.fx(vfx.resize, newsize=(corrected_w, clip.h))
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


def _list_local_backgrounds():
    if not os.path.isdir(BACKGROUNDS_DIR):
        return []
    return [
        os.path.join(BACKGROUNDS_DIR, f)
        for f in os.listdir(BACKGROUNDS_DIR)
        if f.lower().endswith((".mp4", ".mov", ".m4v"))
    ]


def _get_canvas_size():
    """
    Uses the ACTUAL (pixel-aspect-corrected) resolution of your first
    background clip as the video's canvas size. Falls back to a
    standard 1080x1920 canvas only when there's no local background
    file to read a real size from.
    """
    files = _list_local_backgrounds()
    if files:
        clip = _load_background_clip(files[0])
        size = clip.size
        clip.close()
        return size
    return (1080, 1920)


def _random_background_segment(canvas_w, canvas_h):
    """Picks a random background file, a random 15s window from it
    (or the whole clip if shorter), speeds it up 1.2x. Pixel-aspect
    correction is applied on load. If the corrected clip already
    matches the canvas size exactly, it's used untouched from there —
    only a genuine size mismatch (e.g. mixing in a different file
    later) triggers any further fitting."""
    files = _list_local_backgrounds()
    path = random.choice(files)
    src = _load_background_clip(path)

    if src.duration <= CLIP_SEGMENT_SECONDS:
        segment = src
    else:
        start = random.uniform(0, src.duration - CLIP_SEGMENT_SECONDS)
        segment = src.subclip(start, start + CLIP_SEGMENT_SECONDS)
    segment = segment.fx(vfx.speedx, CLIP_SPEED)

    if segment.size == [canvas_w, canvas_h] or segment.size == (canvas_w, canvas_h):
        return segment
    return _contain_fit(segment, canvas_w, canvas_h)


def _build_random_clip_timeline(total_duration: float, canvas_w, canvas_h):
    segments = []
    covered = 0.0
    while covered < total_duration:
        seg = _random_background_segment(canvas_w, canvas_h)
        segments.append(seg)
        covered += seg.duration
    timeline = concatenate_videoclips(segments, method="compose")
    return timeline.subclip(0, total_duration)


def _get_pexels_fallback_background(pexels_key: str, total_duration: float):
    query = os.getenv("SATISFYING_QUERY", "soap cutting satisfying asmr")
    video_url = _pexels_search_video(query, pexels_key) if pexels_key else None
    if video_url:
        path = os.path.join(WORKDIR, f"satisfying_{uuid.uuid4().hex[:6]}.mp4")
        _download(video_url, path)
        clip = VideoFileClip(path).without_audio()
        clip = _contain_fit(clip, W, H)
        return clip.fx(vfx.loop, duration=total_duration).subclip(0, total_duration)

    from PIL import Image
    import numpy as np
    color = tuple(random.randint(30, 70) for _ in range(3))
    img_path = os.path.join(WORKDIR, f"fallback_{uuid.uuid4().hex[:6]}.png")
    Image.fromarray(np.full((H, W, 3), color, dtype=np.uint8)).save(img_path)
    return ImageClip(img_path).set_duration(total_duration)


def _build_background(total_duration: float, pexels_key: str, canvas_w, canvas_h):
    os.makedirs(WORKDIR, exist_ok=True)
    if _list_local_backgrounds():
        return _build_random_clip_timeline(total_duration, canvas_w, canvas_h)
    return _get_pexels_fallback_background(pexels_key, total_duration)


def _get_intro_audio():
    if not os.path.isdir(INTRO_SOUND_DIR):
        return None
    files = [f for f in os.listdir(INTRO_SOUND_DIR) if f.lower().endswith((".mp3", ".wav", ".m4a", ".aac"))]
    if not files:
        return None
    path = os.path.join(INTRO_SOUND_DIR, random.choice(files))
    clip = AudioFileClip(path)
    if clip.duration > INTRO_MAX_SECONDS:
        clip = clip.subclip(0, INTRO_MAX_SECONDS)
    return clip


def _build_scene_content(scene: dict, freesound_key: str, idx: int):
    os.makedirs(WORKDIR, exist_ok=True)
    scene_id = f"scene_{idx}_{uuid.uuid4().hex[:6]}"

    audio_path = os.path.join(WORKDIR, f"{scene_id}_voice.mp3")
    asyncio.run(_tts(scene["text"], audio_path))
    voice_clip = AudioFileClip(audio_path)
    voice_clip = voice_clip.fx(afx.audio_normalize)
    voice_clip = _trim_silence(voice_clip)
    duration = voice_clip.duration

    word_captions = []
    try:
        words = _whisper_captions(audio_path)
        for word, start, end in words:
            word_captions.append((word, start, min(end, duration)))
    except Exception:
        word_captions = None

    sfx_path = os.path.join(WORKDIR, f"{scene_id}_sfx.mp3")
    sfx_downloaded = _freesound_download(scene.get("sfx", "whoosh"), freesound_key, sfx_path)
    audio_tracks = [voice_clip]
    if sfx_downloaded:
        audio_tracks.append(AudioFileClip(sfx_downloaded).volumex(0.35).set_start(0))
    combined_audio = CompositeAudioClip(audio_tracks).set_duration(duration)

    return {
        "text": scene["text"],
        "duration": duration,
        "audio": combined_audio,
        "word_captions": word_captions,
    }


def build_video(script: dict, pexels_key: str = "", freesound_key: str = "") -> str:
    set_status("video_agent", "running", "assembling scenes")
    try:
        os.makedirs(WORKDIR, exist_ok=True)

        canvas_w, canvas_h = _get_canvas_size()
        caption_y = canvas_h * 0.46
        # Font sizes scale with the actual canvas width instead of using
        # a fixed pixel size — a fixed size looked oversized once the
        # canvas became your video's real (narrower) width instead of
        # the old fixed 1080px assumption.
        word_fontsize = max(20, int(canvas_w * 0.12))
        fallback_fontsize = max(16, int(canvas_w * 0.085))
        stroke_w_word = max(2, int(canvas_w * 0.0055))
        stroke_w_fallback = max(2, int(canvas_w * 0.0045))

        intro_audio = _get_intro_audio()
        intro_duration = intro_audio.duration if intro_audio else 0.0

        scene_contents = []
        cursor = intro_duration
        for i, scene in enumerate(script["scenes"]):
            content = _build_scene_content(scene, freesound_key, i)
            content["offset"] = cursor
            scene_contents.append(content)
            cursor += content["duration"]
        total_duration = cursor

        background = _build_background(total_duration, pexels_key, canvas_w, canvas_h)

        caption_clips = []
        audio_tracks = []
        if intro_audio:
            audio_tracks.append(intro_audio.volumex(0.9).set_start(0))

        for content in scene_contents:
            offset = content["offset"]
            audio_tracks.append(content["audio"].set_start(offset))

            if content["word_captions"]:
                for word, local_start, local_end in content["word_captions"]:
                    txt = TextClip(word, **_text_clip_kwargs(
                        fontsize=word_fontsize, color="white", stroke_color="black",
                        stroke_width=stroke_w_word, method="label",
                    ))
                    txt = txt.set_start(offset + local_start).set_end(offset + local_end)
                    txt = txt.set_position(("center", caption_y))
                    caption_clips.append(txt)
            else:
                txt = TextClip(content["text"], **_text_clip_kwargs(
                    fontsize=fallback_fontsize, color="white", stroke_color="black",
                    stroke_width=stroke_w_fallback, method="caption", size=(canvas_w * 0.85, None),
                ))
                txt = txt.set_start(offset).set_end(offset + content["duration"])
                txt = txt.set_position(("center", caption_y))
                caption_clips.append(txt)

        final_audio = CompositeAudioClip(audio_tracks).set_duration(total_duration)
        final_video = CompositeVideoClip([background, *caption_clips], size=(canvas_w, canvas_h)).set_duration(total_duration)
        final = final_video.set_audio(final_audio)

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