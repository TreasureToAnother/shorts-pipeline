"""
Long-Form Video Agent
----------------------
Takes the chaptered script from longform_script_agent.py and produces
a 1920x1080 landscape video:
  - background b-roll pulled from Pexels (free API key), searched with
    queries matched to the general's era (ancient ruins, cannons, tall
    ships, tanks...), cut into short random segments for constant
    visual change
  - free Microsoft TTS narration per chapter (edge-tts) in a deep
    documentary voice at normal pace — no speed-up here, unlike the
    shorts
  - a brief title card at the start of each chapter (the general's
    name, then each battle) instead of per-word captions
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

import requests

sys.path.append("..")
sys.path.append(os.path.dirname(__file__))
from status_store import set_status
from video_agent import (
    _download, _get_background_music, _load_background_clip,
    _text_clip_kwargs, _trim_silence, _tts,
)

from PIL import Image as _PILImage
if not hasattr(_PILImage, "ANTIALIAS"):
    _PILImage.ANTIALIAS = _PILImage.LANCZOS

from moviepy.editor import (
    AudioFileClip, CompositeAudioClip, CompositeVideoClip,
    TextClip, concatenate_videoclips, vfx,
)
from moviepy.audio.fx.all import audio_normalize

W, H = 1920, 1080
WORKDIR = os.path.join(os.path.dirname(__file__), "..", "data", "work")

SEGMENT_SECONDS = 8.0   # stock clips are short; switch often for constant visual change
FOOTAGE_CLIPS = 14      # distinct Pexels clips downloaded per video
CHAPTER_CARD_SECONDS = 4.0
NARRATOR_VOICE = os.getenv("LONGFORM_VOICE_NAME", "en-US-ChristopherNeural")

ERA_QUERIES = {
    "ancient": ["ancient ruins", "roman ruins", "desert landscape", "sword", "horses running",
                "stone fortress", "campfire night", "marble statue"],
    "eastern": ["japanese temple", "chinese great wall", "misty mountains", "horse riding",
                "samurai", "bow and arrow", "fortress wall", "grassland steppe"],
    "gunpowder": ["cannon", "historic fort", "musket", "horses galloping", "old castle",
                  "battlefield reenactment", "cavalry", "foggy field"],
    "naval": ["sailing ship", "tall ship", "stormy sea", "ocean waves", "ship cannon",
              "harbor fog", "lighthouse storm", "navy ship"],
    "modern": ["tank", "military", "soldiers marching", "war memorial", "military aircraft",
               "barbed wire", "army truck", "binoculars"],
}
GENERIC_QUERIES = ["old map", "chess", "smoke", "fire flames", "dramatic clouds", "candle dark"]


def _pexels_landscape_videos(query: str, api_key: str) -> list:
    r = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": api_key},
        params={"query": query, "per_page": 15, "orientation": "landscape"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("videos", [])


def _best_file_link(video: dict):
    """Prefers an exact 1920x1080 file (no resizing needed), else the
    smallest file that's still at least 1280 wide — Pexels' biggest
    files are often 4K, which only slows downloading and rendering."""
    files = [f for f in video.get("video_files", []) if f.get("width") and f.get("height")]
    exact = [f for f in files if f["width"] == W and f["height"] == H]
    if exact:
        return exact[0]["link"]
    usable = sorted((f for f in files if f["width"] >= 1280), key=lambda f: f["width"])
    return usable[0]["link"] if usable else None


def _download_footage(era: str, api_key: str) -> list:
    if not api_key:
        raise RuntimeError("PEXELS_API_KEY is required for long-form background footage")
    queries = ERA_QUERIES.get(era, []) + GENERIC_QUERIES
    random.shuffle(queries)
    seen, paths = set(), []
    for query in queries:
        if len(paths) >= FOOTAGE_CLIPS:
            break
        try:
            videos = _pexels_landscape_videos(query, api_key)
        except Exception:
            continue
        random.shuffle(videos)
        for video in videos[:2]:  # max 2 per query, so no single query dominates
            if video["id"] in seen or video.get("duration", 0) < 4:
                continue
            link = _best_file_link(video)
            if not link:
                continue
            path = os.path.join(WORKDIR, f"pexels_{video['id']}.mp4")
            try:
                if not os.path.exists(path):
                    _download(link, path)
            except Exception:
                continue
            seen.add(video["id"])
            paths.append(path)
    if not paths:
        raise RuntimeError("Couldn't download any Pexels footage")
    return paths


def _cover_fit(clip, target_w, target_h):
    """Scales to fill the whole frame and center-crops the overflow —
    unlike the shorts' letterboxing, black bars look wrong on stock b-roll."""
    scale = max(target_w / clip.w, target_h / clip.h)
    new_w, new_h = int(round(clip.w * scale)), int(round(clip.h * scale))
    resized = clip.fx(vfx.resize, newsize=(new_w, new_h))
    return resized.fx(vfx.crop, x_center=new_w / 2, y_center=new_h / 2, width=target_w, height=target_h)


