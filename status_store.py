"""
Tiny shared JSON state file used to drive the node-graph dashboard.
Every agent calls set_status() at the start/end of its work so the
dashboard can show idle / running / done / error in near-real-time.

When running inside GitHub Actions, each status update is also
committed and pushed back to the repo, so a dashboard hosted elsewhere
(e.g. Vercel) can read live-ish progress by fetching the file's raw
GitHub URL, instead of only being viewable on whatever machine is
actually running the pipeline.
"""
import json
import os
import subprocess
import time
import threading

STATUS_PATH = os.path.join(os.path.dirname(__file__), "data", "status.json")
_lock = threading.Lock()

AGENTS = ["script_agent", "video_agent", "upload_agent", "discord_agent"]


def _read():
    if not os.path.exists(STATUS_PATH):
        return {a: {"status": "idle", "message": "", "updated_at": None} for a in AGENTS}
    with open(STATUS_PATH, "r") as f:
        return json.load(f)


def _write(data):
    os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
    with open(STATUS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _push_to_github():
    """Only runs inside GitHub Actions (detected via its built-in env
    var). Commits and pushes the status file so a remote dashboard can
    see progress during the run, not just after it finishes."""
    if os.getenv("GITHUB_ACTIONS") != "true":
        return
    try:
        subprocess.run(["git", "add", "data/status.json"], check=True, capture_output=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if diff.returncode == 0:
            return  # nothing changed, skip an empty commit
        subprocess.run(["git", "commit", "-m", "status update [skip ci]"], check=True, capture_output=True)
        subprocess.run(["git", "push"], check=True, capture_output=True)
    except Exception:
        pass  # never let a status push failure break the actual pipeline


def set_status(agent: str, status: str, message: str = ""):
    """status is one of: idle, running, done, error"""
    with _lock:
        data = _read()
        data[agent] = {
            "status": status,
            "message": message,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _write(data)
    _push_to_github()


def get_all():
    with _lock:
        return _read()


def reset():
    with _lock:
        _write({a: {"status": "idle", "message": "", "updated_at": None} for a in AGENTS})
    _push_to_github()