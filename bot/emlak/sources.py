"""Kaynaklar: Google Haberler RSS (başlıklar) + elle bakılan doğrulanmış bilgi dosyası (data/baglum_bilgi.md)."""
from __future__ import annotations

import logging
import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

from .. import config, net

log = logging.getLogger("emlak.sources")
FACTS_FILE = config.DATA_DIR / "baglum_bilgi.md"


@dataclass
class NewsItem:
    title: str
    source: str
    date: datetime
    link: str = ""

    def line(self) -> str:
        return f"- ({self.date:%d.%m.%Y}, {self.source}) {self.title}"


def _rss(query: str) -> list[NewsItem]:
    url = f"https://news.google.com/rss/search?q={quote(query)}&hl=tr&gl=TR&ceid=TR:tr"
    r = net._session().get(url, timeout=25)
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3].strip()
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        if title:
            out.append(NewsItem(title, src or "haber", dt, it.findtext("link") or ""))
    return out


def fetch_news(days: int | None = None, limit: int = 14) -> list[NewsItem]:
    days = days or config.NEWS_DAYS
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    seen, items = set(), []
    for q in config.NEWS_QUERIES:
        try:
            for n in _rss(q):
                key = re.sub(r"\W+", "", n.title.lower())[:60]
                if n.date >= cutoff and key not in seen:
                    seen.add(key)
                    items.append(n)
        except Exception as e:  # noqa: BLE001
            log.warning("Haber araması başarısız (%s): %s", q, e)
    items.sort(key=lambda n: n.date, reverse=True)
    log.info("Haber başlığı: %d adet (son %d gün)", len(items), days)
    return items[:limit]


def load_facts(k: int = 8, rng: random.Random | None = None) -> list[str]:
    """Bilgi dosyasındaki madde satırlarından (- ile başlayan) rastgele k tanesi."""
    try:
        text = FACTS_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    facts = [ln[2:].strip() for ln in text.splitlines() if ln.startswith("- ") and len(ln) > 12]
    (rng or random).shuffle(facts)
    return facts[:k]
