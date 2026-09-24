"""Gemini istemcisi: model yedekleme zinciri, 503/429 için üstel geri çekilme, sağlam JSON ayrıştırma."""
from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Any, Callable

from . import config

log = logging.getLogger("shorts.gemini")

_client = None


class GeminiError(RuntimeError):
    pass


def client():
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise GeminiError("GEMINI_API_KEY tanımlı değil")
        from google import genai
        from google.genai import types
        _client = genai.Client(api_key=config.GEMINI_API_KEY,
                               http_options=types.HttpOptions(timeout=config.GEMINI_TIMEOUT_S * 1000))
    return _client


def parse_json(text: str) -> Any:
    if text is None:
        raise ValueError("boş yanıt")
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start >= 0 and end > start:
            return json.loads(t[start:end + 1])
        raise


def _brief(e: Exception) -> str:
    """API hata mesajının okunur kısa özeti (kota metriği vb.)."""
    text = str(e)
    m = re.search(r"'message':\s*'([^']{0,400})", text) or re.search(r'"message":\s*"([^"]{0,400})', text)
    return re.sub(r"\s+", " ", m.group(1) if m else text)[:300]


def _is_gemini3(model: str) -> bool:
    return bool(re.match(r"gemini-(3|4)", model))


_dead: set[str] = set()  # bu çalışmada artık denenmeyecek modeller (404 / kota yok)


def _kind(e: Exception) -> tuple[str, int]:
    """Hatayı sınıflandır: (tür, kod)."""
    from google.genai import errors
    if isinstance(e, errors.APIError):
        code = getattr(e, "code", None) or 0
        msg = str(e).lower()
        if code == 404 or "not found" in msg or "no longer available" in msg:
            return "gone", code
        if code == 429:
            if re.search(r"limit:\s*0\b", msg) or any(k in msg.replace(" ", "") for k in ("perday", "daily")):
                return "quota_day", code
            return "quota_min", code
        if code in (400, 403):
            return "bad_request", code
        if code in (500, 502, 503, 504) or code == 0:
            return "busy", code
        return "other", code
    if "timeout" in type(e).__name__.lower() or "timed out" in str(e).lower():
        return "timeout", 0
    if isinstance(e, (ValueError, TypeError, KeyError)):
        return "bad_json", 0
    return "network", 0


