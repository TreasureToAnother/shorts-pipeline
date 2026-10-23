"""
Wikipedia helpers for the long-form pipeline: article text, and the
battle infobox (commanders, strengths, casualties, date, result) parsed
straight from the wikitext, so those facts never pass through a model.

Requests follow Wikimedia's API etiquette — a descriptive User-Agent,
serial requests, and backing off when told to (HTTP 429 + Retry-After).
"""
import html
import re
import time

import requests

API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "faceless-yt-pipeline/1.0 (https://github.com/TreasureToAnother/shorts-pipeline)"}
_cache = {}


def _get(params: dict) -> dict:
    key = tuple(sorted(params.items()))
    if key in _cache:
        return _cache[key]
    for attempt in range(6):
        r = requests.get(API, headers=HEADERS, timeout=30, params={**params, "format": "json", "formatversion": 2})
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(min(60, int(r.headers.get("retry-after", 0) or 0) or 5 * (attempt + 1)))
            continue
        r.raise_for_status()
        _cache[key] = r.json()
        return _cache[key]
    raise RuntimeError(f"Wikipedia kept refusing {params.get('action')} {params.get('page') or params.get('titles')}")


def wikitext(title: str) -> str:
    return _get({"action": "parse", "page": title, "prop": "wikitext", "redirects": 1})["parse"]["wikitext"]


def plain_text(title: str) -> str:
    pages = _get({"action": "query", "prop": "extracts", "explaintext": 1, "titles": title, "redirects": 1})["query"]["pages"]
    return pages[0].get("extract", "") if pages else ""


def _split_top(s: str, sep: str = "|") -> list:
    """Splits on sep only outside {{templates}} and [[links]]."""
    parts, depth, cur, i = [], 0, [], 0
    while i < len(s):
        two = s[i:i + 2]
        if two in ("{{", "[["):
            depth += 1; cur.append(two); i += 2; continue
        if two in ("}}", "]]"):
            depth -= 1; cur.append(two); i += 2; continue
        if s[i] == sep and depth == 0:
            parts.append("".join(cur)); cur = []; i += 1; continue
        cur.append(s[i]); i += 1
    parts.append("".join(cur))
    return parts


_LIST_TEMPLATES = {"blist", "bulleted list", "plainlist", "ubl", "unbulleted list", "flatlist", "hlist", "ublist", "collapsible list"}
_KEEP_FIRST_ARG = {"nowrap", "small", "nobr", "flag", "flagcountry", "flagdeco", "army", "flagu", "abbr", "sc"}
_MARKERS = {"kia": " (killed)", "dow": " (died of wounds)", "pow": " (captured)", "wia": " (wounded)"}


def _expand_templates(v: str) -> str:
    """Replaces templates innermost-first: lists become "a; b", wrappers
    keep their text, status markers become words, the rest is dropped."""
    for _ in range(200):
        m = re.search(r"\{\{([^{}]*)\}\}", v)
        if not m:
            break
        parts = [p.strip() for p in _split_top(m.group(1))]
        name = parts[0].lower().strip()
        args = [p for p in parts[1:] if not re.match(r"^\w[\w ]*=", p)]
        if name in _LIST_TEMPLATES:
            rep = "[" + "; ".join(a.lstrip("•· ").strip() for a in args if a.lstrip("•· ").strip()) + "]"
        elif name in _KEEP_FIRST_ARG:
            rep = args[0] if args else ""
        elif name == "lang":
            rep = args[-1] if args else ""
        elif name in _MARKERS:
            rep = _MARKERS[name]
        elif name in ("circa", "c."):
            rep = "c. " + (args[0] if args else "")
        elif name == "convert":
            rep = " ".join(args[:2])
        else:
            rep = ""
        v = v[:m.start()] + rep + v[m.end():]
    return v


def _clean(v: str) -> str:
    v = re.sub(r"<ref[^>]*/>", "", v)
    v = re.sub(r"<ref[^>]*>.*?</ref>", "", v, flags=re.S)
    v = re.sub(r"<!--.*?-->", "", v, flags=re.S)
    v = _expand_templates(v)
    v = re.sub(r"\[\[(?:File|Image):[^\]]*\]\]", "", v)
    v = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", v)
    v = re.sub(r"<br\s*/?>", "; ", v)
    v = re.sub(r"<[^>]+>", "", v)
    v = re.sub(r"'{2,}", "", v)
    v = html.unescape(v).replace("\xa0", " ")
    v = re.sub(r"^\s*[*#:]+\s*", "", v, flags=re.M)
    v = re.sub(r"\s*\n\s*", "; ", v)
    v = re.sub(r"\[\s*\]|\(\s*\)", "", v)
    v = re.sub(r"\s*;\s*(;\s*)*", "; ", v)
    v = re.sub(r"\s*\[\s*", " [", v)
    return re.sub(r"\s+", " ", v).strip(" ;,")


