"""
news_providers.py — универсальный реестр источников экономического календаря.

Архитектура специально сделана "плагинной": чтобы добавить новый провайдер —
допиши одну функцию fetch_<name>(api_key, from_date, to_date) -> list[dict] и
зарегистрируй её в PROVIDERS (одна строка). Всё остальное (news_calendar.py,
вкладка "Новости" в desktop_app.py) уже умеет работать с любым
зарегистрированным провайдером — их не нужно трогать.

Готовые источники:
  mt5      — ВСТРОЕННЫЙ календарь MetaTrader 5. Бесплатно, без ключа, без
             лимитов. Данные приходят через файл, который пишет сервис
             mql5/CalendarExport.mq5 (python-пакет MT5 календарь не отдаёт).
  finnhub  — сторонний API, нужен бесплатный ключ с finnhub.io.

Источники можно выстроить в цепочку (fetch_with_fallback): основной — mt5,
запасной — finnhub. Они закрывают слабые места друг друга: календарю MT5
нужен открытый терминал с запущенным сервисом, Finnhub упирается в лимиты
бесплатного тарифа.

Единый формат события (dict), который должна возвращать КАЖДАЯ функция fetch_*:
    {
        "time": datetime,      # время события (локальное время этого компьютера)
        "currency": "USD",     # 3-буквенный код валюты/актива
        "event": "Nonfarm Payrolls",
        "impact": "high" | "medium" | "low",
        "actual": "...", "estimate": "...", "prev": "...",  # "" если данных ещё нет
    }
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

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


# =====================================================================
# ВСТРОЕННЫЙ КАЛЕНДАРЬ METATRADER 5
# =====================================================================
# Полностью бесплатный источник: без API-ключа, без регистрации, без лимитов
# запросов. Это те же данные, что видны во вкладке "Календарь" в терминале.
#
# Загвоздка: python-пакет MetaTrader5 календарь НЕ отдаёт — функции
# CalendarValue* существуют только в MQL5. Поэтому данные приходят через
# файл, который пишет сервис mql5/CalendarExport.mq5 (см. его шапку и
# install/Install-CalendarExport.ps1).

MT5_CALENDAR_FILENAME = "calendar_export.json"
# Файл старше этого срока считаем несвежим: значит сервис в терминале
# остановлен или терминал закрыт. Молча отдавать протухший календарь нельзя —
# фильтр новостей пропустил бы реальный выход данных.
MT5_CALENDAR_MAX_AGE_SECONDS = 3600


def mt5_calendar_path() -> str:
    """Путь к файлу календаря внутри папки данных терминала.

    Путь спрашиваем у самого MT5 (terminal_info().data_path) — руками его
    прописывать не нужно, и он останется верным даже для портативного
    терминала или нескольких установок."""
    import MetaTrader5 as mt5

    info = mt5.terminal_info()
    if info is None:
        raise RuntimeError("Нет связи с терминалом MetaTrader 5 — путь к календарю неизвестен.")
    return os.path.join(info.data_path, "MQL5", "Files", MT5_CALENDAR_FILENAME)


def _parse_mt5_time(raw: str, server_utc_offset: int):
    """Время события из файла -> местное время этого компьютера.

    Календарь MT5 отдаёт время в часовом поясе ТОРГОВОГО СЕРВЕРА (у многих
    брокеров UTC+2/+3). Без пересчёта фильтр новостей промахнулся бы на
    несколько часов — то есть блокировал бы торговлю не тогда, когда надо."""
    t = datetime.strptime(raw, "%Y.%m.%d %H:%M:%S")
    utc = (t - timedelta(seconds=server_utc_offset)).replace(tzinfo=timezone.utc)
    # .astimezone() без аргумента переводит в часовой пояс этого компьютера,
    # .replace(tzinfo=None) возвращает "наивное" время — в таком виде живут
    # все остальные даты в программе, смешивать наивные и осведомлённые нельзя.
    return utc.astimezone().replace(tzinfo=None)


def fetch_mt5(api_key: str, from_date: str, to_date: str) -> list:
    """Встроенный календарь MT5 через файл сервиса CalendarExport.

    api_key не используется — источнику ключ не нужен (см. KEYLESS_PROVIDERS)."""
    path = mt5_calendar_path()
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Файл календаря не найден: {path}. Запусти сервис CalendarExport "
            f"в терминале (Навигатор -> Сервисы) — см. install/Install-CalendarExport.ps1")

    age = time.time() - os.path.getmtime(path)
    if age > MT5_CALENDAR_MAX_AGE_SECONDS:
        raise RuntimeError(
            f"Календарь не обновлялся {int(age / 60)} мин — похоже, сервис "
            f"CalendarExport остановлен или терминал закрыт.")

    # utf-8-sig, а не utf-8: если файл вдруг придёт с меткой BOM в начале,
    # обычный utf-8 отдал бы её первым символом и json.load споткнулся бы.
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        data = json.load(f)

    offset = int(data.get("server_utc_offset_seconds", 0))
    since = datetime.strptime(from_date, "%Y-%m-%d")
    until = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)

    events = []
    for item in (data.get("events") or []):
        try:
            t = _parse_mt5_time(item.get("time", ""), offset)
        except (ValueError, TypeError):
            continue
        if not (since <= t <= until):
            continue
        events.append({
            "time": t,
            "currency": (item.get("currency") or "").upper(),
            "event": item.get("event", ""),
            "impact": (item.get("impact") or "low").lower(),
            "actual": item.get("actual", ""),
            "estimate": item.get("estimate", ""),
            "prev": item.get("prev", ""),
        })
    return events


def looks_like_broken_encoding(events) -> bool:
    """Названия новостей превратились в "??????"?

    Старая версия сервиса CalendarExport писала файл в однобайтовой кодировке
    ANSI, куда русские буквы не помещаются: терминал подставлял вместо каждой
    буквы знак вопроса. Восстановить такой текст нельзя — буквы потеряны в
    файле, а не в шрифте. Единственное лечение: обновить и перезапустить сервис
    в терминале. Эта проверка нужна, чтобы программа сказала об этом прямо, а
    не показывала молча строку из вопросительных знаков.

    Признак: у события есть название, но в нём нет ни одной буквы или цифры —
    одни "?" (и, возможно, пробелы и знаки препинания)."""
    damaged = 0
    named = 0
    for item in events or []:
        name = str(item.get("event", "") or "").strip()
        if not name:
            continue
        named += 1
        if "?" in name and not any(ch.isalnum() for ch in name):
            damaged += 1
    if named == 0:
        return False
    return damaged * 2 > named   # больше половины названий нечитаемы


BROKEN_ENCODING_HINT = (
    "Названия новостей приходят из терминала как «??????». Это старая версия "
    "сервиса CalendarExport: он записывал файл в кодировке, где нет русских "
    "букв. Лечится так: вкладка «Система» → «Установить в MetaTrader» "
    "(файл обновится и соберётся заново), затем в терминале MT5: Навигатор → "
    "Сервисы → CalendarExport → правой кнопкой → Перезапустить."
)


# Реестр провайдеров — добавляй сюда новые по образцу fetch_finnhub (например
# fetch_tradingeconomics, fetch_fmp) и впиши их сюда одной строкой.
PROVIDERS = {
    "mt5": fetch_mt5,
    "finnhub": fetch_finnhub,
}

# Провайдеры, которым API-ключ не нужен вовсе.
KEYLESS_PROVIDERS = {"mt5"}

# Понятные человеку названия для интерфейса.
PROVIDER_TITLES = {
    "mt5": "Календарь MetaTrader 5 (бесплатно, без ключа)",
    "finnhub": "Finnhub (нужен бесплатный ключ)",
}


def fetch_upcoming_events(provider: str, api_key: str, days_ahead: int = 3):
    """Возвращает (events, error_or_None). Кэширует ответ на CACHE_TTL_SECONDS —
    и вкладка "Новости", и сам торговый цикл могут звать эту функцию часто,
    а сторонний API — нет смысла дёргать чаще раза в 10 минут."""
    if not provider or provider not in PROVIDERS:
        return [], (f"Неизвестный провайдер новостей: '{provider}'. "
                     f"Доступные: {', '.join(PROVIDERS) or '—'}")
    if not api_key and provider not in KEYLESS_PROVIDERS:
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


def fetch_with_fallback(chain, keys: dict, days_ahead: int = 3):
    """Идёт по списку провайдеров и возвращает первый, который реально ответил.

    Возвращает (events, used_provider, error_or_None).

    Зачем цепочка: календарь MT5 бесплатен и не имеет лимитов, но зависит от
    того, что терминал открыт, а сервис CalendarExport запущен. Finnhub, наоборот,
    работает всегда, но упирается в лимиты бесплатного тарифа. Вместе они
    закрывают слабые места друг друга.

    ВАЖНО: пустой список событий НЕ считается отказом — на выходных новостей
    действительно нет, и переключаться на следующий источник из-за этого
    неправильно. Переход дальше по цепочке происходит только при ОШИБКЕ."""
    problems = []
    for provider in chain:
        events, error = fetch_upcoming_events(provider, (keys or {}).get(provider, ""), days_ahead)
        if error is None:
            return events, provider, None
        problems.append(f"{provider}: {error}")
        if events:
            # источник отдал устаревшие данные — берём их, но честно сообщаем
            return events, provider, error
    if not problems:
        return [], "", "Список источников новостей пуст — задай его на вкладке «Новости»."
    return [], "", "Ни один источник новостей не ответил. " + "; ".join(problems)
