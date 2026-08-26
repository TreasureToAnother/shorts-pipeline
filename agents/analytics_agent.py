"""
Analytics Agent
----------------
Snapshots the channel's current stats (subscribers, total views) and
the latest videos' stats from the YouTube Data API, then:
  - writes data/analytics_latest.json — the current numbers + latest
    videos, for the dashboard's stat cards and video list
  - appends today's snapshot to data/analytics_history.json — a daily
    time series the dashboard charts for subscriber/view growth

YouTube's public API has no "views gained this month" endpoint, so
that's derived here by diffing today's snapshot against the earliest
snapshot recorded this calendar month.

Uses a plain YOUTUBE_API_KEY (config/.env) rather than the upload
OAuth token — subscriber/view counts are public data, an API key
covers it, and it keeps this fully decoupled from the upload flow's
credentials (the upload token's "youtube.upload" scope alone can't
read channel/video stats — that scope is write-only).

Meant to run on a schedule (see .github/workflows/analytics.yml).
"""
import json
import os
from datetime import datetime, timezone

from googleapiclient.discovery import build

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
LATEST_PATH = os.path.join(DATA_DIR, "analytics_latest.json")
HISTORY_PATH = os.path.join(DATA_DIR, "analytics_history.json")
HISTORY_MAX_POINTS = 180  # ~6 months of daily snapshots


def _youtube_client(api_key: str):
    return build("youtube", "v3", developerKey=api_key)


def _channel_stats(yt, channel_id: str) -> dict:
    res = yt.channels().list(id=channel_id, part="statistics,snippet").execute()
    items = res.get("items", [])
    if not items:
        raise RuntimeError(f"No channel found for id {channel_id}")
    stats = items[0]["statistics"]
    snippet = items[0]["snippet"]
    return {
        "title": snippet.get("title", ""),
        "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
        "subscriberCount": int(stats.get("subscriberCount", 0)),
        "viewCount": int(stats.get("viewCount", 0)),
        "videoCount": int(stats.get("videoCount", 0)),
    }


def _latest_videos(yt, channel_id: str, max_results: int = 5) -> list:
    search = yt.search().list(
        channelId=channel_id, order="date", part="id",
        maxResults=max_results, type="video",
    ).execute()
    ids = [item["id"]["videoId"] for item in search.get("items", [])]
    if not ids:
        return []
    details = yt.videos().list(id=",".join(ids), part="snippet,statistics").execute()
    videos = []
    for v in details.get("items", []):
        thumbs = v["snippet"].get("thumbnails", {})
        thumb = thumbs.get("medium") or thumbs.get("default") or {}
        videos.append({
            "id": v["id"],
            "title": v["snippet"]["title"],
            "publishedAt": v["snippet"]["publishedAt"],
            "thumbnail": thumb.get("url", ""),
            "views": int(v["statistics"].get("viewCount", 0)),
            "likes": int(v["statistics"].get("likeCount", 0)),
            "comments": int(v["statistics"].get("commentCount", 0)),
            "url": f"https://youtube.com/shorts/{v['id']}",
        })
    videos.sort(key=lambda v: v["publishedAt"], reverse=True)
    return videos


def _load_history() -> list:
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return []


def _append_history(history: list, snapshot: dict) -> list:
    today = snapshot["date"]
    history = [h for h in history if h["date"] != today]  # de-dupe same-day re-runs
    history.append(snapshot)
    history.sort(key=lambda h: h["date"])
    return history[-HISTORY_MAX_POINTS:]


def _month_start_value(history: list, field: str, today: str):
    month_prefix = today[:7]  # "YYYY-MM"
    month_points = [h for h in history if h["date"].startswith(month_prefix)]
    if not month_points:
        return None
    return month_points[0][field]


def run():
    channel_id = os.environ["YOUTUBE_CHANNEL_ID"]
    yt = _youtube_client(os.environ["YOUTUBE_API_KEY"])
    channel = _channel_stats(yt, channel_id)
    videos = _latest_videos(yt, channel_id, max_results=5)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot = {
        "date": today,
        "subscriberCount": channel["subscriberCount"],
        "viewCount": channel["viewCount"],
        "videoCount": channel["videoCount"],
    }

    history = _append_history(_load_history(), snapshot)
    month_start_subs = _month_start_value(history, "subscriberCount", today)
    month_start_views = _month_start_value(history, "viewCount", today)

    latest = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "channel": {
            "title": channel["title"],
            "thumbnail": channel["thumbnail"],
            "subscriberCount": channel["subscriberCount"],
            "viewCount": channel["viewCount"],
            "videoCount": channel["videoCount"],
            "subsThisMonth": None if month_start_subs is None else channel["subscriberCount"] - month_start_subs,
            "viewsThisMonth": None if month_start_views is None else channel["viewCount"] - month_start_views,
        },
        "latestVideos": videos,
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LATEST_PATH, "w") as f:
        json.dump(latest, f, indent=2)
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)

    print(f"Analytics snapshot saved: {channel['subscriberCount']} subs, {channel['viewCount']} views")


if __name__ == "__main__":
    run()
