"""
news_calendar.py — экономический календарь: РЕАЛЬНЫЕ данные через news_providers.py
(если на вкладке "Новости" настроен провайдер + API-ключ), иначе — прежняя
безопасная заглушка (fail-open: ничего не блокирует и не выдумывает).

ЧЕСТНО ОБ ОГРАНИЧЕНИЯХ:
  - MT5 python-пакет не даёт доступа к встроенному календарю терминала — поэтому
    данные берутся из внешнего API (news_providers.py), не из MT5.
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


def _get_events():
    provider = getattr(cfg, "NEWS_API_PROVIDER", "")
    keys = getattr(cfg, "NEWS_API_KEYS", {}) or {}
    api_key = keys.get(provider, "")
    return npv.fetch_upcoming_events(provider, api_key)


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
    if not getattr(cfg, "USE_NEWS_FILTER", True):
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

    now = datetime.now()
    recent_event = None
    for e in events:
        if e["currency"] not in codes or e["impact"] != "high":
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
