"""history_export.py — выгрузка настоящей истории вашего брокера в файлы.

=====================================================================
ЗАЧЕМ
=====================================================================
Проверить стратегию можно только на данных. Своих данных у программы нет:
она смотрит на рынок в реальном времени и ничего не запоминает. Значит
историю надо один раз выгрузить из MetaTrader и положить рядом.

ЧЬИ ИМЕННО ДАННЫЕ. Только вашего брокера и только из вашего терминала.
Чужие котировки не годятся: спред, время сервера и даже сами свечи у разных
брокеров отличаются, и проверка на чужих данных проверяет чужую систему.

=====================================================================
ЧТО СОХРАНЯЕТСЯ
=====================================================================
Рядом с программой появляется папка history/raw, и в ней на каждый
инструмент два файла:

  EURUSD_M5.csv        — сами свечи
  EURUSD_M5.meta.json  — паспорт данных

Паспорт нужен не меньше самих свечей. В нём записано: брокер, сервер, номер
счёта, смещение времени сервера относительно UTC, размер пункта, минимальный
и максимальный лот, шаг лота, цена тика и — главное — СКОЛЬКО ДЕНЕГ СТОИТ
ОДИН ПУНКТ ОДНОГО ЛОТА по расчёту самого терминала. Без этого числа объём
сделки в проверке пришлось бы считать приближением, а именно на приближении
и расходились деньги по золоту.

=====================================================================
ПОСЛЕДНЯЯ СВЕЧА ВСЕГДА ЗАКРЫТА
=====================================================================
Выгрузка начинается с позиции 1, а не 0. Позиция 0 в MetaTrader — текущая,
ещё не закрытая свеча. Попади она в файл — проверка на истории увидела бы
кусок будущего в последней свече каждого прогона. Это та же самая ошибка,
которая была найдена в живой торговле (см. mt5_connector.get_rates_df), и
повторять её в данных нельзя тем более.

=====================================================================
ЧЕГО ЗДЕСЬ НЕТ
=====================================================================
Ничего не придумывается и не достраивается. Пропуски в истории остаются
пропусками и честно считаются при проверке качества (history_data.py).
Синтетических, случайных и «дорисованных» свечей в файлах не бывает.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5

import mt5_connector as mt5c

log = logging.getLogger("history_export")

# Инструменты, ради которых всё затевается. XAUUSD идёт ОТДЕЛЬНО и никогда не
# смешивается с EURUSD: это разные рынки с разной ценой пункта и разным
# поведением, и общая статистика по ним не значит ничего.
# Список нарочно не «все подряд»: это ровно те инструменты, по которым счёт
# действительно торговал (видно в отчёте брокера), плюс золото как отдельный
# исследовательский случай. Выгружать сотни пар незачем — время терминала не
# бесплатное, а проверять всё равно можно только то, чем торгуют.
DEFAULT_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "USDCHF",
                   "AUDUSD", "USDCAD", "NZDUSD", "XAUUSD")

# ТРИ ТАЙМФРЕЙМА, А НЕ ОДИН — И ЭТО НЕ ПРИХОТЬ.
#
# Средняя сделка живёт около восьми минут, то есть ПОЛТОРА бара M5. Проверка
# по M5 физически не может воспроизвести, как программа ведёт позицию: живая
# смотрит на цену каждые пять секунд, а бар меняется раз в пять минут. На M1
# та же сделка занимает восемь баров — сопоставление становится осмысленным.
# M15 нужен как старший таймфрейм тренда: сейчас он собирается из M5 сложением
# по три свечи, а настоящий M15 от брокера точнее.
DEFAULT_TIMEFRAMES = ("M1", "M5", "M15")
DEFAULT_TIMEFRAME = "M5"

# Сколько свечей просить. 200 000 баров M5 — это примерно два года торговли.
# Больше терминал обычно и не отдаёт, а меньше не хватит на разделение
# истории на обучение, проверку и чистую проверку (walk-forward).
DEFAULT_BARS = 200000

# СКОЛЬКО ПРОСИТЬ НА КАЖДОМ ТАЙМФРЕЙМЕ — И ПОЧЕМУ ЧИСЛА РАЗНЫЕ.
#
# Одинаковое число баров означает РАЗНЫЙ отрезок времени: 200 000 баров M5 —
# два года, а M1 — всего сто сорок дней. Считать «поровну» тут не значит
# «справедливо».
#
# M1 = 100 000 (около семидесяти дней). Этого с запасом хватает на весь
#   период настоящей торговли счёта, ради сверки с которым M1 и нужен.
#   Просить два года минуток незачем: файл вырос бы в пять раз, а сверять
#   его не с чем.
# M5 = 200 000 (около двух лет) — основной таймфрейм, на нём считается
#   baseline, и ему нужен полный запас на walk-forward.
# M15 = 100 000 (около трёх лет) — старший таймфрейм тренда, ему достаточно.
#
# ЧЕСТНО О ЦЕНЕ: файлы уезжают в репозиторий и остаются в его истории
# навсегда. Каждая лишняя сотня тысяч баров — это мегабайты, которые уже не
# удалить. Поэтому берётся столько, сколько нужно делу, а не «побольше».
БАРОВ_ПО_ТФ = {"M1": 100000, "M5": 200000, "M15": 100000}

RAW_FOLDER = os.path.join("history", "raw")

# Колонки файла. Порядок фиксирован: файл читается программой, и молчаливая
# перестановка колонок сломала бы чтение старых выгрузок.
COLUMNS = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]


def base_dir() -> str:
    """Папка РЯДОМ С ПРОГРАММОЙ, а не рядом с её кодом.

    Разница существенная. У собранной программы код лежит в подпапке
    _internal, и os.path.dirname(__file__) указывает именно туда. Владелец
    нажал «Выгрузить историю», а файлы уехали в _internal\\history — то есть
    в служебную папку, куда человек не заглядывает и заглядывать не должен.
    У запущенной из исходников программы обе папки совпадают, поэтому при
    разработке ошибка не видна вовсе."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def raw_path(symbol: str, timeframe: str = DEFAULT_TIMEFRAME, folder: str = "") -> str:
    root = folder or os.path.join(base_dir(), RAW_FOLDER)
    return os.path.join(root, f"{symbol}_{timeframe}.csv")


