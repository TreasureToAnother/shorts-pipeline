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

This is the "set it and forget it" free-hosting option — 6 scheduled
shorts/day, right at YouTube's 6-upload/day quota, at zero dollar cost.
A separate weekly long-form pipeline (~10 minute military-history
videos) also runs on Actions every Sunday — see section 6. Sunday's 6th
Short is skipped to make room for it under the 6-upload/day quota.

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
   6x/day. Adjust the six `cron` lines to your preferred posting times
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

- YouTube: 6 uploads/day quota (a hard API limit, independent of the
  Testing/Production publishing status) — the 6 shorts alone use all
  of it, which is why Sunday's 6th Short is skipped for the weekly
  long-form video.
- Pexels/Freesound free tiers: generous rate limits, fine for a few videos/day.
- Whisper `tiny.en` model runs on CPU in GitHub Actions' free runner
  (2 cores/7GB RAM) without issue for ~60s clips.
- Nothing in this stack requires a credit card.

---

## 6. Weekly long-form videos (military history)

A second, separate pipeline: one ~10 minute video a week on a famous
general or tactician — three of their battles, and the specific tactic
that won each one (the situation, the tactic, how the battle unfolded,
the result, and the lesson).

It runs on GitHub Actions every Sunday at 13:00 UTC
(`.github/workflows/longform.yml`), using the same repo secrets as the
shorts. Sunday's 6th Short is skipped so that day stays at 6 uploads
(YouTube's daily limit). You can also trigger it from the **Actions**
tab -> "Run Weekly Long-Form Video" -> **Run workflow**.

How it works:

- **Script** (`agents/longform_script_agent.py`) — the general and their
  three battles come from the curated `GENERALS` list, not the model, so
  no battle is ever invented. Each battle chapter is grounded in that
  battle's Wikipedia article, and written by `qwen2.5:7b` via Ollama (the
  shorts' 3B `llama3.2` got too many facts wrong). Generals already
  covered are tracked in `data/longform_used_topics.json` so none repeats
  until the whole list has been done.
- **Video** (`agents/longform_video_agent.py`) — 1920x1080, a deep
  documentary narrator voice (edge-tts), b-roll from Pexels matched to
  the general's era (ruins, cannons, tall ships, tanks...), a title card
  per chapter, and the background music track underneath.
- **Upload** — a regular (non-Shorts) public video in the Education
  category, with music and footage credits in the description.
- **Thumbnail** (`agents/thumbnail_agent.py`) — Ollama picks one scene
  and a 2-4 word hook; Cloudflare Workers AI (Flux, free daily
  allowance) draws the scene as a stickman cartoon with no text; Pillow
  adds the hook text. It's set on the video after upload and attached
  to the Discord post. Skipped (YouTube auto-picks a frame) if the
  Cloudflare secrets aren't set or anything fails. Shorts don't get one —
  API-set thumbnails don't reliably show in the Shorts feed.

Thumbnail setup (one time):

1. Custom thumbnails need a phone-verified channel: YouTube Studio >
   Settings > Channel > Feature eligibility.
2. Free Cloudflare account at https://dash.cloudflare.com, then **AI >
   Workers AI > Use REST API**: copy the Account ID and click **Create a
   Workers AI API Token**.
3. Add both as repo secrets `CLOUDFLARE_ACCOUNT_ID` and
   `CLOUDFLARE_API_TOKEN` (and in `config/.env` for local tests).
4. Preview one locally: `python agents/thumbnail_agent.py` writes
   `data/output/thumbnail_preview.jpg`.

Test it locally (renders to `data/output/`, never uploads):

```bash
ollama pull qwen2.5:7b
python longform_orchestrator.py 2   # optional: target length in minutes
```

### Tuning

- `GENERALS` in `agents/longform_script_agent.py` is the topic pool —
  add a general with their era and three `(battle, tactic)` pairs.
- `TARGET_MINUTES` in the same file sets the video length (the model
  tends to run ~10% long).
- `STYLE_PREFIX` and the `TEXT_*` settings in `agents/thumbnail_agent.py`
  control the thumbnail's art style and hook-text look.
- `ERA_QUERIES` in `agents/longform_video_agent.py` controls which
  Pexels searches supply the footage; `LONGFORM_VOICE_NAME` overrides
  the narrator voice.
