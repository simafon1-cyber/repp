"""
trading_schedule.py — ответ на вопрос "когда бот будет работать, а когда нет
и почему".

Смысл модуля: собрать в одном месте ВСЕ причины, по которым бот не входит в
сделку, и показать их заранее — по времени, с названием новости и списком
затронутых пар.

ГЛАВНОЕ ПРАВИЛО ЭТОГО ФАЙЛА: он не решает ничего сам и НЕ является вторым,
параллельным набором правил. Он повторяет ровно те проверки, что стоят на
входе в сделку (main.process_symbol -> risk_manager / news_calendar), и берёт
те же самые настройки. Если расписание показывает "не входит с 15:00 до
15:30" — значит бот действительно не войдёт, а не "примерно тогда же".

Разойтись расписание и реальность могут только одним способом: если кто-то
поменяет фильтр входа и забудет поменять здесь. Ровно на это и написаны
тесты в tests/test_schedule.py — они сверяют оба места.
"""

from datetime import datetime, timedelta

import config as cfg
import news_calendar

# Что бот делает в этом окне
ACTION_BLOCK = "block"      # новые сделки не открываются вовсе
ACTION_PENALTY = "penalty"  # входит, но порог строже (score штрафуется)

ACTION_TITLES = {
    ACTION_BLOCK: "не входит",
    ACTION_PENALTY: "входит строже",
}

# Почему бот не работает — причины, не связанные с новостями
REASON_HOURS = "Вне разрешённых часов торговли"
REASON_WEEKEND = "Выходной — рынок закрыт"
REASON_NEWS = "Рядом важная новость"
REASON_OK = "Работает"


def hard_block_minutes() -> int:
    """Окно жёсткой блокировки вокруг важной новости — ровно то же значение,
    что читает main.process_symbol."""
    return getattr(cfg, "NEWS_HARD_BLOCK_WINDOW_MIN",
                   getattr(cfg, "NEWS_BREAKOUT_WINDOW_MIN", 15))


def soft_penalty_minutes() -> int:
    """Окно мягкого штрафа score — ровно то же значение, что читает
    signal_engine. Оно отдельное от окна блокировки: блокировка это пауза, а
    штраф — нет, и снятие пауз не должно отключать штраф заодно."""
    return int(getattr(cfg, "NEWS_SOFT_PENALTY_WINDOW_MIN", 30) or 0)


def symbol_codes(symbol: str) -> list:
    """Какие валюты/металлы затрагивает этот символ. Берём функцию из
    news_calendar, чтобы сопоставление было ОДНО на всю программу."""
    return news_calendar._symbol_codes(symbol)


def event_affects(symbol: str, event: dict) -> bool:
    return event.get("currency", "") in symbol_codes(symbol)


def affected_symbols(symbols, event: dict) -> list:
    return [s for s in symbols if event_affects(s, event)]


def event_window(event: dict):
    """Окно, в котором это событие влияет на торговлю: (начало, конец, действие).

    Важная новость блокирует вход целиком, средняя — только ужесточает порог.
    Слабые новости не влияют никак, для них возвращается None."""
    impact = event.get("impact", "low")
    if impact not in ("high", "medium"):
        return None
    if impact == "high":
        minutes = hard_block_minutes()
        action = ACTION_BLOCK
    else:
        minutes = soft_penalty_minutes()
        action = ACTION_PENALTY
    if minutes <= 0:
        # Окно нулевой ширины — это не окно. Иначе вкладка «Календарь» рисовала
        # бы «паузу» точкой на графике и строкой в таблице, хотя торговля в это
        # время не прерывается: расписание обязано показывать то, что
        # происходит на самом деле, иначе ему нельзя верить вообще.
        return None
    start = event["time"] - timedelta(minutes=minutes)
    end = event["time"] + timedelta(minutes=minutes)
    return start, end, action


def news_filter_enabled() -> bool:
    return bool(getattr(cfg, "USE_NEWS_FILTER", True))


def build_schedule(symbols, events, now: datetime = None, hours_ahead: int = 24) -> list:
    """Список окон влияния новостей на ближайшие hours_ahead часов.

    Каждая запись:
        {"event", "currency", "impact", "time", "start", "end",
         "action", "symbols", "active_now"}

    Отсортировано по времени события. События, не затрагивающие ни один из
    ВАШИХ символов, отбрасываются: показывать выход японской статистики
    человеку, торгующему только EURUSD и золотом, — значит топить полезное в
    шуме."""
    if now is None:
        now = datetime.now()
    horizon = now + timedelta(hours=hours_ahead)

    rows = []
    if not news_filter_enabled():
        return rows

    for e in events:
        window = event_window(e)
        if window is None:
            continue
        start, end, action = window
        # Берём и уже идущие окна (начались раньше "сейчас", но ещё не кончились)
        if end < now or e["time"] > horizon:
            continue
        affected = affected_symbols(symbols, e)
        if not affected:
            continue
        rows.append({
            "event": e.get("event", ""),
            "currency": e.get("currency", ""),
            "impact": e.get("impact", "low"),
            "time": e["time"],
            "start": start,
            "end": end,
            "action": action,
            "symbols": affected,
            "active_now": start <= now <= end,
        })

    rows.sort(key=lambda r: r["time"])
    return rows


