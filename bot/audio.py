"""Ses kurgusu: seslendirme + fon müziği (dosya ya da özgün üretim) + geçiş efektleri, ducking ve limiter."""
from __future__ import annotations

import logging
import random
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import config
from .media import decode_audio
from .tts import SR, SceneVoice, Word

log = logging.getLogger("shorts.audio")

LEAD_IN = 0.2   # açılıştaki "boom" efektinden hemen sonra konuşma başlar


def db(x: float) -> float:
    return 10 ** (x / 20)


@dataclass
class Timeline:
    scene_starts: list[float]          # seslendirmenin başladığı an (global)
    scene_ends: list[float]            # seslendirmenin bittiği an
    cuts: list[float]                  # görüntünün değiştiği an (sahne i başlangıcı)
    duration: float
    words: list[list[Word]] = field(default_factory=list)  # sahne bazlı, global zamanlı

    @property
    def all_words(self) -> list[Word]:
        return [w for ws in self.words for w in ws]


def build_timeline(voices: list[SceneVoice]) -> tuple[Timeline, np.ndarray]:
    t = LEAD_IN
    starts, ends, words = [], [], []
    for i, v in enumerate(voices):
        starts.append(t)
        ends.append(t + v.duration)
        words.append([Word(w.text, w.start + t, w.end + t) for w in v.words])
        t += v.duration + (config.SCENE_GAP if i < len(voices) - 1 else 0)
    duration = t + config.TAIL_SECONDS
    cuts = [0.0] + [max(0.0, s - 0.06) for s in starts[1:]]
    track = np.zeros(int(duration * SR) + SR // 10, dtype=np.float32)
    for s, v in zip(starts, voices):
        i0 = int(s * SR)
        track[i0: i0 + len(v.samples)] += v.samples
    return Timeline(starts, ends, cuts, duration, words), track


# ----------------------------------------------------------------------------
# Efekt sentezi
# ----------------------------------------------------------------------------
def _band_noise_sweep(dur: float, f0: float, f1: float, rng: np.random.Generator, bw: float = 0.5) -> np.ndarray:
    n = int(dur * SR)
    noise = rng.standard_normal(n + 2048).astype(np.float32)
    out = np.zeros(n + 2048, dtype=np.float32)
    block, hop = 1024, 512
    win = np.hanning(block).astype(np.float32)
    freqs = np.fft.rfftfreq(block, 1 / SR)
    for pos in range(0, n, hop):
        prog = pos / max(1, n)
        fc = f0 * (f1 / f0) ** prog
        spec = np.fft.rfft(noise[pos: pos + block] * win)
        gain = np.exp(-0.5 * (np.log2(np.maximum(freqs, 1) / fc) / bw) ** 2)
        out[pos: pos + block] += np.fft.irfft(spec * gain, block).astype(np.float32) * win
    return out[:n]


def whoosh(rng: np.random.Generator, dur: float = 0.5) -> np.ndarray:
    x = _band_noise_sweep(dur, 350, 4200, rng, bw=0.7)
    t = np.linspace(0, 1, len(x), dtype=np.float32)
    env = np.where(t < 0.65, (t / 0.65) ** 2.2, np.exp(-(t - 0.65) * 11))
    x *= env
    return (x / (np.abs(x).max() + 1e-9)).astype(np.float32)


def impact(rng: np.random.Generator, dur: float = 1.4) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    freq = 42 + 70 * np.exp(-t * 9)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    boom = np.sin(phase) * np.exp(-t * 3.2)
    hit = _band_noise_sweep(0.18, 2500, 300, rng, bw=1.2)
    hit *= np.exp(-np.linspace(0, 8, len(hit)))
    x = boom.astype(np.float32)
    x[: len(hit)] += 0.35 * hit / (np.abs(hit).max() + 1e-9)
    return (x / (np.abs(x).max() + 1e-9)).astype(np.float32)


# ----------------------------------------------------------------------------
# Özgün fon müziği (telif derdi yok)
# ----------------------------------------------------------------------------
_NOTE = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6, "G": 7,
         "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}

MOODS = {
    #            progression (kök, minör?)                           bpm  pulse  arp   bright
    "epic":       ([("A", 1), ("F", 0), ("C", 0), ("G", 0)],         92,  2,     0,    0.55),
    "mysterious": ([("D", 1), ("Bb", 0), ("G", 1), ("A", 0)],        78,  1,     1,    0.35),
    "uplifting":  ([("C", 0), ("G", 0), ("A", 1), ("F", 0)],         104, 2,     1,    0.8),
    "emotional":  ([("E", 1), ("C", 0), ("G", 0), ("D", 0)],         70,  0,     1,    0.5),
    "energetic":  ([("E", 1), ("C", 0), ("G", 0), ("D", 0)],         118, 4,     1,    0.75),
}


def _hz(semitone_from_a4: float) -> float:
    return 440.0 * 2 ** (semitone_from_a4 / 12)


def _midi_hz(name: str, octave: int) -> float:
    return _hz(_NOTE[name] + 12 * (octave - 4) - 9)


def _fft_lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    spec *= 1 / np.sqrt(1 + (f / cutoff) ** 4)
    return np.fft.irfft(spec, len(x)).astype(np.float32)


def _reverb(x: np.ndarray, rng: np.random.Generator, seconds: float = 2.2, wet: float = 0.28) -> np.ndarray:
    n = int(seconds * SR)
    ir = rng.standard_normal(n).astype(np.float32) * np.exp(-np.linspace(0, 7, n)).astype(np.float32)
    ir = _fft_lowpass(ir, 5000)
    ir /= np.sqrt((ir ** 2).sum()) + 1e-9
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    y = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)].astype(np.float32)
    return (1 - wet) * x + wet * y * (np.abs(x).max() / (np.abs(y).max() + 1e-9))


