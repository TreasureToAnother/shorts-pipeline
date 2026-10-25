"""
Fact sheets and fact-anchored narration for the long-form scripts.

A small local model retelling a battle from a Wikipedia article gets the
outline right but invents specifics (the wrong commander, a frozen river
that wasn't, made-up numbers), and asking it to review its own chapter
afterwards doesn't work either — it removes good sentences and keeps bad
ones. So every factual sentence is anchored to a fact that is anchored
to the article:

  quote   a sentence copied from the article (checked to really be there)
  fact    a short paraphrase of that quote (checked against the quote)
  line    a narration sentence retelling one fact (checked against the fact)

Each check is one small yes/no question, which a small model answers far
more reliably than "review this chapter". A paraphrased fact that drifts
from its quote is replaced by the quote. A narration sentence is checked
against the facts it cites plus the facts it overlaps with most (the
writer often cites the wrong one); one that fails gets one rewrite from
those facts and is re-checked, or is dropped. Sentences with no claims
at all ("commentary": why the tactic works, mood, lessons) pass freely.
Chapters are written in four sections, each checked before the next is
written, so the story the model continues from is always checked text.
"""
import re

from wikipedia import has_battle_infobox, infobox, reference_text

# Capitalized words that are fine anywhere, whether or not the sheet has them.
COMMON_CAPS = set("""I BC AD BCE CE Republic Empire Emperor King Queen General Admiral
January February March April May June July August September October November December
God North South East West Northern Southern Eastern Western Sea River Lake""".split())


def _words(text: str) -> list:
    return re.sub(r"[^a-z0-9]+", " ", text.lower().replace("’", "'")).split()


def numbers_in(text: str) -> set:
    """Numbers as plain values ("40,000" -> 40000), ignoring ones under 10
    ("two wings", "3 columns" are fine as figures of speech)."""
    found = set()
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        try:
            value = float(m.replace(",", ""))
        except ValueError:
            continue
        if value >= 10:
            found.add(int(value) if value == int(value) else value)
    return found


def names_in(text: str) -> set:
    """Capitalized words not at the start of a sentence — names of
    people, places and peoples."""
    names = set()
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for word in re.findall(r"[A-Za-z][A-Za-z'-]*", sentence)[1:]:
            if word[0].isupper():
                names.add(re.sub(r"'s$", "", word))
    return names - COMMON_CAPS


def _all_caps_words(text: str) -> set:
    return {re.sub(r"'s$", "", w) for w in re.findall(r"[A-Z][A-Za-z'-]+", text)}


def _quote_in_reference(quote: str, reference_words: str) -> bool:
    words = _words(quote)
    if len(words) < 5:
        return False
    span = min(8, len(words))  # any 8-word run must appear verbatim
    return any(" ".join(words[i:i + span]) in reference_words for i in range(0, len(words) - span + 1))


ENTAILS_PROMPT = """SOURCE: {source}

SENTENCE: {sentence}

Does the SENTENCE say anything that the SOURCE does not say? Different wording, dramatic tone and leaving things out are all fine. What counts: any added or changed name, number, date, place, unit, action, cause, result, weather, terrain or detail, or anything that contradicts the SOURCE.

Return ONLY JSON: {{"adds": "<what it adds or changes, or empty>", "faithful": true or false}}"""

CLAIM_PROMPT = """SENTENCE: {sentence}

Does this sentence state a specific historical claim — who did what, when, where, how many, what the weather or ground was like, or how something turned out? Explaining in general why a tactic works, describing feelings or tension in general terms, asking the viewer a question, and drawing lessons are NOT claims.

Return ONLY JSON: {{"claim": true or false}}"""


def faithful(sentence: str, source: str, model: str, ollama_json) -> bool:
    if not numbers_in(sentence) <= numbers_in(source):
        return False
    if not names_in(sentence) <= (_all_caps_words(source) | COMMON_CAPS):
        return False
    try:
        data = ollama_json(ENTAILS_PROMPT.format(source=source, sentence=sentence), model)
        return data.get("faithful") is True
    except Exception:
        return False


def is_claim(sentence: str, model: str, ollama_json) -> bool:
    try:
        return ollama_json(CLAIM_PROMPT.format(sentence=sentence), model).get("claim") is not False
    except Exception:
        return True


EVENTS_PROMPT = """Below is a Wikipedia article. Pick out the facts needed to retell how the {battle} went, for a video about {general} and this tactic: {tactic}.

List 14 to 18 facts, in the order things happened: the situation before, how each side was positioned, the weather and terrain if the article mentions them, the plan, each phase of the fighting, and how it ended. For each fact give:
- "fact": one short, plain sentence in your own words
- "quote": the exact sentence from the article that states it, copied word for word

Only use what the article says. Article:
\"\"\"
{reference}
\"\"\"

Return ONLY JSON: {{"facts": [{{"fact": "...", "quote": "..."}}]}}"""


