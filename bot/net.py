"""Ortak HTTP oturumu: otomatik tekrar deneme, kimlikli User-Agent, güvenli indirme."""
from __future__ import annotations

import logging
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config

log = logging.getLogger("shorts.net")

USER_AGENT = f"GununAnlamiShortsBot/2.0 ({config.WIKI_CONTACT}) python-requests/{requests.__version__}"


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=4, connect=4, read=3, backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
        respect_retry_after_header=False,  # saatlerce beklemeyi önle
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=16)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"})
    return s


SESSION = _session()


def get_json(url: str, params: dict | None = None, headers: dict | None = None, timeout: float = 25):
    r = SESSION.get(url, params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


def download(url: str, dest: Path, headers: dict | None = None,
             max_bytes: int = 120_000_000, timeout: float = 60) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    size = 0
    with SESSION.get(url, headers=headers, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(f"dosya çok büyük (> {max_bytes} bayt): {url}")
                f.write(chunk)
    if size == 0:
        raise ValueError(f"boş dosya: {url}")
    tmp.replace(dest)
    return dest
