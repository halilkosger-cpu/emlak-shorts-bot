"""ffmpeg yardımcıları (sistemde yoksa imageio-ffmpeg'in gömülü ikilisi kullanılır)."""
from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    sys_ff = shutil.which("ffmpeg")
    if sys_ff:
        return sys_ff
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def decode_audio(path: Path | str, sr: int = 48000, mono: bool = True) -> np.ndarray:
    """Herhangi bir ses dosyasını float32 numpy dizisine çevirir."""
    cmd = [ffmpeg_bin(), "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1" if mono else "2",
           "-ar", str(sr), "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    arr = np.frombuffer(out, dtype=np.float32).copy()
    return arr if mono else arr.reshape(-1, 2)


def probe_duration(path: Path | str) -> float:
    """ffprobe olmadan süre: ffmpeg çıktısındaki 'Duration' satırı."""
    import re
    p = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", p.stderr)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def probe_video_size(path: Path | str) -> tuple[int, int]:
    import re
    p = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", p.stderr)
    w, h = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    # telefon videolarında dönüş bilgisi
    if re.search(r"rotate\s*:\s*-?(90|270)", p.stderr) or re.search(r"rotation of -?(90|270)", p.stderr):
        w, h = h, w
    return w, h
