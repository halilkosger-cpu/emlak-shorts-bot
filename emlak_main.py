"""Bağlum emlak videoları — günde 1-2 otonom Short üretir (yüklemez: mp4 + açıklama üretir, Drive'a koyar).

Akış: içerik türü seç (piyasa / bölge / ilan) → kaynaklar (haber başlıkları, doğrulanmış bilgi, ilan bilgisi)
→ Gemini senarist → Edge-TTS → müşterinin fotoğraf/videoları → kurgu → output/ + (isteğe bağlı) Google Drive.
"""
from __future__ import annotations

import logging
import os
import random
import sys
import traceback
import zlib
from datetime import date, datetime

from bot import config, history, tts
from bot.audio import build_timeline, mix
from bot.emlak import drive, library, sources, writer
from bot.render import render
from bot.util import date_tr, setup_logging, write_json
from bot.visuals import Visual

log = logging.getLogger("emlak")
DISCLAIMER = ("Bu içerik bilgilendirme amaçlıdır; yatırım tavsiyesi değildir. Fiyat ve koşullar değişebilir, "
              "işlem öncesi tapu ve imar durumu mutlaka kontrol edilmelidir.")


def step_summary(md: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(md + "\n")


def pick_kind(slot: int, today: date, hist: list[dict], lst: list[library.Listing], news_ok: bool, facts_ok: bool):
    """(tür, ilan|None). Slot 2 mümkünse ilan; diğerleri piyasa/bölge dönüşümlü."""
    forced = (os.environ.get("KIND") or "").strip().lower()
    last_listing: dict[str, int] = {}
    for i, it in enumerate(hist):
        if it.get("listing"):
            last_listing[it["listing"]] = i
    ranked = sorted(lst, key=lambda l: last_listing.get(l.key, -1))
    if forced == "ilan" or (not forced and slot == 2 and ranked):
        if ranked:
            forced_key = (os.environ.get("LISTING") or "").strip()
            chosen = next((l for l in ranked if l.key == forced_key), ranked[0])
            return "ilan", chosen
        log.warning("İlan istendi ama ilan yok → piyasa/bölge")
    kind = forced if forced in ("piyasa", "bolge") else ("piyasa" if (today.toordinal() + slot) % 2 == 0 else "bolge")
    if kind == "piyasa" and not news_ok and facts_ok:
        kind = "bolge"
    if kind == "bolge" and not facts_ok and news_ok:
        kind = "piyasa"
    return kind, None


def build_visuals(kind, listing, n, hist, workdir) -> tuple[list[Visual], list[str]]:
    if listing:
        pool = list(listing.files)
    else:
        pool = library.least_used(library.general_media(), hist, n)
        if len(pool) < n:
            extra = [f for l in library.listings() for f in l.files]
            pool += [p for p in library.least_used(extra, hist, n) if p not in pool]
    vis, used = [], []
    ok = [(p, library.to_visual(p)) for p in pool]
    ok = [(p, v) for p, v in ok if v]
    if not ok:
        log.warning("Kullanılabilir medya yok → tüm sahneler kart (medya/genel klasörüne fotoğraf/video ekleyin)")
    for i in range(n):
        if ok:
            p, v = ok[i % len(ok)]
            vis.append(Visual(v.kind, v.path, v.width, v.height, v.source, duration=v.duration))
            used.append(library.rel(p))
        else:
            vis.append(Visual("card"))
    return vis, list(dict.fromkeys(used))


def run() -> int:
    now = datetime.now(config.TZ)
    today = date.fromisoformat(config.FORCE_DATE) if config.FORCE_DATE else now.date()
    slot = int(os.environ.get("SLOT") or (1 if now.hour < 14 else 2))
    force = (os.environ.get("FORCE_RUN") or "").lower() in ("1", "true")
    workdir = config.OUTPUT_ROOT / f"{today.isoformat()}-slot{slot}"
    workdir.mkdir(parents=True, exist_ok=True)
    setup_logging(workdir / "run.log")
    log.info("=== Emlak videosu: %s, slot %d (dry_run=%s) ===", date_tr(today), slot, config.DRY_RUN)

    hist = history.load()
    if not config.DRY_RUN and not force and any(it.get("date") == today.isoformat() and it.get("slot") == slot for it in hist):
        log.info("Bu slot bugün zaten üretilmiş → çıkılıyor (FORCE_RUN=true ile zorlanır)")
        step_summary(f"## {date_tr(today)} slot {slot} — zaten üretildi, atlandı")
        return 0

    lst = library.listings()
    rng = random.Random(zlib.crc32(f"{today}-{slot}".encode()))
    news = sources.fetch_news()
    facts = sources.load_facts(8, rng)
    kind, listing = pick_kind(slot, today, hist, lst, bool(news), bool(facts))
    log.info("Tür: %s%s", kind, f" ({listing.key})" if listing else "")

    if kind == "ilan":
        src = "İLAN BİLGİSİ (müşterinin notu, birebir doğru kabul et):\n" + listing.info
    elif kind == "piyasa":
        src = "SON HABER BAŞLIKLARI (tarih ve kaynak parantezde; yalnızca başlıktaki bilgiyi kullan):\n" + \
              "\n".join(n.line() for n in news[:12])
        if facts:
            src += "\n\nDOĞRULANMIŞ BÖLGE BİLGİSİ (isteğe bağlı bağlam):\n" + "\n".join(f"- {f}" for f in facts[:4])
    else:
        src = "DOĞRULANMIŞ BÖLGE BİLGİSİ:\n" + "\n".join(f"- {f}" for f in facts)
        if news:
            src += "\n\nGÜNCEL HABER BAŞLIKLARI (bağlam):\n" + "\n".join(n.line() for n in news[:5])
    if kind != "ilan" and not (news or facts):
        raise RuntimeError("Kaynak yok: data/baglum_bilgi.md'ye madde ekleyin veya haber araması sonuç vermedi")

    used_titles = [it.get("title", "") for it in hist if it.get("kind") == kind]
    try:
        story = writer.write(kind, src, used_titles, f"{date_tr(today)} {today.year}")
        write_json(workdir / "story.json", {"kind": kind, **story.to_dict()})
    except Exception as e:  # noqa: BLE001
        log.error("❌ Senaryo başarısız: %s", e)
        step_summary(f"## ❌ Senaryo hatası\n```\n{e}\n```")
        return 1

    try:
        voices = tts.synthesize_scenes([s.narration for s in story.scenes], workdir, max_seconds=config.MAX_SECONDS)
        tl, voice_track = build_timeline(voices)
        vis, used_media = build_visuals(kind, listing, len(story.scenes), hist, workdir)
        seed = zlib.crc32(f"{today}-{slot}-{kind}".encode())
        wav = workdir / "mix.wav"
        mix(voice_track, tl, story.mood, seed, wav)
        badge = config.BADGE if kind != "ilan" else "FIRSAT İLAN"
        video = render(story, tl, vis, badge, wav, workdir / "final_shorts.mp4", seed)
    except Exception as e:  # noqa: BLE001
        log.error("❌ Render başarısız: %s", e)
        step_summary(f"## ❌ Render hatası\n```\n{traceback.format_exc()[-3000:]}\n```")
        return 1

    # Açıklama (paylaşım metni)
    parts = [story.description]
    if config.BRAND_PHONE:
        parts.append(f"📞 {config.BRAND_NAME}: {config.BRAND_PHONE}")
    if kind == "piyasa" and news:
        srcs = list(dict.fromkeys(n.source for n in news[:12] if n.source))[:5]
        if srcs:
            parts.append("Kaynaklar: " + ", ".join(srcs))
    parts.append(" ".join(dict.fromkeys(story.hashtags + [f"#{config.REGION.lower().replace(' ', '')}", "#ankara", "#emlak", "#gayrimenkul"])))
    parts.append(DISCLAIMER)
    caption = "\n\n".join(p for p in parts if p)

    base = f"baglum-{kind}-{today.isoformat()}-{slot}"
    (workdir / f"{base}.txt").write_text(caption, encoding="utf-8")
    final = workdir / f"{base}.mp4"
    video.replace(final)
    write_json(workdir / "meta.json", {"title": story.title, "kind": kind, "duration": round(tl.duration, 1),
                                        "caption": caption})
    log.info("✓ Video hazır: %s (%.1f sn)", final.name, tl.duration)

    try:
        links = drive.upload([final, workdir / f"{base}.txt"])
    except Exception as e:  # noqa: BLE001
        log.warning("Drive yüklemesi başarısız (video Artifacts'ta): %s", e)
        links = []

    if not config.DRY_RUN:
        hist.append({"date": today.isoformat(), "slot": slot, "kind": kind, "title": story.title,
                     "media": used_media, "listing": listing.key if listing else "", "file": final.name})
        history.save(hist)
    step_summary("\n".join([f"## {date_tr(today)} — {story.title}", f"- Tür: {kind}", f"- Süre: {tl.duration:.0f} sn",
                            *(f"- Drive: {l}" for l in links), "", "```", caption, "```"]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception:  # noqa: BLE001
        logging.getLogger("emlak").error("HATA:\n%s", traceback.format_exc())
        sys.exit(1)
