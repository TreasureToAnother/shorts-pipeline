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

PUBLISH_DELAY_HOURS = float(os.getenv("PUBLISH_DELAY_HOURS", "5"))


def run_pipeline():
    reset()
    try:
        script = generate_script(ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2"))

        video_path = build_video(
            script,
            pexels_key=os.getenv("PEXELS_API_KEY", ""),
            freesound_key=os.getenv("FREESOUND_API_KEY", ""),
        )

        publish_at = None
        if PUBLISH_DELAY_HOURS > 0:
            publish_dt = datetime.now(timezone.utc) + timedelta(hours=PUBLISH_DELAY_HOURS)
            publish_at = publish_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        url = upload_video(
            video_path,
            title=script["title"][:100],
            description="A story time short. Follow for more.",
            publish_at=publish_at,
        )

        webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
        if webhook:
            if publish_at:
                notify_upload(webhook, title=f"{script['title']} (scheduled {publish_at} UTC)", url=url)
            else:
                notify_upload(webhook, title=script["title"], url=url)

        print(f"Pipeline complete: {url}" + (f" (scheduled for {publish_at})" if publish_at else ""))
        return url

    except Exception:
        traceback.print_exc()
        set_status("orchestrator", "error", traceback.format_exc()[-500:])
        raise


if __name__ == "__main__":
    run_pipeline()