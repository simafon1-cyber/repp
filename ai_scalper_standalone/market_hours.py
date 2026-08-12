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
   отношение к СОБСТВЕННОЙ НОРМЕ пары. Норма — нижний квартиль замеров за
   сутки, то есть спред спокойного, живого рынка; подробности и история
   ошибки — у BASELINE_* ниже. Само настраивается под любой инструмент и
   любого брокера.

ЧЕСТНО О ГРАНИЦАХ ЭТОГО МОДУЛЯ

Ночью 22:00-02:00 рынок НЕ закрыт: котировки идут, брокер торговлю не
запрещает. Признаки 1 и 2 в это время не сработают и не должны. По ночным
убыткам работает признак 3 — неликвидность.

И признак 3 подтверждён механизмом, а не данными: спреды в отчёте MT5 не
записаны, проверить их задним числом не по чему. Механизм известный
(ночью ликвидность уходит, спред расширяется), но числом из отчёта он здесь
не доказан — в отличие от самой ночной просадки, которая посчитана.

ПЕРВАЯ ВЕРСИЯ ЭТОГО ПРИЗНАКА НЕ РАБОТАЛА. Норма считалась по короткому окну
в 17 минут и потому подстраивалась под ночь за те же 17 минут. Отчёт за
12.08.2026 показал это прямо: торговля с 00:01 до 05:33, минус 12.60, вход
не закрыт ни разу. Разбор и исправление — в комментарии к BASELINE_* ниже.
"""

import json
import logging
import os
import statistics
import sys
import threading
import time

log = logging.getLogger("market_hours")

# Короткое окно — только чтобы знать ТЕКУЩИЙ спред. 200 замеров при опросе
# раз в 5 секунд это около 17 минут.
SPREAD_SAMPLES = 200

# ДОЛГАЯ НОРМА — вот она и есть главное.
#
# ПОЧЕМУ ПРИШЛОСЬ ПЕРЕДЕЛЫВАТЬ. Сначала «обычный спред» считался медианой
# того самого короткого окна. Это оказалось дырой, и отчёт владельца за
# 12.08.2026 показал её в лоб: торговля шла с 00:01 до 05:33 и дала -12.60,
# а защита от неликвида не сработала НИ РАЗУ.
#
# Причина арифметическая. Ночью спред широкий ВСЁ ВРЕМЯ. Короткое окно
# длиной 17 минут за эти же 17 минут целиком заполняется ночными замерами,
# медиана становится ночной — и отношение «текущий к обычному» равно
# единице. Порог 2.5 недостижим в принципе. Защита ловила только РЕЗКИЕ
# скачки и была слепа к затяжной неликвидности, то есть ровно к тому
# случаю, ради которого писалась.
#
# Теперь норма считается по СУТКАМ и берётся НИЖНИМ КВАРТИЛЕМ, а не
# медианой: нижний квартиль суточной выборки — это спред спокойного, живого
# рынка. Ночной спред сравнивается уже с ним, и разница видна.
#
# Замер в долгую норму берётся раз в минуту, а не каждый проход: иначе
# сутки не поместились бы никуда, а частота замеров ничего не уточняет.
BASELINE_SECONDS = 60          # как часто добавлять замер в долгую норму
BASELINE_SAMPLES = 1440        # 24 часа при замере раз в минуту
BASELINE_MIN_SAMPLES = 60      # раньше часа не судим вовсе
BASELINE_PERCENTILE = 25       # «спокойный» спред, а не средний по суткам
BASELINE_FILE = "spread_baseline.json"

_lock = threading.Lock()
_spreads: dict = {}        # symbol -> list[int]   короткое окно
_baseline: dict = {}       # symbol -> list[int]   долгая норма
_baseline_at: dict = {}    # symbol -> когда последний раз пополняли норму
_last_quote: dict = {}     # symbol -> (отметка времени брокера, наш момент)
_dirty = False             # норма менялась и ещё не сохранена


# =====================================================================
# ЗАМЕРЫ
# =====================================================================
def note_spread(symbol: str, spread_points, now: float = None) -> None:
    """Запомнить очередной замер спреда. Мусор молча игнорируем: одна
    кривая цифра не должна испортить норму."""
    global _dirty
    try:
        value = int(spread_points)
    except (TypeError, ValueError):
        return
    if value <= 0:
        return
    if now is None:
        now = time.time()
    with _lock:
        row = _spreads.setdefault(symbol, [])
        row.append(value)
        if len(row) > SPREAD_SAMPLES:
            del row[:len(row) - SPREAD_SAMPLES]

        # В долгую норму — не чаще раза в минуту.
        last = _baseline_at.get(symbol)
        if last is None or now - last >= BASELINE_SECONDS:
            _baseline_at[symbol] = now
            long_row = _baseline.setdefault(symbol, [])
            long_row.append(value)
            if len(long_row) > BASELINE_SAMPLES:
                del long_row[:len(long_row) - BASELINE_SAMPLES]
            _dirty = True


def current_spread(symbol: str) -> float:
    """Спред прямо сейчас — медиана короткого окна.

    Медиана, а не последнее значение: один выброс не должен объявлять рынок
    неликвидным, а один узкий тик — объявлять его здоровым."""
    with _lock:
        row = list(_spreads.get(symbol, ()))
    if not row:
        return 0.0
    return float(statistics.median(row))


def normal_spread(symbol: str) -> float:
    """Спред этой пары на СПОКОЙНОМ рынке — нижний квартиль суточной выборки.

    Не медиана и не среднее. Медиана суток на паре, которая полночи стоит
    неликвидной, сама наполовину состоит из ночи — и «широкий спред»
    переставал бы считаться широким. Нижний квартиль отражает живой рынок,
    с которым и нужно сравнивать.

    0.0 означает «нормы ещё нет» — сравнивать не с чем, и судить нельзя."""
    with _lock:
        row = sorted(_baseline.get(symbol, ()))
    if len(row) < BASELINE_MIN_SAMPLES:
        return 0.0
    index = max(0, min(len(row) - 1,
                       int(len(row) * BASELINE_PERCENTILE / 100)))
    return float(row[index])


def spread_samples(symbol: str) -> int:
    """Сколько замеров в ДОЛГОЙ норме. Именно она решает, можно ли судить."""
    with _lock:
        return len(_baseline.get(symbol, ()))


def short_samples(symbol: str) -> int:
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
            _baseline.clear()
            _baseline_at.clear()
            _last_quote.clear()
        else:
            _spreads.pop(symbol, None)
            _baseline.pop(symbol, None)
            _baseline_at.pop(symbol, None)
            _last_quote.pop(symbol, None)


# =====================================================================
# НОРМА ПЕРЕЖИВАЕТ ПЕРЕЗАПУСК
# =====================================================================
# Без сохранения на диск получалась бы та же дыра, только медленнее:
# программу перезапустили ночью — норма собирается заново, ИЗ НОЧНЫХ
# замеров, и через час широкий ночной спред снова считается нормальным.
# А перезапускают её как раз тогда, когда что-то пошло не так, то есть
# ночью в том числе.
def _store_path() -> str:
    if getattr(sys, "frozen", False):
        folder = os.path.dirname(sys.executable)
    else:
        folder = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(folder, BASELINE_FILE)


def save_baseline(path: str = "") -> bool:
    """Сохранить долгую норму. Не удалось — не беда, но скажем в журнал."""
    global _dirty
    with _lock:
        if not _dirty and not path:
            return True
        data = {name: list(row) for name, row in _baseline.items() if row}
        _dirty = False
    try:
        with open(path or _store_path(), "w", encoding="utf-8") as f:
            json.dump({"spreads": data}, f)
        return True
    except OSError as e:
        log.debug("Не удалось сохранить норму спреда: %s", e)
        return False


def load_baseline(path: str = "") -> int:
    """Прочитать норму прошлого запуска. Возвращает число загруженных пар."""
    try:
        with open(path or _store_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return 0
    rows = data.get("spreads")
    if not isinstance(rows, dict):
        return 0
    loaded = 0
    with _lock:
        for name, row in rows.items():
            if not isinstance(row, list):
                continue
            clean = [int(v) for v in row
                     if isinstance(v, (int, float)) and not isinstance(v, bool)
                     and 0 < v < 1_000_000]
            if clean:
                _baseline[name] = clean[-BASELINE_SAMPLES:]
                loaded += 1
    return loaded


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
