"""
Orchestrator
------------
Runs the full pipeline once, in order:
  script_agent -> video_agent -> upload_agent -> discord_agent
"""
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

sys.path.append(os.path.dirname(__file__))
load_dotenv(os.path.join(os.path.dirname(__file__), "config", ".env"))

from agents.script_agent import generate_script
from agents.video_agent import build_video
from agents.upload_agent import upload_video
from agents.discord_agent import notify_upload
from status_store import reset, set_status
from daily_queue import claim_next_slot, mark_uploaded, mark_error

PUBLISH_DELAY_HOURS = float(os.getenv("PUBLISH_DELAY_HOURS", "5"))
IS_CI = os.getenv("GITHUB_ACTIONS") == "true"


def run_pipeline():
    reset()
    slot_index = claim_next_slot()
    try:
        script = generate_script(ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2"))

        video_path = build_video(
            script,
            pexels_key=os.getenv("PEXELS_API_KEY", ""),
        )

        if not IS_CI:
            # Local runs stop here — no YouTube upload, no Discord ping.
            # Just the rendered file in data/output/ for review.
            print(f"Pipeline complete (LOCAL TEST — not uploaded): {video_path}")
            return video_path

        publish_at = None
        if PUBLISH_DELAY_HOURS > 0:
            publish_dt = datetime.now(timezone.utc) + timedelta(hours=PUBLISH_DELAY_HOURS)
            publish_at = publish_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        hashtags = "#story #storytime #storytelling"
        max_title_len = 100 - len(hashtags) - 1
        youtube_title = f"{script['title'][:max_title_len]} {hashtags}"[:100]

        url = upload_video(
            video_path,
            title=youtube_title,
            description="A story time short. Follow for more.",
            publish_at=publish_at,
        )

        webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
        if webhook:
            if publish_at:
                notify_upload(webhook, title=f"{script['title']} (scheduled {publish_at} UTC)", url=url)
            else:
                notify_upload(webhook, title=script["title"], url=url)

        mark_uploaded(slot_index, script["title"][:100], url, publish_at or "")

        if publish_at:
            print(f"Pipeline complete: {url} (scheduled for {publish_at})")
        else:
            print(f"Pipeline complete: {url}")
        return url

    except Exception:
        traceback.print_exc()
        set_status("orchestrator", "error", traceback.format_exc()[-500:])
        mark_error(slot_index)
        raise


if __name__ == "__main__":
    run_pipeline()
