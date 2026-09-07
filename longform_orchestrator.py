"""
Long-Form Orchestrator
-----------------------
Runs the long-form pipeline once: longform_script_agent -> longform_
video_agent -> upload_agent -> discord_agent.

Unlike orchestrator.py (shorts), this is meant to run locally only —
generating and encoding 60-100 minutes of video isn't practical on
GitHub Actions' free shared runners (see README section 6). Run it
manually, or wire it into your own local cron/launchd schedule for
"1 long-form video/day."

Uploads straight to YouTube as a regular (non-Shorts) video and
publishes immediately — there's no CI "test vs real" distinction here
the way there is for orchestrator.py, since local IS the only place
this ever runs.
"""
import os
import sys

from dotenv import load_dotenv

sys.path.append(os.path.dirname(__file__))
sys.path.append(os.path.join(os.path.dirname(__file__), "agents"))
load_dotenv(os.path.join(os.path.dirname(__file__), "config", ".env"))

from agents.longform_script_agent import generate_longform_script
from agents.longform_video_agent import build_longform_video
from agents.upload_agent import upload_video
from agents.discord_agent import notify_upload, notify_pipeline_failure
from status_store import reset, set_status

LONGFORM_HASHTAGS = "#selfimprovement #motivation #minecraft"


def run_longform_pipeline(target_minutes: int = None, privacy_status: str = "public"):
    reset()
    try:
        script = generate_longform_script(
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2"),
            target_minutes=target_minutes,
        )

        video_path = build_longform_video(script)

        max_title_len = 100 - len(LONGFORM_HASHTAGS) - 1
        youtube_title = f"{script['title'][:max_title_len]} {LONGFORM_HASHTAGS}"[:100]

        url = upload_video(
            video_path,
            title=youtube_title,
            description=(
                f"A motivational speech on {script['theme']}, for anyone who "
                f"needs it today.\n\n{LONGFORM_HASHTAGS}"
            ),
            tags=["motivation", "self improvement", "minecraft parkour", "discipline"],
            privacy_status=privacy_status,
            is_short=False,
        )

        webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
        if webhook:
            notify_upload(webhook, title=script["title"], url=url)

        print(f"Long-form pipeline complete: {url}")
        return url

    except Exception:
        import traceback
        error_text = traceback.format_exc()
        traceback.print_exc()
        set_status("longform_orchestrator", "error", error_text[-500:])
        notify_pipeline_failure(os.getenv("DISCORD_WEBHOOK_URL", ""), error_text)
        raise


if __name__ == "__main__":
    run_longform_pipeline()