def _infobox_facts(box: dict) -> tuple:
    """Infobox fields as facts — (before, after): the setup (date, place,
    commanders, strength) and the outcome (result, losses). The facts are
    numbered in story order, so the outcome goes after the events; at the
    top, the writer opened chapters with the death toll. They're parsed,
    not paraphrased, so each line is its own quote."""
    before, after = [], []
    for key, label in (("date", "Date"), ("place", "Place")):
        if key in box:
            before.append(f"{label}: {box[key]}")
    for side in ("1", "2"):
        name = box.get(f"combatant{side}")
        if not name:
            continue
        for key, label, bucket in (("commander", "commanders", before), ("strength", "strength", before),
                                   ("casualties", "losses", after)):
            if box.get(key + side):
                bucket.append(f"{name} {label}: {box[key + side]}")
    if "result" in box:
        after.insert(0, f"Result: {box['result']}")
    as_facts = lambda lines: [{"fact": f, "quote": f} for f in lines]
    return as_facts(before), as_facts(after)


def build_fact_sheet(title: str, battle: str, tactic: str, general: str, model: str, ollama_json) -> dict:
    """ollama_json(prompt, model) -> dict is passed in so this module
    doesn't pick the model settings."""
    keywords = re.findall(r"[A-Za-z]{4,}", battle + " " + tactic)
    reference = reference_text(title, keywords)
    reference_words = " ".join(_words(reference))
    box = infobox(title) if has_battle_infobox(title) else {}

    events = []
    try:
        data = ollama_json(EVENTS_PROMPT.format(battle=battle, general=general, tactic=tactic, reference=reference), model)
        for item in data.get("facts", []):
            if not isinstance(item, dict):
                continue
            fact, quote = str(item.get("fact", "")).strip(), str(item.get("quote", "")).strip()
            if not fact or not _quote_in_reference(quote, reference_words):
                continue  # the quote isn't really in the article
            if quote.rstrip(".") != fact.rstrip(".") and not faithful(fact, quote, model, ollama_json):
                fact = quote  # the paraphrase drifted; use the article's own words
            # The article's plain text drops some templated numbers, which
            # leaves sentences cut off mid-way ("...equates the toll to").
            if len(fact.split()) < 6 or not re.search(r"[.!?)\]]$", fact.strip()):
                continue
            if any(fact == e["fact"] for e in events):
                continue
            events.append({"fact": fact, "quote": quote})
    except Exception:
        pass

    before, after = _infobox_facts(box)
    facts = [{"fact": f"{general}'s tactic in this battle: {tactic}.", "quote": tactic}] + before + events + after
    return {"title": title, "facts": facts,
            "text": "\n".join(f"F{i}. {f['fact']}" for i, f in enumerate(facts, start=1))}


WRITE_PROMPT = """You are writing narration for part {num} of 3 of a military history YouTube video about {general}. This part covers the {battle}.

Numbered facts — the ONLY facts you may use:
{facts}
{story_so_far}
Now write the next section: {section}

Write {count} sentences. Each is ONE sentence of at most 30 words, and is one of two kinds:
- a FACT sentence: retells the facts listed in "facts" (one or two numbers), vividly but adding nothing — no extra names, numbers, places, actions, weather or details
- a COMMENTARY sentence ("facts": []): states no historical fact at all; it explains in general terms why the tactic works, builds tension, or draws a lesson

Rules:
- The facts are numbered in the order things happened: tell events in that order
- Start every sentence with a clear subject ("The Romans", "Hannibal's cavalry"), never "they", "them", "he", "also" or "this" pointing back to an earlier sentence — any sentence may be cut, so each must make sense on its own
- Don't repeat what the story so far already said
- Plain spoken English, like a good history documentary. Never read out labels, brackets or lists. Write every number in digits, e.g. "an army of about 40,000 men", "15,000 killed"
- Never repeat something the story so far already said

Return ONLY JSON: {{"sentences": [{{"text": "...", "facts": [<numbers>]}}]}}"""

SECTIONS = [
    ("who was fighting, where and when, and what the odds looked like", 8),
    ("the plan and the tactic, explained so someone with no military background understands why it works", 8),
    ("how the battle unfolded, step by step", 10),
    ("how it ended, what it cost each side, and the lesson a strategist can take from it", 8),
]
MAX_COMMENTARY_RUN = 2
# Appended to the last section's instructions for battles 1 and 2: a
# reason to keep watching across the chapter break.
TEASE = (" Finish with one COMMENTARY sentence that makes the viewer want to see the next battle, the {next}, "
         "without saying anything about how it went.")