def synth_music(duration: float, mood: str, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    prog, bpm, pulse, arp, bright = MOODS.get(mood, MOODS["epic"])
    n = int(duration * SR)
    t = np.arange(n, dtype=np.float32) / SR
    beat = 60.0 / bpm
    chord_len = beat * 8  # 2 ölçü
    pad = np.zeros(n, dtype=np.float32)
    bass = np.zeros(n, dtype=np.float32)
    idx = 0
    start = 0.0
    while start < duration:
        root, minor = prog[idx % len(prog)]
        third = 3 if minor else 4
        notes = [(_NOTE[root], 3), (_NOTE[root] + third, 3), (_NOTE[root] + 7, 3), (_NOTE[root], 4)]
        a, b = int(start * SR), min(n, int((start + chord_len + 0.8) * SR))
        seg_t = t[a:b] - start
        env = np.minimum(1, seg_t / 0.7) * np.clip((chord_len + 0.8 - seg_t) / 0.8, 0, 1)
        for semi, octv in notes:
            f = _hz(semi + 12 * (octv - 4) - 9)
            for det in (-0.12, 0.0, 0.13):
                ff = f * 2 ** (det / 12)
                ph = rng.uniform(0, 2 * np.pi)
                pad[a:b] += (np.sin(2 * np.pi * ff * seg_t + ph) + 0.25 * np.sin(4 * np.pi * ff * seg_t + ph)) * env * 0.08
        fb = _midi_hz(root, 2)
        if pulse:
            k = 0.0
            while k < chord_len:
                p0 = int((start + k) * SR)
                p1 = min(n, p0 + int(beat * SR))
                if p0 >= n:
                    break
                lt = t[p0:p1] - (start + k)
                tail = np.clip((lt[-1] - lt) / 0.006, 0, 1) if len(lt) else lt
                bass[p0:p1] += np.sin(2 * np.pi * fb * lt) * np.exp(-lt * 4.5) * tail * 0.55
                k += beat * (2 if pulse == 1 else 1)
        else:
            bass[a:b] += np.sin(2 * np.pi * fb * seg_t) * env * 0.35
        if arp:
            step = beat / 2
            k, j = 0.0, 0
            seq = [0, third, 7, 12, 7, third]
            while k < chord_len:
                p0 = int((start + k) * SR)
                p1 = min(n, p0 + int(0.9 * SR))
                if p0 >= n:
                    break
                lt = t[p0:p1] - (start + k)
                f = _hz(_NOTE[root] + seq[j % len(seq)] + 12 - 9)
                bass[p0:p1] += np.sin(2 * np.pi * f * lt) * np.exp(-lt * 6) * 0.07 * bright
                k += step
                j += 1
        start += chord_len
        idx += 1
    drums = np.zeros(n, dtype=np.float32)
    if pulse >= 2:
        k = 0.0
        kick_n = int(0.35 * SR)
        kt = np.arange(kick_n, dtype=np.float32) / SR
        kick = np.sin(2 * np.pi * np.cumsum(45 + 90 * np.exp(-kt * 30)) / SR) * np.exp(-kt * 9) * np.clip((kt[-1] - kt) / 0.01, 0, 1)
        hat = rng.standard_normal(int(0.05 * SR)).astype(np.float32) * np.exp(-np.linspace(0, 9, int(0.05 * SR)))
        hat = hat - np.convolve(hat, np.ones(8) / 8, mode="same")  # kaba yüksek geçiren
        every = 1 if pulse >= 4 else 2
        b = 0
        while k < duration:
            p0 = int(k * SR)
            if b % every == 0:
                drums[p0: p0 + kick_n] += kick[: max(0, min(kick_n, n - p0))] * 0.5
            h0 = int((k + beat / 2) * SR)
            if h0 < n:
                drums[h0: h0 + len(hat)] += hat[: max(0, min(len(hat), n - h0))] * 0.05 * bright
            k += beat
            b += 1
    pad = _fft_lowpass(pad, 1400 + 2200 * bright)
    mix = pad + bass + drums
    mix = _reverb(mix, rng)
    # 6 sn'lik yavaş nefes (LFO) + giriş/çıkış
    mix *= (0.85 + 0.15 * np.sin(2 * np.pi * t / 6.0)).astype(np.float32)
    fade_in, fade_out = int(0.4 * SR), int(1.2 * SR)
    mix[:fade_in] *= np.linspace(0, 1, fade_in, dtype=np.float32)
    mix[-fade_out:] *= np.linspace(1, 0, fade_out, dtype=np.float32)
    return (mix / (np.abs(mix).max() + 1e-9)).astype(np.float32)


def _music_from_files(duration: float, rng: random.Random) -> tuple[np.ndarray | None, str]:
    exts = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
    files = sorted(p for p in config.MUSIC_DIR.glob("*") if p.suffix.lower() in exts)
    if not files:
        return None, ""
    f = rng.choice(files)
    try:
        x = decode_audio(f, SR)
    except Exception as e:  # noqa: BLE001
        log.warning("Müzik okunamadı (%s): %s", f.name, e)
        return None, ""
    n = int(duration * SR)
    if len(x) < SR:
        return None, ""
    # parçanın ortasından başla (girişler genelde sönük), gerekirse döngü
    start = rng.randint(0, max(0, len(x) - n)) if len(x) > n else 0
    reps = int(np.ceil((n + start) / len(x))) + 1
    y = np.tile(x, reps)[start: start + n].astype(np.float32)
    fi, fo = int(0.3 * SR), int(1.2 * SR)
    y[:fi] *= np.linspace(0, 1, fi, dtype=np.float32)
    y[-fo:] *= np.linspace(1, 0, fo, dtype=np.float32)
    credit_file = f.with_suffix(".txt")
    credit = credit_file.read_text(encoding="utf-8").strip() if credit_file.exists() else ""
    log.info("Fon müziği: %s", f.name)
    return y, credit


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x ** 2) + 1e-12))


