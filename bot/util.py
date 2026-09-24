"""Küçük yardımcılar: Türkçe büyük/küçük harf, tarih biçimleri, metin temizliği, loglama."""
from __future__ import annotations

import html
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path

MONTHS_TR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
             "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

log = logging.getLogger("shorts")


def setup_logging(logfile: Path | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows konsolunda Türkçe karakterler için
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if logfile:
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    for noisy in ("httpx", "urllib3", "googleapiclient.discovery_cache", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# Büyük harfe çevrilirken İngilizce kuralıyla yazılan (i → I, İ değil) marka / proje adları: GITHUB, GEMINI...
# Botlar çalışırken (ör. repo adıyla) genişletebilir. Türkçede de kullanılan kelimeler (pilot, kimi, final...) eklenmez.
LATIN_WORDS: set[str] = {
    "github", "gemini", "linux", "windows", "android", "wikipedia", "linkedin", "discord", "twitter", "spotify",
    "netflix", "tiktok", "twitch", "nintendo", "minecraft", "firefox", "chrome", "chromium", "bing", "openai",
    "chatgpt", "deepmind", "midjourney", "nvidia", "iphone", "ipad", "macbook", "copilot", "ollama", "llama",
    "mistral", "qwen", "whisper", "diffusion", "comfyui", "huggingface", "deepseek", "pixel", "reddit", "webui",
}
_TOKEN = re.compile(r"^(\W*)([\w.+-]*\w)(.*)$", re.S)


def _basic_upper(s: str) -> str:
    return s.replace("i", "İ").replace("ı", "I").upper()


def _is_latin(core: str) -> bool:
    dotless = core.replace("İ", "I")
    if not dotless.isascii():
        return False
    low = dotless.lower()
    return (bool(re.search(r"[a-z][A-Z]", core)) or low in LATIN_WORDS
            or any(part in LATIN_WORDS for part in re.split(r"[-_.+]", low) if part))


def tr_upper(s: str) -> str:
    """Türkçe büyük harf (i → İ, ı → I). İngilizce marka / CamelCase adlar İngilizce kuralla:
    GitHub'da → GITHUB'DA, gemini-cli → GEMINI-CLI. Kesme işaretinden sonraki Türkçe ek Türkçe kuralla büyür."""
    out = []
    for tok in s.split(" "):
        base, apos, suffix = (re.split(r"(['’])", tok, maxsplit=1) + ["", ""])[:3]
        m = _TOKEN.match(base)
        if m and _is_latin(m.group(2)):
            lead, core, rest = m.groups()
            conv = _basic_upper(lead) + core.replace("İ", "I").upper() + _basic_upper(rest)
        else:
            conv = _basic_upper(base)
        out.append(conv + apos + _basic_upper(suffix))
    return " ".join(out)


def tr_lower(s: str) -> str:
    return s.replace("I", "ı").replace("İ", "i").lower()


def date_tr(d: date) -> str:
    """21 Eylül"""
    return f"{d.day} {MONTHS_TR[d.month - 1]}"


_WORD_RE = re.compile(r"[^0-9a-zçğıöşüâîû]+")


def norm_word(w: str) -> str:
    """Karşılaştırma için: küçük harf, noktalama ve kesme işaretleri silinmiş."""
    return _WORD_RE.sub("", tr_lower(w))


_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(s: str) -> str:
    s = _TAG_RE.sub(" ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def human_int(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def truncate_words(s: str, max_chars: int) -> str:
    s = clean_spaces(s)
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-–—")


def truncate_bytes(s: str, max_bytes: int) -> str:
    b = s.encode("utf-8")
    if len(b) <= max_bytes:
        return s
    return b[:max_bytes].decode("utf-8", errors="ignore").rstrip()


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def hex_to_rgb(h: str, default: tuple[int, int, int] = (255, 214, 10)) -> tuple[int, int, int]:
    h = (h or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return default
