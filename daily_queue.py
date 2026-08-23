"""
Tracks today's 6-slot video production queue for the dashboard's
"Today's Queue" / "Awaiting Release" lists. Resets automatically once
the Eastern-time date rolls over to a new day. Only updated during
real GitHub Actions runs (scheduled or manual) — local test runs never
touch this, so private local test uploads don't clutter the queue.
"""
import json
import os
import subprocess
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

QUEUE_PATH = os.path.join(os.path.dirname(__file__), "data", "queue.json")
SLOT_HOURS_ET = [4, 5, 6, 7, 8, 9]


def _now_et():
    if ET:
        return datetime.now(ET)
    return datetime.utcnow() - timedelta(hours=4)


def _today_str():
    return _now_et().strftime("%Y-%m-%d")


def _fresh_queue():
    return {
        "date": _today_str(),
        "slots": [
            {
                "slot": i,
                "hour_et": h,
                "stage": "pending",
                "title": None,
                "video_url": None,
                "publish_at": None,
            }
            for i, h in enumerate(SLOT_HOURS_ET)
        ],
    }


def _read():
    if not os.path.exists(QUEUE_PATH):
        return _fresh_queue()
    try:
        with open(QUEUE_PATH, "r") as f:
            data = json.load(f)
    except Exception:
        return _fresh_queue()
    if data.get("date") != _today_str():
        return _fresh_queue()
    return data


def _write(data):
    os.makedirs(os.path.dirname(QUEUE_PATH), exist_ok=True)
    with open(QUEUE_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _push_to_github():
    if os.getenv("GITHUB_ACTIONS") != "true":
        return
    try:
        subprocess.run(["git", "add", "data/queue.json"], check=True, capture_output=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if diff.returncode == 0:
            return
        subprocess.run(["git", "commit", "-m", "queue update [skip ci]"], check=True, capture_output=True)
        subprocess.run(["git", "push"], check=True, capture_output=True)
    except Exception:
        pass


def claim_next_slot():
    """Finds the slot matching the ACTUAL current hour and marks it
    'generating'. Previously this just grabbed the first pending slot
    in list order, which mislabeled a run as an earlier time slot if
    that earlier slot never actually ran. Matching by real hour keeps
    a genuinely-missed slot correctly showing as never started."""
    if os.getenv("GITHUB_ACTIONS") != "true":
        return None
    data = _read()
    current_hour = _now_et().hour

    for slot in data["slots"]:
        if slot["hour_et"] == current_hour and slot["stage"] == "pending":
            slot["stage"] = "generating"
            _write(data)
            _push_to_github()
            return slot["slot"]

    for slot in data["slots"]:
        if slot["stage"] == "pending":
            slot["stage"] = "generating"
            _write(data)
            _push_to_github()
            return slot["slot"]
    return None


def mark_uploaded(slot_index, title, video_url, publish_at):
    if slot_index is None or os.getenv("GITHUB_ACTIONS") != "true":
        return
    data = _read()
    for slot in data["slots"]:
        if slot["slot"] == slot_index:
            slot["stage"] =
cat > daily_queue.py << 'PYEOF'
"""
Tracks today's 6-slot video production queue for the dashboard's
"Today's Queue" / "Awaiting Release" lists. Resets automatically once
the Eastern-time date rolls over to a new day. Only updated during
real GitHub Actions runs (scheduled or manual) — local test runs never
touch this, so private local test uploads don't clutter the queue.
"""
import json
import os
import subprocess
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None

QUEUE_PATH = os.path.join(os.path.dirname(__file__), "data", "queue.json")
SLOT_HOURS_ET = [4, 5, 6, 7, 8, 9]


def _now_et():
    if ET:
        return datetime.now(ET)
    return datetime.utcnow() - timedelta(hours=4)


def _today_str():
    return _now_et().strftime("%Y-%m-%d")


def _fresh_queue():
    return {
        "date": _today_str(),
        "slots": [
            {
                "slot": i,
                "hour_et": h,
                "stage": "pending",
                "title": None,
                "video_url": None,
                "publish_at": None,
            }
            for i, h in enumerate(SLOT_HOURS_ET)
        ],
    }


def _read():
    if not os.path.exists(QUEUE_PATH):
        return _fresh_queue()
    try:
        with open(QUEUE_PATH, "r") as f:
            data = json.load(f)
    except Exception:
        return _fresh_queue()
    if data.get("date") != _today_str():
        return _fresh_queue()
    return data


def _write(data):
    os.makedirs(os.path.dirname(QUEUE_PATH), exist_ok=True)
    with open(QUEUE_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _push_to_github():
    if os.getenv("GITHUB_ACTIONS") != "true":
        return
    try:
        subprocess.run(["git", "add", "data/queue.json"], check=True, capture_output=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if diff.returncode == 0:
            return
        subprocess.run(["git", "commit", "-m", "queue update [skip ci]"], check=True, capture_output=True)
        subprocess.run(["git", "push"], check=True, capture_output=True)
    except Exception:
        pass


def claim_next_slot():
    """Finds the slot matching the ACTUAL current hour and marks it
    'generating'. Previously this just grabbed the first pending slot
    in list order, which mislabeled a run as an earlier time slot if
    that earlier slot never actually ran. Matching by real hour keeps
    a genuinely-missed slot correctly showing as never started."""
    if os.getenv("GITHUB_ACTIONS") != "true":
        return None
    data = _read()
    current_hour = _now_et().hour

    for slot in data["slots"]:
        if slot["hour_et"] == current_hour and slot["stage"] == "pending":
            slot["stage"] = "generating"
            _write(data)
            _push_to_github()
            return slot["slot"]

    for slot in data["slots"]:
        if slot["stage"] == "pending":
            slot["stage"] = "generating"
            _write(data)
            _push_to_github()
            return slot["slot"]
    return None


def mark_uploaded(slot_index, title, video_url, publish_at):
    if slot_index is None or os.getenv("GITHUB_ACTIONS") != "true":
        return
    data = _read()
    for slot in data["slots"]:
        if slot["slot"] == slot_index:
            slot["stage"] = "uploaded"
            slot["title"] = title
            slot["video_url"] = video_url
            slot["publish_at"] = publish_at
            break
    _write(data)
    _push_to_github()


def mark_error(slot_index):
    if slot_index is None or os.getenv("GITHUB_ACTIONS") != "true":
        return
    data = _read()
    for slot in data["slots"]:
        if slot["slot"] == slot_index:
            slot["stage"] = "error"
            break
    _write(data)
    _push_to_github()
