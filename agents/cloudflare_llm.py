"""
Text generation on Cloudflare Workers AI, for the long-form scripts.

The local 7B model (Ollama) can't reliably keep a history script to its
sources — it invents commanders, numbers and events, and can't judge its
own sentences either. Workers AI runs much larger open models on the same
free daily allowance the illustrations use (10,000 neurons, reset at
00:00 UTC), so the script is written the day before the video is
rendered (see .github/workflows/longform-script.yml) and the two never
compete for one day's allowance.
"""
import json
import os
import re
import time

import requests

MODEL = os.getenv("LONGFORM_SCRIPT_MODEL", "@cf/openai/gpt-oss-120b")
usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}


def available() -> bool:
    return bool(os.getenv("CLOUDFLARE_ACCOUNT_ID") and os.getenv("CLOUDFLARE_API_TOKEN"))


def _post(body: dict) -> dict:
    url = f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CLOUDFLARE_ACCOUNT_ID']}/ai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}"}
    for attempt in range(4):
        r = requests.post(url, headers=headers, json=body, timeout=300)
        if r.status_code == 429 and "daily free allocation" not in r.text or r.status_code >= 500:
            time.sleep(10 * (attempt + 1))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"Workers AI {r.status_code}: {r.text[:300]}")
        return r.json()
    raise RuntimeError("Workers AI kept failing")


def _parse_json(text: str) -> dict:
    """The model's reply as a dict, tolerating code fences or a sentence
    around the JSON object."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def llm_json(prompt: str, model: str = None, temperature: float = 0.2) -> dict:
    """Same shape as the Ollama helper in longform_script_agent.py, so
    fact_check.py can use either. `model` is ignored unless it names a
    Workers AI model ("@cf/..."), so callers can pass their Ollama model."""
    chosen = model if model and model.startswith("@cf/") else MODEL
    data = _post({
        "model": chosen,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": 8000,  # gpt-oss reasons before answering; leave room for both
    })
    u = data.get("usage") or {}
    usage["prompt_tokens"] += u.get("prompt_tokens", 0)
    usage["completion_tokens"] += u.get("completion_tokens", 0)
    usage["calls"] += 1
    message = (data.get("choices") or [{}])[0].get("message") or {}
    return _parse_json(message.get("content") or "")
