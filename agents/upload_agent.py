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
import os
import sys

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
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds


def upload_video(video_path: str, title: str, description: str, tags=None) -> str:
    set_status("upload_agent", "running", f"uploading {os.path.basename(video_path)}")
    try:
        creds = _get_credentials()
        youtube = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": tags or ["history", "shorts", "didyouknow"],
                "categoryId": "27",  # Education
            },
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
        }
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            status, response = request.next_chunk()
        video_id = response["id"]
        url = f"https://youtube.com/shorts/{video_id}"
        set_status("upload_agent", "done", url)
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
        url = upload_video(videos[-1], "History Fact You Never Learned", "Follow for daily hidden history.")
        print("Uploaded:", url)
