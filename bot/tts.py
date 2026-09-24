"""Seslendirme: her sahne ayrı sentezlenir → kelime zamanlamaları kesin, sahne geçişleri sese tam oturur."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import config
from .media import decode_audio
from .util import norm_word

log = logging.getLogger("shorts.tts")

SR = 48000


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class SceneVoice:
    samples: np.ndarray
    words: list[Word] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return len(self.samples) / SR


async def _synth_async(text: str, out: Path, rate: str) -> list[tuple[str, float, float]]:
    import edge_tts
    comm = edge_tts.Communicate(text, config.TTS_VOICE, rate=rate, pitch=config.TTS_PITCH,
                                boundary="WordBoundary")
    marks = []
    with open(out, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                s = chunk["offset"] / 1e7
                marks.append((chunk["text"], s, s + chunk["duration"] / 1e7))
    return marks


def _synth(text: str, out: Path, rate: str) -> list[tuple[str, float, float]]:
    last = None
    for attempt in range(1, 5):
        try:
            marks = asyncio.run(_synth_async(text, out, rate))
            if out.exists() and out.stat().st_size > 1000:
                return marks
            raise RuntimeError("boş ses")
        except Exception as e:  # noqa: BLE001
            last = e
            wait = 3 * attempt
            log.warning("Edge-TTS hatası (%s) — %d sn sonra tekrar (%d/4)", e, wait, attempt)
            time.sleep(wait)
    raise RuntimeError(f"Edge-TTS başarısız: {last}")


def _speech_bounds(x: np.ndarray) -> tuple[float, float]:
    """Enerji eşiğiyle konuşmanın başı/sonu (kelime işareti yoksa)."""
    if len(x) == 0:
        return 0.0, 0.0
    win = int(SR * 0.02)
    n = len(x) // win
    if n == 0:
        return 0.0, len(x) / SR
    rms = np.sqrt((x[: n * win].reshape(n, win) ** 2).mean(axis=1) + 1e-12)
    thr = max(rms.max() * 0.06, 1e-4)
    idx = np.where(rms > thr)[0]
    if len(idx) == 0:
        return 0.0, len(x) / SR
    return idx[0] * win / SR, (idx[-1] + 1) * win / SR


def align_words(text: str, marks: list[tuple[str, float, float]], t0: float, t1: float) -> list[Word]:
    """Senaryodaki kelimeleri (noktalamalarıyla) TTS kelime işaretlerine eşler; eksikleri enterpolasyonla doldurur."""
    tokens = text.split()
    if not tokens:
        return []
    times: list[tuple[float, float] | None] = [None] * len(tokens)
    j = 0
    for i, tok in enumerate(tokens):
        nt = norm_word(tok)
        if not nt or j >= len(marks):
            continue
        # birkaç işaret ileriye bak (TTS bazen sayıları böler / birleştirir)
        for k in range(j, min(j + 8, len(marks))):
            nm = norm_word(marks[k][0])
            if nm and (nm == nt or nt.startswith(nm) or nm.startswith(nt)):
                start, end = marks[k][1], marks[k][2]
                acc, kk = nm, k + 1
                while kk < len(marks) and len(acc) < len(nt):
                    nxt = norm_word(marks[kk][0])
                    if nxt and nt.startswith(acc + nxt):
                        acc += nxt
                        end = marks[kk][2]
                        kk += 1
                    else:
                        break
                times[i] = (start, end)
                j = kk
                break
    matched = sum(t is not None for t in times)
    if matched < max(1, int(len(tokens) * 0.5)):
        # eşleşme zayıf: karakter uzunluğuna göre orantılı dağıt
        weights = [len(norm_word(t)) + 2 + (3 if re.search(r"[.,!?;:…]$", t) else 0) for t in tokens]
        total = sum(weights)
        acc, out = t0, []
        for tok, wgt in zip(tokens, weights):
            d = (t1 - t0) * wgt / total
            out.append(Word(tok, acc, acc + d * 0.92))
            acc += d
        return out
    # eksikleri komşulara göre doldur
    out = []
    for i, tok in enumerate(tokens):
        if times[i] is None:
            prev_end = next((times[k][1] for k in range(i - 1, -1, -1) if times[k]), t0)
            nxt_start = next((times[k][0] for k in range(i + 1, len(tokens)) if times[k]), t1)
            gap_tokens = 1
            k = i + 1
            while k < len(tokens) and times[k] is None:
                gap_tokens += 1
                k += 1
            span = max(0.05, nxt_start - prev_end) / (gap_tokens + 0)
            times[i] = (prev_end, prev_end + span * 0.95)
        out.append(Word(tok, times[i][0], times[i][1]))
    return out


def _rate_pct(rate: str) -> int:
    m = re.match(r"([+-]?\d+)%", rate.strip())
    return int(m.group(1)) if m else 0


def synthesize_scenes(narrations: list[str], workdir: Path, max_seconds: float | None = None) -> list[SceneVoice]:
    rate = config.TTS_RATE
    limit = max_seconds or config.MAX_SECONDS
    for round_ in range(2):
        voices = []
        for i, text in enumerate(narrations):
            mp3 = workdir / f"voice_{i + 1:02d}.mp3"
            marks = _synth(text, mp3, rate)
            x = decode_audio(mp3, SR)
            if marks:
                s = max(0.0, marks[0][1] - 0.05)
                e = min(len(x) / SR, marks[-1][2] + 0.16)
            else:
                s, e = _speech_bounds(x)
                s, e = max(0.0, s - 0.03), min(len(x) / SR, e + 0.08)
            seg = x[int(s * SR): int(e * SR)]
            # kısa fade (tık sesini önler)
            f = min(len(seg) // 4, int(0.008 * SR))
            if f > 0:
                seg[:f] *= np.linspace(0, 1, f, dtype=np.float32)
                seg[-f:] *= np.linspace(1, 0, f, dtype=np.float32)
            shifted = [(t, a - s, b - s) for (t, a, b) in marks]
            words = align_words(text, shifted, 0.02, len(seg) / SR - 0.05)
            voices.append(SceneVoice(seg, words))
            if not marks:
                log.warning("Sahne %d: kelime zamanlaması gelmedi → orantılı yaklaşım", i + 1)
        total = sum(v.duration for v in voices) + config.SCENE_GAP * (len(voices) - 1) + config.TAIL_SECONDS
        log.info("Seslendirme: %.1f sn (hız %s)", total, rate)
        if total <= limit or round_ == 1:
            return voices
        pct = _rate_pct(rate)
        new_pct = min(30, pct + int((total / limit - 1) * 100) + 3)
        if new_pct <= pct:
            return voices
        rate = f"{new_pct:+d}%"
        log.info("Süre hedefi aşıldı → hız %s ile yeniden sentezleniyor", rate)
    return voices
