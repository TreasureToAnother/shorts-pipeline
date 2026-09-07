# History Shorts Pipeline

A 4-agent pipeline that writes, edits, uploads, and reports on daily
faceless history YouTube Shorts — entirely on free tiers. Plus a
node-graph dashboard that shows each agent as idle / running / done /
error in real time.

```
script_agent -> video_agent -> upload_agent -> discord_agent
```

- **script_agent** — pulls a real "on this day" history fact from
  Wikipedia (free, no key) and shapes it into a punchy short-form script.
- **video_agent** — free Pexels stock footage + free Microsoft TTS
  (edge-tts) + free Whisper captions + free Freesound SFX, assembled
  with FFmpeg/moviepy into a finished 9:16 video.
- **upload_agent** — uploads to YouTube via the free YouTube Data API.
- **discord_agent** — posts an upload notification to your server, and
  (optionally, run separately) a live bot that answers `!latest` and
  `!stats`.

---

## 1. One-time setup

### 1.1 Get the repo running locally

```bash
python3 -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp config/.env.example config/.env
```

You'll also need **ffmpeg** and **imagemagick** installed locally
(macOS: `brew install ffmpeg imagemagick`, Ubuntu: `sudo apt install
ffmpeg imagemagick`).

### 1.2 Pexels (free stock footage)

1. Go to https://www.pexels.com/api/ and sign up.
2. Copy your API key into `config/.env` as `PEXELS_API_KEY`.

### 1.3 Freesound (free SFX)

1. Create a free account at https://freesound.org
2. Apply for an API key at https://freesound.org/apiv2/apply/ (instant).
3. Put it in `config/.env` as `FREESOUND_API_KEY`.

### 1.4 YouTube Data API (free)

1. Go to https://console.cloud.google.com/ and create a project.
2. **APIs & Services > Library** — enable "YouTube Data API v3".
3. **APIs & Services > OAuth consent screen** — choose External, fill
   the minimum fields, add your own Google account as a test user.
4. **APIs & Services > Credentials > Create Credentials > OAuth client
   ID** — Application type: **Desktop app**.
5. Download the JSON, save it as `config/client_secret.json`.
6. Find your channel ID (Studio > Settings > Channel > Advanced) and
   put it in `config/.env` as `YOUTUBE_CHANNEL_ID`.
7. Run `python agents/upload_agent.py` once locally — it'll open a
   browser for one-time consent and save `config/token.json`. After
   this, uploads are fully automatic (the token auto-refreshes).
8. Same Cloud Console project: **APIs & Services > Credentials >
   Create Credentials > API key**. Put it in `config/.env` as
   `YOUTUBE_API_KEY` — this powers the analytics dashboard (subscriber/
   view counts). No OAuth consent needed, it's read-only public data.

### 1.5 Discord

1. Go to https://discord.com/developers/applications, **New
   Application**, then **Bot > Add Bot**. Copy the token into
   `config/.env` as `DISCORD_BOT_TOKEN`.
2. Under **OAuth2 > URL Generator**, check `bot`, permission `Send
   Messages`, open the generated URL to invite it to your server.
3. For simple upload notifications (no bot needed): in your Discord
   channel, **Edit Channel > Integrations > Webhooks > New Webhook**,
   copy the URL into `config/.env` as `DISCORD_WEBHOOK_URL`.

---

## 2. Run it locally

```bash
# one full pipeline run: script -> video -> upload -> discord ping
python orchestrator.py
```

Watch it live:

```bash
# in a second terminal
python serve_dashboard.py
# open http://localhost:5050
```

For the interactive `!latest` / `!stats` Discord bot (separate from
the pipeline, keep it running continuously):

```bash
python agents/discord_agent.py bot
```

---

## 3. Run it for free, on a schedule (GitHub Actions)

This is the "set it and forget it" free-hosting option — 3 scheduled
shorts/day, well under YouTube's 6-upload quota, at zero dollar cost.
A separate long-form pipeline (1-2hr self-improvement videos, Minecraft
parkour background) runs locally — see section 6.

1. Push this repo to GitHub (make sure `.gitignore` is respected —
   never commit `.env`, `client_secret.json`, or `token.json` directly).
