"""Ayarlar. Hepsi ortam değişkeniyle (GitHub Secrets / Variables) değiştirilebilir."""
from __future__ import annotations

import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONTS_DIR = ASSETS / "fonts"
MUSIC_DIR = ASSETS / "music"
DATA_DIR = ROOT / "data"
MEDIA_DIR = ROOT / "medya"
OUTPUT_ROOT = ROOT / "output"

TZ = ZoneInfo("Europe/Istanbul")


def _env(name: str, default: str = "") -> str:
    val = os.environ.get(name)
    return val.strip() if val and val.strip() else default


def _bool(name: str, default: bool = False) -> bool:
    val = _env(name).lower()
    return default if not val else val in ("1", "true", "yes", "on", "evet")


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in _env(name, default).split(",") if x.strip()]


HISTORY_FILE = ROOT / _env("HISTORY_FILE", "data/history.json")

# --- Gizli anahtarlar ---
GEMINI_API_KEY = _env("GEMINI_API_KEY")
GEMINI_MODELS = _list("GEMINI_MODELS", "gemini-3.6-flash,gemini-3.5-flash,gemini-3.8-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite")
GEMINI_TIMEOUT_S = _int("GEMINI_TIMEOUT_S", 180)
EDITOR_WEB_GROUNDING = False

# --- Seslendirme ---
TTS_VOICE = _env("TTS_VOICE", "tr-TR-AhmetNeural")
TTS_RATE = _env("TTS_RATE", "+8%")
TTS_PITCH = _env("TTS_PITCH", "+0Hz")
if not re.fullmatch(r"[+-]\d{1,3}%", TTS_RATE):
    TTS_RATE = "+8%"
if not re.fullmatch(r"[+-]\d{1,3}Hz", TTS_PITCH):
    TTS_PITCH = "+0Hz"

# --- Süre ---
WORDS_MIN = _int("WORDS_MIN", 75)
WORDS_MAX = _int("WORDS_MAX", 100)
MAX_SECONDS = _float("MAX_SECONDS", 45.0)
SCENE_GAP = _float("SCENE_GAP", 0.12)
TAIL_SECONDS = _float("TAIL_SECONDS", 0.9)

MUSIC_MODE = _env("MUSIC_MODE", "auto").lower()   # auto | files | synth | off
MUSIC_VOLUME_DB = _float("MUSIC_VOLUME_DB", -21.0)

DRY_RUN = _bool("DRY_RUN", False)
FORCE_DATE = _env("FORCE_DATE")
WIKI_CONTACT = _env("WIKI_CONTACT", "https://github.com/")
SEED = _env("SEED")

# --- Marka (müşteriye göre değiştirilir: Repo → Settings → Variables) ---
BRAND_NAME = _env("BRAND_NAME", "Bağlum Emlak")            # videoda sağ üst köşe filigranı
BRAND_PHONE = _env("BRAND_PHONE", "")                      # açıklamaya ve son sahneye eklenir
BADGE = _env("BADGE", "BAĞLUM'DA GAYRİMENKUL")             # ekranın üstündeki rozet
CHANNEL_HANDLE = BRAND_NAME + (f" · {BRAND_PHONE}" if BRAND_PHONE else "")
ACCENT_COLOR = _env("ACCENT_COLOR", "#F5B301")
REGION = _env("REGION", "Bağlum")                          # bölge adı (Ankara / Altındağ ...)
NEWS_QUERIES = _list("NEWS_QUERIES", "Bağlum emlak,Bağlum arsa,Ankara konut piyasası,Ankara konut fiyatları,Ankara Bağlum imar")
NEWS_DAYS = _int("NEWS_DAYS", 10)
BOT_TZ_NOTE = "Europe/Istanbul"

# Render'da abone ol düğmesi yok (müşteri için anlamsız).
SUBSCRIBE_CTA = False
CTA_TEXT, CTA_DONE_TEXT, CTA_SECONDS = "", "", 3.0

# --- Teslimat (isteğe bağlı): Google Drive ---
DRIVE_TOKEN_JSON = _env("DRIVE_TOKEN_JSON")     # OAuth token (drive.file kapsamı)
DRIVE_FOLDER_ID = _env("DRIVE_FOLDER_ID")       # müşteriyle paylaşılan klasör

# --- Video ---
W, H, FPS = 1080, 1920, 30
X264_PRESET = _env("X264_PRESET", "fast")
X264_CRF = _int("X264_CRF", 20)
