#!/usr/bin/env python3
"""Тесты PHASE 3: подбор параметров под присмотром.

САМОЕ ОПАСНОЕ МЕСТО ВО ВСЁМ ПРОЕКТЕ — ИМЕННО ЗДЕСЬ.

Подбор параметров по истории умеет выдавать красивый отчёт для чего угодно.
Достаточно перебрать сотню вариантов — и какой-нибудь окажется «лучшим» даже
если все они одинаково бесполезны. Поэтому здесь проверяется не то, что
подбор работает, а то, что он НЕ МОЖЕТ соврать:

  * риск не подбирается — попытка тронуть его роняет прогон;
  * настройки после прогона возвращаются на место, иначе следующий вариант
    считался бы по чужим числам и вся таблица оказалась бы ложной;
  * куски истории идут строго по времени и не перекрываются;
  * разгон индикаторов берётся ТОЛЬКО из прошлого;
  * «лучше убыточного» не считается прибыльным.

Запуск:  python3 tests/test_optimize.py
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
APP = ROOT / "ai_scalper_standalone"
sys.path.insert(0, str(APP))

passed = 0
failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  OK   {name}")
    else:
        failed += 1
        print(f"  СБОЙ {name}" + (f" -> {detail}" if detail else ""))


cfg = types.ModuleType("config")
cfg.__file__ = str(APP / "config.py.example")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg
CFG = cfg

fake_mt5 = types.ModuleType("MetaTrader5")
for _имя, _знач in (("ORDER_TYPE_BUY", 0), ("ORDER_TYPE_SELL", 1),
                    ("TIMEFRAME_M1", 1), ("TIMEFRAME_M5", 5), ("TIMEFRAME_M15", 15),
                    ("TIMEFRAME_M30", 30), ("TIMEFRAME_H1", 60), ("TIMEFRAME_H4", 240),
                    ("TIMEFRAME_D1", 1440), ("ORDER_FILLING_IOC", 1),
                    ("ORDER_FILLING_FOK", 2), ("TRADE_RETCODE_DONE", 10009),
                    ("TRADE_RETCODE_REQUOTE", 10004),
                    ("TRADE_RETCODE_PRICE_CHANGED", 10020),
                    ("TRADE_RETCODE_PRICE_OFF", 10021),
                    ("POSITION_TYPE_BUY", 0), ("POSITION_TYPE_SELL", 1)):
    setattr(fake_mt5, _имя, _знач)
for _имя in ("symbol_info", "symbol_info_tick", "order_calc_profit",
             "order_calc_margin", "copy_rates_from_pos", "positions_get",
             "account_info", "last_error", "terminal_info"):
    setattr(fake_mt5, _имя, lambda *a, **k: None)
sys.modules["MetaTrader5"] = fake_mt5

import baseline_engine          # noqa: E402
import optimize                 # noqa: E402
import risk_manager as rm       # noqa: E402


# =====================================================================
# СВЕЧИ ДЛЯ ПРОВЕРКИ МЕХАНИКИ (не для выводов о стратегии)
# =====================================================================
def свечи(n: int, старт: int = 1_700_000_000):
    """Ровный ряд. Здесь проверяется механика нарезки, а не торговля."""
    ряд = []
    цена = 1.1000
    for i in range(n):
        цена += 0.0001 if i % 3 else -0.0001
        ряд.append({"time": старт + i * 300, "open": цена, "high": цена + 0.0002,
                    "low": цена - 0.0002, "close": цена, "tick_volume": 100,
                    "spread": 10, "real_volume": 0})
    return ряд


МЕТА = {"point": 0.00001, "digits": 5, "volume_min": 0.01, "volume_max": 100.0,
        "volume_step": 0.01, "trade_tick_value": 1.0, "trade_tick_size": 0.00001,
        "trade_contract_size": 100000.0, "stops_level": 0,
        "money_per_point_per_lot": 1.0, "server_utc_offset_hours": 0.0}


# =====================================================================
def test_risk_can_never_be_optimized() -> None:
    """ПОЧЕМУ ЭТОТ ТЕСТ ПЕРВЫЙ. Владелец прямо запретил подбирать риск.

    Запрет держится не на памяти и не на добрых намерениях: подбор риска по
    истории ВСЕГДА улучшает отчёт (больше риск — больше прибыль на тех же
    сделках) и ВСЕГДА ухудшает шанс пережить настоящую просадку. Соблазн
    слишком велик, чтобы полагаться на обещание, поэтому запрет — код."""
    print("\n[Риск подобрать невозможно — прогон падает]")

    for имя, знач in (("risk_percent", 5.0),
                      ("max_open_positions", 20),
                      ("max_total_risk_pct", 50.0),
                      ("MAX_TRADE_RISK_PERCENT_OF_EQUITY", 10.0),
                      ("LIVE_TRADING", True),
                      ("RISK_PROFILE", "yolo"),
                      ("UPDATE_REPO", "чужой/репозиторий"),
                      ("REQUIRE_LOGIN", False)):
        источник = "профиль" if имя.islower() else "настройки"
        try:
            optimize.Правка(**{источник: {имя: знач}})
            check(False, f"{имя}: попытка подобрать ДОЛЖНА была упасть")
        except ValueError as e:
            check("запрещено" in str(e), f"{имя}: подобрать нельзя", str(e)[:60])

    # А разрешённое — проходит.
    optimize.Правка(профиль={"min_score_to_trade": 70})
    optimize.Правка(настройки={"USE_TRAILING_STOP": False})
    check(True, "Качество входа и ведение сделки подбирать можно")

    # И ни один готовый вариант не пытается тронуть запрещённое.
    for в in optimize.variants("XAUUSD"):
        поля = set((в.get("профиль") or {})) | set((в.get("настройки") or {}))
        check(not (поля & optimize.ЗАПРЕЩЕНО),
              f"Вариант {в['код']} не трогает риск", в["имя"])


def test_settings_always_come_back() -> None:
    """ПОЧЕМУ ЭТОТ ТЕСТ ПОЯВИЛСЯ. Правка настроек — общая на всю программу.
    Если она переживёт прогон хотя бы одного варианта, все следующие
    посчитаются по чужим числам, а таблица будет выглядеть безупречно.

    Такую ошибку невозможно заметить глазами в отчёте: числа будут
    правдоподобными. Поэтому она ловится тестом, а не чтением."""
    print("\n[Настройки возвращаются на место всегда]")
    профиль = rm.get_profile()
    было_порог = профиль["min_score_to_trade"]
    было_трейл = CFG.TRAILING_ATR_MULTIPLIER

    with optimize.Правка(профиль={"min_score_to_trade": 99},
                         настройки={"TRAILING_ATR_MULTIPLIER": 9.9}):
        check(rm.get_profile()["min_score_to_trade"] == 99,
              "Внутри правки действует новое значение")
        check(CFG.TRAILING_ATR_MULTIPLIER == 9.9,
              "И для настроек тоже")
    check(rm.get_profile()["min_score_to_trade"] == было_порог,
          "После правки порог вернулся", str(rm.get_profile()["min_score_to_trade"]))
    check(CFG.TRAILING_ATR_MULTIPLIER == было_трейл,
          "И трейлинг вернулся", str(CFG.TRAILING_ATR_MULTIPLIER))

    # ГЛАВНОЕ: возврат обязан случиться и при ошибке внутри прогона.
    try:
        with optimize.Правка(профиль={"min_score_to_trade": 99}):
            raise RuntimeError("прогон упал")
    except RuntimeError:
        pass
    check(rm.get_profile()["min_score_to_trade"] == было_порог,
          "ПОСЛЕ ПАДЕНИЯ прогона настройки тоже вернулись",
          str(rm.get_profile()["min_score_to_trade"]))

    # И параметра, которого в настройках не было, после правки не остаётся
    # чужого значения.
    check(not hasattr(CFG, "ВЫДУМАННЫЙ_ПАРАМЕТР") or
          CFG.ВЫДУМАННЫЙ_ПАРАМЕТР is None, "Чужих полей не осталось")


def test_history_is_cut_by_time_only() -> None:
    """ПОЧЕМУ ЭТО ВАЖНО. Если куски истории перепутаются местами или
    перекроются, «чистая проверка» перестанет быть чистой: вариант увидит на
    OOS те же свечи, на которых его выбирали. Отчёт при этом останется
    красивым — и полностью бессмысленным."""
    print("\n[История режется по времени, куски не перекрываются]")
    ряд = свечи(1000)
    гр = optimize.segments(ряд)

    check(гр["TRAIN"][0] == 0, "TRAIN начинается с начала истории")
    check(гр["TRAIN"][1] == гр["VALIDATION"][0],
          "VALIDATION начинается ровно там, где кончился TRAIN")
    check(гр["VALIDATION"][1] == гр["OOS"][0],
          "OOS начинается ровно там, где кончился VALIDATION")
    check(гр["OOS"][1] == len(ряд), "OOS доходит до конца истории")
    check(гр["TRAIN"][1] > гр["TRAIN"][0] and гр["OOS"][1] > гр["OOS"][0],
          "Ни один кусок не пустой")
    check(гр["TRAIN"][1] - гр["TRAIN"][0] > гр["OOS"][1] - гр["OOS"][0],
          "TRAIN больше OOS — соревнованию нужно больше данных")

    # Разгон берётся ТОЛЬКО из прошлого.
    кусок = optimize.slice_for(ряд, гр["VALIDATION"])
    первое_решение = кусок[baseline_engine.ОКНО_БАРОВ]
    check(первое_решение["time"] == ряд[гр["VALIDATION"][0]]["time"],
          "Первое решение куска приходится ровно на его первую свечу")
    check(кусок[-1]["time"] == ряд[гр["VALIDATION"][1] - 1]["time"],
          "И кусок не заглядывает за свою правую границу")
    check(all(б["time"] < первое_решение["time"]
              for б in кусок[:baseline_engine.ОКНО_БАРОВ]),
          "Весь разгон — строго в прошлом относительно первого решения")

    # У самого первого куска прошлого нет, и выдумывать его нельзя.
    первый = optimize.slice_for(ряд, гр["TRAIN"])
    check(первый[0]["time"] == ряд[0]["time"],
          "У первого куска разгон не выдумывается — берётся что есть")


def test_less_bad_is_not_good() -> None:
    """ПОЧЕМУ ЭТОТ ТЕСТ ПОЯВИЛСЯ. Baseline убыточен: профит-фактор 0.4.
    Значит почти любое осмысленное изменение будет «лучше». Вариант с
    профит-фактором 0.7 пройдёт все пять способов проверки — и останется
    убыточным.

    Принять такое значит поменять быстрый убыток на медленный и назвать это
    победой. Поэтому у ворот два порога, а не один."""
    print("\n[«Менее убыточно» — это не «прибыльно»]")
    import trade_stats

    def сводка_из(деньги):
        сделки = [{"money": м, "points": м, "r": м, "direction": 1,
                   "mae_points": 0, "mfe_points": 0, "held_seconds": 60,
                   "entry_time": i, "exit_time": i + 60}
                  for i, м in enumerate(деньги)]
        return trade_stats.summarize(сделки, "проба")

    убыточно = сводка_из([1.0] * 30 + [-2.0] * 70)
    check(not optimize.profitable(убыточно),
          "Убыточный набор прибыльным не считается",
          f"PF {убыточно['профит_фактор']}")

    менее_убыточно = сводка_из([1.0] * 45 + [-1.2] * 55)
    check(float(менее_убыточно["профит_фактор"]) > float(убыточно["профит_фактор"]),
          "Второй набор действительно лучше первого",
          f"{убыточно['профит_фактор']} -> {менее_убыточно['профит_фактор']}")
    check(not optimize.profitable(менее_убыточно),
          "НО прибыльным он всё равно не считается — он всё ещё в минусе",
          f"PF {менее_убыточно['профит_фактор']}")

    прибыльно = сводка_из([2.0] * 45 + [-1.0] * 55)
    check(optimize.profitable(прибыльно),
          "А настоящая прибыль признаётся прибылью",
          f"PF {прибыльно['профит_фактор']}")

    ровно_в_ноль = сводка_из([1.0] * 50 + [-1.0] * 50)
    check(not optimize.profitable(ровно_в_ноль),
          "Ровно в ноль — не прибыль. Ничья засчитывается против изменения")

    # И мало сделок — это не «слабое доказательство», а его отсутствие.
    check(not optimize.enough(сводка_из([1.0] * 10)),
          "Десять сделок — судить нечем")
    check(optimize.enough(сводка_из([1.0] * optimize.МИНИМУМ_СДЕЛОК)),
          "А начиная с порога — уже можно")


def test_direction_filter_is_research_only() -> None:
    """ПОЧЕМУ ЭТО ВАЖНО. Проверка «только продажи» — исследовательский рычаг.
    Если бы он был настройкой программы, он мог бы незаметно оказаться
    включённым в живой торговле — и владелец получил бы бота, который
    торгует в одну сторону, ни разу этого не попросив."""
    print("\n[Проверка одной стороны в настройки программы не просачивается]")
    import inspect

    исходник = (APP / "config.py.example").read_text(encoding="utf-8")
    check("ONLY_DIRECTION" not in исходник,
          "В настройках программы такого поля нет")

    подпись = inspect.signature(baseline_engine.run)
    check("only_direction" in подпись.parameters,
          "Рычаг есть — но только у движка проверки")
    check(подпись.parameters["only_direction"].default == 0,
          "И по умолчанию выключен: обе стороны, как торгует программа",
          str(подпись.parameters["only_direction"].default))

    src = (APP / "baseline_engine.py").read_text(encoding="utf-8")
    check('getattr(cfg, "ONLY_DIRECTION"' not in src,
          "Движок не читает его из настроек — только из аргумента")


def test_direction_filter_actually_blocks() -> None:
    """Рычаг обязан РАБОТАТЬ, а не только существовать.

    Проверяется на настоящем прогоне: при only_direction=-1 не должно
    остаться ни одной покупки, и наоборот."""
    print("\n[Проверка одной стороны действительно работает]")
    ряд = свечи(700)
    for сторона, имя in ((1, "покупки"), (-1, "продажи")):
        итог = baseline_engine.run("EURUSD", ряд, МЕТА, only_direction=сторона)
        чужих = [t for t in итог["trades"] if t["direction"] != сторона]
        check(not чужих, f"При «только {имя}» другой стороны нет",
              f"нашлось {len(чужих)}")

    обе = baseline_engine.run("EURUSD", ряд, МЕТА)
    check(isinstance(обе["trades"], list),
          "Без рычага прогон работает как раньше")


def test_no_change_means_no_difference() -> None:
    """ПОЧЕМУ ЭТОТ ТЕСТ ПОЯВИЛСЯ. Вариант «БАЗА» обязан давать РОВНО то же
    самое, что обычный прогон. Если сама машинерия подбора хоть чуть-чуть
    меняет результат, то все сравнения идут не с реальной программой, а с её
    искажённой копией — и весь PHASE 3 не значит ничего."""
    print("\n[Пустой вариант ничего не меняет]")
    ряд = свечи(700)
    отрезок = (0, len(ряд))

    прямо = baseline_engine.run("EURUSD", ряд, МЕТА)
    через = optimize.run_variant("EURUSD", ряд, МЕТА, optimize.БАЗА, отрезок)

    check(прямо["bars_seen"] > 0,
          "Прогон вообще состоялся — сравнивать есть что",
          str(прямо["bars_seen"]))
    check(len(прямо["trades"]) == len(через["trades"]),
          "Число сделок совпадает",
          f"{len(прямо['trades'])} и {len(через['trades'])}")
    check([t["money"] for t in прямо["trades"]] == через["money"],
          "И результат каждой сделки совпадает копейка в копейку")

    # А непустой вариант обязан что-то менять — иначе правка не доезжает.
    строгий = {"код": "X", "имя": "порог 99", "профиль": {"min_score_to_trade": 99}}
    итог = optimize.run_variant("EURUSD", ряд, МЕТА, строгий, отрезок)
    check(len(итог["trades"]) <= len(через["trades"]),
          "Более строгий порог не увеличивает число сделок",
          f"{len(через['trades'])} -> {len(итог['trades'])}")


def test_fragile_result_is_rejected() -> None:
    """ПОЧЕМУ ЭТО ВАЖНО. Настройка, которая работает ровно при одном числе и
    рассыпается при сдвиге на пятую часть, — это не найденная закономерность,
    а точка, случайно попавшая в сетку перебора. На новых данных от неё не
    останется ничего."""
    print("\n[Хрупкий результат отклоняется]")

    def прогон(деньги, имя="сосед"):
        return {"вариант": имя, "имя": имя, "money": деньги,
                "trades": [], "сводка": {}, "rejects": {}}

    база = прогон([-1.0] * 60 + [1.0] * 40, "база")
    хорошие = [
        прогон([2.0] * 60 + [-1.0] * 40, "сосед меньше"),
        прогон([2.1] * 60 + [-1.0] * 40, "сосед больше"),
    ]
    итог = optimize.robust(база, хорошие)
    check(итог["устойчив"], "Оба соседа держатся — вариант устойчив")

    плохие = [
        прогон([2.0] * 60 + [-1.0] * 40, "сосед меньше"),
        прогон([-3.0] * 60 + [1.0] * 40, "сосед больше"),
    ]
    итог = optimize.robust(база, плохие)
    check(not итог["устойчив"],
          "Достаточно ОДНОГО рассыпавшегося соседа, чтобы отклонить")

    check(not optimize.robust(база, [])["устойчив"],
          "Без соседей устойчивость не подтверждается — молчание не довод")


def test_sensitivity_moves_only_numbers() -> None:
    """Сдвигать можно только числа. Переключатель «да/нет» подвинуть на 20%
    нельзя — попытка сделать это дала бы бессмыслицу вроде «True * 0.8»."""
    print("\n[Шевелятся только числа, а не переключатели]")
    ряд = свечи(700)
    отрезок = (0, len(ряд))

    числовой = {"код": "B", "имя": "стоп короче", "профиль": {"atr_sl_multiplier": 1.4}}
    соседи = optimize.sensitivity("EURUSD", ряд, МЕТА, числовой, отрезок)
    check(len(соседи) == 2, "У числа два соседа: меньше и больше",
          str(len(соседи)))

    переключатель = {"код": "D", "имя": "без Profit Lock",
                     "настройки": {"USE_PROFIT_LOCK_TRAILING": False}}
    check(optimize.sensitivity("EURUSD", ряд, МЕТА, переключатель, отрезок) == [],
          "У переключателя соседей нет — и это честно, а не молча пропущено")

    сторона = {"код": "F", "имя": "только продажи", "направление": -1}
    check(optimize.sensitivity("EURUSD", ряд, МЕТА, сторона, отрезок) == [],
          "У стороны сделки соседей тоже нет")


def test_runner_reads_its_arguments() -> None:
    """ПОЧЕМУ ЭТОТ ТЕСТ ПОЯВИЛСЯ. Первый же запуск с «--bars 4000» принял
    число 4000 за имя инструмента: разбор искал ключ по всему списку, а
    значение после него так и оставалось лежать среди имён. Программа честно
    искала историю пары «4000» и не находила."""
    print("\n[Число после ключа не превращается в имя инструмента]")
    src = (APP / "run_optimize.py").read_text(encoding="utf-8")
    check("while i < len(argv)" in src,
          "Разбор идёт по порядку, а не поиском по списку")
    check("i += 2" in src, "Значение после ключа пропускается")


if __name__ == "__main__":
    print("=" * 62)
    print("ТЕСТЫ: PHASE 3 — ПОДБОР ПАРАМЕТРОВ ПОД ПРИСМОТРОМ")
    print("=" * 62)
    test_risk_can_never_be_optimized()
    test_settings_always_come_back()
    test_history_is_cut_by_time_only()
    test_less_bad_is_not_good()
    test_direction_filter_is_research_only()
    test_direction_filter_actually_blocks()
    test_no_change_means_no_difference()
    test_fragile_result_is_rejected()
    test_sensitivity_moves_only_numbers()
    test_runner_reads_its_arguments()
    print()
    print("=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    sys.exit(1 if failed else 0)