INFOBOX_FIELDS = ("date", "place", "result", "combatant1", "combatant2", "commander1", "commander2",
                  "strength1", "strength2", "casualties1", "casualties2")


def infobox(title: str) -> dict:
    """The article's "Infobox military conflict" fields, as plain text."""
    text = wikitext(title)
    m = re.search(r"\{\{\s*Infobox (?:military conflict|battle)", text, re.I)
    if not m:
        return {}
    depth, i = 0, m.start()
    while i < len(text):
        if text.startswith("{{", i):
            depth += 1; i += 2; continue
        if text.startswith("}}", i):
            depth -= 1; i += 2
            if depth == 0:
                break
            continue
        i += 1
    out = {}
    for part in _split_top(text[m.start() + 2:i - 2])[1:]:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip().lower()
        if key in INFOBOX_FIELDS:
            value = _clean(value)
            if value:
                out[key] = value
    return out


def has_battle_infobox(title: str) -> bool:
    try:
        return bool(re.search(r"\{\{\s*Infobox (?:military conflict|battle)", wikitext(title), re.I))
    except Exception:
        return False


def find_battle_article(battle: str):
    """Best article for a battle name like "Battle of Cannae (216 BC)":
    the exact title without the year first, then search results — and
    only an article that actually has a battle infobox (a plain search
    for the Trebia returns the river)."""
    name = re.sub(r"\s*\([^)]*\)\s*$", "", battle).strip()
    candidates = [name, battle]
    hits = _get({"action": "query", "list": "search", "srsearch": f'{name} battle', "srlimit": 5})["query"]["search"]
    candidates += [h["title"] for h in hits]
    for title in dict.fromkeys(candidates):
        if has_battle_infobox(title):
            return wikitext_title(title)
    return None


def wikitext_title(title: str) -> str:
    """The canonical title after redirects."""
    return _get({"action": "parse", "page": title, "prop": "wikitext", "redirects": 1})["parse"]["title"]


_PRIORITY_HEADINGS = re.compile(r"battle|engagement|prelude|deploy|disposition|course|order|fighting|assault|"
                                r"siege|march|campaign|ambush|attack|tactic|maneuver|manoeuvre|aftermath|outcome", re.I)
_SKIP_HEADINGS = re.compile(r"references|notes|sources|bibliography|further reading|external links|see also|"
                            r"citations|legacy|in popular culture|commemoration|historiography|footnotes", re.I)


def reference_text(title: str, keywords: list, max_words: int = 2500) -> str:
    """The article's intro, then the sections that matter for retelling
    the battle — ones with battle-ish headings first, then the ones that
    mention the keywords most — in article order, up to max_words.
    Taking just the start of the article mostly gets background; the
    fighting itself is usually described much further down."""
    text = plain_text(title)
    chunks = re.split(r"\n(={2,6})\s*(.*?)\s*\1\n", text)
    intro, sections = chunks[0].strip(), []
    for i in range(1, len(chunks) - 2, 3):
        heading, body = chunks[i + 1], chunks[i + 2].strip()
        if body and not _SKIP_HEADINGS.search(heading):
            sections.append((len(sections), heading, body))
    kw = [k.lower() for k in keywords if len(k) > 3]

    def score(sec):
        _, heading, body = sec
        low = body.lower()
        return (2 if _PRIORITY_HEADINGS.search(heading) else 0) + sum(low.count(k) for k in kw) / max(1, len(low.split()) / 100)

    chosen, words = [], len(intro.split())
    for sec in sorted(sections, key=score, reverse=True):
        n = len(sec[2].split())
        if words + n > max_words:
            continue
        chosen.append(sec)
        words += n
    parts = [intro] + [f"{heading}:\n{body}" for _, heading, body in sorted(chosen)]
    return "\n\n".join(parts)
