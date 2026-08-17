"""history_data.py — чтение выгруженной истории и ЧЕСТНАЯ проверка её качества.

=====================================================================
ЗАЧЕМ ПРОВЕРЯТЬ ДАННЫЕ
=====================================================================
Проверка стратегии на плохих данных даёт плохой ответ, который выглядит
ровно так же убедительно, как хороший. Поэтому данные проверяются ДО того,
как по ним что-то считается, и все замечания печатаются человеку.

Что проверяется и почему именно это:

  1. ПОРЯДОК. Свечи обязаны идти от старых к новым. Перепутанный порядок
     означает, что «прошлое» окажется после «будущего», и любая проверка на
     истории превратится в заглядывание вперёд.
  2. ДУБЛИКАТЫ. Одна и та же свеча дважды — это лишняя сделка на ровном
     месте и завышенная статистика.
  3. ПРОПУСКИ. Между свечами M5 обязано быть ровно 300 секунд. Пропуски
     бывают законные (выходные, ночь у некоторых инструментов) и незаконные
     (терминал не докачал историю). Здесь они считаются и показываются, но
     НЕ ЗАПОЛНЯЮТСЯ: дорисованная свеча — это выдуманные данные.
  4. НЕЗАКРЫТАЯ ПОСЛЕДНЯЯ СВЕЧА. Выгрузка идёт с позиции 1, но файл мог
     быть получен и другим способом. Если последняя свеча моложе своего
     периода — она не закрыта и в расчёт идти не может.
  5. ЧАСОВОЙ ПОЯС. Свечи размечены временем СЕРВЕРА брокера. Без записанного
     смещения нельзя ни определить сессию, ни сопоставить свечу с новостью,
     поэтому данные без паспорта считаются неполными.
  6. БИТЫЕ ЦЕНЫ. high не может быть меньше low, цены не могут быть нулями
     или отрицательными.

=====================================================================
СЫРОЕ И ОБРАБОТАННОЕ ХРАНЯТСЯ ОТДЕЛЬНО
=====================================================================
history/raw — ровно то, что отдал терминал, не меняется никогда.
history/clean — то, что прошло проверку и годится для расчёта.
Так всегда можно вернуться к исходнику и посмотреть, что именно было
выброшено и почему.
"""

import json
import logging
import os
import sys

log = logging.getLogger("history_data")

RAW_FOLDER = os.path.join("history", "raw")
CLEAN_FOLDER = os.path.join("history", "clean")

# Сколько секунд в одном баре. Ключи совпадают с именами таймфреймов MT5.
BAR_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800,
               "H1": 3600, "H4": 14400, "D1": 86400}

# Разрыв больше этого числа баров считается ЗАКОННЫМ перерывом (выходные,
# праздники, ночной перерыв инструмента), а не потерей данных. Двенадцать
# часов на M5 — это 144 бара; обычная ночная пауза короче, выходные длиннее.
ЗАКОННЫЙ_ПЕРЕРЫВ_БАРОВ = 144


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


def raw_path(symbol: str, timeframe: str = "M5", folder: str = "") -> str:
    root = folder or os.path.join(base_dir(), RAW_FOLDER)
    return os.path.join(root, f"{symbol}_{timeframe}.csv")