BANNED_OPENERS = re.compile(r"\b(in this video|welcome|we'll (explore|delve|look|dive)|join (us|me)|let's (dive|explore|delve)|"
                            r"today we|stay tuned|before we begin)\b", re.IGNORECASE)

FRAME_PROMPT = """You are writing the {part} of a military history YouTube video about {general}, which covers three battles.

Numbered facts — the ONLY facts you may use:
{facts}

{instructions}

Write about {words} words, as a list of single sentences. Each is either a FACT sentence that retells only the facts listed in "facts", or a COMMENTARY sentence ("facts": []) that states no historical fact at all. Plain spoken English; no markdown.

Return ONLY JSON: {{"sentences": [{{"text": "...", "facts": [<numbers>]}}]}}"""

REWRITE_PROMPT = """FACTS: {source}

SENTENCE: {sentence}

Rewrite the SENTENCE so it says only what the FACTS say — keep its tone and role in the story, but remove or correct anything the FACTS don't support. Speak numbers naturally; no brackets or labels.

Return ONLY JSON: {{"sentence": "..."}}"""


def _sentences_from(data: dict, split_sentences) -> list:
    """[(sentence, [fact numbers])], splitting items that hold several
    sentences — each part keeps the item's fact numbers."""
    out = []
    for item in data.get("sentences", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict) or not str(item.get("text", "")).strip():
            continue
        refs = item.get("facts", item.get("fact", []))
        refs = refs if isinstance(refs, list) else [refs]
        numbers = []
        for r in refs:
            try:
                if int(r) > 0:
                    numbers.append(int(r))
            except (TypeError, ValueError):
                pass
        for sentence in split_sentences(str(item["text"]).strip()):
            out.append((sentence, numbers))
    return out


def _looks_like_data(sentence: str) -> bool:
    """Infobox-style text that shouldn't be read aloud."""
    return bool(re.search(r"[\[\]]|\b\w+ (strength|losses|commanders):", sentence))


def _related_facts(sentence: str, facts: list, cited: list, k: int = 4) -> list:
    """The facts a sentence cited, plus the k facts sharing the most
    words with it — the writer often cites the wrong number, or none."""
    words = {w for w in _words(sentence) if len(w) > 3}
    scored = sorted(range(len(facts)), key=lambda i: -len(words & set(_words(facts[i]["fact"]))))
    chosen = [n - 1 for n in cited if 1 <= n <= len(facts)]
    for i in scored:
        if len(chosen) >= len(cited) + k:
            break
        if i not in chosen and words & set(_words(facts[i]["fact"])):
            chosen.append(i)
    return [facts[i] for i in sorted(chosen)]


def _too_similar(sentence: str, earlier: list) -> bool:
    """Near-repeats of a sentence already kept ("The Romans suffered
    20,000 killed" twice in a row)."""
    words = {w for w in _words(sentence) if len(w) > 3}
    for other in earlier:
        theirs = {w for w in _words(other) if len(w) > 3}
        # Share of the shorter sentence's words found in the other; short
        # sentences are exempt ("The Romans crossed the river" twice can be
        # two different crossings).
        if min(len(words), len(theirs)) >= 5 and len(words & theirs) / min(len(words), len(theirs)) >= 0.75:
            return True
    return False


def _tidy(sentence: str) -> str:
    sentence = re.sub(r"^(commentary|fact|fact sentence|commentary sentence)\s*[:\-–—]\s*", "", sentence.strip(), flags=re.I)
    sentence = re.sub(r"^(also|and|but|so),?\s+", "", sentence, flags=re.I)
    return sentence[:1].upper() + sentence[1:]


def _repeats_numbers(sentence: str, earlier: list) -> bool:
    """A sentence whose numbers were all already given ("15,000 killed"
    for the third time)."""
    numbers = numbers_in(sentence)
    said = set().union(*[numbers_in(e) for e in earlier]) if earlier else set()
    return bool(numbers) and numbers <= said


_GENERIC = set("""battle battles enemy enemies army armies force forces troops soldiers general generals tactic
tactics victory defeat commander commanders through against their there which would could should every great
strategy strategist strategists lesson lessons moment power plan plans movement movements position""".split())


