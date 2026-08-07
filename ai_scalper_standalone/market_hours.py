"""market_hours.py — рынок закрыт? неликвиден? Спрашиваем у САМОГО РЫНКА.

ЗАЧЕМ ЭТО ПОЯВИЛОСЬ

Разбор отчёта владельца за 06-07.08.2026 показал, что весь минус счёта дала
ночь:

    ночь 22:00-01:59   22 сделки   -20.66   выигрышей 27%
    день 02:00-21:59  189 сделок    +0.67   выигрышей 48%

при итоге счёта -19.99. Напрашивалось решение «не торговать с 22 до 2». Но
владелец возразил точнее, чем предлагалось: не по часам, а «когда именно
рынок закрыт».

И он прав. Часы — плохая мера, сразу по трём причинам:

  1. Время компьютера и время сервера брокера отличаются на 2-3 часа, а
     летом и зимой по-разному. Окно, заданное часами, легко промахивается
     мимо цели целиком.
  2. У разных брокеров разное серверное время. Настройка, верная для одного,
     врёт для другого.
  3. Час — это не причина убытка. Причина — состояние рынка. Час лишь
     коррелирует с ним, и то не всегда.

Здесь состояние рынка определяется по самому рынку, без единого допущения о
часовом поясе.

ТРИ ПРИЗНАКА

1. БРОКЕР ЗАПРЕТИЛ ТОРГОВЛЮ по инструменту (trade_mode). Самый надёжный
   признак: это прямой ответ брокера, а не наша догадка.

2. КОТИРОВКИ ЗАМЕРЛИ. Считаем не «сколько времени на часах брокера», а
   сколько прошло ПО НАШИМ ЧАСАМ с момента, когда цена последний раз
   менялась. Часовые пояса в этом расчёте не участвуют вообще — сравниваем
   отметку времени брокера саму с собой. Замершая цена означает выходной,
   технический перерыв или мёртвый инструмент.

3. РЫНОК НЕЛИКВИДЕН. Спред намного шире обычного ДЛЯ ЭТОЙ ЖЕ ПАРЫ. Не
   абсолютный порог в пунктах (он у каждого инструмента и брокера свой), а
   отношение к собственной медиане пары за последние замеры. Само
   настраивается под любой инструмент и любого брокера.

ЧЕСТНО О ГРАНИЦАХ ЭТОГО МОДУЛЯ

Ночью 22:00-02:00 рынок НЕ закрыт: котировки идут, брокер торговлю не
запрещает. Признаки 1 и 2 в это время не сработают и не должны. По ночным
убыткам работает признак 3 — неликвидность.

И признак 3 подтверждён механизмом, а не данными: спреды в отчёте MT5 не
записаны, проверить их задним числом не по чему. Механизм известный
(ночью ликвидность уходит, спред расширяется), но числом из отчёта он здесь
не доказан — в отличие от самой ночной просадки, которая посчитана.
"""

import logging
import statistics
import threading
import time

log = logging.getLogger("market_hours")

# Сколько замеров спреда держим на пару. 200 при опросе раз в 5 секунд —
# это около 15 минут: достаточно, чтобы медиана отражала ТЕКУЩИЙ режим
# рынка, и мало, чтобы дневная норма не тянула ночную оценку вверх.
SPREAD_SAMPLES = 200

_lock = threading.Lock()
_spreads: dict = {}        # symbol -> list[int]
_last_quote: dict = {}     # symbol -> (отметка времени брокера, наш момент)


# =====================================================================
# ЗАМЕРЫ
# =====================================================================
def note_spread(symbol: str, spread_points) -> None:
    """Запомнить очередной замер спреда. Мусор молча игнорируем: одна
    кривая цифра не должна испортить медиану."""
    try:
        value = int(spread_points)
    except (TypeError, ValueError):
        return
    if value <= 0:
        return
    with _lock:
        row = _spreads.setdefault(symbol, [])
        row.append(value)
        if len(row) > SPREAD_SAMPLES:
            del row[:len(row) - SPREAD_SAMPLES]


def normal_spread(symbol: str) -> float:
    """Обычный спред этой пары — МЕДИАНА замеров, а не среднее.

    Именно медиана: один выброс на новостях сдвинул бы среднее вверх, и
    тогда «широкий спред» перестал бы считаться широким ровно тогда, когда
    он опаснее всего."""
    with _lock:
        row = list(_spreads.get(symbol, ()))
    if not row:
        return 0.0
    return float(statistics.median(row))


def spread_samples(symbol: str) -> int:
    with _lock:
        return len(_spreads.get(symbol, ()))


