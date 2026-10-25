"""
Long-Form Orchestrator
-----------------------
Runs the long-form pipeline once: longform_script_agent -> longform_
video_agent -> upload_agent -> discord_agent.

A ~10 minute military-history video — one general, three battles, the
tactic behind each. Runs weekly on GitHub Actions in two steps:
  - Saturday (.github/workflows/longform-script.yml): `--script-only`
    writes the script with a large model on Cloudflare Workers AI and
    saves it to data/longform_next.json
  - Sunday (.github/workflows/longform.yml): renders that script —
    illustrations, narration, thumbnail — and uploads it as a regular
    (non-Shorts) video, published immediately
Each day gets its own free Workers AI allowance this way. If Sunday finds
no saved script it stops and alerts Discord rather than writing one with
the local model, whose scripts proved too inaccurate to publish. (Local
test runs still fall back to it, so they work without Cloudflare.)

Like orchestrator.py, local runs stop after rendering — no upload, no
Discord ping — so you can review the file in data/output/ first.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

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
# Long-form uploads stay off until the format is signed off: until then a
# run (scheduled or manual) renders the video and the workflow keeps it as
# a downloadable artifact instead of putting it on YouTube. Turn on by
# setting the repo variable LONGFORM_UPLOADS to "on".
UPLOADS_ON = os.getenv("LONGFORM_UPLOADS", "").strip().lower() == "on"
LONGFORM_HASHTAGS = "#history #militaryhistory #artofwar"
CREDITS = "Narration and illustrations generated with AI."
NEXT_SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "data", "longform_next.json")
NEXT_SCRIPT_MAX_AGE = timedelta(days=4)


def write_next_script(target_minutes: int = None) -> str:
    """Saturday's step: write the script and save it for Sunday."""
    ollama_model = os.getenv("LONGFORM_OLLAMA_MODEL", "qwen2.5:7b")
    script = generate_longform_script(ollama_model=ollama_model, target_minutes=target_minutes)
    script["created_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(NEXT_SCRIPT_PATH, "w") as f:
        json.dump(script, f, indent=1, ensure_ascii=False)
    words = sum(len(c["text"].split()) for c in script["chapters"])
    print(f"Script saved for {script['general']}: {words} words, model {script['script_model']}")
    return NEXT_SCRIPT_PATH


def _load_next_script():
    """The script Saturday saved, if there is a recent one."""
    try:
        with open(NEXT_SCRIPT_PATH) as f:
            script = json.load(f)
        created = datetime.strptime(script["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created <= NEXT_SCRIPT_MAX_AGE:
            return script
    except (OSError, ValueError, KeyError):
        pass
    return None


def run_longform_pipeline(target_minutes: int = None, privacy_status: str = "public"):
    reset()
    # A 7B model, not the shorts' llama3.2 — the 3B model swaps army
    # sizes and invents events even with the Wikipedia reference.
    ollama_model = os.getenv("LONGFORM_OLLAMA_MODEL", "qwen2.5:7b")
    try:
        script = _load_next_script()
        if script:
            print(f"Using the script saved {script['created_at']} for {script['general']}")
        elif IS_CI:
            raise RuntimeError("No script from Saturday's run (data/longform_next.json missing or older than "
                               f"{NEXT_SCRIPT_MAX_AGE.days} days) — skipping this week's video rather than "
                               "publishing one written by the local model. Re-run 'Write Weekly Long-Form Script', "
                               "then this workflow, from the Actions tab.")
        else:
            script = generate_longform_script(ollama_model=ollama_model, target_minutes=target_minutes,
                                              use_cloudflare=False)

        video_path = build_longform_video(script, ollama_model=ollama_model)
        thumbnail_path = generate_thumbnail(script, video_path, ollama_model=ollama_model)

        if not IS_CI or not UPLOADS_ON:
            where = "LOCAL TEST" if not IS_CI else "uploads are off (LONGFORM_UPLOADS) — kept as a workflow artifact"
            print(f"Long-form pipeline complete ({where}, not uploaded): {video_path}")
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

        if os.path.exists(NEXT_SCRIPT_PATH):
            os.remove(NEXT_SCRIPT_PATH)  # used; the workflow commits the removal
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
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    minutes = int(args[0]) if args else None  # e.g. `python longform_orchestrator.py 2` for a quick test
    if "--script-only" in sys.argv:
        write_next_script(target_minutes=minutes)
    else:
        run_longform_pipeline(target_minutes=minutes)
