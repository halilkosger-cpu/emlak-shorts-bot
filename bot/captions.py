"""Ekran yazıları: kelime kelime vurgulu altyazı, açılış kancası, tarih rozeti (Pillow ile RGBA)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config
from .tts import Word
from .util import hex_to_rgb, norm_word, tr_upper

FONT_CAPTION = config.FONTS_DIR / "Montserrat[wght].ttf"
FONT_DISPLAY = config.FONTS_DIR / "Anton-Regular.ttf"
FALLBACKS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"]

WHITE = (255, 255, 255)
ACCENT = hex_to_rgb(config.ACCENT_COLOR)
CAPTION_MAX_W = 820
CAPTION_Y = 1190        # altyazı merkezinin y'si
HOOK_TOP = 196          # kanca metninin üst kenarı (rozetin hemen altı)
BADGE_Y = 118           # tarih rozetinin merkezi
CTA_Y = 1375            # abone ol butonunun merkezi: altyazının altı, Shorts arayüzünün (alt %25) üstü


@lru_cache(maxsize=64)
def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    path, var = {"caption": (FONT_CAPTION, "Black"), "label": (FONT_CAPTION, "ExtraBold"),
                 "display": (FONT_DISPLAY, None)}[kind]
    try:
        f = ImageFont.truetype(str(path), size)
        if var:
            try:
                f.set_variation_by_name(var)
            except Exception:  # noqa: BLE001
                pass
        return f
    except OSError:
        for fb in FALLBACKS:
            try:
                return ImageFont.truetype(fb, size)
            except OSError:
                continue
        return ImageFont.load_default(size)


def _text_w(f: ImageFont.FreeTypeFont, s: str) -> int:
    return int(f.getlength(s))


# ----------------------------------------------------------------------------
# Altyazı sayfaları
# ----------------------------------------------------------------------------
@dataclass
class Page:
    words: list[Word]
    start: float
    end: float
    emphasis: set[int] = field(default_factory=set)
    size: int = 92
    lines: list[list[int]] = field(default_factory=list)


def build_pages(scene_words: list[list[Word]], scene_emph: list[list[str]], scene_ends: list[float],
                max_words: int = 3, max_chars: int = 18) -> list[Page]:
    pages: list[Page] = []
    for si, words in enumerate(scene_words):
        emph = {norm_word(e) for e in (scene_emph[si] if si < len(scene_emph) else []) if e}
        cur: list[Word] = []
        chars = 0

        def flush():
            nonlocal cur, chars
            if cur:
                p = Page(words=cur, start=cur[0].start, end=cur[-1].end)
                p.emphasis = {i for i, w in enumerate(cur) if norm_word(w.text) in emph and norm_word(w.text)}
                pages.append(p)
            cur, chars = [], 0

        for w in words:
            if cur and (len(cur) >= max_words or chars + len(w.text) > max_chars
                        or w.start - cur[-1].end > 0.35):
                flush()
            cur.append(w)
            chars += len(w.text) + 1
            if re.search(r"[.!?;:…]$", w.text) or (re.search(r",$", w.text) and len(cur) >= 2):
                flush()
        flush()
    # zaman boşluklarını kapat: her sayfa bir sonrakinin başlangıcına kadar kalsın
    for i, p in enumerate(pages):
        if i + 1 < len(pages):
            nxt = pages[i + 1].start
            p.end = nxt if nxt - p.end < 0.6 else p.end + 0.25
        else:
            p.end = p.end + 0.45
    for p in pages:
        _layout(p)
    return pages


def _layout(p: Page, base: int = 92) -> None:
    size = base
    while size >= 56:
        f = font("caption", size)
        space = _text_w(f, " ")
        widths = [_text_w(f, tr_upper(w.text)) for w in p.words]
        if max(widths) <= CAPTION_MAX_W:
            lines, cur, cw = [], [], 0
            for i, wd in enumerate(widths):
                add = wd if not cur else cw + space + wd
                if cur and add > CAPTION_MAX_W:
                    lines.append(cur)
                    cur, cw = [i], wd
                else:
                    cur.append(i)
                    cw = add
            if cur:
                lines.append(cur)
            if len(lines) <= 2:
                p.size, p.lines = size, lines
                return
        size -= 6
    p.size, p.lines = 56, [list(range(len(p.words)))]


def _shadowed(canvas: Image.Image, blur: int = 9, offset: int = 8, opacity: float = 0.6) -> Image.Image:
    alpha = canvas.getchannel("A")
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sh.putalpha(alpha.filter(ImageFilter.GaussianBlur(blur)).point(lambda a: int(a * opacity)))
    base = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    base.alpha_composite(sh, (0, offset))
    base.alpha_composite(canvas)
    return base


def render_page(p: Page, active: int) -> np.ndarray:
    f = font("caption", p.size)
    stroke = max(6, p.size // 9)
    space = _text_w(f, " ")
    top, bottom = f.getbbox("ĞÜÇŞ")[1], f.getbbox("ĞÜÇŞ")[3]
    line_h = int((bottom - top) * 1.12)
    texts = [tr_upper(w.text) for w in p.words]
    widths = [_text_w(f, t) for t in texts]
    line_ws = [sum(widths[i] for i in ln) + space * (len(ln) - 1) for ln in p.lines]
    pad = 40
    cw = max(line_ws) + 2 * pad
    ch = line_h * len(p.lines) + 2 * pad
    img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for li, ln in enumerate(p.lines):
        x = (cw - line_ws[li]) // 2
        y = pad + li * line_h - top
        for i in ln:
            color = ACCENT if i == active else WHITE
            d.text((x, y), texts[i], font=f, fill=color + (255,), stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
            x += widths[i] + space
    return np.asarray(_shadowed(img))


def render_hook(text: str) -> np.ndarray | None:
    text = tr_upper(text or "").strip()
    if not text:
        return None
    words = text.split()
    size = 124
    while size >= 70:
        f = font("display", size)
        space = _text_w(f, " ")
        lines, cur, cw = [], [], 0
        for w in words:
            wd = _text_w(f, w)
            add = wd if not cur else cw + space + wd
            if cur and add > 940:
                lines.append(cur)
                cur, cw = [w], wd
            else:
                cur.append(w)
                cw = add
        if cur:
            lines.append(cur)
        if len(lines) <= 3 and all(_text_w(f, " ".join(ln)) <= 960 for ln in lines):
            break
        size -= 8
    f = font("display", size)
    space = _text_w(f, " ")
    top, bottom = f.getbbox("ĞÜÇŞ")[1], f.getbbox("ĞÜÇŞ")[3]
    line_h = int((bottom - top) * 1.08)
    stroke = max(7, size // 13)
    pad = 44
    lw = [_text_w(f, " ".join(ln)) for ln in lines]
    img = Image.new("RGBA", (max(lw) + 2 * pad, line_h * len(lines) + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for li, ln in enumerate(lines):
        x = (img.width - lw[li]) // 2
        y = pad + li * line_h - top
        for w in ln:
            color = ACCENT if re.search(r"\d", w) else WHITE
            d.text((x, y), w, font=f, fill=color + (255,), stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
            x += _text_w(f, w) + space
    return np.asarray(_shadowed(img, blur=12, offset=10, opacity=0.7))


def render_badge(label: str) -> np.ndarray:
    f = font("display", 58)
    text = tr_upper(label)
    tw = _text_w(f, text)
    asc, desc = f.getmetrics()
    padx, pady = 34, 12
    w, h = tw + 2 * padx, asc + desc + 2 * pady
    img = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((20, 20, 20 + w, 20 + h), radius=h // 2, fill=ACCENT + (255,))
    d.text((20 + padx, 20 + pady - 2), text, font=f, fill=(12, 12, 12, 255))
    return np.asarray(_shadowed(img, blur=10, offset=6, opacity=0.5))


def render_label(text: str, size: int = 34, opacity: int = 170) -> np.ndarray:
    f = font("label", size)
    tw = _text_w(f, text)
    asc, desc = f.getmetrics()
    img = Image.new("RGBA", (tw + 24, asc + desc + 16), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((12, 6), text, font=f, fill=(255, 255, 255, opacity), stroke_width=3,
                             stroke_fill=(0, 0, 0, opacity))
    return np.asarray(img)


# ----------------------------------------------------------------------------
# "ABONE OL" butonu (kendi tasarımımız; YouTube logosu kullanılmaz)
# ----------------------------------------------------------------------------
CTA_H = 112


def _check(d: ImageDraw.ImageDraw, x: float, y: float, s: float, fill) -> None:
    d.line((x, y, x + s * 0.38, y + s * 0.36, x + s, y - s * 0.42), fill=fill, width=max(6, int(s * 0.2)),
           joint="curve")


@lru_cache(maxsize=4)
def render_cta(done: bool) -> np.ndarray:
    f = font("caption", 56)
    on, off = tr_upper(config.CTA_TEXT), tr_upper(config.CTA_DONE_TEXT)
    check_w = 64
    w = max(_text_w(f, on), _text_w(f, off) + check_w) + 2 * 50
    h = CTA_H
    img = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if done:
        d.rounded_rectangle((20, 20, 20 + w, 20 + h), radius=h // 2, fill=(58, 58, 62, 255),
                            outline=(150, 150, 156, 255), width=4)
        tw = _text_w(f, off) + check_w
        x = 20 + (w - tw) // 2
        _check(d, x + 4, 20 + h / 2 + 2, 40, (255, 255, 255, 255))
        d.text((x + check_w, 20 + h // 2), off, font=f, fill=(235, 235, 240, 255), anchor="lm")
    else:
        d.rounded_rectangle((20, 20, 20 + w, 20 + h), radius=h // 2, fill=(230, 33, 38, 255),
                            outline=(255, 255, 255, 255), width=4)
        d.text((20 + w // 2, 20 + h // 2), on, font=f, fill=(255, 255, 255, 255), anchor="mm")
    return np.asarray(_shadowed(img, blur=10, offset=6, opacity=0.55))


@lru_cache(maxsize=2)
def render_bell() -> np.ndarray:
    s = CTA_H
    img = Image.new("RGBA", (s + 40, s + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((20, 20, 20 + s, 20 + s), fill=(255, 255, 255, 255))
    cx, cy, r = 20 + s / 2, 20 + s / 2, s * 0.30
    dark = (24, 24, 28, 255)
    d.ellipse((cx - r * 0.16, cy - r * 1.22, cx + r * 0.16, cy - r * 0.9), fill=dark)            # tepe
    d.pieslice((cx - r * 0.78, cy - r * 1.05, cx + r * 0.78, cy + r * 0.5), 180, 360, fill=dark)   # kubbe
    d.polygon([(cx - r * 0.78, cy - r * 0.28), (cx + r * 0.78, cy - r * 0.28),
               (cx + r * 1.02, cy + r * 0.62), (cx - r * 1.02, cy + r * 0.62)], fill=dark)       # gövde
    d.rounded_rectangle((cx - r * 1.1, cy + r * 0.56, cx + r * 1.1, cy + r * 0.78), radius=int(r * 0.1), fill=dark)
    d.ellipse((cx - r * 0.24, cy + r * 0.74, cx + r * 0.24, cy + r * 1.18), fill=dark)            # tokmak
    return np.asarray(_shadowed(img, blur=10, offset=6, opacity=0.55))


@lru_cache(maxsize=2)
def render_cursor() -> np.ndarray:
    """Beyaz ok imleci (siyah kenarlı), uç noktası (6, 6)."""
    sc = 4.2
    pts = [(0, 0), (0, 17), (4, 13), (7, 20), (10, 19), (7, 12), (12.5, 12.5)]
    img = Image.new("RGBA", (80, 110), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    poly = [(6 + x * sc, 6 + y * sc) for x, y in pts]
    d.polygon(poly, fill=(255, 255, 255, 255), outline=(0, 0, 0, 255))
    d.line(poly + [poly[0]], fill=(0, 0, 0, 255), width=5, joint="curve")
    return np.asarray(_shadowed(img, blur=5, offset=4, opacity=0.5))
