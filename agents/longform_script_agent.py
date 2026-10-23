"""
Long-Form Script Agent
-----------------------
Generates a ~10 minute military-history script: one famous general or
tactician per video, three of their battles, and the specific tactic
that won each one — the battle's situation, the tactic itself, how it
unfolded, the result, and the lesson.

The general and their three battles come from the curated GENERALS
list below rather than from the model — a small local model asked to
"pick three battles" will happily invent one. Ollama only writes the
narration around those fixed facts: an intro, one chapter per battle
(~3-4 minutes each), and an outro, each with its own call.

No external API dependency. The general is tracked in
data/longform_used_topics.json (committed by the long-form workflow)
so nobody repeats until the whole list has been covered.
"""
import json
import os
import random

import requests

USED_TOPICS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "longform_used_topics.json")

WORDS_PER_MINUTE = 150   # normal (non-sped-up) narration pace for this genre
TARGET_MINUTES = 10
INTRO_WORDS = 120
OUTRO_WORDS = 100

# era picks the Pexels stock-footage queries in longform_video_agent.py.
# look and thumb drive thumbnail_agent.py: look is the general's costume
# (fixed here so it's always historically right), thumb.battle is the
# index of the battle the thumbnail features, thumb.visual is the small
# background scene that hints at it, and thumb.hook is the big thumbnail
# text (2-4 words; the last line is drawn in yellow). Never put a real
# person's name in look/visual — the image API's safety filter refuses
# prompts that name real people.
GENERALS = [
    {"name": "Hannibal Barca", "era": "ancient",
     "look": "a round bronze helmet with a short crest on top and a long purple cape",
     "thumb": {"battle": 2, "visual": "a small crowd of scared tiny cartoon men in red-crested helmets squeezed together, surrounded on all sides by tiny smiling cartoon men", "ground": "flat sandy ground", "hook": "THE PERFECT TRAP"},
     "battles": [
        ("Battle of the Trebia (218 BC)", "luring the Romans across a freezing river into a hidden ambush"),
        ("Battle of Lake Trasimene (217 BC)", "ambushing an entire Roman army marching along a fog-covered lakeshore"),
        ("Battle of Cannae (216 BC)", "the double envelopment, letting his center bend back so his flanks could close around the Romans"),
    ]},
    {"name": "Alexander the Great", "era": "ancient",
     "look": "a bronze helmet with a tall white plume, a gold breastplate and a red cape",
     "thumb": {"battle": 2, "visual": "tiny cartoon horsemen charging in a pointy wedge shape through a gap in a long line of tiny surprised cartoon men", "ground": "flat dusty yellow ground", "hook": "STRAIGHT FOR THE KING"},
     "battles": [
        ("Battle of the Granicus (334 BC)", "seizing the initiative with a cavalry charge across a defended river"),
        ("Battle of Issus (333 BC)", "fighting on a narrow coastal plain that cancelled out Persia's numbers, then striking straight at Darius"),
        ("Battle of Gaugamela (331 BC)", "drifting right to stretch the Persian line, then driving a wedge through the gap that opened"),
    ]},
    {"name": "Julius Caesar", "era": "ancient",
     "look": "a green leaf wreath around his head, a white robe and a red cape",
     "thumb": {"battle": 2, "visual": "a long wooden wall curving around in a big circle, with tiny annoyed cartoon men peeking over it", "ground": "flat brown ground", "hook": "WALLS ON BOTH SIDES"},
     "battles": [
        ("Ilerda campaign (49 BC)", "winning by maneuver, cutting the enemy off from water and supplies until they surrendered"),
        ("Battle of Pharsalus (48 BC)", "hiding a fourth line of infantry to ambush Pompey's cavalry"),
        ("Siege of Alesia (52 BC)", "building two rings of siege walls, one to trap Vercingetorix and one to hold off the relief army"),
    ]},
    {"name": "Scipio Africanus", "era": "ancient",
     "look": "a bronze Roman helmet with a red crest and a red cape",
     "thumb": {"battle": 2, "visual": "a big grey cartoon elephant running harmlessly down an open lane between two rows of tiny relieved cartoon men", "ground": "flat sandy ground", "hook": "LET THEM THROUGH"},
     "battles": [
        ("Capture of New Carthage (209 BC)", "storming the city across a lagoon at low tide"),
        ("Battle of Ilipa (206 BC)", "reversing his usual deployment to put his best troops on the wings"),
        ("Battle of Zama (202 BC)", "opening lanes in his lines so Hannibal's war elephants charged straight through"),
    ]},
    {"name": "Han Xin", "era": "eastern",
     "look": "a small black Chinese official's hat and a long dark red robe",
     "thumb": {"battle": 0, "visual": "a flat blue river right behind him, and on a far hill tiny cartoon men swapping a big flag for a red one", "ground": "flat green grass", "hook": "BACK TO THE RIVER"},
     "battles": [
        ("Battle of Jingxing (204 BC)", "fighting with his back to a river while a hidden force swapped the enemy camp's flags"),
        ("Battle of Wei River (203 BC)", "damming a river, then releasing it to split the enemy army in half"),
        ("Battle of Gaixia (202 BC)", "surrounding Xiang Yu and breaking his army's morale with the songs of Chu"),
    ]},
    {"name": "Belisarius", "era": "ancient",
     "look": "a shiny round helmet and a purple cape over golden armor",
     "thumb": {"battle": 0, "visual": "a long straight ditch in the ground with tiny confused cartoon men stopped at its edge", "ground": "flat dusty ground", "hook": "THE DITCH TRICK"},
     "battles": [
        ("Battle of Dara (530)", "digging ditches to channel the Persian attack and hiding cavalry to strike its flank"),
        ("Battle of Ad Decimum (533)", "keeping control of his army while a poorly coordinated Vandal ambush fell apart"),
        ("Siege of Rome (537-538)", "holding Rome with a small garrison against a much larger Gothic army"),
    ]},
    {"name": "Khalid ibn al-Walid", "era": "ancient",
     "look": "a white turban and a long white robe",
     "thumb": {"battle": 1, "visual": "a long line of tiny cartoon men riding camels across an empty yellow desert under a big sun", "ground": "flat yellow desert sand", "hook": "CROSSED THE IMPOSSIBLE DESERT"},
     "battles": [
        ("Battle of Walaja (633)", "a double envelopment using cavalry hidden behind the enemy's flanks"),
        ("March across the Syrian desert (634)", "a surprise desert crossing that put his army behind enemy lines"),
        ("Battle of Yarmouk (636)", "using a mobile cavalry reserve and cutting off the Byzantine line of retreat"),
    ]},
    {"name": "Subutai", "era": "eastern",
     "look": "a round fur-trimmed Mongol hat and a brown fur coat",
     "thumb": {"battle": 0, "visual": "tiny cartoon horsemen riding away while a long, tired line of tiny cartoon men chases after them", "ground": "flat green steppe grass", "hook": "THE FAKE RETREAT"},
     "battles": [
        ("Battle of the Kalka River (1223)", "a long feigned retreat that strung out the pursuing armies"),
        ("Invasion of Europe (1241)", "several armies advancing on separate fronts in close coordination"),
        ("Battle of Mohi (1241)", "a night river crossing to hit the flank, then leaving a gap in the encirclement to cut down the fleeing army"),
    ]},
    {"name": "Oda Nobunaga", "era": "eastern",
     "look": "black samurai armor and a samurai helmet with two tall gold horns",
     "thumb": {"battle": 1, "visual": "a long wooden fence with puffs of white smoke rising from behind it, and tiny cartoon horsemen stopping in surprise", "ground": "flat green grass", "hook": "GUNS VS SAMURAI"},
     "battles": [
        ("Battle of Okehazama (1560)", "a surprise attack under cover of a rainstorm against a far larger army"),
        ("Battle of Nagashino (1575)", "massed gunfire from behind wooden palisades to break cavalry charges"),
        ("Second Battle of Kizugawaguchi (1578)", "iron-plated warships to break a naval blockade"),
    ]},
    {"name": "Admiral Yi Sun-sin", "era": "naval",
     "look": "a black wide-brimmed Korean hat with a red tassel and a red robe",
     "thumb": {"battle": 1, "visual": "a few small wooden ships in a narrow channel between two cliffs, facing a huge crowd of enemy ships", "ground": "flat blue sea", "hook": "13 SHIPS VS 133"},
     "battles": [
        ("Battle of Hansan Island (1592)", "luring the Japanese fleet into open water and closing a crane-wing formation around it"),
        ("Battle of Myeongnyang (1597)", "holding a narrow strait with just 13 ships against more than 130"),
        ("Battle of Noryang (1598)", "a night attack on a retreating enemy fleet"),
    ]},
    {"name": "Gustavus Adolphus", "era": "gunpowder",
     "look": "a wide black hat with a white feather and a yellow leather coat",
     "thumb": {"battle": 0, "visual": "small cartoon cannons on wheels being pushed along by tiny cheerful cartoon men", "ground": "flat green field", "hook": "THE CANNON REVOLUTION"},
     "battles": [
        ("Battle of Breitenfeld (1631)", "small, flexible brigades and mobile field artillery against massive tercio formations"),
        ("Battle of Rain (1632)", "crossing the Lech River under a smoke screen and artillery cover"),
        ("Battle of Lutzen (1632)", "combined arms, coordinating infantry, cavalry, and artillery together"),
    ]},
    {"name": "Frederick the Great", "era": "gunpowder",
     "look": "a black three-cornered hat and a dark blue coat",
     "thumb": {"battle": 2, "visual": "tiny cartoon men marching secretly behind a low green hill while other tiny cartoon men look the wrong way", "ground": "flat green field", "hook": "THE HIDDEN MARCH"},
     "battles": [
        ("Battle of Hohenfriedberg (1745)", "a night march to catch the enemy unprepared at dawn"),
        ("Battle of Rossbach (1757)", "a hidden march behind hills to strike an enemy column on the move"),
        ("Battle of Leuthen (1757)", "the oblique order, massing against one end of the enemy line while hiding the approach behind low ridges"),
    ]},
    {"name": "Napoleon Bonaparte", "era": "gunpowder",
     "look": "a huge black hat shaped like a half moon, worn sideways with the points sticking out left and right, and a dark blue coat with gold buttons",
     "thumb": {"battle": 2, "visual": "tiny cartoon men in white coats walking down off a green hill, not noticing what is waiting for them", "ground": "flat green field", "hook": "HE FAKED WEAKNESS"},
     "battles": [
        ("Battle of Rivoli (1797)", "the central position, using interior lines to beat separated enemy columns one at a time"),
        ("Ulm campaign (1805)", "a huge wheeling march that surrounded an entire Austrian army before it understood what was happening"),
        ("Battle of Austerlitz (1805)", "faking weakness on his right to lure the enemy off the Pratzen Heights, then seizing the center"),
    ]},
    {"name": "the Duke of Wellington", "era": "gunpowder",
     "look": "a huge black hat shaped like a half moon, worn sideways with the points sticking out left and right, and a plain dark blue coat",
     "thumb": {"battle": 2, "visual": "a row of tiny cartoon men in red coats hiding just behind the top of a green hill", "ground": "flat green field", "hook": "HIDE BEHIND THE HILL"},
     "battles": [
        ("Lines of Torres Vedras (1810)", "secretly built defensive lines and a scorched-earth retreat that starved the French"),
        ("Battle of Salamanca (1812)", "spotting a gap in an overextended French march and striking at once"),
        ("Battle of Waterloo (1815)", "the reverse slope defense, holding on until the Prussians arrived"),
    ]},
    {"name": "Admiral Horatio Nelson", "era": "naval",
     "look": "a huge black hat shaped like a half moon, worn sideways with the points sticking out left and right, and a navy blue coat with gold shoulder pads, one empty sleeve pinned to his chest",
     "thumb": {"battle": 2, "visual": "two lines of small wooden sailing ships heading straight at a long row of enemy ships", "ground": "flat blue sea", "hook": "SAIL STRAIGHT AT THEM"},
     "battles": [
        ("Battle of the Nile (1798)", "sailing between an anchored French fleet and the shore to attack it from both sides"),
        ("Battle of Copenhagen (1801)", "pressing the attack in shallow, dangerous waters despite orders to break off"),
        ("Battle of Trafalgar (1805)", "breaking the enemy line with two columns sailing straight at it"),
    ]},
    {"name": "Ulysses S. Grant", "era": "gunpowder",
     "look": "a slouchy black hat, a short scruffy beard and a dark blue army coat",
     "thumb": {"battle": 1, "visual": "small boats sailing past a hilltop town at night under a big yellow moon", "ground": "flat brown riverbank", "hook": "RUN THE GAUNTLET"},
     "battles": [
        ("Battle of Fort Donelson (1862)", "combined river and land operations, ending in a demand for unconditional surrender"),
        ("Vicksburg campaign (1863)", "running gunboats past the river batteries, landing south of the city, and cutting loose from his supply lines"),
        ("Overland Campaign (1864)", "relentless pressure and repeated flanking moves to pin down Lee's army"),
    ]},
    {"name": "Robert E. Lee", "era": "gunpowder",
     "look": "a neat white beard and a grey army coat",
     "thumb": {"battle": 2, "visual": "tiny cartoon men sneaking through dark green woods toward a camp of unaware cartoon men", "ground": "flat green grass", "hook": "HE SPLIT HIS ARMY"},
     "battles": [
        ("Second Battle of Bull Run (1862)", "sending Jackson on a wide flank march to wreck the enemy's supply base"),
        ("Battle of Fredericksburg (1862)", "a strong defensive position on high ground behind a stone wall"),
        ("Battle of Chancellorsville (1863)", "splitting his army in the face of a larger enemy to hit its exposed flank"),
    ]},
    {"name": "Erwin Rommel", "era": "modern",
     "look": "a peaked officer cap with goggles on it and a tan desert uniform",
     "thumb": {"battle": 2, "visual": "small cartoon tanks driving around the far end of a long line of sandbags in the desert", "ground": "flat yellow desert sand", "hook": "THE DESERT HOOK"},
     "battles": [
        ("Battle of Caporetto (1917)", "infiltration tactics with a small mountain unit that captured thousands of prisoners"),
        ("Fall of France (1940)", "speed and leading from the front with the 7th Panzer Division"),
        ("Battle of Gazala (1942)", "a sweeping armored hook around the southern end of the Allied line"),
    ]},
    {"name": "George S. Patton", "era": "modern",
     "look": "a shiny steel helmet with stars on it and a tan army jacket",
     "thumb": {"battle": 2, "visual": "a long line of small cartoon tanks making a sharp turn on a snowy road", "ground": "flat white snow", "hook": "90 DEGREES IN DAYS"},
     "battles": [
        ("Sicily campaign (1943)", "a fast advance across the island to Palermo and the race to Messina"),
        ("Breakout from Normandy (1944)", "exploiting a breakthrough with fast-moving armored columns"),
        ("Relief of Bastogne (1944)", "turning an entire army ninety degrees in a matter of days"),
    ]},
    {"name": "Georgy Zhukov", "era": "modern",
     "look": "a peaked officer cap with a red star and an olive green uniform covered in medals",
     "thumb": {"battle": 1, "visual": "two lines of small cartoon tanks closing around a snowy town from both sides", "ground": "flat white snow", "hook": "CLOSE THE CIRCLE"},
     "battles": [
        ("Battle of Khalkhin Gol (1939)", "building up forces in secret, then a surprise double envelopment"),
        ("Operation Uranus (1942)", "striking the weaker flanks to encircle the German army at Stalingrad"),
        ("Battle of Berlin (1945)", "massed artillery and a two-front race to the city"),
    ]},
]

