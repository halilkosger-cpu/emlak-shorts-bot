"""Kare kare video kurgusu → ffmpeg (libx264). Ken Burns, bulanık dolgu, geçişler, altyazı, kanca, ilerleme çubuğu."""
from __future__ import annotations

import logging
import math
import random
import subprocess
import time
from datetime import date
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from . import config
from .audio import Timeline
from .captions import (BADGE_Y, CAPTION_Y, CTA_Y, HOOK_TOP, build_pages, render_badge, render_bell, render_cta,
                       render_cursor, render_hook, render_label, render_page)
from .media import ffmpeg_bin
from .util import date_tr, hex_to_rgb
from .visuals import Visual
from .writer import Story

log = logging.getLogger("shorts.render")

W, H, FPS = config.W, config.H, config.FPS
XF = 0.18                # çapraz geçiş süresi
FG_CENTER_Y = 850        # yatay görsellerin dikey merkezi (altyazının üstü)
HOOK_DUR = 2.7


def _smooth(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def _ease_out(u: float) -> float:
    u = min(1.0, max(0.0, u))
    return 1 - (1 - u) ** 3


# ----------------------------------------------------------------------------
# Katmanlar
# ----------------------------------------------------------------------------
class ImageLayer:
    """Durağan görsel + Ken Burns. Dikey görsel: tam ekran. Yatay: bulanık arka plan + ortada tam görsel."""

    MOTIONS = ["in", "out", "left", "right", "up", "down"]

    def __init__(self, path: Path, duration: float, rng: random.Random, strong: bool = False,
                 scroll_start: float = 0.0):
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        self.duration = max(0.5, duration)
        self.src_aspect = img.width / img.height
        self.mode = "scroll" if self.src_aspect < 0.45 else "cover" if self.src_aspect <= 0.8 else "fit"
        self.motion = "in" if strong else rng.choice(self.MOTIONS)
        self.zmax = 1.16 if strong else 1.11
        self.pan = (rng.uniform(0.35, 0.65), rng.uniform(0.35, 0.6))
        arr = np.asarray(img)
        if self.mode == "scroll":  # uzun ekran görüntüsü: telefonda kaydırıyormuş gibi aşağı in
            s = W / img.width
            self.src = cv2.resize(arr, (W, max(H, int(round(img.height * s)))),
                                  interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
            max_off = max(0, self.src.shape[0] - H)
            self.off0 = min(max_off, max(0.0, scroll_start) * max_off)
            self.off1 = min(max_off, self.off0 + 190 * self.duration)
            self.out_w, self.out_h, self.bg, self.y0 = W, H, None, 0
            return
        if self.mode == "cover":
            self.out_w, self.out_h = W, H
            self.src = self._prep(arr, W, H, self.zmax)
            self.bg = None
            self.y0 = 0
        else:
            fh = int(round(W / self.src_aspect))
            fh = min(fh, int(H * 0.72))
            self.out_w, self.out_h = W, fh
            self.zmax = 1.08 if not strong else 1.12
            self.src = self._prep(arr, W, fh, self.zmax)
            self.y0 = int(FG_CENTER_Y - fh / 2)
            self.bg = self._blur_bg(arr)

    @staticmethod
    def _prep(arr: np.ndarray, ow: int, oh: int, zmax: float) -> np.ndarray:
        h, w = arr.shape[:2]
        s = max(ow / w, oh / h) * zmax
        nw, nh = max(ow, int(math.ceil(w * s))), max(oh, int(math.ceil(h * s)))
        interp = cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC
        return cv2.resize(arr, (nw, nh), interpolation=interp)

    def _blur_bg(self, arr: np.ndarray) -> np.ndarray:
        small = self._prep(arr, W // 12, H // 12, 1.0)
        h, w = small.shape[:2]
        x0, y0 = (w - W // 12) // 2, (h - H // 12) // 2
        small = small[y0:y0 + H // 12, x0:x0 + W // 12]
        small = cv2.GaussianBlur(small, (0, 0), 3.2)
        bg = cv2.resize(small, (W, H), interpolation=cv2.INTER_CUBIC).astype(np.float32) * 0.62
        # görselin alt/üst kenarına yumuşak gölge
        yy = np.arange(H, dtype=np.float32)[:, None]
        top, bot = self.y0, self.y0 + self.out_h
        dist = np.minimum(np.abs(yy - top), np.abs(yy - bot))
        shadow = 1 - 0.55 * np.exp(-(dist / 38) ** 2)
        bg *= shadow[..., None]
        return np.clip(bg, 0, 255).astype(np.uint8)

    def _view(self, t: float) -> np.ndarray:
        u = min(1.0, max(0.0, t / self.duration))
        zmax = self.zmax
        if self.motion == "in":
            z = 1 + (zmax - 1) * u
            px, py = self.pan
        elif self.motion == "out":
            z = zmax - (zmax - 1) * u
            px, py = self.pan
        else:
            z = 1 + (zmax - 1) * 0.75
            d = u - 0.5
            px = 0.5 + (d * 0.8 if self.motion == "right" else -d * 0.8 if self.motion == "left" else 0)
            py = 0.5 + (d * 0.8 if self.motion == "down" else -d * 0.8 if self.motion == "up" else 0)
        sh, sw = self.src.shape[:2]
        a = z / zmax                          # kaynak → çıktı ölçeği
        vw, vh = self.out_w / a, self.out_h / a
        cx = vw / 2 + (sw - vw) * min(1, max(0, px))
        cy = vh / 2 + (sh - vh) * min(1, max(0, py))
        x0, y0 = cx - vw / 2, cy - vh / 2
        m = np.float32([[a, 0, -a * x0], [0, a, -a * y0]])
        return cv2.warpAffine(self.src, m, (self.out_w, self.out_h), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REFLECT)

    def frame(self, t: float) -> np.ndarray:
        if self.mode == "scroll":
            u = _smooth(t / self.duration)
            off = self.off0 + (self.off1 - self.off0) * u
            m = np.float32([[1, 0, 0], [0, 1, -off]])
            return cv2.warpAffine(self.src, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        view = self._view(t)
        if self.mode == "cover":
            return view
        out = self.bg.copy()
        out[self.y0:self.y0 + self.out_h] = view
        return out

    def close(self):
        pass


class VideoLayer:
    """ffmpeg ile akış halinde okunan video (gerekirse döngü). Yatay video → bulanık dolgu."""

    def __init__(self, path: Path, duration: float, src_w: int, src_h: int):
        self.path, self.duration = path, duration
        portrait = src_h >= src_w * 1.2 if src_w and src_h else True
        if portrait:
            graph = (f"[0:v]fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase:flags=bicubic,"
                     f"crop={W}:{H},setsar=1[out]")
        else:
            sw, sh = W // 10, H // 10
            graph = (f"[0:v]fps={FPS},split=2[a][b];"
                     f"[a]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},gblur=sigma=3,"
                     f"scale={W}:{H}:flags=bicubic,eq=brightness=-0.16:saturation=1.1[bg];"
                     f"[b]scale={W}:-2:flags=bicubic[fg];"
                     f"[bg][fg]overlay=(W-w)/2:{FG_CENTER_Y}-h/2,setsar=1[out]")
        cmd = [ffmpeg_bin(), "-v", "error", "-stream_loop", "-1", "-i", str(path), "-t", f"{duration + 0.2:.3f}",
               "-filter_complex", graph, "-map", "[out]", "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=W * H * 3)
        self.idx = -1
        self.last = np.zeros((H, W, 3), np.uint8)

    def frame(self, t: float) -> np.ndarray:
        want = int(round(t * FPS))
        while self.idx < want and self.proc:
            buf = self.proc.stdout.read(W * H * 3)
            if len(buf) < W * H * 3:
                self.close()
                break
            self.last = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
            self.idx += 1
        return self.last

    def close(self):
        if self.proc:
            try:
                self.proc.stdout.close()
                self.proc.kill()
                self.proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            self.proc = None


class FuncLayer:
    """Kod ile üretilen animasyonlu grafik (terminal, grafik, sayaç...)."""

    def __init__(self, duration: float, fn):
        self.duration, self.fn = duration, fn

    def frame(self, t: float) -> np.ndarray:
        fr = self.fn(t)
        return fr if fr.flags["C_CONTIGUOUS"] and fr.dtype == np.uint8 else np.ascontiguousarray(fr, dtype=np.uint8)

    def close(self):
        pass


class CardLayer:
    """Hiç görsel bulunamazsa: hareketli koyu degrade."""

    def __init__(self, duration: float, seed: int):
        rng = np.random.default_rng(seed)
        c1 = np.array(rng.choice([[18, 24, 58], [40, 12, 48], [10, 40, 52]]), np.float32)
        c2 = np.array([200, 60, 90], np.float32) * 0.35
        gw, gh = int(W * 1.4), int(H * 1.4)
        yy, xx = np.mgrid[0:gh, 0:gw].astype(np.float32)
        u = ((xx / gw) * 0.4 + (yy / gh) * 0.6)[..., None]
        self.grad = (c1 * (1 - u) + c2 * u).astype(np.uint8)
        self.duration = duration

    def frame(self, t: float) -> np.ndarray:
        u = t / max(self.duration, 0.1)
        x0 = int((self.grad.shape[1] - W) * (0.2 + 0.6 * u))
        y0 = int((self.grad.shape[0] - H) * (0.5 + 0.3 * math.sin(u * math.pi)))
        return np.ascontiguousarray(self.grad[y0:y0 + H, x0:x0 + W])

    def close(self):
        pass


# ----------------------------------------------------------------------------
# Katman üstü efektler
# ----------------------------------------------------------------------------
def _grade_mask() -> np.ndarray:
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / math.sqrt(2)
    m = 1 - 0.38 * r ** 2.2                                             # vinyet
    m *= 1 - 0.42 * np.clip(1 - yy / 620, 0, 1) ** 1.6                   # üst: rozet + kanca okunaklılığı
    m *= 1 - 0.30 * np.exp(-((yy - CAPTION_Y) / 250) ** 2)             # altyazı bandı
    m *= 1 - 0.30 * np.clip((yy - 1480) / 440, 0, 1) ** 1.4              # alt: YouTube arayüzü
    return np.repeat((np.clip(m, 0, 1) * 255).astype(np.uint8)[..., None], 3, axis=2)


def _blend(frame: np.ndarray, rgba: np.ndarray, x: int, y: int, alpha: float = 1.0) -> None:
    h, w = rgba.shape[:2]
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + w, W), min(y + h, H)
    if x1 <= x0 or y1 <= y0 or alpha <= 0.01:
        return
    sub = rgba[y0 - y:y1 - y, x0 - x:x1 - x]
    a = sub[..., 3:4].astype(np.float32) * (alpha / 255.0)
    roi = frame[y0:y1, x0:x1].astype(np.float32)
    frame[y0:y1, x0:x1] = (roi + (sub[..., :3].astype(np.float32) - roi) * a).astype(np.uint8)


def _scaled(rgba: np.ndarray, s: float) -> np.ndarray:
    if abs(s - 1) < 0.01:
        return rgba
    h, w = rgba.shape[:2]
    return cv2.resize(rgba, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_LINEAR)


def _rotate(rgba: np.ndarray, angle: float, pivot: tuple[float, float]) -> np.ndarray:
    if abs(angle) < 0.2:
        return rgba
    m = cv2.getRotationMatrix2D(pivot, angle, 1.0)
    return cv2.warpAffine(rgba, m, (rgba.shape[1], rgba.shape[0]), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))


def _draw_cta(frame: np.ndarray, u: float) -> None:
    """Son saniyelerdeki "ABONE OL" animasyonu. u: animasyon başından beri geçen süre (sn)."""
    CLICK = 1.0
    done = u >= CLICK + 0.06
    pill = render_cta(done)
    bell = render_bell()
    # giriş "pop" + tıklamada hafif basılma
    if u < 0.3:
        s = 0.55 + 0.53 * _ease_out(u / 0.22) - 0.08 * _smooth((u - 0.22) / 0.08)
    else:
        s = 1.0
    if CLICK <= u < CLICK + 0.2:
        s *= 1 - 0.07 * math.sin(math.pi * (u - CLICK) / 0.2)
    alpha = min(1.0, u / 0.12)
    ps = _scaled(pill, s)
    # zil: tıklamadan sonra sönümlü sallanma
    ang = 0.0
    if done and u < CLICK + 1.1:
        k = u - CLICK - 0.06
        ang = 20 * math.exp(-k * 3.2) * math.sin(2 * math.pi * 5.5 * k)
    bl = _rotate(bell, ang, (bell.shape[1] / 2, 34))
    bs = _scaled(bl, s)
    gap = int(10 * s)
    total = ps.shape[1] + gap + bs.shape[1]
    x0 = (W - total) // 2
    _blend(frame, ps, x0, CTA_Y - ps.shape[0] // 2, alpha)
    _blend(frame, bs, x0 + ps.shape[1] + gap, CTA_Y - bs.shape[0] // 2, alpha)
    # tıklama halkası
    tx, ty = x0 + int(ps.shape[1] * 0.62), CTA_Y + 8
    if CLICK <= u < CLICK + 0.35:
        k = (u - CLICK) / 0.35
        ov = frame.copy()
        cv2.circle(ov, (tx, ty), int(22 + 60 * k), (255, 255, 255), 6, lineType=cv2.LINE_AA)
        a = 0.75 * (1 - k)
        frame[:] = cv2.addWeighted(ov, a, frame, 1 - a, 0)
    # imleç: sağ alttan gelir, tıklar, sonra çekilir
    if 0.25 <= u < 1.9:
        cur = render_cursor()
        sx, sy = W * 0.80, CTA_Y + 330
        if u < CLICK:
            k = _smooth((u - 0.25) / (CLICK - 0.25))
            cx, cy = sx + (tx - sx) * k, sy + (ty - sy) * k
        else:
            k = _smooth((u - CLICK - 0.45) / 0.45)
            cx, cy = tx + 90 * k, ty + 160 * k
        cs = 0.84 if CLICK <= u < CLICK + 0.12 else 1.0
        ca = min(1.0, (u - 0.25) / 0.15) * (1 - _smooth((u - CLICK - 0.5) / 0.4))
        img = _scaled(cur, cs)
        _blend(frame, img, int(cx - 6 * cs), int(cy - 6 * cs), ca)


# ----------------------------------------------------------------------------
# Ana kurgu
# ----------------------------------------------------------------------------
def render(story: Story, tl: Timeline, visuals: list[Visual], badge: str | date, wav: Path, out: Path,
           seed: int) -> Path:
    t_start = time.time()
    rng = random.Random(seed)
    n = len(visuals)
    ends = tl.cuts[1:] + [tl.duration]
    layers = []
    for i, v in enumerate(visuals):
        dur = ends[i] - tl.cuts[i] + XF + 0.1
        try:
            if v.kind == "image" and v.path:
                layers.append(ImageLayer(v.path, dur, rng, strong=(i == 0),
                                         scroll_start=float(v.params.get("scroll_start", 0.0))))
            elif v.kind == "func" and v.frame_fn is not None:
                layers.append(FuncLayer(dur, v.frame_fn))
            elif v.kind == "video" and v.path:
                layers.append(VideoLayer(v.path, dur, v.width, v.height))
            else:
                layers.append(CardLayer(dur, seed + i))
        except Exception as e:  # noqa: BLE001
            log.warning("Sahne %d katmanı kurulamadı (%s) → kart", i + 1, e)
            layers.append(CardLayer(dur, seed + i))

    mask = _grade_mask()
    badge_img = render_badge(date_tr(badge) if isinstance(badge, date) else str(badge))
    hook = render_hook(story.hook_text)
    label = render_label(config.CHANNEL_HANDLE) if config.CHANNEL_HANDLE else None
    pages = build_pages(tl.words, [s.emphasis for s in story.scenes], tl.scene_ends)
    accent = hex_to_rgb(config.ACCENT_COLOR)
    # abone ol animasyonu: son saniyeler; hüzünlü/anma temalı videolarda (mood=emotional) gösterilmez
    cta_on = config.SUBSCRIBE_CTA and tl.duration >= 12 and getattr(story, "mood", "") != "emotional"
    cta_t0 = tl.duration - config.CTA_SECONDS if cta_on else None

    cmd = [ffmpeg_bin(), "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", str(wav),
           "-map", "0:v", "-map", "1:a",
           "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
           "-c:v", "libx264", "-preset", config.X264_PRESET, "-crf", str(config.X264_CRF),
           "-profile:v", "high", "-g", str(FPS * 2),
           "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-movflags", "+faststart", "-shortest", str(out)]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    total = int(round(tl.duration * FPS))
    page_i, cache_key, cache_img = 0, None, None
    closed = set()
    try:
        for fi in range(total):
            t = fi / FPS
            i = max(k for k in range(n) if tl.cuts[k] <= t + 1e-6)
            lt = t - tl.cuts[i]
            frame = layers[i].frame(lt)
            if i > 0 and lt < XF:
                prev = layers[i - 1].frame(t - tl.cuts[i - 1])
                a = _smooth(lt / XF)
                frame = cv2.addWeighted(frame, a, prev, 1 - a, 0)
            elif i > 0 and (i - 1) not in closed:
                layers[i - 1].close()
                closed.add(i - 1)
            frame = cv2.multiply(frame, mask, scale=1 / 255.0)
            # beyaz flaş: kancadan sonraki ilk kesme
            if i == 1 and lt < 0.12:
                frame = cv2.addWeighted(frame, 1.0, np.full_like(frame, 255), 0.35 * (1 - lt / 0.12), 0)

            _blend(frame, badge_img, (W - badge_img.shape[1]) // 2, BADGE_Y - badge_img.shape[0] // 2)
            if label is not None:
                _blend(frame, label, W - label.shape[1] - 36, 44)

            if hook is not None and t < HOOK_DUR:
                # ilk kare de dolu görünsün (önizleme/küçük resim için): opak başla, hafif "pop" ile otur
                s = (0.86 + 0.18 * _ease_out(t / 0.2) - 0.04 * _smooth((t - 0.2) / 0.12)) if t < 0.32 else 1.0
                alpha = 1 - _smooth((t - (HOOK_DUR - 0.25)) / 0.25)
                img = _scaled(hook, s)
                _blend(frame, img, (W - img.shape[1]) // 2, HOOK_TOP - 44 + (hook.shape[0] - img.shape[0]) // 2,
                       alpha)

            while page_i + 1 < len(pages) and pages[page_i + 1].start <= t:
                page_i += 1
            if pages and pages[page_i].start <= t < pages[page_i].end:
                p = pages[page_i]
                active = max((k for k, w in enumerate(p.words) if w.start <= t + 0.03), default=0)
                key = (page_i, active)
                if key != cache_key:
                    cache_key, cache_img = key, render_page(p, active)
                s = 1.0
                pt = t - p.start
                if pt < 0.1:
                    s = 0.86 + 0.14 * _ease_out(pt / 0.1)
                if active in p.emphasis:
                    wt = t - p.words[active].start
                    if 0 <= wt < 0.18:
                        s *= 1 + 0.08 * math.sin(math.pi * wt / 0.18)
                img = _scaled(cache_img, s)
                _blend(frame, img, (W - img.shape[1]) // 2, CAPTION_Y - img.shape[0] // 2)

            if cta_t0 is not None and t >= cta_t0:
                _draw_cta(frame, t - cta_t0)

            prog = int(W * (t / tl.duration))
            if prog > 0:
                frame[0:9, :prog] = accent
            enc.stdin.write(frame.tobytes())
            if fi % (FPS * 5) == 0:
                log.info("  render %d/%d kare", fi, total)
    finally:
        for L in layers:
            L.close()
        try:
            enc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        err = enc.stderr.read().decode(errors="ignore") if enc.stderr else ""
        enc.wait()
    if enc.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg kodlama hatası: {err[-800:]}")
    log.info("Video hazır: %s (%.1f sn, render %.0f sn)", out.name, tl.duration, time.time() - t_start)
    return out