def generate_json(
    contents: Any,
    *,
    system: str,
    schema: dict | None = None,
    validate: Callable[[Any], Any] | None = None,
    label: str = "",
    web_search: bool = False,
    media_resolution: str | None = None,
    attempts_per_model: int = 3,
    budget_s: float = 900,
    rounds: int = 3,
) -> Any:
    """JSON üretir. Tüm modelleri turlar halinde dener; yoğunlukta turlar arasında bekler ve
    sonraki turlarda daha hafif ayarlar (düşük düşünme / düşük görsel çözünürlük) kullanır."""
    from google.genai import types

    t0 = time.monotonic()
    last_err: Exception | None = None

    def over_budget() -> bool:
        return time.monotonic() - t0 > budget_s

    for rnd in range(rounds):
        light = rnd > 0
        for model in config.GEMINI_MODELS:
            if model in _dead:
                continue
            use_schema, use_search, use_thinking = schema is not None, web_search and not light, True
            use_media_res = ("MEDIA_RESOLUTION_LOW" if light else media_resolution) if media_resolution else None
            attempt = 0
            while attempt < attempts_per_model:
                if over_budget():
                    raise GeminiError(f"[{label}] süre bütçesi ({budget_s:.0f} sn) doldu. Son hata: {last_err}")
                attempt += 1
                cfg: dict[str, Any] = {
                    "system_instruction": system, "response_mime_type": "application/json",
                    # fonksiyon çağırma kullanmıyoruz → SDK'nın AFC uyarısını da kapatır
                    "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True)}
                if use_schema:
                    cfg["response_json_schema"] = schema
                if use_search:
                    cfg["tools"] = [types.Tool(google_search=types.GoogleSearch())]
                if use_media_res:
                    cfg["media_resolution"] = use_media_res
                if _is_gemini3(model):
                    if use_thinking:
                        cfg["thinking_config"] = types.ThinkingConfig(thinking_level="low" if light else "high")
                else:
                    cfg["temperature"] = 0.9
                try:
                    log.info("[%s] %s isteği gönderildi (tur %d, deneme %d%s)…", label, model, rnd + 1, attempt,
                             ", hafif ayar" if light else "")
                    resp = client().models.generate_content(
                        model=model, contents=contents, config=types.GenerateContentConfig(**cfg))
                    data = parse_json(resp.text)
                    if validate:
                        data = validate(data) or data
                    log.info("Gemini OK [%s] model=%s tur=%d deneme=%d", label, model, rnd + 1, attempt)
                    return data
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    kind, code = _kind(e)
                    if kind == "gone":
                        log.warning("[%s] %s kullanılamıyor (%s) → listeden çıkarıldı", label, model, _brief(e))
                        _dead.add(model)
                        break
                    if kind == "quota_day":
                        if use_search:
                            use_search = False
                            attempt -= 1
                            continue
                        log.warning("[%s] %s kotası yok/doldu → listeden çıkarıldı. Ayrıntı: %s",
                                    label, model, _brief(e))
                        _dead.add(model)
                        break
                    if kind == "quota_min":
                        if use_search:  # Google Search grounding ücretsiz katmanda yok
                            log.warning("[%s] web araması bu anahtarda kullanılamıyor → aramasız devam", label)
                            use_search = False
                            attempt -= 1
                            continue
                        if attempt >= 2:
                            log.warning("[%s] %s hâlâ 429 → sonraki model", label, model)
                            break
                        wait = 20 + random.uniform(0, 3)
                        log.warning("[%s] %s → 429 (dakikalık limit); %.0f sn bekleniyor", label, model, wait)
                        time.sleep(wait)
                        continue
                    if kind == "bad_request":
                        # Özellik uyumsuzluğu: ilgili özelliği kapatıp tekrar dene (deneme hakkı yemeden)
                        msg = str(e).lower()
                        if use_schema and "schema" in msg:
                            use_schema = False
                        elif use_search and any(k in msg for k in ("tool", "search", "grounding")):
                            use_search = False
                        elif use_media_res and "media" in msg:
                            use_media_res = None
                        elif use_thinking and "think" in msg:
                            use_thinking = False
                        elif use_search:
                            use_search = False
                        elif use_media_res:
                            use_media_res = None
                        elif use_thinking:
                            use_thinking = False
                        elif use_schema:
                            use_schema = False
                        else:
                            log.error("[%s] %s %s: %s", label, model, code, _brief(e))
                            break
                        log.warning("[%s] %s %s → bir özellik kapatılıp tekrar deneniyor (%s)", label, model,
                                    code, _brief(e)[:160])
                        attempt -= 1
                        continue
                    if kind == "busy":
                        if attempt >= 2:
                            log.warning("[%s] %s → %s (yoğun) → sonraki model", label, model, code)
                            break
                        wait = 5 + random.uniform(0, 3)
                        log.warning("[%s] %s → %s; %.0f sn bekleniyor", label, model, code, wait)
                        time.sleep(wait)
                        continue
                    if kind == "timeout":
                        log.warning("[%s] %s %d sn içinde yanıt vermedi → sonraki model", label, model,
                                    config.GEMINI_TIMEOUT_S)
                        break
                    if kind == "bad_json":
                        log.warning("[%s] geçersiz/eksik JSON (%s) — tekrar deneniyor", label, e)
                        time.sleep(1.5)
                        continue
                    wait = min(30, 5 * attempt)
                    log.warning("[%s] %s hata (%s: %s); %d sn sonra tekrar", label, model, type(e).__name__,
                                _brief(e)[:160], wait)
                    time.sleep(wait)
                    continue
        alive = [m for m in config.GEMINI_MODELS if m not in _dead]
        if not alive or rnd == rounds - 1:
            break
        pause = 45 * (rnd + 1)
        if time.monotonic() - t0 + pause > budget_s:
            break
        log.warning("[%s] şu an tüm modeller yoğun; %d sn sonra %d. tur (daha hafif ayarlarla)",
                    label, pause, rnd + 2)
        time.sleep(pause)
    raise GeminiError(f"[{label}] Gemini başarısız: {last_err}")


def image_part(data: bytes, mime: str = "image/jpeg"):
    from google.genai import types
    return types.Part.from_bytes(data=data, mime_type=mime)


def text_part(text: str):
    from google.genai import types
    return types.Part.from_text(text=text)


def user_content(parts: list):
    from google.genai import types
    return [types.Content(role="user", parts=parts)]
