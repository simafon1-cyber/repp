"""Календарь новостей на текущий день для контекста Claude.

Библиотека MetaTrader5 для Python не даёт доступ к экономическому
календарю терминала, поэтому используется открытый календарь ForexFactory.
Если он недоступен — мост честно сообщает об этом, ничего не выдумывая.
(Советник фильтрует новости независимо, через встроенный календарь MT5.)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from config import CONFIG

_cache: dict = {"fetched_at": 0.0, "events": None}
CACHE_SECONDS = 30 * 60


def get_todays_news() -> dict:
    result = {"available": False, "source": "", "events": [], "error": ""}
    cal = CONFIG["calendar"]
    if not cal["enabled"]:
        result["error"] = "календарь выключен в конфиге"
        return result

    now = time.time()
    if _cache["events"] is None or now - _cache["fetched_at"] > CACHE_SECONDS:
        try:
            resp = httpx.get(cal["url"], timeout=10)
            resp.raise_for_status()
            _cache["events"] = resp.json()
            _cache["fetched_at"] = now
        except Exception as exc:  # noqa: BLE001 — важно не упасть, а честно сообщить
            if _cache["events"] is None:
                result["error"] = f"календарь недоступен: {exc}"
                return result
            # используем старые данные, но помечаем их
            result["error"] = f"свежий календарь недоступен ({exc}), данные могли устареть"

    today = datetime.now(timezone.utc).date()
    events = []
    for ev in _cache["events"] or []:
        try:
            ev_date = datetime.fromisoformat(str(ev.get("date", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if ev_date.astimezone(timezone.utc).date() != today:
            continue
        events.append(
            {
                "time_utc": ev_date.astimezone(timezone.utc).isoformat(),
                "country": ev.get("country", ""),
                "title": ev.get("title", ""),
                "impact": ev.get("impact", ""),
            }
        )

    result["available"] = True
    result["source"] = "ForexFactory"
    result["events"] = events
    return result