2. In your repo: **Settings > Secrets and variables > Actions > New
   repository secret**, add each of:
   - `PEXELS_API_KEY`
   - `FREESOUND_API_KEY`
   - `YOUTUBE_CHANNEL_ID`
   - `YOUTUBE_API_KEY`
   - `DISCORD_WEBHOOK_URL`
   - `YOUTUBE_CLIENT_SECRET_JSON` — paste the full contents of your
     local `config/client_secret.json`
   - `YOUTUBE_TOKEN_JSON` — paste the full contents of your local
     `config/token.json` (generated in step 1.4.7 above)
3. The workflow in `.github/workflows/pipeline.yml` runs automatically
   3x/day. Adjust the three `cron` lines to your preferred posting times
   (they're in UTC).
4. You can also trigger a run manually anytime from the repo's
   **Actions** tab -> "Run History Shorts Pipeline" -> **Run workflow**.
5. `.github/workflows/analytics.yml` runs separately every 4 hours to
   snapshot channel/video stats for the dashboard — same
   `YOUTUBE_API_KEY` and `YOUTUBE_CHANNEL_ID` secrets, no extra setup.

Note: the interactive Discord bot (`!stats`, `!latest`) needs a
long-lived connection, so it can't run on GitHub Actions' scheduled
jobs. Run it on your own machine, or a small always-on free host like
Railway's or Fly.io's free tier.

---

## 4. Tuning retention

- Edit the beat structure in `agents/script_agent.py` to change
  pacing/hook style.
- Caption styling (font, size, highlight color, position) lives in
  `_build_scene_clip()` in `agents/video_agent.py`.
- Swap `voice = "en-US-GuyNeural"` in `video_agent.py` for any other
  free edge-tts voice — run `edge-tts --list-voices` to see all options.

## 5. Costs & limits to know about

- YouTube: 6 uploads/day on an unverified OAuth app — 3 shorts + 1
  long-form/day stays comfortably under that.
- Pexels/Freesound free tiers: generous rate limits, fine for a few videos/day.
- Whisper `tiny.en` model runs on CPU in GitHub Actions' free runner
  (2 cores/7GB RAM) without issue for ~60s clips.
- Nothing in this stack requires a credit card.

---

## 6. Long-form videos (local only)

A second, separate pipeline for 60-100 minute self-improvement/
motivation videos — the "motivational speech over Minecraft parkour"
genre. This never runs on GitHub Actions: rendering that much video,
TTS, and (if it used them) Whisper transcription isn't a good fit for
free shared CI runners, so it's designed to run on your own machine.

### Setup

1. Drop your own Minecraft parkour (or similar) `.mp4`/`.mov`/`.m4v`
   clips into `data/longform_backgrounds/`. That folder is gitignored
   — nothing in it ever needs to be committed, since this pipeline
   only runs locally. 20-60 minutes of varied source clips is plenty;
   they get randomly sampled/looped to fill however long the final
   video needs to be, the same trick the shorts pipeline uses.
2. (Optional) Background music reuses the same `data/background_music/`
   folder the shorts pipeline uses, looped quietly underneath.

### Run it

```bash
python longform_orchestrator.py
```

This generates a chaptered script (an outline + one Ollama call per
chapter — more reliable than asking a small local model for 10,000+
words in one shot), renders the video (1920x1080, chapter-title cards
instead of per-word captions, no SFX), and uploads it straight to
YouTube as a regular (non-Shorts) video, published immediately — there's
no private/test mode here the way there is for the shorts orchestrator,
since local is the only place this ever runs.

To automate "1 long-form video/day" without GitHub Actions, wire this
into your own local scheduler (macOS: `launchd`; cron works too) —
your machine just needs to be on and awake at whatever time you pick.

### Tuning

- `MIN_TARGET_MINUTES` / `MAX_TARGET_MINUTES` in `agents/longform_script_agent.py`
  control the random video length range.
- `THEMES` in the same file is the topic pool — edit freely, tracked
  in `data/longform_used_topics.json` (also gitignored) so themes
  don't repeat until the pool's exhausted.
- `CLIP_SEGMENT_SECONDS` in `agents/longform_video_agent.py` controls
  how long each background chunk plays before switching.