TITLE_TEMPLATES = [
    "{name}: 3 Battles, 3 Tactics That Changed War",
    "How {name} Won: The 3 Greatest Tactics Explained",
    "The 3 Battles That Made {name} a Legend",
    "{name}'s Most Brilliant Tactics, Explained",
]


def _load_used():
    if not os.path.exists(USED_TOPICS_PATH):
        return []
    try:
        with open(USED_TOPICS_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return []


def _save_used(used):
    os.makedirs(os.path.dirname(USED_TOPICS_PATH), exist_ok=True)
    with open(USED_TOPICS_PATH, "w") as f:
        json.dump(used, f, indent=2)


def _pick_general():
    used = _load_used()
    remaining = [g for g in GENERALS if g["name"] not in used]
    if not remaining:
        used = []
        remaining = GENERALS
    general = random.choice(remaining)
    used.append(general["name"])
    _save_used(used)
    return general


_ACCURACY_RULES = (
    "- Stick to well-established historical facts. If you're unsure of an "
    "exact number, date, or name, describe it in general terms instead of "
    "inventing one\n"
    "- Spoken documentary narration, meant to be read aloud: no headers, "
    "no bullet points, no markdown, no stage directions\n"
    "- No preamble, no title, no quotes — return ONLY the spoken text itself"
)


WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_CONTEXT_WORDS = 1500


def _wikipedia_context(battle: str) -> str:
    """The battle's Wikipedia article text (first ~1500 words), used to
    ground the narration — without it, the small model gets the broad
    strokes right but invents units, names, and maneuvers. Returns ""
    if Wikipedia can't be reached, so the chapter still gets written."""
    headers = {"User-Agent": "faceless-yt-pipeline (personal YouTube automation)"}
    try:
        search = requests.get(WIKI_API, headers=headers, timeout=20, params={
            "action": "query", "list": "search", "srsearch": battle,
            "srlimit": 1, "format": "json",
        }).json()
        hits = search.get("query", {}).get("search", [])
        if not hits:
            return ""
        page = requests.get(WIKI_API, headers=headers, timeout=20, params={
            "action": "query", "prop": "extracts", "explaintext": 1,
            "titles": hits[0]["title"], "format": "json", "redirects": 1,
        }).json()
        extract = next(iter(page["query"]["pages"].values())).get("extract", "")
        return " ".join(extract.split()[:WIKI_CONTEXT_WORDS])
    except Exception:
        return ""


def _ollama_text(prompt: str, model: str, what: str) -> str:
    import ollama
    # Ollama's default context window can be as small as 2048 tokens,
    # which a Wikipedia reference + a ~500 word answer would overflow.
    result = ollama.generate(model=model, prompt=prompt, options={"num_ctx": 8192})
    text = result.get("response", "").strip().strip('"')
    if not text:
        raise RuntimeError(f"Ollama returned an empty {what}")
    return text


def _generate_intro(general: dict, target_words: int, model: str) -> str:
    battles = "; ".join(b for b, _ in general["battles"])
    prompt = (
        f"Write the opening of a military history YouTube video about "
        f"{general['name']}. The video covers three battles: {battles}.\n\n"
        f"Hook the viewer in the first sentence, say briefly who "
        f"{general['name']} was and why their tactics still matter, then "
        f"preview the three battles without giving away how they were won.\n\n"
        f"Requirements:\n"
        f"- Roughly {target_words} words\n"
        f"{_ACCURACY_RULES}"
    )
    return _ollama_text(prompt, model, "intro")


def _generate_battle_chapter(general: dict, battle: str, tactic: str, num: int,
                             target_words: int, model: str) -> str:
    reference = _wikipedia_context(battle)
    grounding = (
        f"Reference material (from Wikipedia) — take every name, number, "
        f"unit, and maneuver from this, and don't add facts that aren't in it:\n"
        f"\"\"\"\n{reference}\n\"\"\"\n\n" if reference else ""
    )
    prompt = grounding + (
        f"Write part {num} of 3 of a military history YouTube video about "
        f"{general['name']}. This part covers the {battle}, and the tactic "
        f"at its heart: {tactic}.\n\n"
        f"Cover, in this order:\n"
        f"1. The situation: who was fighting, why, and what the odds looked like\n"
        f"2. The tactic itself, explained clearly enough that someone with no "
        f"military background understands why it works\n"
        f"3. How the battle actually unfolded, step by step\n"
        f"4. The result, and the lesson a strategist can take from it\n\n"
        f"Requirements:\n"
        f"- Roughly {target_words} words\n"
        f"- Vivid and dramatic, but clear — like a good history documentary\n"
        f"{_ACCURACY_RULES}"
    )
    return _ollama_text(prompt, model, f"chapter for the {battle}")


def _generate_outro(general: dict, target_words: int, model: str) -> str:
    tactics = "; ".join(t for _, t in general["battles"])
    prompt = (
        f"Write the closing of a military history YouTube video about "
        f"{general['name']}, which covered these three tactics: {tactics}.\n\n"
        f"Tie the three together: what they reveal about how "
        f"{general['name']} thought about war. End by asking viewers which "
        f"general they want covered next, and to subscribe for a new one "
        f"every week.\n\n"
        f"Requirements:\n"
        f"- Roughly {target_words} words\n"
        f"{_ACCURACY_RULES}"
    )
    return _ollama_text(prompt, model, "outro")


def generate_longform_script(ollama_model: str = "llama3.2", target_minutes: int = None) -> dict:
    target_minutes = target_minutes or TARGET_MINUTES
    target_words = target_minutes * WORDS_PER_MINUTE
    scale = target_minutes / TARGET_MINUTES  # shrinks intro/outro too on short test runs
    intro_words = max(40, int(INTRO_WORDS * scale))
    outro_words = max(40, int(OUTRO_WORDS * scale))
    battle_words = max(100, (target_words - intro_words - outro_words) // 3)

    general = _pick_general()
    chapters = [{"topic": general["name"], "text": _generate_intro(general, intro_words, ollama_model)}]
    for i, (battle, tactic) in enumerate(general["battles"], start=1):
        text = _generate_battle_chapter(general, battle, tactic, i, battle_words, ollama_model)
        chapters.append({"topic": battle, "text": text})
    chapters.append({"topic": f"What {general['name']} Teaches Us",
                     "text": _generate_outro(general, outro_words, ollama_model)})

    title = random.choice(TITLE_TEMPLATES).format(name=general["name"])
    title = title[0].upper() + title[1:]  # "the Duke of Wellington" leading a title
    return {"title": title, "general": general["name"], "era": general["era"], "chapters": chapters,
            "look": general["look"], "thumb": general["thumb"], "battles": general["battles"]}


if __name__ == "__main__":
    script = generate_longform_script(target_minutes=2)  # short run for a quick local test
    print(json.dumps(script, indent=2))
    total_words = sum(len(c["text"].split()) for c in script["chapters"])
    print(f"\n{len(script['chapters'])} chapters, ~{total_words} words, "
          f"~{total_words / WORDS_PER_MINUTE:.1f} min narration")