def _mentions_sheet(sentence: str, facts: list) -> bool:
    """Commentary that talks about the battle's concrete things (the
    river, the cavalry, the Romans' numbers) or its conditions is making
    claims about them, so it has to be checked like a fact sentence."""
    if re.search(r"\b(larger|smaller|bigger|more|fewer|less|outnumber\w*|even|deep|shallow|frozen|icy|ice|snow\w*|"
                 r"fog\w*|mist\w*|rain\w*|cold|heat|hot|narrow|wide|steep|night|dawn|morning|exhaust\w*|hungry|tired)\b",
                 sentence, re.I):
        return True
    words = {w for w in _words(sentence) if len(w) > 4} - _GENERIC
    sheet = {w for f in facts for w in _words(f["fact"]) if len(w) > 4} - _GENERIC
    return bool(words & sheet)


def _check_sentences(sentences: list, facts: list, allowed_names: set, model: str, ollama_json, report: list,
                     earlier: list = (), kinds: list = None) -> list:
    """Keeps a sentence if it's pure commentary (no claims), or if its
    related facts support it — else rewrites it once from those facts
    and re-checks, else drops it. allowed_names: names commentary may
    use freely (general, sides, battles)."""
    kept = []
    for text, refs in sentences:
        text = _tidy(text)
        if BANNED_OPENERS.search(text):
            report.append({"sentence": text, "action": "removed (stock YouTube phrasing)"})
            continue
        if _too_similar(text, list(earlier) + kept) or _repeats_numbers(text, list(earlier) + kept):
            report.append({"sentence": text, "action": "removed (repeats an earlier sentence)"})
            continue
        if _looks_like_data(text):
            report.append({"sentence": text, "action": "removed (reads like data)"})
            continue
        if not refs and not numbers_in(text) and not (names_in(text) - allowed_names) \
                and not _mentions_sheet(text, facts) and not is_claim(text, model, ollama_json):
            kept.append(text)
            if kinds is not None:
                kinds.append("commentary")
            continue
        related = _related_facts(text, facts, refs)
        if not related:
            report.append({"sentence": text, "action": "removed (no supporting fact)"})
            continue
        source = " ".join(f["fact"] for f in related)
        if faithful(text, source, model, ollama_json):
            kept.append(text)
            if kinds is not None:
                kinds.append("fact")
            continue
        try:
            new = str(ollama_json(REWRITE_PROMPT.format(source=source, sentence=text), model).get("sentence", "")).strip()
        except Exception:
            new = ""
        new = _tidy(new) if new else new
        if new and not _looks_like_data(new) and not _too_similar(new, list(earlier) + kept) \
                and faithful(new, source, model, ollama_json):
            kept.append(new)
            if kinds is not None:
                kinds.append("fact")
            report.append({"sentence": text, "action": "rewritten", "new": new})
        else:
            report.append({"sentence": text, "action": "removed (not supported by the facts)"})
    return kept


def write_battle_chapter(general: str, battle: str, num: int, words: int, sheet: dict,
                         allowed_names: set, model: str, ollama_json, split_sentences, next_battle: str = None) -> tuple:
    """Written in four sections, each checked before the next is written
    (so the story so far is only ever checked text). Returns (text, report)."""
    kept, kinds, report = [], [], []
    for k, (section, count) in enumerate(SECTIONS):
        if next_battle and k == len(SECTIONS) - 1:
            section += TEASE.format(next=next_battle)
        story = (f"\nThe story so far:\n{' '.join(kept)}\n") if kept else ""
        data = ollama_json(WRITE_PROMPT.format(num=num, general=general, battle=battle, facts=sheet["text"],
                                               story_so_far=story, section=section, count=count), model, 0.6)
        kept += _check_sentences(_sentences_from(data, split_sentences), sheet["facts"], allowed_names,
                                 model, ollama_json, report, earlier=kept, kinds=kinds)
    # At most 2 commentary sentences in a row: a stretch of generic
    # "the lesson is..." lines is where viewers drift off.
    out, run = [], 0
    for text, kind in zip(kept, kinds):
        run = run + 1 if kind == "commentary" else 0
        if run > MAX_COMMENTARY_RUN:
            report.append({"sentence": text, "action": "removed (too much commentary in a row)"})
            continue
        out.append(text)
    return " ".join(out), report


def write_frame(part: str, general: str, facts: list, instructions: str, words: int,
                allowed_names: set, model: str, ollama_json, split_sentences) -> tuple:
    """The intro or outro, which may only cite the given (trusted) facts."""
    fact_text = "\n".join(f"F{i}. {f['fact']}" for i, f in enumerate(facts, start=1))
    data = ollama_json(FRAME_PROMPT.format(part=part, general=general, facts=fact_text,
                                           instructions=instructions, words=words), model, 0.7)
    report = []
    kept = _check_sentences(_sentences_from(data, split_sentences), facts, allowed_names, model, ollama_json, report)
    return " ".join(kept), report
