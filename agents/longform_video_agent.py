"""
Long-Form Video Agent
----------------------
Takes the chaptered script from longform_script_agent.py and produces
a 1920x1080 landscape video:
  - background sampled/looped from data/longform_backgrounds/ (your
    own Minecraft parkour clips — that folder is gitignored, this
    pipeline only ever runs locally, never in CI)
  - free Microsoft TTS narration per chapter (edge-tts) at normal
    pace — no speed-up here, unlike the shorts; this genre is meant
    to feel measured, not snappy
  - a brief chapter-title card at the start of each chapter instead
    of per-word captions — transcribing 60-100 minutes of audio with
    Whisper just to flash captions nobody reads at that length isn't
    worth the compute
  - optional background music from data/background_music/ (same
    folder the shorts pipeline uses), looped quietly underneath

No SFX, no overlay image, no intro stinger — those are short-form-
specific touches that don't fit this format.
"""
import asyncio
import os
import random
import sys
import uuid

sys.path.append("..")
sys.path.append(os.path.dirname(__file__))
from status_store import set_status
from video_agent import (
    CAPTION_FONT, _contain_fit, _get_background_music, _load_background_clip,
    _text_clip_kwargs, _trim_silence, _tts,
)

from PIL import Image as _PILImage
if not hasattr(_PILImage, "ANTIALIAS"):
    _PILImage.ANTIALIAS = _PILImage.LANCZOS

from moviepy.editor import (
    AudioFileClip, CompositeAudioClip, CompositeVideoClip,
    TextClip, concatenate_videoclips,
)
from moviepy.audio.fx.all import audio_normalize

W, H = 1920, 1080
WORKDIR = os.path.join(os.path.dirname(__file__), "..", "data", "work")
LONGFORM_BACKGROUNDS_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "longform_backgrounds")

CLIP_SEGMENT_SECONDS = 60.0  # longer chunks than shorts — a 90min video at 15s/segment would be ~360 clips
CHAPTER_CARD_SECONDS = 4.0


def _list_longform_backgrounds():
    if not os.path.isdir(LONGFORM_BACKGROUNDS_DIR):
        return []
    return [
        os.path.join(LONGFORM_BACKGROUNDS_DIR, f)
        for f in os.listdir(LONGFORM_BACKGROUNDS_DIR)
        if f.lower().endswith((".mp4", ".mov", ".m4v"))
    ]


def _get_canvas_size():
    files = _list_longform_backgrounds()
    if files:
        clip = _load_background_clip(files[0])
        size = clip.size
        clip.close()
        return size
    return (W, H)


def _random_background_segment(canvas_w, canvas_h):
    files = _list_longform_backgrounds()
    path = random.choice(files)
    src = _load_background_clip(path)
    if src.duration <= CLIP_SEGMENT_SECONDS:
        segment = src
    else:
        start = random.uniform(0, src.duration - CLIP_SEGMENT_SECONDS)
        segment = src.subclip(start, start + CLIP_SEGMENT_SECONDS)
    if segment.size == [canvas_w, canvas_h] or segment.size == (canvas_w, canvas_h):
        return segment
    return _contain_fit(segment, canvas_w, canvas_h)


def _build_background_timeline(total_duration, canvas_w, canvas_h):
    segments = []
    covered = 0.0
    while covered < total_duration:
        seg = _random_background_segment(canvas_w, canvas_h)
        segments.append(seg)
        covered += seg.duration
    timeline = concatenate_videoclips(segments, method="compose")
    return timeline.subclip(0, total_duration)


def _build_chapter_audio(chapter: dict, idx: int):
    os.makedirs(WORKDIR, exist_ok=True)
    chapter_id = f"longform_ch{idx}_{uuid.uuid4().hex[:6]}"
    audio_path = os.path.join(WORKDIR, f"{chapter_id}_voice.mp3")
    asyncio.run(_tts(chapter["text"], audio_path, rate="+0%"))
    voice_clip = AudioFileClip(audio_path)
    voice_clip = voice_clip.fx(audio_normalize)
    voice_clip = _trim_silence(voice_clip)
    return voice_clip


def build_longform_video(script: dict) -> str:
    set_status("longform_video_agent", "running", "assembling chapters")
    try:
        if not _list_longform_backgrounds():
            raise RuntimeError(
                "No clips in data/longform_backgrounds/ — add some Minecraft "
                "parkour (or similar) .mp4 files there before running this."
            )
        os.makedirs(WORKDIR, exist_ok=True)

        canvas_w, canvas_h = _get_canvas_size()
        title_fontsize = max(28, int(canvas_w * 0.032))
        stroke_w = max(2, int(canvas_w * 0.0018))

        chapter_contents = []
        cursor = 0.0
        for i, chapter in enumerate(script["chapters"], start=1):
            voice_clip = _build_chapter_audio(chapter, i)
            duration = voice_clip.duration
            chapter_contents.append({
                "topic": chapter["topic"],
                "duration": duration,
                "audio": voice_clip,
                "offset": cursor,
            })
            cursor += duration
        total_duration = cursor

        background = _build_background_timeline(total_duration, canvas_w, canvas_h)
        music = _get_background_music(total_duration)

        audio_tracks = [c["audio"].set_start(c["offset"]) for c in chapter_contents]
        if music:
            audio_tracks.append(music.set_start(0))

        card_clips = []
        for c in chapter_contents:
            card_duration = min(CHAPTER_CARD_SECONDS, c["duration"])
            txt = TextClip(c["topic"], **_text_clip_kwargs(
                fontsize=title_fontsize, color="white", stroke_color="black",
                stroke_width=stroke_w, method="caption", size=(canvas_w * 0.7, None),
            ))
            txt = txt.set_start(c["offset"]).set_duration(card_duration)
            txt = txt.fadein(0.6).fadeout(0.6)
            txt = txt.set_position(("center", canvas_h * 0.82))
            card_clips.append(txt)

        final_audio = CompositeAudioClip(audio_tracks).set_duration(total_duration)
        final_video = CompositeVideoClip([background, *card_clips], size=(canvas_w, canvas_h)).set_duration(total_duration)
        final = final_video.set_audio(final_audio)

        out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "output")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"longform_{uuid.uuid4().hex[:8]}.mp4")
        final.write_videofile(
            out_path, fps=30, codec="libx264", audio_codec="aac", threads=8,
            preset="fast", logger=None, ffmpeg_params=["-movflags", "+faststart"],
        )
        set_status("longform_video_agent", "done", out_path)
        return out_path
    except Exception as e:
        set_status("longform_video_agent", "error", str(e))
        raise


if __name__ == "__main__":
    from longform_script_agent import generate_longform_script
    s = generate_longform_script(target_minutes=8)  # short run for a quick local test
    path = build_longform_video(s)
    print("Video written to:", path)