def meta_path(symbol: str, timeframe: str = DEFAULT_TIMEFRAME, folder: str = "") -> str:
    return raw_path(symbol, timeframe, folder).replace(".csv", ".meta.json")


def server_utc_offset_hours(symbol: str) -> float:
    """На сколько часов время сервера брокера отличается от всемирного (UTC).

    Свечи MetaTrader размечены ВРЕМЕНЕМ СЕРВЕРА, а не вашим и не всемирным.
    У разных брокеров оно разное — обычно UTC+2 или UTC+3, и меняется при
    переходе на летнее время. Без этого числа нельзя ни определить торговую
    сессию, ни сопоставить свечу с новостью. Возвращает None, если спросить
    не удалось: выдумывать смещение нельзя, лучше честно не знать."""
    try:
        tick = mt5c.get_tick(symbol)
        server = getattr(tick, "time", 0)
        if not server:
            return None
        сейчас = datetime.now(timezone.utc).timestamp()
        # Округляем до получаса: брокеры используют целые и получасовые пояса,
        # а разница в секундах — это задержка котировки, а не часовой пояс.
        return round((server - сейчас) / 1800.0) * 0.5
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось определить смещение времени сервера: %s", e)
        return None


def money_per_point_per_lot(symbol: str, point: float) -> float:
    """Сколько денег даёт ОДИН пункт на ОДНОМ лоте — по расчёту терминала.

    Это то самое число, ради которого в PHASE 1 появился order_calc_profit.
    Считаем его один раз при выгрузке и кладём в паспорт: во время проверки
    на истории терминала под рукой уже не будет, а приближение по цене тика
    расходится с действительностью ровно на золоте и кроссах."""
    if point <= 0:
        return 0.0
    try:
        tick = mt5c.get_tick(symbol)
        цена = float(getattr(tick, "ask", 0) or 0)
        if цена <= 0:
            return 0.0
        # Берём расстояние в 1000 пунктов и делим: на одном пункте терминал
        # может округлить ответ до нуля.
        шаг = point * 1000.0
        profit = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, symbol, 1.0, цена, цена + шаг)
        if profit is None:
            return 0.0
        return abs(float(profit)) / 1000.0
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось спросить у терминала цену пункта для %s: %s", symbol, e)
        return 0.0


