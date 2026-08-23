"""
Orchestrator
------------
Runs the full pipeline once, in order:
  script_agent -> video_agent -> upload_agent -> discord_agent

Each step updates data/status.json (via status_store) so the
dashboard's node graph reflects live progress. Designed to be
triggered by cron / GitHub Actions for scheduled uploads, or run
manually for testing.
"""
import os
import sys
import traceback

from dotenv import load_dotenv

sys.path.append(os.path.dirname(__file__))
load_dotenv(os.path.join(os.path.dirname(__file__), "config", ".env"))

from agents.script_agent import generate_script
from agents.video_agent import build_video
from agents.upload_agent import upload_video
from agents.discord_agent import notify_upload
from status_store import reset, set_status


def run_pipeline():
    reset()
    try:
        script = generate_script(ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2"))

        video_path = build_video(
            script,
            pexels_key=os.getenv("PEXELS_API_KEY", ""),
            freesound_key=os.getenv("FREESOUND_API_KEY", ""),
        )

        url = upload_video(
            video_path,
            title=script["title"][:100],
            description="Daily hidden history fact. Follow for more.",
        )

        webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
        if webhook:
            notify_upload(webhook, title=script["title"], url=url)

        print(f"Pipeline complete: {url}")
        return url

    except Exception:
        traceback.print_exc()
        set_status("orchestrator", "error", traceback.format_exc()[-500:])
        raise


if __name__ == "__main__":
    run_pipeline()
