"""
news_providers.py — универсальный реестр источников экономического календаря.

Архитектура специально сделана "плагинной": чтобы добавить новый провайдер —
допиши одну функцию fetch_<name>(api_key, from_date, to_date) -> list[dict] и
зарегистрируй её в PROVIDERS (одна строка). Всё остальное (news_calendar.py,
вкладка "Новости" в desktop_app.py) уже умеет работать с любым
зарегистрированным провайдером — их не нужно трогать.

Единый формат события (dict), который должна возвращать КАЖДАЯ функция fetch_*:
    {
        "time": datetime,      # время события (локальное время этого компьютера)
        "currency": "USD",     # 3-буквенный код валюты/актива
        "event": "Nonfarm Payrolls",
        "impact": "high" | "medium" | "low",
        "actual": "...", "estimate": "...", "prev": "...",  # "" если данных ещё нет
    }
"""

import logging
from datetime import datetime, timedelta

log = logging.getLogger("news_providers")

_CACHE: dict = {}  # provider -> {"ts": datetime, "events": [...]}
CACHE_TTL_SECONDS = 600  # не дёргать API чаще раза в 10 минут (важно для бесплатных тарифов)


def fetch_finnhub(api_key: str, from_date: str, to_date: str) -> list:
    """https://finnhub.io — бесплатный тариф, экономический календарь."""
    import requests

    url = "https://finnhub.io/api/v1/calendar/economic"
    resp = requests.get(url, params={"token": api_key, "from": from_date, "to": to_date}, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    events = []
    for item in (data.get("economicCalendar") or []):
        raw_time = item.get("time", "")
        try:
            t = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue
        events.append({
            "time": t,
            "currency": (item.get("country") or "").upper(),
            "event": item.get("event", ""),
            "impact": (item.get("impact") or "low").lower(),
            "actual": item.get("actual", ""),
            "estimate": item.get("estimate", ""),
            "prev": item.get("prev", ""),
        })
    return events


# Реестр провайдеров — добавляй сюда новые по образцу fetch_finnhub (например
# fetch_tradingeconomics, fetch_fmp) и впиши их сюда одной строкой.
PROVIDERS = {
    "finnhub": fetch_finnhub,
}


def fetch_upcoming_events(provider: str, api_key: str, days_ahead: int = 3):
    """Возвращает (events, error_or_None). Кэширует ответ на CACHE_TTL_SECONDS —
    и вкладка "Новости", и сам торговый цикл могут звать эту функцию часто,
    а сторонний API — нет смысла дёргать чаще раза в 10 минут."""
    if not provider or provider not in PROVIDERS:
        return [], (f"Неизвестный провайдер новостей: '{provider}'. "
                     f"Доступные: {', '.join(PROVIDERS) or '—'}")
    if not api_key:
        return [], "API-ключ не задан — впиши его на вкладке «Новости»."

    cached = _CACHE.get(provider)
    if cached and (datetime.now() - cached["ts"]).total_seconds() < CACHE_TTL_SECONDS:
        return cached["events"], None

    try:
        today = datetime.now()
        from_date = today.strftime("%Y-%m-%d")
        to_date = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        events = PROVIDERS[provider](api_key, from_date, to_date)
        events.sort(key=lambda e: e["time"])
        _CACHE[provider] = {"ts": datetime.now(), "events": events}
        return events, None
    except Exception as e:
        log.warning("Не удалось получить новости от %s: %s", provider, e)
        if cached:
            # лучше отдать устаревшие данные, чем ничего (fail-soft)
            return cached["events"], f"Не удалось обновить (показаны старые данные): {e}"
        return [], f"Ошибка получения новостей: {e}"