def _build_background_timeline(footage_paths: list, total_duration: float):
    # Each source is opened once and subclipped many times, instead of
    # spawning a new ffmpeg reader for every one of ~75 segments.
    sources = []
    for path in footage_paths:
        try:
            clip = _load_background_clip(path)
        except Exception:
            continue
        if tuple(clip.size) != (W, H):
            clip = _cover_fit(clip, W, H)
        sources.append(clip)
    if not sources:
        raise RuntimeError("None of the downloaded Pexels clips could be opened")

    segments, covered, last = [], 0.0, None
    while covered < total_duration:
        choices = [s for s in sources if s is not last] or sources
        src = random.choice(choices)
        last = src
        if src.duration <= SEGMENT_SECONDS:
            seg = src
        else:
            start = random.uniform(0, src.duration - SEGMENT_SECONDS)
            seg = src.subclip(start, start + SEGMENT_SECONDS)
        segments.append(seg)
        covered += seg.duration
    timeline = concatenate_videoclips(segments, method="chain")
    return timeline.subclip(0, total_duration)


def _build_chapter_audio(chapter: dict, idx: int):
    os.makedirs(WORKDIR, exist_ok=True)
    chapter_id = f"longform_ch{idx}_{uuid.uuid4().hex[:6]}"
    audio_path = os.path.join(WORKDIR, f"{chapter_id}_voice.mp3")
    asyncio.run(_tts(chapter["text"], audio_path, voice=NARRATOR_VOICE, pitch="-5Hz", rate="+0%"))
    voice_clip = AudioFileClip(audio_path)
    voice_clip = voice_clip.fx(audio_normalize)
    voice_clip = _trim_silence(voice_clip)
    return voice_clip


def build_longform_video(script: dict, pexels_key: str = "") -> str:
    set_status("longform_video_agent", "running", "assembling chapters")
    try:
        os.makedirs(WORKDIR, exist_ok=True)
        canvas_w, canvas_h = W, H
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

        footage = _download_footage(script.get("era", ""), pexels_key)
        background = _build_background_timeline(footage, total_duration)
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
            txt = txt.crossfadein(0.6).crossfadeout(0.6)
            txt = txt.set_position(("center", canvas_h * 0.82))
            card_clips.append(txt)

        final_audio = CompositeAudioClip(audio_tracks).set_duration(total_duration)
        final_video = CompositeVideoClip([background, *card_clips], size=(canvas_w, canvas_h)).set_duration(total_duration)
        final = final_video.set_audio(final_audio)

        out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "output")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"longform_{uuid.uuid4().hex[:8]}.mp4")
        final.write_videofile(
            out_path, fps=30, codec="libx264", audio_codec="aac", threads=os.cpu_count() or 4,
            preset="veryfast", logger=None, ffmpeg_params=["-movflags", "+faststart"],
        )
        set_status("longform_video_agent", "done", out_path)
        return out_path
    except Exception as e:
        set_status("longform_video_agent", "error", str(e))
        raise


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", ".env"))
    from longform_script_agent import generate_longform_script
    s = generate_longform_script(target_minutes=2)  # short run for a quick local test
    path = build_longform_video(s, pexels_key=os.getenv("PEXELS_API_KEY", ""))
    print("Video written to:", path)
