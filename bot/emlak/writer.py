"""Emlak senaristi: piyasa / bölge / ilan videoları için Türkçe Shorts senaryosu."""
from __future__ import annotations

import logging
import re

from .. import config, gemini
from ..util import clean_spaces, truncate_words
from ..writer import MOODS, Scene, Story, speakable

log = logging.getLogger("emlak.writer")
KINDS = ("piyasa", "bolge", "ilan")

SYSTEM = """You are an elite Turkish short-form video scriptwriter (Instagram Reels / YouTube Shorts) working for a
local real-estate consultant in {REGION}, Ankara. You write the voice-over for ONE vertical video of about 35-45
seconds ({WMIN}-{WMAX} spoken words in total, {SMIN}-{SMAX} scenes). Output language: Turkish.

VIDEO TYPE: {KIND_DESC}

GOLDEN RULES
1. Accuracy first: use ONLY facts written in SOURCE. Never invent prices, percentages, dates, distances, projects,
   zoning status, quotes or statistics. If SOURCE lacks a detail, leave it out. When you cite a number from a news
   headline, say where it comes from ("... haberlerine göre") and never present it as your own measurement.
2. Legal & ethical (Turkish advertising / consumer rules): NEVER promise or imply guaranteed profit, guaranteed price
   rise, "kaçırmayın", "kesin kazanç", "garanti getiri". Prefer neutral, factual, calm wording ("değerlendirilen
   bölgelerden biri", "talep görüyor"). No pressure tactics. No claims about people, competitors or politics.
3. Hook in the first 2 seconds: scene 1 has at most 12 words and opens a curiosity gap (a question, a concrete
   number from SOURCE, or a surprising angle). No greetings, no "merhaba arkadaşlar".
4. Spoken Turkish, short punchy sentences (6-14 words), one idea per scene, natural for a phone video. Write numbers
   the way they are spoken ("iki buçuk milyon lira", "yüz elli metrekare"). No emojis, no hashtags, no parentheses
   inside narration. Never read out phone numbers or web addresses.
5. The LAST scene is a soft call to action: contact details are on screen and in the caption ("Detaylar için bize
   ulaşabilirsiniz" style). Vary the wording between videos.
6. Do NOT repeat angles/titles listed under ALREADY USED.
7. hook_text: 2-5 words shown huge on screen in the first seconds (ALL-CAPS Turkish, e.g. "ARSA MI, KONUT MU?").
8. description: 2-3 short Turkish sentences for the post caption (no hashtags, no phone).
9. emphasis: for each scene up to 2 words copied EXACTLY from that scene's narration (numbers, key nouns).
10. mood: uplifting | energetic | epic | mysterious | emotional — pick what fits (default uplifting).
"""

KIND_DESC = {
    "piyasa": "MARKET UPDATE. Explain what the latest headlines in SOURCE mean for someone thinking about land or a "
              "home in and around {REGION}/Ankara. Neutral, informative, no predictions stated as facts.",
    "bolge": "WHY THIS REGION. Present {REGION} as a location worth a look, using ONLY the verified facts in SOURCE "
             "(location, transport, development, lifestyle). Balanced tone: mention what a buyer should check "
             "(zoning status, title deed, access) in one scene.",
    "ilan": "LISTING SPOTLIGHT. Present the listing described in SOURCE using only its stated details. Highlight what "
            "makes it interesting, then who it may suit. Do not invent features. If the price is given, say it once.",
}


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "hook_text": {"type": "string"},
            "scenes": {"type": "array", "items": {
                "type": "object",
                "properties": {"narration": {"type": "string"},
                               "emphasis": {"type": "array", "items": {"type": "string"}}},
                "required": ["narration", "emphasis"]}},
            "description": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
            "mood": {"type": "string", "enum": MOODS},
        },
        "required": ["title", "hook_text", "scenes", "description", "hashtags", "mood"],
    }


_BANNED = re.compile(r"garanti|kesin (kazan|yüksel|artacak)|kaçırma|risksiz|yüzde yüz|%100", re.I)


def _validate(data: dict):
    scenes = data.get("scenes") or []
    if not 5 <= len(scenes) <= 8:
        raise ValueError(f"sahne sayısı {len(scenes)}")
    total = 0
    for sc in scenes:
        sc["narration"] = speakable(sc.get("narration", ""))
        if not sc["narration"]:
            raise ValueError("boş anlatım")
        if _BANNED.search(sc["narration"]):
            raise ValueError("yasak vaat ifadesi: " + sc["narration"])
        total += len(sc["narration"].split())
    if not config.WORDS_MIN <= total <= config.WORDS_MAX:
        raise ValueError(f"kelime sayısı {total} (hedef {config.WORDS_MIN}-{config.WORDS_MAX})")
    if not (data.get("title") or "").strip():
        raise ValueError("başlık yok")
    return data


def write(kind: str, source_block: str, used_titles: list[str], today_text: str) -> Story:
    system = (SYSTEM.replace("{REGION}", config.REGION).replace("{KIND_DESC}", KIND_DESC[kind].replace("{REGION}", config.REGION))
              .replace("{WMIN}", str(config.WORDS_MIN)).replace("{WMAX}", str(config.WORDS_MAX))
              .replace("{SMIN}", "5").replace("{SMAX}", "8"))
    brief = [f"TARİH: {today_text}", f"BÖLGE: {config.REGION}, Ankara"]
    if used_titles:
        brief.append("ALREADY USED (tekrar etme): " + " | ".join(used_titles[-12:]))
    brief.append(f"HEDEF: 5-8 sahne, toplam {config.WORDS_MIN}-{config.WORDS_MAX} kelime.")
    brief.append("\nSOURCE:\n" + source_block + "\n\nŞimdi JSON'u üret.")
    data = gemini.generate_json(gemini.user_content([gemini.text_part("\n".join(brief))]), system=system,
                                schema=_schema(), validate=_validate, label=f"emlak-{kind}", budget_s=900)
    tags = []
    story = Story(
        title=truncate_words(clean_spaces(data["title"].replace("<", "").replace(">", "")), 90),
        hook_text=truncate_words(clean_spaces(data.get("hook_text", "")), 40),
        scenes=[Scene(narration=s["narration"], emphasis=[e for e in (s.get("emphasis") or []) if isinstance(e, str)][:2])
                for s in data["scenes"]],
        description=clean_spaces((data.get("description") or "").replace("<", "").replace(">", "")),
        hashtags=[h if h.startswith("#") else f"#{h}" for h in
                  (re.sub(r"\s+", "", x) for x in data.get("hashtags", []) if isinstance(x, str)) if len(h) > 1][:6],
        tags=tags,
        mood=data.get("mood") if data.get("mood") in MOODS else "uplifting",
    )
    log.info("Senaryo: \"%s\" — %d sahne, %d kelime", story.title, len(story.scenes), story.word_count)
    for i, s in enumerate(story.scenes, 1):
        log.info("  %d. %s", i, s.narration)
    return story