def meta_for(csv_path: str) -> dict:
    """Паспорт данных рядом с файлом свечей. Пустой словарь — паспорта нет."""
    path = csv_path.replace(".csv", ".meta.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        log.warning("Паспорт данных %s не читается: %s", path, e)
        return {}


def load_csv(path: str) -> list:
    """Прочитать свечи. Возвращает список словарей в порядке файла.

    Ничего не сортирует и не чинит: сначала надо увидеть данные такими, какие
    они есть, и только потом решать, что с ними не так."""
    if not os.path.exists(path):
        return []
    строки = []
    with open(path, "r", encoding="utf-8") as f:
        заголовок = f.readline().strip().split(";")
        for line in f:
            части = line.strip().split(";")
            if len(части) < len(заголовок):
                continue
            row = {}
            плохая = False
            for имя, значение in zip(заголовок, части):
                try:
                    row[имя] = int(значение) if имя in ("time", "tick_volume",
                                                        "spread", "real_volume") \
                        else float(значение)
                except (TypeError, ValueError):
                    плохая = True
                    break
            if not плохая:
                строки.append(row)
    return строки


def check_quality(bars, timeframe: str = "M5", meta: dict = None,
                  now_server: int = 0) -> dict:
    """Что не так с данными. Ничего не исправляет — только называет.

    now_server: время сервера «сейчас», если известно. Нужно только для
    проверки незакрытой последней свечи."""
    период = BAR_SECONDS.get(timeframe, 300)
    отчёт = {
        "bars": len(bars or []),
        "timeframe": timeframe,
        "first": 0, "last": 0,
        "duplicates": 0,
        "out_of_order": 0,
        "gaps": 0,
        "missing_bars": 0,
        "legal_breaks": 0,
        "bad_prices": 0,
        "last_bar_open": False,
        "timezone_known": False,
        "server_utc_offset_hours": None,
        "problems": [],
        "usable": False,
    }
    if not bars:
        отчёт["problems"].append("Данных нет вовсе: файл пустой или не найден.")
        return отчёт

    отчёт["first"] = int(bars[0].get("time", 0))
    отчёт["last"] = int(bars[-1].get("time", 0))

    видели = set()
    прошлое = None
    for b in bars:
        t = int(b.get("time", 0))
        if t in видели:
            отчёт["duplicates"] += 1
        видели.add(t)

        if прошлое is not None:
            шаг = t - прошлое
            if шаг <= 0:
                отчёт["out_of_order"] += 1
            elif шаг > период:
                пропущено = int(шаг / период) - 1
                if пропущено >= ЗАКОННЫЙ_ПЕРЕРЫВ_БАРОВ:
                    отчёт["legal_breaks"] += 1
                else:
                    отчёт["gaps"] += 1
                    отчёт["missing_bars"] += пропущено
        прошлое = t

        h, l = float(b.get("high", 0)), float(b.get("low", 0))
        o, c = float(b.get("open", 0)), float(b.get("close", 0))
        if h < l or min(h, l, o, c) <= 0 or o > h or o < l or c > h or c < l:
            отчёт["bad_prices"] += 1

    meta = meta or {}
    смещение = meta.get("server_utc_offset_hours")
    отчёт["server_utc_offset_hours"] = смещение
    отчёт["timezone_known"] = смещение is not None

    # Последняя свеча обязана быть закрытой. Если известно время сервера —
    # проверяем прямо; иначе верим паспорту, а без паспорта не верим никому.
    if now_server:
        отчёт["last_bar_open"] = (now_server - отчёт["last"]) < период
    else:
        отчёт["last_bar_open"] = not bool(meta.get("last_bar_closed"))

    # Замечания словами, по убыванию важности.
    if отчёт["out_of_order"]:
        отчёт["problems"].append(
            f"Свечи идут не по порядку ({отчёт['out_of_order']} шт.). "
            f"Проверять на таких данных нельзя: прошлое перемешано с будущим.")
    if отчёт["last_bar_open"]:
        отчёт["problems"].append(
            "Последняя свеча не закрыта. В расчёт сигнала она попасть не "
            "должна — это кусок будущего внутри данных.")
    if отчёт["duplicates"]:
        отчёт["problems"].append(
            f"Повторяющиеся свечи: {отчёт['duplicates']}. Одна и та же свеча "
            f"дважды — это лишняя сделка и завышенная статистика.")
    if отчёт["bad_prices"]:
        отчёт["problems"].append(
            f"Битые цены: {отчёт['bad_prices']} свечей, где максимум ниже "
            f"минимума или цена не положительна.")
    if not отчёт["timezone_known"]:
        отчёт["problems"].append(
            "Неизвестно смещение времени сервера. Без него нельзя определить "
            "торговую сессию и сопоставить свечу с новостью. Выгрузите "
            "историю через программу — она записывает это в паспорт данных.")
    if отчёт["gaps"]:
        доля = отчёт["missing_bars"] / max(отчёт["bars"], 1) * 100.0
        отчёт["problems"].append(
            f"Пропуски в истории: {отчёт['gaps']} разрывов, не хватает "
            f"{отчёт['missing_bars']} свечей ({доля:.2f}% от объёма). "
            f"Ничего не дорисовано — дорисованная свеча это выдуманные данные.")

    отчёт["usable"] = not (отчёт["out_of_order"] or отчёт["duplicates"]
                           or отчёт["bad_prices"] or отчёт["last_bar_open"])
    return отчёт


def clean(bars, timeframe: str = "M5") -> list:
    """Данные, годные для расчёта: по порядку, без повторов, без битых цен.

    НИЧЕГО НЕ ДОРИСОВЫВАЕТ. Пропуск остаётся пропуском — движок просто
    увидит, что между свечами больше времени, чем обычно."""
    видели = set()
    чистые = []
    for b in sorted(bars or [], key=lambda x: int(x.get("time", 0))):
        t = int(b.get("time", 0))
        if t in видели:
            continue
        h, l = float(b.get("high", 0)), float(b.get("low", 0))
        o, c = float(b.get("open", 0)), float(b.get("close", 0))
        if h < l or min(h, l, o, c) <= 0 or o > h or o < l or c > h or c < l:
            continue
        видели.add(t)
        чистые.append(b)
    return чистые


def save_clean(bars, symbol: str, timeframe: str = "M5", folder: str = "") -> str:
    """Сохранить обработанные данные ОТДЕЛЬНО от сырых."""
    root = folder or os.path.join(base_dir(), CLEAN_FOLDER)
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, f"{symbol}_{timeframe}.csv")
    колонки = ["time", "open", "high", "low", "close", "tick_volume", "spread"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(";".join(колонки) + "\n")
        for b in bars:
            f.write(";".join(str(b.get(c, "")) for c in колонки) + "\n")
    return path


def load(symbol: str, timeframe: str = "M5", folder: str = "") -> dict:
    """Прочитать, проверить и подготовить историю одного инструмента.

    Возвращает {"bars", "clean", "meta", "quality", "path"}."""
    path = raw_path(symbol, timeframe, folder)
    сырые = load_csv(path)
    meta = meta_for(path)
    качество = check_quality(сырые, timeframe, meta)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "path": path,
        "meta": meta,
        "bars": сырые,
        "clean": clean(сырые, timeframe),
        "quality": качество,
    }


