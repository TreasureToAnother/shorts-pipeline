"""
Discord Agent
-------------
Two pieces, both free:

1. notify_upload() - a simple webhook POST announcing a new video.
   Works from anywhere, including GitHub Actions, with no persistent
   process required. Create a webhook in your Discord channel's
   Integrations settings -> copy the URL into DISCORD_WEBHOOK_URL.

2. HistoryBot - a real, persistent discord.py bot with commands like
   !latest and !stats that pulls live numbers from the YouTube Data
   API. This needs to run continuously somewhere (your machine, or a
   small free host like Railway/Fly.io's free tier) since Discord bots
   require a long-lived websocket connection - GitHub Actions'
   scheduled runs can't host this part.
"""
import os
import sys

import requests

sys.path.append("..")
from status_store import set_status


def notify_upload(webhook_url: str, title: str, url: str, thumbnail_url: str = ""):
    set_status("discord_agent", "running", "posting upload notification")
    try:
        payload = {
            "embeds": [{
                "title": f"New video uploaded: {title}",
                "url": url,
                "description": "Just went live on the channel.",
                "color": 0x8B5CF6,
                "image": {"url": thumbnail_url} if thumbnail_url else None,
            }]
        }
        r = requests.post(webhook_url, json=payload, timeout=15)
        r.raise_for_status()
        set_status("discord_agent", "done", f"notified: {title}")
    except Exception as e:
        set_status("discord_agent", "error", str(e))
        raise


def notify_pipeline_failure(webhook_url: str, error_summary: str):
    """
    Pings Discord when a pipeline run fails, so a broken run shows up
    immediately instead of only being noticed days later as channel
    silence. Deliberately swallows its own errors — a failed alert
    should never mask or replace the original pipeline failure.
    """
    if not webhook_url:
        return
    try:
        payload = {
            "embeds": [{
                "title": "Pipeline run failed",
                "description": f"```{error_summary[-1000:]}```",
                "color": 0xE53E3E,
            }]
        }
        requests.post(webhook_url, json=payload, timeout=15)
    except Exception:
        pass


# ---------------------------------------------------------------------
# Persistent bot (run separately: `python agents/discord_agent.py bot`)
# ---------------------------------------------------------------------

def run_bot():
    import discord
    from discord.ext import commands
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    def _youtube_client():
        token_path = os.path.join(os.path.dirname(__file__), "..", "config", "token.json")
        creds = Credentials.from_authorized_user_file(
            token_path, ["https://www.googleapis.com/auth/youtube.readonly"]
        )
        return build("youtube", "v3", credentials=creds)

    @bot.command()
    async def latest(ctx):
        """Show the most recently uploaded video."""
        yt = _youtube_client()
        channel_id = os.getenv("YOUTUBE_CHANNEL_ID")
        res = yt.search().list(channelId=channel_id, order="date", part="snippet", maxResults=1, type="video").execute()
        if not res.get("items"):
            await ctx.send("No videos found yet.")
            return
        item = res["items"][0]
        await ctx.send(f"Latest: **{item['snippet']['title']}** — https://youtube.com/watch?v={item['id']['videoId']}")

    @bot.command()
    async def stats(ctx):
        """Show view counts for the last 5 uploads, best performer highlighted."""
        yt = _youtube_client()
        channel_id = os.getenv("YOUTUBE_CHANNEL_ID")
        search = yt.search().list(channelId=channel_id, order="date", part="id", maxResults=5, type="video").execute()
        ids = [i["id"]["videoId"] for i in search.get("items", [])]
        if not ids:
            await ctx.send("No videos found yet.")
            return
        details = yt.videos().list(id=",".join(ids), part="snippet,statistics").execute()
        lines = []
        best = None
        for v in details["items"]:
            views = int(v["statistics"].get("viewCount", 0))
            title = v["snippet"]["title"]
            lines.append(f"- {title}: {views} views")
            if best is None or views > best[1]:
                best = (title, views)
        msg = "**Last 5 uploads:**\n" + "\n".join(lines)
        if best:
            msg += f"\n\nBest performer: **{best[0]}** ({best[1]} views) — lean into whatever hook/topic that one used."
        await ctx.send(msg)

    bot.run(os.environ["DISCORD_BOT_TOKEN"])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "bot":
        run_bot()
    else:
        notify_upload(
            os.getenv("DISCORD_WEBHOOK_URL", ""),
            title="Test video",
            url="https://youtube.com/shorts/example",
        )
