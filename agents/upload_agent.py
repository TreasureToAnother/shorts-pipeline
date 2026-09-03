"""
Upload Agent
------------
Uploads the finished video to YouTube as a Short, using the free
YouTube Data API v3 (quota: ~6 uploads/day on unverified apps, which
matches the requested cadence).

Requires a one-time OAuth consent (config/client_secret.json downloaded
from Google Cloud Console). After the first run, config/token.json
caches the refresh token so future runs (including GitHub Actions,
with the token stored as a secret) don't need re-consent.
"""
import json
import os
import sys
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

sys.path.append("..")
from status_store import set_status

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CONFIG_DIR = os.path.join(os.path.dirname(__file__), "..", "config")
CLIENT_SECRET_PATH = os.path.join(CONFIG_DIR, "client_secret.json")
TOKEN_PATH = os.path.join(CONFIG_DIR, "token.json")
TOKEN_ISSUED_AT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "token_issued_at.json")


def _record_fresh_consent():
    """
    Testing-mode Google OAuth apps cap refresh tokens at ~7 days from
    when they're minted, regardless of use — see token_watchdog.py.
    Only a brand-new browser consent (not an access-token refresh)
    resets that clock, so this only fires from the fresh-consent branch
    below.
    """
    os.makedirs(os.path.dirname(TOKEN_ISSUED_AT_PATH), exist_ok=True)
    with open(TOKEN_ISSUED_AT_PATH, "w") as f:
        json.dump({"issued_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, f, indent=2)


def _get_credentials():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_PATH, SCOPES)
            creds = flow.run_local_server(port=0)  # one-time browser consent
            _record_fresh_consent()
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds


def upload_video(video_path: str, title: str, description: str, tags=None,
                  publish_at: str = None, privacy_status: str = None) -> str:
    set_status("upload_agent", "running", f"uploading {os.path.basename(video_path)}")
    try:
        creds = _get_credentials()
        youtube = build("youtube", "v3", credentials=creds)

        status = {"selfDeclaredMadeForKids": False}
        if publish_at:
            status["privacyStatus"] = "private"
            status["publishAt"] = publish_at
        elif privacy_status:
            status["privacyStatus"] = privacy_status
        else:
            status["privacyStatus"] = "public"

        body = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": tags or ["storytime", "shorts", "reddit"],
                "categoryId": "24",
            },
            "status": status,
        }
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            status_resp, response = request.next_chunk()
        video_id = response["id"]
        url = f"https://youtube.com/shorts/{video_id}"
        if publish_at:
            msg = f"scheduled for {publish_at}: {url}"
        elif privacy_status == "private":
            msg = f"uploaded PRIVATE (manual publish needed): {url}"
        else:
            msg = url
        set_status("upload_agent", "done", msg)
        return url
    except Exception as e:
        set_status("upload_agent", "error", str(e))
        raise


if __name__ == "__main__":
    import glob
    videos = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..", "data", "output", "*.mp4")))
    if not videos:
        print("No videos found in data/output/. Run video_agent.py first.")
    else:
        url = upload_video(
            videos[-1],
            "Test upload",
            "Local test upload — private, publish manually if you want it live.",
            privacy_status="private",
        )
        print("Uploaded PRIVATE (manual publish needed):", url)