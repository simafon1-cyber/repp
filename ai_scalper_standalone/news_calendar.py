"""
news_calendar.py — экономический календарь: реальные данные через
news_providers.py (цепочка источников NEWS_PROVIDER_CHAIN). Если ни один
источник не ответил — fail-open: фильтр ничего не блокирует и ничего не
выдумывает. Это осознанный выбор: молча запретить всю торговлю из-за
недоступного календаря хуже, чем торговать без новостного фильтра, о чём
программа честно пишет на вкладке "Календарь".

ЧЕСТНО ОБ ОГРАНИЧЕНИЯХ:
  - MT5 python-пакет не даёт доступа к встроенному календарю терминала напрямую.
    Обходится сервисом mql5/CalendarExport.mq5: он выгружает календарь в файл,
    а провайдер "mt5" его читает. Если сервис не запущен — цепочка источников
    (NEWS_PROVIDER_CHAIN) переключается на запасной внешний API.
  - detect_news_breakout() намеренно консервативна: направление считается по
    простой эвристике силы движения цены за последние минуты ПОСЛЕ важной
    новости (тело свечи относительно диапазона), а не "предсказывается" —
    если движения ещё не было, сигнала не будет, и это ожидаемо.
  - Сопоставление символ -> валюта — по вхождению 3-буквенного кода в имя
    символа (XAUUSDs -> USD, тоже совпадёт и с XAU — учитываются оба).
"""

from datetime import datetime, timedelta

import config as cfg
import news_providers as npv

_KNOWN_CODES = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "XAU", "XAG", "BTC", "ETH"]


def _symbol_codes(symbol: str) -> list:
    up = symbol.upper()
    return [c for c in _KNOWN_CODES if c in up]


def news_source_chain() -> list:
    """Список источников по порядку: первый ответивший и используется.

    NEWS_PROVIDER_CHAIN задаёт цепочку целиком. Если её нет (конфиг с прошлых
    версий) — работаем по-старому, с одним NEWS_API_PROVIDER."""
    chain = getattr(cfg, "NEWS_PROVIDER_CHAIN", None)
    if chain:
        return [p for p in chain if p in npv.PROVIDERS]
    single = getattr(cfg, "NEWS_API_PROVIDER", "")
    return [single] if single else []


def _get_events():
    keys = getattr(cfg, "NEWS_API_KEYS", {}) or {}
    events, _used, error = npv.fetch_with_fallback(news_source_chain(), keys)
    return events, error


def get_events_with_source():
    """То же самое, но ещё и сообщает, КАКОЙ источник ответил — для вкладки
    «Новости»: пользователь должен видеть, откуда пришли данные, особенно
    когда основной источник отвалился и сработал запасной."""
    keys = getattr(cfg, "NEWS_API_KEYS", {}) or {}
    return npv.fetch_with_fallback(news_source_chain(), keys)


def _relevant_events_near(symbol: str, window_minutes: int, min_impact: str = "high"):
    events, _ = _get_events()
    if not events:
        return []
    codes = _symbol_codes(symbol)
    if not codes:
        return []
    now = datetime.now()
    window = timedelta(minutes=window_minutes)
    impact_rank = {"low": 0, "medium": 1, "high": 2}
    min_rank = impact_rank.get(min_impact, 2)
    return [
        e for e in events
        if e["currency"] in codes
        and impact_rank.get(e["impact"], 0) >= min_rank
        and abs(e["time"] - now) <= window
    ]


def is_high_impact_event_near(symbol: str, window_minutes: int) -> bool:
    """Есть ли рядом важная новость, из-за которой вход блокируется.

    Ноль минут означает «блокировки нет». Без этой проверки нулевое окно всё
    равно ловило бы событие, попавшее ровно в текущую минуту: разница времён
    в пределах одной минуты не больше нуля секунд только формально."""
    if not getattr(cfg, "USE_NEWS_FILTER", True):
        return False
    if int(window_minutes or 0) <= 0:
        return False
    return len(_relevant_events_near(symbol, window_minutes, min_impact="high")) > 0


def soft_news_penalty(symbol: str, window_minutes: int, penalty_points: float) -> float:
    if not getattr(cfg, "USE_NEWS_FILTER", True):
        return 0.0
    near = _relevant_events_near(symbol, window_minutes, min_impact="medium")
    if not near:
        return 0.0
    closest = min(near, key=lambda e: abs(e["time"] - datetime.now()))
    return penalty_points if closest["impact"] == "high" else penalty_points * 0.5


def detect_news_breakout(symbol: str, window_minutes: int):
    """Возвращает (has_signal, direction, confidence). См. докстринг модуля —
    направление считается по факту движения цены ПОСЛЕ важной новости, не
    предсказывается заранее."""
    if not getattr(cfg, "USE_NEWS_FILTER", True):
        return False, 0, 0.0

    events, _ = _get_events()
    if not events:
        return False, 0, 0.0
    codes = _symbol_codes(symbol)
    if not codes:
        return False, 0, 0.0

    # КАКИЕ новости вообще считаются поводом для входа.
    #
    # Владелец: «календарь отрабатывает все новости? я не заметил за ним
    # этого». Не все — и это было сделано намеренно, но нигде не написано:
    # входом считалась ТОЛЬКО важность "high". Событий такого уровня по
    # одной валюте выходит несколько штук в день, остальные (medium, low)
    # календарь показывал на вкладке «Новости», но торговать по ним не
    # пытался — со стороны это и выглядит как «новости не отрабатываются».
    #
    # Теперь порог задаётся настройкой. По умолчанию остаётся "high":
    # средние новости двигают рынок слабее, а спред на них расширяется так
    # же — снижать порог стоит осознанно.
    rank = {"low": 0, "medium": 1, "high": 2}
    min_rank = rank.get(
        str(getattr(cfg, "NEWS_TRADE_MIN_IMPACT", "high") or "high").lower(), 2)

    now = datetime.now()
    recent_event = None
    for e in events:
        if e["currency"] not in codes:
            continue
        if rank.get(e["impact"], 0) < min_rank:
            continue
        minutes_since = (now - e["time"]).total_seconds() / 60.0
        if 0 <= minutes_since <= window_minutes:
            recent_event = e
            break
    if recent_event is None:
        return False, 0, 0.0

    try:
        import mt5_connector as mt5c
        df = mt5c.get_rates_df(symbol, "M1", count=max(int(window_minutes) + 5, 10))
    except Exception:
        return False, 0, 0.0
    if df is None or len(df) < 3:
        return False, 0, 0.0

    tail = df.tail(3)
    body = float(tail["close"].iloc[-1] - tail["open"].iloc[0])
    rng = float(tail["high"].max() - tail["low"].min()) or 1e-9
    body_pct = abs(body) / rng * 100.0

    min_body_pct = getattr(cfg, "NEWS_BREAKOUT_MIN_BODY_PCT", 55)
    if body_pct < min_body_pct:
        return False, 0, 0.0

    direction = 1 if body > 0 else -1
    confidence_score = min(90.0, 50.0 + body_pct / 2)
    return True, direction, confidence_score


def upcoming_events(days_ahead: int = 3, min_impact: str = "medium"):
    """Для вкладки "Новости" в desktop_app.py: возвращает (events, error_or_None)."""
    events, error = _get_events()
    impact_rank = {"low": 0, "medium": 1, "high": 2}
    min_rank = impact_rank.get(min_impact, 1)
    now = datetime.now()
    horizon = now + timedelta(days=days_ahead)
    filtered = [
        e for e in events
        if impact_rank.get(e["impact"], 0) >= min_rank and now <= e["time"] <= horizon
    ]
    return filtered, error
