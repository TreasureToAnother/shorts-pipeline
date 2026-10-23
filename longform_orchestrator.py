"""
Long-Form Orchestrator
-----------------------
Runs the long-form pipeline once: longform_script_agent -> longform_
video_agent -> upload_agent -> discord_agent.

A ~10 minute military-history video — one general, three battles, the
tactic behind each. Runs weekly on GitHub Actions (see
.github/workflows/longform.yml), and uploads to YouTube as a regular
(non-Shorts) video, published immediately.

Like orchestrator.py, local runs stop after rendering — no upload, no
Discord ping — so you can review the file in data/output/ first.
"""
import os
import sys

from dotenv import load_dotenv

sys.path.append(os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), "agents"))
load_dotenv(os.path.join(os.path.dirname(__file__), "config", ".env"))

from agents.longform_script_agent import generate_longform_script
from agents.longform_video_agent import build_longform_video
from agents.thumbnail_agent import generate_thumbnail
from agents.upload_agent import upload_video
from agents.discord_agent import notify_upload, notify_pipeline_failure
from status_store import reset, set_status

IS_CI = os.getenv("GITHUB_ACTIONS") == "true"
LONGFORM_HASHTAGS = "#history #militaryhistory #artofwar"
CREDITS = "Narration and illustrations generated with AI."


def run_longform_pipeline(target_minutes: int = None, privacy_status: str = "public"):
    reset()
    # A 7B model, not the shorts' llama3.2 — the 3B model swaps army
    # sizes and invents events even with the Wikipedia reference.
    ollama_model = os.getenv("LONGFORM_OLLAMA_MODEL", "qwen2.5:7b")
    try:
        script = generate_longform_script(ollama_model=ollama_model, target_minutes=target_minutes)

        video_path = build_longform_video(script, ollama_model=ollama_model)
        thumbnail_path = generate_thumbnail(script, video_path, ollama_model=ollama_model)

        if not IS_CI:
            print(f"Long-form pipeline complete (LOCAL TEST — not uploaded): {video_path}")
            if thumbnail_path:
                print(f"Thumbnail: {thumbnail_path}")
            return video_path

        battles = "\n".join(f"- {c['topic']}" for c in script["chapters"][1:-1])
        url = upload_video(
            video_path,
            title=script["title"],
            description=(
                f"Three battles, three tactics: how {script['general']} won.\n\n"
                f"{battles}\n\n"
                f"A new general every week — subscribe so you don't miss one.\n\n"
                f"{CREDITS}\n\n{LONGFORM_HASHTAGS}"
            ),
            tags=["military history", "art of war", "military tactics", "history",
                  "battles", script["general"]],
            privacy_status=privacy_status,
            is_short=False,
            category_id="27",  # Education
            thumbnail_path=thumbnail_path,
        )

        webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
        if webhook:
            notify_upload(webhook, title=script["title"], url=url, thumbnail_path=thumbnail_path or "")

        print(f"Long-form pipeline complete: {url}")
        return url

    except Exception:
        import traceback
        error_text = traceback.format_exc()
        traceback.print_exc()
        set_status("longform_orchestrator", "error", error_text[-500:])
        if IS_CI:
            notify_pipeline_failure(os.getenv("DISCORD_WEBHOOK_URL", ""), error_text)
        raise


if __name__ == "__main__":
    minutes = int(sys.argv[1]) if len(sys.argv) > 1 else None  # e.g. `python longform_orchestrator.py 2` for a quick test
    run_longform_pipeline(target_minutes=minutes)
