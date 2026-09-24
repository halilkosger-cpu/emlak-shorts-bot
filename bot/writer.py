"""Senaryo veri yapıları (render motoru bunları okur) + seslendirme metni temizliği."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .util import clean_spaces

MOODS = ["epic", "mysterious", "uplifting", "emotional", "energetic"]


@dataclass
class Scene:
    narration: str
    emphasis: list[str] = field(default_factory=list)
    media: str = ""          # bu sahnede gösterilecek medya (yol); boşsa sıradaki medya


@dataclass
class Story:
    title: str
    hook_text: str
    scenes: list[Scene]
    description: str
    hashtags: list[str]
    tags: list[str]
    mood: str = "uplifting"
    facts_check: list = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return sum(len(s.narration.split()) for s in self.scenes)

    def to_dict(self) -> dict:
        return asdict(self)


_ABBR = [
    (r"\bvb\.", "ve benzeri"), (r"\bvs\.", "vesaire"), (r"\bm²", "metrekare"), (r"\bm2\b", "metrekare"),
    (r"\bkm\b", "kilometre"), (r"\bTL\b", "lira"), (r"₺", " lira "), (r"\bTCMB\b", "Merkez Bankası"),
    (r"\bTÜİK\b", "Türkiye İstatistik Kurumu"), (r"%\s?(\d+)", r"yüzde \1"),
]


def speakable(text: str) -> str:
    t = text
    for pat, rep in _ABBR:
        t = re.sub(pat, rep, t)
    t = re.sub(r"[\"“”«»()\[\]{}*#_~^|<>]", "", t)
    t = re.sub(r"[\U0001F000-\U0001FAFF☀-➿️]", "", t)
    return clean_spaces(t)
