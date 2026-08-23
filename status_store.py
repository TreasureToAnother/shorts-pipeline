"""
Tiny shared JSON state file used to drive the node-graph dashboard.
Every agent calls set_status() at the start/end of its work so the
dashboard can show idle / running / done / error in near-real-time.
"""
import json
import os
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


def get_all():
    with _lock:
        return _read()


def reset():
    with _lock:
        _write({a: {"status": "idle", "message": "", "updated_at": None} for a in AGENTS})
