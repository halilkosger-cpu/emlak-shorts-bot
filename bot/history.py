"""Yayınlanan videoların kaydı (data/history.json). Tekrarları önlemek ve çeşitlilik için kullanılır."""
from __future__ import annotations

import json
import logging
from datetime import date

from . import config
from .util import write_json

log = logging.getLogger("shorts.history")


def load() -> list[dict]:
    try:
        data = json.loads(config.HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:  # noqa: BLE001
        log.warning("history.json okunamadı (%s) — boş kabul ediliyor", e)
        return []


def save(items: list[dict]) -> None:
    config.HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_json(config.HISTORY_FILE, items[-1500:])


def recent(items: list[dict], n: int = 14) -> list[dict]:
    return items[-n:]


def same_day(items: list[dict], d: date) -> list[dict]:
    md = f"{d.month:02d}-{d.day:02d}"
    return [it for it in items if str(it.get("date", ""))[5:10] == md and str(it.get("date", "")) != d.isoformat()]


def used_keys(items: list[dict]) -> set[str]:
    return {it.get("topic_key", "") for it in items if it.get("topic_key")}