def mix(voice: np.ndarray, tl: Timeline, mood: str, seed: int, out_wav: Path) -> str:
    """Tüm sesleri karıştırıp WAV yazar. Müzik kredisini (varsa) döndürür."""
    rng = np.random.default_rng(seed)
    n = len(voice)
    active = np.abs(voice) > 1e-3
    v_rms = _rms(voice[active]) if active.any() else 0.1
    voice = voice * (db(-15.5) / max(v_rms, 1e-6))

    music, credit = None, ""
    mode = config.MUSIC_MODE
    if mode in ("auto", "files"):
        music, credit = _music_from_files(n / SR, random.Random(seed))
    if music is None and mode in ("auto", "synth"):
        music = synth_music(n / SR, mood, seed)
        log.info("Fon müziği: özgün sentez (%s)", mood)
    out = voice.copy()
    if music is not None:
        music = music[:n] if len(music) >= n else np.pad(music, (0, n - len(music)))
        music = music * (db(-15.5 + config.MUSIC_VOLUME_DB) / max(_rms(music), 1e-6))
        # ducking: konuşma varken kıs, boşluklarda aç
        win = int(0.12 * SR)
        env = np.convolve(np.abs(voice), np.ones(win) / win, mode="same")
        env = np.clip(env / (np.percentile(env[active], 60) + 1e-9 if active.any() else 1), 0, 1)
        duck = 1.0 - 0.45 * env
        gap_boost = np.where(env < 0.05, db(4), 1.0)
        out += music * duck * gap_boost
    # efektler
    imp = impact(rng)
    out[: len(imp)] += imp[: n] * db(-12)
    wh = whoosh(rng)
    for c in tl.cuts[1:]:
        i0 = int((c - 0.30) * SR)
        if 0 <= i0 < n:
            seg = wh[: n - i0] * db(-17)
            out[i0: i0 + len(seg)] += seg
    # yumuşak limiter
    peak = np.abs(out).max()
    if peak > 0.89:
        out = np.tanh(out * 1.1) / np.tanh(1.1)
        out *= 0.97 / max(np.abs(out).max(), 1e-6)
    stereo = np.stack([out, out], axis=1)
    pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return credit