def hours_window_text() -> str:
    """Разрешённые часы торговли одной строкой — для показа человеку."""
    if not getattr(cfg, "USE_TRADING_HOURS", False):
        return "круглосуточно"
    start = getattr(cfg, "TRADING_START_HOUR", 0)
    end = getattr(cfg, "TRADING_END_HOUR", 24)
    if start == end:
        return "круглосуточно"
    return f"{start:02d}:00–{end:02d}:00"


def within_trading_hours(moment: datetime) -> bool:
    """Повторяет risk_manager.trading_hours_ok(), но для ЛЮБОГО момента, а не
    только для "сейчас" — расписание должно уметь смотреть вперёд.

    Диапазон через полночь (например 22..6) обрабатывается так же, как там."""
    if not getattr(cfg, "USE_TRADING_HOURS", False):
        return True
    start = getattr(cfg, "TRADING_START_HOUR", 0)
    end = getattr(cfg, "TRADING_END_HOUR", 24)
    if start == end:
        return True
    hour = moment.hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def is_market_weekend(moment: datetime) -> bool:
    """Суббота и воскресенье до вечера — рынок форекс закрыт.

    Это подсказка для человека, а не торговый фильтр: бот и так не сможет
    ничего открыть при закрытом рынке, брокер отклонит ордер. Но в расписании
    честнее написать "выходной", чем "работает"."""
    weekday = moment.weekday()          # 0 = понедельник
    if weekday == 5:                    # суббота
        return True
    if weekday == 6:                    # воскресенье
        return True
    return False


def current_status(symbols, events, now: datetime = None) -> dict:
    """Что происходит ПРЯМО СЕЙЧАС: работает бот или нет и почему.

    Возвращает {"trading": bool, "reason": str, "detail": str, "until": datetime|None}."""
    if now is None:
        now = datetime.now()

    if is_market_weekend(now):
        return {"trading": False, "reason": REASON_WEEKEND,
                "detail": "Торги возобновятся в понедельник.", "until": None}

    if not within_trading_hours(now):
        return {"trading": False, "reason": REASON_HOURS,
                "detail": f"Разрешённые часы: {hours_window_text()}.", "until": None}

    active = [r for r in build_schedule(symbols, events, now, hours_ahead=1)
              if r["active_now"] and r["action"] == ACTION_BLOCK]
    if active:
        soonest_end = min(r["end"] for r in active)
        names = ", ".join(sorted({r["event"] for r in active}))
        pairs = ", ".join(sorted({s for r in active for s in r["symbols"]}))
        return {"trading": False, "reason": REASON_NEWS,
                "detail": f"{names} ({pairs}). Вход откроется в "
                          f"{soonest_end.strftime('%H:%M')}.",
                "until": soonest_end}

    return {"trading": True, "reason": REASON_OK,
            "detail": f"Разрешённые часы: {hours_window_text()}.", "until": None}


def next_block(symbols, events, now: datetime = None):
    """Ближайшая будущая блокировка или None. Для строки "следующая пауза в …"."""
    if now is None:
        now = datetime.now()
    future = [r for r in build_schedule(symbols, events, now, hours_ahead=48)
              if r["action"] == ACTION_BLOCK and r["start"] > now]
    return min(future, key=lambda r: r["start"]) if future else None


def quiet_windows(symbols, events, now: datetime = None, hours_ahead: int = 12) -> list:
    """Промежутки, в которые бот точно свободен — ни новостей, ни запрета по
    часам. Возвращает список (начало, конец).

    Зачем отдельно: человеку удобнее видеть "спокойно с 16:10 до 19:45", чем
    вычитать это в уме из списка блокировок."""
    if now is None:
        now = datetime.now()
    horizon = now + timedelta(hours=hours_ahead)

    busy = []
    for r in build_schedule(symbols, events, now, hours_ahead):
        if r["action"] == ACTION_BLOCK:
            busy.append((max(r["start"], now), min(r["end"], horizon)))

    # Часы вне разрешённых — тоже занятое время. Идём по часам: точность в
    # один час здесь достаточна, настройка и задана целыми часами.
    hour = now.replace(minute=0, second=0, microsecond=0)
    while hour < horizon:
        nxt = hour + timedelta(hours=1)
        if not within_trading_hours(hour) or is_market_weekend(hour):
            busy.append((max(hour, now), min(nxt, horizon)))
        hour = nxt

    if not busy:
        return [(now, horizon)]

    # Сортировка обязательна: идём слева направо и двигаем курсор по концам
    # занятых окон. На неотсортированном списке далёкое окно сдвинуло бы
    # курсор вперёд, и более раннее занятое время попало бы в "свободные".
    # Перекрытия отдельно склеивать не нужно — max(cursor, end) делает это сам.
    busy.sort()
    free = []
    cursor = now
    for start, end in busy:
        if start > cursor:
            free.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < horizon:
        free.append((cursor, horizon))

    # Окна короче пары минут человеку бесполезны — это щели между блокировками
    return [(a, b) for a, b in free if (b - a).total_seconds() >= 120]