def describe(quality: dict, meta: dict = None) -> str:
    """Качество данных словами — для отчёта и для человека."""
    meta = meta or {}
    из_времени = quality.get("first", 0)
    по_время = quality.get("last", 0)

    def дата(секунды):
        if not секунды:
            return "?"
        from datetime import datetime, timezone as tz
        return datetime.fromtimestamp(секунды, tz.utc).strftime("%Y-%m-%d %H:%M")

    строки = [
        f"Инструмент: {meta.get('symbol', '?')} {quality.get('timeframe', '?')}",
        f"Брокер: {meta.get('broker') or '?'} / сервер {meta.get('server') or '?'}",
        f"Время свечей: сервер брокера, смещение "
        f"{quality.get('server_utc_offset_hours')} ч от UTC",
        f"Свечей: {quality.get('bars', 0)}",
        f"Период: {дата(из_времени)} — {дата(по_время)} (по времени сервера)",
        f"Пропусков: {quality.get('gaps', 0)} "
        f"(не хватает {quality.get('missing_bars', 0)} свечей), "
        f"законных перерывов: {quality.get('legal_breaks', 0)}",
        f"Повторов: {quality.get('duplicates', 0)}, "
        f"не по порядку: {quality.get('out_of_order', 0)}, "
        f"битых цен: {quality.get('bad_prices', 0)}",
        f"Последняя свеча закрыта: {'нет' if quality.get('last_bar_open') else 'да'}",
        f"Годно для расчёта: {'да' if quality.get('usable') else 'НЕТ'}",
    ]
    for p in quality.get("problems", ()):
        строки.append(f"  ! {p}")
    return "\n".join(строки)
