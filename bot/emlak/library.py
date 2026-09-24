"""Müşterinin medya kütüphanesi.

medya/genel/            → Bağlum'dan genel fotoğraf/videolar (piyasa ve bölge videolarında kullanılır)
medya/ilanlar/<ad>/     → her ilan için bir klasör: fotoğraf/videolar + bilgi.txt (fiyat, m², imar ... serbest metin)
Klasörün içine _pasif.txt dosyası koyulursa ilan atlanır (satıldı).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps

from .. import config
from ..media import probe_duration, probe_video_size
from ..visuals import Visual

log = logging.getLogger("emlak.library")
IMG = {".jpg", ".jpeg", ".png", ".webp"}
VID = {".mp4", ".mov", ".m4v", ".webm"}


def _files(d: Path) -> list[Path]:
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMG | VID and not p.name.startswith("."))


@dataclass
class Listing:
    key: str
    folder: Path
    info: str
    files: list[Path] = field(default_factory=list)


def general_media() -> list[Path]:
    d = config.MEDIA_DIR / "genel"
    return _files(d) if d.is_dir() else []


def listings() -> list[Listing]:
    root = config.MEDIA_DIR / "ilanlar"
    out = []
    if not root.is_dir():
        return out
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if (d / "_pasif.txt").exists() or d.name.startswith("ornek"):
            continue
        files = _files(d)
        info_f = d / "bilgi.txt"
        info = info_f.read_text(encoding="utf-8").strip() if info_f.exists() else ""
        if files and info:
            out.append(Listing(d.name, d, info, files))
        else:
            log.warning("İlan atlandı (%s): medya veya bilgi.txt eksik", d.name)
    return out


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(config.ROOT)).replace("\\", "/")
    except ValueError:
        return str(p)


def least_used(paths: list[Path], hist: list[dict], n: int) -> list[Path]:
    """Geçmişte en az / en eski kullanılan n medyayı seç (çeşitlilik için)."""
    last: dict[str, int] = {}
    for i, it in enumerate(hist):
        for m in it.get("media", []) or []:
            last[m] = i
    ranked = sorted(paths, key=lambda p: (last.get(rel(p), -1), p.name))
    return ranked[:n]


def to_visual(p: Path) -> Visual | None:
    try:
        if p.suffix.lower() in VID:
            w, h = probe_video_size(p)
            return Visual("video", p, w, h, "musteri", duration=probe_duration(p))
        with Image.open(p) as im:
            im = ImageOps.exif_transpose(im)
            w, h = im.size
        if min(w, h) < 300:
            return None
        return Visual("image", p, w, h, "musteri")
    except Exception as e:  # noqa: BLE001
        log.warning("Medya açılamadı (%s): %s", p.name, e)
        return None