def collect_meta(symbol: str, timeframe: str, bars: list, account=None) -> dict:
    """Паспорт данных: всё, что понадобится, когда терминала рядом не будет."""
    info = mt5.symbol_info(symbol)
    point = float(getattr(info, "point", 0) or 0)
    первая = bars[0]["time"] if bars else 0
    последняя = bars[-1]["time"] if bars else 0
    return {
        "version": 1,
        "symbol": symbol,
        "timeframe": timeframe,
        "broker": str(getattr(account, "company", "") or ""),
        "server": str(getattr(account, "server", "") or ""),
        "account": int(getattr(account, "login", 0) or 0),
        "account_currency": str(getattr(account, "currency", "") or ""),
        # Время свечей — ВРЕМЯ СЕРВЕРА БРОКЕРА, а не местное и не UTC.
        "bar_time_zone": "server",
        "server_utc_offset_hours": server_utc_offset_hours(symbol),
        "exported_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "bars": len(bars),
        "first_bar_server": int(первая),
        "last_bar_server": int(последняя),
        # Свойства инструмента — из терминала, а не из головы.
        "point": point,
        "digits": int(getattr(info, "digits", 0) or 0),
        "volume_min": float(getattr(info, "volume_min", 0) or 0),
        "volume_max": float(getattr(info, "volume_max", 0) or 0),
        "volume_step": float(getattr(info, "volume_step", 0) or 0),
        "trade_tick_value": float(getattr(info, "trade_tick_value", 0) or 0),
        "trade_tick_size": float(getattr(info, "trade_tick_size", 0) or 0),
        "trade_contract_size": float(getattr(info, "trade_contract_size", 0) or 0),
        "stops_level": int(getattr(info, "trade_stops_level", 0) or 0),
        "path": str(getattr(info, "path", "") or ""),
        # Главное число: цена пункта одного лота по расчёту САМОГО терминала.
        "money_per_point_per_lot": money_per_point_per_lot(symbol, point),
        # Последняя свеча заведомо закрыта: выгрузка идёт с позиции 1.
        "last_bar_closed": True,
    }


# Сколько раз переспросить терминал и сколько ждать между попытками.
#
# ЗАЧЕМ ЭТО. MetaTrader не хранит всю историю у себя: он подкачивает её с
# сервера брокера ПО ЗАПРОСУ и на первый запрос почти всегда отвечает
# пустотой — не «нет данных», а «ещё не готово». Владелец получил из-за этого
# «Терминал не отдал историю по EURUSD» при открытом терминале и работающей
# торговле: программа спросила один раз и сдалась.
ПОПЫТОК = 8
ПАУЗА_СЕКУНД = 2.0
# После этой попытки просим меньше свечей. Двухсот тысяч баров у брокера может
# просто не быть, и терминал в таком случае отвечает пустотой вместо «вот
# сколько есть».
УМЕНЬШИТЬ_ПОСЛЕ = 3
МИНИМУМ_БАРОВ = 5000


def resolve_symbol(name: str) -> str:
    """Как ЭТОТ инструмент называется у ЭТОГО брокера. Пусто — не нашёлся.

    У многих брокеров к именам добавлена приписка: EURUSD.m, EURUSDm,
    EURUSD_i, XAUUSD.raw. Требовать от человека вписывать точное имя — значит
    переложить на него работу, которую программа делает за секунду.

    Сначала точное совпадение, потом имя, НАЧИНАЮЩЕЕСЯ с нужного. Из
    нескольких похожих берём самое короткое: у него меньше всего лишнего."""
    if mt5.symbol_info(name) is not None:
        return name
    try:
        все = mt5.symbols_get() or ()
    except Exception:  # noqa: BLE001
        return ""
    похожие = [s.name for s in все
               if str(getattr(s, "name", "")).upper().startswith(name.upper())]
    return min(похожие, key=len) if похожие else ""