def note_quote(symbol: str, tick_time, now: float = None) -> float:
    """Запомнить отметку времени котировки. Возвращает, сколько секунд ПО
    НАШИМ ЧАСАМ цена не обновлялась.

    ПОЧЕМУ ТАК, А НЕ «now() - tick.time». Отметка времени в MT5 — это время
    ТОРГОВОГО СЕРВЕРА брокера, а не UTC и не местное. Вычесть её из наших
    часов нельзя: разница в 2-3 часа превратила бы свежую котировку в
    «замерла на три часа» либо наоборот. Здесь отметка брокера сравнивается
    САМА С СОБОЙ: изменилась — значит рынок жив, а сколько прошло, меряем уже
    своими часами. Часовой пояс брокера в расчёт не входит вообще."""
    if now is None:
        now = time.time()
    with _lock:
        previous = _last_quote.get(symbol)
        if previous is None or previous[0] != tick_time:
            _last_quote[symbol] = (tick_time, now)
            return 0.0
        return max(0.0, now - previous[1])


def frozen_seconds(symbol: str, now: float = None) -> float:
    if now is None:
        now = time.time()
    with _lock:
        previous = _last_quote.get(symbol)
    if previous is None:
        return 0.0
    return max(0.0, now - previous[1])


def reset(symbol: str = None) -> None:
    """Забыть замеры. Без символа — по всем парам."""
    with _lock:
        if symbol is None:
            _spreads.clear()
            _last_quote.clear()
        else:
            _spreads.pop(symbol, None)
            _last_quote.pop(symbol, None)


# =====================================================================
# РЕШЕНИЕ
# =====================================================================
def trade_disabled_reason(trade_mode) -> str:
    """Брокер запретил торговлю по инструменту.

    В MT5 SYMBOL_TRADE_MODE_DISABLED = 0. Значение None означает «не знаем»
    (нет описания символа) — это НЕ запрет: молчать надёжнее, чем выдумать
    запрет и остановить торговлю на ровном месте."""
    if trade_mode is None:
        return ""
    try:
        mode = int(trade_mode)
    except (TypeError, ValueError):
        return ""
    if mode == 0:
        return "брокер закрыл торговлю по этому инструменту"
    return ""


def frozen_reason(frozen: float, limit: float) -> str:
    """Котировки не обновлялись дольше допустимого."""
    if limit <= 0 or frozen < limit:
        return ""
    return (f"цена не обновлялась {frozen:.0f} с — рынок закрыт "
            f"или инструмент не торгуется")


def thin_reason(current_points, median_points: float, samples: int,
                ratio: float, min_samples: int) -> str:
    """Спред намного шире обычного для ЭТОЙ пары — рынок неликвиден."""
    if ratio <= 0 or median_points <= 0:
        return ""
    if samples < max(1, int(min_samples)):
        return ""            # ещё не набрали, с чем сравнивать
    try:
        current = float(current_points)
    except (TypeError, ValueError):
        return ""
    if current <= 0:
        return ""
    if current < median_points * ratio:
        return ""
    return (f"спред {current:.0f} при обычном {median_points:.0f} для этой "
            f"пары (в {current / median_points:.1f} раза шире) — рынок "
            f"неликвиден")


def market_block_reason(symbol: str, trade_mode=None, spread_points=None,
                        dead_seconds: float = 90.0, thin_ratio: float = 2.5,
                        thin_min_samples: int = 30, now: float = None) -> str:
    """Одна причина, почему сейчас НЕ СТОИТ ОТКРЫВАТЬ новую сделку по этой
    паре. Пустая строка — препятствий нет.

    Порядок важен: сначала прямой ответ брокера, потом замершая цена, и
    только потом наша оценка ликвидности. Человеку показывается самая
    надёжная из сработавших причин, а не первая попавшаяся.

    ЭТО НЕ ОСТАНОВКА БОТА. Уже открытые сделки продолжают вестись обычным
    порядком — трейлинг, безубыток, частичное закрытие работают. Запрещается
    только ВХОД, и только по этой паре."""
    reason = trade_disabled_reason(trade_mode)
    if reason:
        return reason

    reason = frozen_reason(frozen_seconds(symbol, now), dead_seconds)
    if reason:
        return reason

    return thin_reason(spread_points, normal_spread(symbol),
                       spread_samples(symbol), thin_ratio, thin_min_samples)


def describe(symbol: str) -> str:
    """Строка для окна: что мы вообще знаем про эту пару."""
    median = normal_spread(symbol)
    samples = spread_samples(symbol)
    if not samples:
        return "замеров спреда ещё нет"
    return (f"обычный спред {median:.0f} пунктов "
            f"(по {samples} замерам), цена стоит {frozen_seconds(symbol):.0f} с")
