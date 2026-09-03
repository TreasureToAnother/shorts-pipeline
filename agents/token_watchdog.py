"""
Token Watchdog
--------------
Testing-mode Google OAuth apps (see README section 1.4) cap refresh
tokens at ~7 days from when they're issued, no matter how often
they're used — there's no way to auto-renew that without a live
browser login (see agents/upload_agent.py's _get_credentials()).

This posts a Discord heads-up once the current token is a few days
old, so re-auth happens on a schedule instead of being discovered as
a multi-day silent outage. Reads/writes data/token_issued_at.json,
which upload_agent.py updates automatically on every fresh consent.

Meant to run alongside the hourly analytics workflow.
"""
import json
import os
from datetime import datetime, timezone

import requests

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
STATE_PATH = os.path.join(DATA_DIR, "token_issued_at.json")

TESTING_TOKEN_LIFETIME_DAYS = 7
WARN_FROM_DAY = 5  # start nagging once the token is this many days old


def run():
    if not os.path.exists(STATE_PATH):
        return  # no re-auth has gone through record_fresh_consent() yet
    with open(STATE_PATH, "r") as f:
        state = json.load(f)

    issued_at = datetime.strptime(state["issued_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - issued_at).total_seconds() / 86400

    if age_days < WARN_FROM_DAY:
        return
    if state.get("last_warned_day") == int(age_days):
        return  # already warned today

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")
    days_left = max(0.0, TESTING_TOKEN_LIFETIME_DAYS - age_days)
    if webhook_url:
        payload = {
            "embeds": [{
                "title": "YouTube token expiring soon",
                "description": (
                    f"This token is {age_days:.1f} days old — Testing-mode "
                    f"refresh tokens expire around day {TESTING_TOKEN_LIFETIME_DAYS} "
                    f"(~{days_left:.1f} days left). Re-auth locally:\n"
                    f"```cd agents && python -c \"from upload_agent import "
                    f"_get_credentials; _get_credentials()\"```\n"
                    f"then `gh secret set YOUTUBE_TOKEN_JSON < config/token.json`."
                ),
                "color": 0xF6AD55,
            }]
        }
        try:
            requests.post(webhook_url, json=payload, timeout=15)
        except Exception:
            pass

    state["last_warned_day"] = int(age_days)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


if __name__ == "__main__":
    run()