def _подкачать(symbol: str, tf, bars: int, progress=None):
    """Спросить свечи, переспрашивая, пока терминал их подкачивает.

    Возвращает (свечи, пояснение). Свечи None — не дождались, и в пояснении
    сказано, что именно ответил терминал."""
    сколько = max(int(bars), МИНИМУМ_БАРОВ)
    последняя = ""
    for попытка in range(ПОПЫТОК):
        rates = mt5.copy_rates_from_pos(symbol, tf, 1, сколько)
        if rates is not None and len(rates) > 0:
            return rates, ""
        try:
            последняя = str(mt5.last_error())
        except Exception:  # noqa: BLE001
            последняя = ""
        if progress:
            try:
                progress(f"{symbol}: терминал подкачивает историю, "
                         f"попытка {попытка + 1} из {ПОПЫТОК}...")
            except Exception:  # noqa: BLE001
                pass
        time.sleep(ПАУЗА_СЕКУНД)
        if попытка == УМЕНЬШИТЬ_ПОСЛЕ and сколько > МИНИМУМ_БАРОВ:
            сколько = max(МИНИМУМ_БАРОВ, сколько // 4)
    return None, последняя


def export_symbol(symbol: str, timeframe: str = DEFAULT_TIMEFRAME,
                  bars=None, folder: str = "",
                  account=None, progress=None) -> dict:
    """Выгрузить один инструмент. Возвращает отчёт о том, что получилось."""
    bars = bars_for(timeframe, bars)
    итог = {"symbol": symbol, "timeframe": timeframe, "bars": 0,
            "csv": "", "meta": "", "error": ""}
    tf = mt5c.TF_MAP.get(timeframe)
    if tf is None:
        итог["error"] = f"Неизвестный таймфрейм {timeframe}"
        return итог

    настоящее = resolve_symbol(symbol)
    if not настоящее:
        итог["error"] = (f"У брокера нет инструмента с именем {symbol} и ничего "
                         f"похожего тоже нет. Посмотрите точное имя в «Обзоре "
                         f"рынка» MetaTrader.")
        return итог
    if настоящее != symbol:
        log.info("У этого брокера %s называется %s", symbol, настоящее)
        итог["resolved"] = настоящее
        symbol = настоящее

    # ПОЗИЦИЯ 1, А НЕ 0. Ноль — текущая, ещё не закрытая свеча.
    rates, ответ_терминала = _подкачать(symbol, tf, bars, progress=progress)
    if rates is None or len(rates) == 0:
        итог["error"] = (
            f"Терминал так и не отдал историю по {symbol} за "
            f"{ПОПЫТОК} попыток"
            + (f" (ответ терминала: {ответ_терминала})" if ответ_терминала else "")
            + f". Откройте в MetaTrader график {symbol} {timeframe}, нажмите "
              f"Home и дождитесь, пока внизу перестанет мигать загрузка, "
              f"затем повторите.")
        return итог

    строки = [dict(zip(r.dtype.names, r.tolist())) if hasattr(r, "dtype") else dict(r)
              for r in rates]

    csv_file = raw_path(symbol, timeframe, folder)
    os.makedirs(os.path.dirname(csv_file), exist_ok=True)
    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        f.write(";".join(COLUMNS) + "\n")
        for row in строки:
            f.write(";".join(str(row.get(c, "")) for c in COLUMNS) + "\n")

    meta = collect_meta(symbol, timeframe, строки, account=account)
    with open(meta_path(symbol, timeframe, folder), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    итог.update(bars=len(строки), csv=csv_file, meta=meta_path(symbol, timeframe, folder))
    log.info("Выгружено %s %s: %d свечей -> %s", symbol, timeframe, len(строки), csv_file)
    return итог


def bars_for(timeframe: str, bars=None) -> int:
    """Сколько баров просить на этом таймфрейме.

    bars=None означает «по таблице» — см. пояснение к БАРОВ_ПО_ТФ. Явно
    названное число всегда сильнее таблицы: оно нужно тестам и тем, кто
    сознательно просит меньше."""
    if bars:
        return int(bars)
    return int(БАРОВ_ПО_ТФ.get(str(timeframe).upper(), DEFAULT_BARS))


def export_all(symbols=DEFAULT_SYMBOLS, timeframe=DEFAULT_TIMEFRAMES,
               bars=None, folder: str = "", progress=None) -> list:
    """Выгрузить всё нужное. Терминал должен быть уже подключён.

    timeframe принимает и одно имя, и список: раньше выгружался только M5, и
    этого оказалось мало (см. пояснение к DEFAULT_TIMEFRAMES)."""
    таймфреймы = [timeframe] if isinstance(timeframe, str) else list(timeframe)
    if len(таймфреймы) > 1:
        отчёты = []
        всего = len(symbols) * len(таймфреймы)
        сделано = 0
        for тф in таймфреймы:
            for символ in symbols:
                сделано += 1
                if progress:
                    try:
                        progress(f"[{сделано}/{всего}] {символ} {тф}...")
                    except Exception:  # noqa: BLE001
                        pass
                отчёты.extend(export_all([символ], тф, bars, folder, progress=None))
        return отчёты
    timeframe = таймфреймы[0]
    bars = bars_for(timeframe, bars)
    account = None
    try:
        account = mt5c.get_account_info()
    except Exception:  # noqa: BLE001
        pass

    отчёты = []
    for symbol in symbols:
        if progress:
            try:
                progress(f"Выгружаю {symbol} {timeframe}...")
            except Exception:  # noqa: BLE001
                pass
        # Инструмент должен быть в «Обзоре рынка», иначе истории не будет.
        # И ему нужно время: сразу после добавления терминал ещё не получил с
        # сервера ни котировку, ни свечи. У владельца EURUSD в «Обзоре рынка»
        # не было вовсе — там висели PLNJPY и SEKJPY.
        try:
            что = mt5c.select_symbol(symbol)
            if что == "добавлена":
                if progress:
                    progress(f"{symbol}: добавлен в «Обзор рынка», жду котировку...")
                time.sleep(ПАУЗА_СЕКУНД)
        except Exception:  # noqa: BLE001
            pass
        отчёты.append(export_symbol(symbol, timeframe, bars, folder,
                                     account=account, progress=progress))
    return отчёты


def describe(reports) -> str:
    """Что получилось — человеческими словами."""
    строки = []
    for r in reports or ():
        if r.get("error"):
            строки.append(f"{r['symbol']}: ОШИБКА — {r['error']}")
        else:
            строки.append(f"{r['symbol']} {r['timeframe']}: {r['bars']} свечей -> "
                          f"{os.path.basename(r['csv'])}")
    return "\n".join(строки) or "Ничего не выгружено."
