#!/usr/bin/env python3
"""Тесты фиксации прибыли: подтягивание тейк-профита, спасение в безубыток,
обучение цели прибыли по пикам прошлых сделок.

Главное, что здесь проверяется, — три правила безопасности:
  1. Тейк-профит можно только ПРИБЛИЖАТЬ к цене, никогда не отодвигать.
  2. Подтянутый TP никогда не оказывается в убытке относительно входа.
  3. Спасение в безубыток НЕ трогает стоп-лосс — только добавляет ранний выход.

Запуск:  python3 tests/test_profit_taking.py
"""

from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent
APP = BASE.parent / "ai_scalper_standalone"
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
        print(f"  СБОЙ {name}" + (f"  -> {detail}" if detail else ""))


# =====================================================================
# Заглушки: настоящий config.py в git не хранится (там ключи), MetaTrader5 на
# Linux не ставится. Берём config из config.py.example — то есть ровно те
# значения по умолчанию, с которыми программа приедет к пользователю.
# =====================================================================
def install_stubs() -> types.ModuleType:
    cfg = types.ModuleType("config")
    exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
    sys.modules["config"] = cfg

    # Любая константа mt5.XXX отдаётся как строка с этим же именем: коду
    # выше по стеку важно только, что она есть и уникальна.
    class _FakeMT5(types.ModuleType):
        def __getattr__(self, name):
            if name.startswith("_"):
                raise AttributeError(name)
            return name

    mt5 = _FakeMT5("MetaTrader5")
    mt5.POSITION_TYPE_BUY = 0
    mt5.ORDER_TYPE_BUY = 0
    mt5.ORDER_TYPE_SELL = 1
    mt5.initialize = lambda *a, **k: False
    sys.modules["MetaTrader5"] = mt5
    return cfg


CFG = install_stubs()

import auto_learning as al        # noqa: E402
import trade_manager as tm        # noqa: E402
from state import SymbolState     # noqa: E402


POINT = 0.01          # золото: 1 пункт = 0.01
OPEN = 2000.0


# =====================================================================
# 1. Подтягивание тейк-профита
# =====================================================================
def test_tp_tighten() -> None:
    print("\n[Подтягивание тейк-профита]")

    # BUY без TP: цель 100 пт от входа -> TP = 2001.00
    tp = tm.tighten_take_profit(True, OPEN, 2000.5, 0.0, 100, 10, POINT, 0.0, 5)
    check(abs(tp - 2001.0) < 1e-9, "BUY: цель ставится, когда TP не было", f"{tp}")

    # SELL зеркально
    tp = tm.tighten_take_profit(False, OPEN, 1999.5, 0.0, 100, 10, POINT, 0.0, 5)
    check(abs(tp - 1999.0) < 1e-9, "SELL: цель ставится зеркально", f"{tp}")

    # ГЛАВНОЕ ПРАВИЛО: цель дальше текущего TP -> отказ
    tp = tm.tighten_take_profit(True, OPEN, 2000.5, 2000.5, 100, 10, POINT, 0.0, 5)
    check(tp == 0.0, "BUY: TP НЕЛЬЗЯ отодвинуть дальше", f"{tp}")

    tp = tm.tighten_take_profit(False, OPEN, 1999.5, 1999.5, 100, 10, POINT, 0.0, 5)
    check(tp == 0.0, "SELL: TP НЕЛЬЗЯ отодвинуть дальше", f"{tp}")

    # Ближе — можно
    tp = tm.tighten_take_profit(True, OPEN, 2000.2, 2001.0, 50, 10, POINT, 0.0, 5)
    check(abs(tp - 2000.5) < 1e-9, "BUY: TP подтягивается ближе", f"{tp}")

    tp = tm.tighten_take_profit(False, OPEN, 1999.8, 1999.0, 50, 10, POINT, 0.0, 5)
    check(abs(tp - 1999.5) < 1e-9, "SELL: TP подтягивается ближе", f"{tp}")

    # Шаг: подвинуть на 2 пункта при шаге 5 — не дёргаем сервер
    tp = tm.tighten_take_profit(True, OPEN, 2000.2, 2001.0, 98, 10, POINT, 0.0, 5)
    check(tp == 0.0, "Мельче минимального шага — TP не двигается", f"{tp}")

    # Ровно на шаг — двигаем
    tp = tm.tighten_take_profit(True, OPEN, 2000.2, 2001.0, 95, 10, POINT, 0.0, 5)
    check(abs(tp - 2000.95) < 1e-9, "Ровно на шаг — TP двигается", f"{tp}")

    # Цели нет — ничего не делаем
    check(tm.tighten_take_profit(True, OPEN, 2000.5, 2001.0, 0, 10, POINT, 0.0, 5) == 0.0,
          "Нулевая цель — TP не трогается")
    check(tm.tighten_take_profit(True, OPEN, 2000.5, 2001.0, 100, 10, 0.0, 0.0, 5) == 0.0,
          "point=0 (символ не готов) — TP не трогается")


def test_tp_never_into_loss() -> None:
    print("\n[TP никогда не уходит в убыток]")

    # Цель ужалась до 2 пт при минимуме 10 пт -> TP не ближе входа+10
    tp = tm.tighten_take_profit(True, OPEN, 2000.05, 2001.0, 2, 10, POINT, 0.0, 0)
    check(tp >= OPEN + 10 * POINT - 1e-9, "BUY: TP не ближе минимальной прибыли от входа", f"{tp}")

    tp = tm.tighten_take_profit(False, OPEN, 1999.95, 1999.0, 2, 10, POINT, 0.0, 0)
    check(tp <= OPEN - 10 * POINT + 1e-9, "SELL: TP не ближе минимальной прибыли от входа", f"{tp}")

    # Даже нулевая цель прибыли не может поставить TP в минус
    tp = tm.tighten_take_profit(True, OPEN, 2000.5, 2001.0, 1, 10, POINT, 0.0, 0)
    check(tp > OPEN, "BUY: TP всегда строго выше цены входа", f"{tp}")

    # Дистанция брокера: TP не может оказаться вплотную к цене
    tp = tm.tighten_take_profit(True, OPEN, 2000.9, 2001.5, 20, 5, POINT, 0.30, 0)
    check(tp >= 2000.9 + 0.30 - 1e-9, "TP не ближе минимальной дистанции брокера", f"{tp}")

    tp = tm.tighten_take_profit(False, OPEN, 1999.1, 1998.5, 20, 5, POINT, 0.30, 0)
    check(tp <= 1999.1 - 0.30 + 1e-9, "SELL: то же по дистанции брокера", f"{tp}")


def test_target_shrink() -> None:
    print("\n[Цель прибыли ужимается со временем]")

    check(abs(tm.shrunk_target_points(100, 0, 0.10, 0.25) - 100) < 1e-9,
          "Свежая сделка — полная цель")
    check(abs(tm.shrunk_target_points(100, 60, 0.10, 0.25) - 90) < 1e-9,
          "Через минуту — на 10% меньше")
    check(abs(tm.shrunk_target_points(100, 300, 0.10, 0.25) - 50) < 1e-9,
          "Через 5 минут — половина")
    check(abs(tm.shrunk_target_points(100, 36000, 0.10, 0.25) - 25) < 1e-9,
          "Через 10 часов — упирается в пол 25%, ниже не падает")
    check(tm.shrunk_target_points(0, 600, 0.10, 0.25) == 0.0,
          "Нулевая цель остаётся нулевой")
    check(abs(tm.shrunk_target_points(100, -50, 0.10, 0.25) - 100) < 1e-9,
          "Отрицательный возраст (часы сервера) не раздувает цель")

    # Монотонность: цель никогда не растёт со временем
    prev = tm.shrunk_target_points(100, 0, 0.10, 0.25)
    ok = True
    for sec in range(0, 3600, 30):
        cur = tm.shrunk_target_points(100, sec, 0.10, 0.25)
        if cur > prev + 1e-9:
            ok = False
            break
        prev = cur
    check(ok, "Цель монотонно не растёт на всём диапазоне возраста")


# =====================================================================
# 2. Спасение убыточной сделки в безубыток
# =====================================================================
def test_break_even_rescue() -> None:
    print("\n[Спасение в безубыток]")

    # Не проседала глубоко — обычный режим
    check(tm.break_even_rescue_action(-5, 0, 3600, 20, 600, 3) == "",
          "Мелкая просадка — спасение не включается")

    # Проседала, но ещё молодая
    check(tm.break_even_rescue_action(-50, 0, 60, 20, 600, 3) == "",
          "Просела, но висит меньше порога — ждём")

    # Проседала, старая, ещё в минусе -> ставим цель на ноль
    check(tm.break_even_rescue_action(-50, -20, 900, 20, 600, 3) == "arm",
          "Просела и висит долго — цель переносится на безубыток")

    # Вернулась в плюс -> закрываем
    check(tm.break_even_rescue_action(-50, 5, 900, 20, 600, 3) == "close",
          "Вернулась к нулю — закрываем")

    # Граница ровно на пороге просадки
    check(tm.break_even_rescue_action(-20, -10, 900, 20, 600, 3) == "arm",
          "Просадка ровно на пороге — спасение включается")
    check(tm.break_even_rescue_action(-19.9, -10, 900, 20, 600, 3) == "",
          "Просадка чуть меньше порога — не включается")

    # Граница по возрасту
    check(tm.break_even_rescue_action(-50, -10, 600, 20, 600, 3) == "arm",
          "Возраст ровно на пороге — включается")
    check(tm.break_even_rescue_action(-50, -10, 599, 20, 600, 3) == "",
          "Возраст чуть меньше порога — не включается")

    # Порог выхода задан отрицательным (пользователь готов на маленький минус)
    check(tm.break_even_rescue_action(-50, -1, 900, 20, 600, -2) == "close",
          "Порог выхода можно опустить чуть ниже нуля")

    # Знак порога просадки не важен: пользователь может ввести и 20, и -20
    check(tm.break_even_rescue_action(-50, -10, 900, -20, 600, 3) == "arm",
          "Отрицательный порог просадки трактуется как модуль")


def test_rescue_never_touches_sl() -> None:
    print("\n[Спасение не трогает стоп-лосс]")

    src = (APP / "trade_manager.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "break_even_rescue_action")

    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    check(not any("sl" == x or x.endswith("_sl") for x in names),
          "В функции спасения нет ни одной переменной стоп-лосса", str(sorted(names)))

    # И она возвращает ровно три исхода, ни один из которых не про стоп
    returns = {n.value.value for n in ast.walk(fn)
               if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)}
    check(returns == {"", "arm", "close"},
          "Исходов ровно три: ничего / цель на ноль / закрыть", str(returns))


# =====================================================================
# 3. Обучение цели прибыли по пикам прошлых сделок
# =====================================================================
def test_learned_target() -> None:
    print("\n[Обучение цели прибыли]")

    CFG.USE_AUTO_LEARNING = True
    CFG.USE_TP_LEARNING = True
    CFG.AUTO_LEARNING_MIN_TRADES = 5
    CFG.AUTO_LEARNING_WINDOW = 20
    CFG.TP_LEARN_FRACTION = 0.7
    CFG.TP_LEARN_MIN_POINTS = 10
    CFG.TP_LEARN_MAX_POINTS = 2000

    st = SymbolState(symbol="XAUUSD")
    check(al.learned_profit_points(st, 77.0) == 77.0,
          "Данных нет — возвращается запасная цель")

    for pk in (100, 100, 100, 100):
        al.record_trade_peak(st, pk)
    check(al.learned_profit_points(st, 77.0) == 77.0,
          "Сделок меньше минимума — всё ещё запасная цель")

    al.record_trade_peak(st, 100)
    check(abs(al.learned_profit_points(st, 77.0) - 70.0) < 1e-9,
          "5 сделок по 100 пт -> цель 70 пт (70% от медианы)",
          str(al.learned_profit_points(st, 77.0)))

    # Медиана против среднего: одна новостная свеча не должна ломать цель
    st2 = SymbolState(symbol="XAUUSD")
    for pk in (40, 45, 50, 55, 5000):
        al.record_trade_peak(st2, pk)
    check(abs(al.learned_profit_points(st2, 0.0) - 35.0) < 1e-9,
          "Выброс +5000 пт не сдвигает цель (медиана 50 -> 35)",
          str(al.learned_profit_points(st2, 0.0)))

    # Убыточная серия: пиков нет -> цель не опускается ниже минимума
    st3 = SymbolState(symbol="EURUSD")
    for _ in range(6):
        al.record_trade_peak(st3, 0)
    check(al.learned_profit_points(st3, 123.0) == 123.0,
          "Все пики нулевые — остаётся запасная цель, а не ноль")

    # Потолок
    st4 = SymbolState(symbol="XAUUSD")
    for _ in range(6):
        al.record_trade_peak(st4, 100000)
    check(al.learned_profit_points(st4, 0.0) == CFG.TP_LEARN_MAX_POINTS,
          "Цель упирается в потолок TP_LEARN_MAX_POINTS")

    # Отрицательные пики (сделка не была в плюсе) записываются как 0
    st5 = SymbolState(symbol="XAUUSD")
    al.record_trade_peak(st5, -80)
    check(st5.recent_peaks == [0.0], "Отрицательный пик записывается нулём", str(st5.recent_peaks))

    # Окно не растёт бесконечно
    st6 = SymbolState(symbol="XAUUSD")
    for i in range(100):
        al.record_trade_peak(st6, i)
    check(len(st6.recent_peaks) == CFG.AUTO_LEARNING_WINDOW,
          "Окно пиков ограничено AUTO_LEARNING_WINDOW", str(len(st6.recent_peaks)))

    # Выключатель работает
    CFG.USE_TP_LEARNING = False
    check(al.learned_profit_points(st, 77.0) == 77.0, "USE_TP_LEARNING=False отключает обучение цели")
    CFG.USE_TP_LEARNING = True
    CFG.USE_AUTO_LEARNING = False
    check(al.learned_profit_points(st, 77.0) == 77.0, "USE_AUTO_LEARNING=False тоже отключает")
    CFG.USE_AUTO_LEARNING = True


def test_median() -> None:
    print("\n[Медиана]")
    check(al.median([]) == 0.0, "Пустой список -> 0")
    check(al.median([5]) == 5, "Один элемент")
    check(al.median([3, 1, 2]) == 2, "Нечётное число элементов, порядок не важен")
    check(al.median([4, 1, 2, 3]) == 2.5, "Чётное число элементов -> среднее двух средних")


# =====================================================================
# 4. Архив пиков закрытых позиций
# =====================================================================
def test_closed_peak_archive() -> None:
    print("\n[Архив пиков закрытых сделок]")

    tm._position_peak_points.clear()
    tm._closed_peaks.clear()

    tm.update_peak_profit(111, 10)
    tm.update_peak_profit(111, 55)
    tm.update_peak_profit(111, 30)      # пик уже был выше — не понижается
    check(tm._position_peak_points[111] == 55, "Пик запоминает максимум, а не последнее значение")

    tm.update_peak_profit(222, 12)
    tm.cleanup_peak_profit({222})       # позиция 111 закрылась
    check(tm.pop_closed_peak(111) == 55, "Пик закрытой позиции попадает в архив")
    check(tm.pop_closed_peak(111) is None, "Забирается ровно один раз")
    check(tm.pop_closed_peak(999) is None, "Неизвестная позиция -> None")
    check(222 in tm._position_peak_points, "Открытая позиция остаётся в работе")

    # Архив не растёт бесконечно
    tm._position_peak_points.clear()
    tm._closed_peaks.clear()
    for i in range(tm._CLOSED_PEAKS_LIMIT + 50):
        tm.update_peak_profit(i, i)
    tm.cleanup_peak_profit(set())
    check(len(tm._closed_peaks) == tm._CLOSED_PEAKS_LIMIT,
          "Архив ограничен по размеру", str(len(tm._closed_peaks)))
    check(tm.pop_closed_peak(0) is None, "Самые старые записи вытесняются первыми")

    # Просадка
    tm._position_trough_points.clear()
    tm.update_position_trough(1, -5)
    tm.update_position_trough(1, -40)
    tm.update_position_trough(1, 10)
    check(tm._position_trough_points[1] == -40, "Просадка запоминает минимум")
    tm.cleanup_peak_profit(set())
    check(1 not in tm._position_trough_points, "Просадка чистится при закрытии позиции")


def test_position_age() -> None:
    print("\n[Возраст сделки считается по своим часам]")

    tm._position_first_seen.clear()

    check(tm.position_age_seconds(7, now=1000.0) == 0.0, "Первая встреча — возраст 0")
    check(tm.position_age_seconds(7, now=1300.0) == 300.0, "Через 5 минут — 300 секунд")

    # Часы брокера тут вообще не участвуют: даже если сервер на 3 часа впереди,
    # возраст остаётся честным. Проверяем, что p.time не читается.
    src = (APP / "trade_manager.py").read_text(encoding="utf-8")
    fn_src = src[src.index("def manage_open_positions"):]
    check("p.time" not in fn_src,
          "Возраст НЕ берётся из времени сервера брокера (p.time)")

    # Время, идущее назад (перевод часов) не даёт отрицательный возраст
    check(tm.position_age_seconds(7, now=900.0) == 0.0, "Часы назад — возраст не отрицательный")

    tm.cleanup_peak_profit(set())
    check(7 not in tm._position_first_seen, "Отметка времени чистится при закрытии позиции")


# =====================================================================
# 5. Настройки: всё, что читает код, реально есть в config.py.example
# =====================================================================
def test_config_params_exist() -> None:
    print("\n[Настройки на месте]")

    needed = [
        "USE_TP_TIGHTEN", "TP_TIGHTEN_START_ATR", "TP_TIGHTEN_SHRINK_PER_MINUTE",
        "TP_TIGHTEN_MIN_FRACTION", "TP_TIGHTEN_MIN_PROFIT_POINTS", "TP_TIGHTEN_STEP_POINTS",
        "USE_BREAK_EVEN_RESCUE", "BE_RESCUE_MIN_DRAWDOWN_POINTS",
        "BE_RESCUE_AFTER_MINUTES", "BE_RESCUE_EXIT_POINTS",
        "USE_TP_LEARNING", "TP_LEARN_FRACTION", "TP_LEARN_MIN_POINTS", "TP_LEARN_MAX_POINTS",
    ]
    for name in needed:
        check(hasattr(CFG, name), f"config.py.example содержит {name}")

    # И они выведены в интерфейс программы
    src = (APP / "desktop_app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    shown: set = set()
    sections: list = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            target = getattr(node.targets[0], "id", "")
            if target == "ADVANCED_PARAMS":
                shown = {p[0] for p in ast.literal_eval(node.value)}
            elif target == "CONFIG_SECTIONS":
                sections = ast.literal_eval(node.value)
    for name in needed:
        check(name in shown, f"Настройка {name} видна в интерфейсе")

    groups = {g for _, gs in sections for g in gs}
    check("Фиксация прибыли" in groups,
          "Группа 'Фиксация прибыли' попала во вкладку настроек", str(sorted(groups)))

    # Значения по умолчанию осмысленные
    check(0 < CFG.TP_TIGHTEN_MIN_FRACTION <= 1, "TP_TIGHTEN_MIN_FRACTION в диапазоне 0..1")
    check(0 <= CFG.TP_TIGHTEN_SHRINK_PER_MINUTE < 1, "TP_TIGHTEN_SHRINK_PER_MINUTE в диапазоне 0..1")
    check(0 < CFG.TP_LEARN_FRACTION <= 1, "TP_LEARN_FRACTION в диапазоне 0..1")
    check(CFG.TP_LEARN_MIN_POINTS < CFG.TP_LEARN_MAX_POINTS, "Мин. выученная цель меньше макс.")
    check(CFG.BE_RESCUE_EXIT_POINTS >= 0, "Порог 'нуля' не отрицательный по умолчанию")


# =====================================================================
# 6. Сквозная проверка: настоящая manage_open_positions на поддельной позиции
# =====================================================================
class FakePos:
    def __init__(self, ticket, is_buy, price_open, sl, tp, volume=0.10):
        self.ticket = ticket
        self.position_id = ticket
        self.symbol = "XAUUSD"
        self.magic = CFG.MAGIC_NUMBER
        self.type = 0 if is_buy else 1
        self.price_open = price_open
        self.sl = sl
        self.tp = tp
        self.volume = volume
        self.time = 0          # намеренно ноль: время брокера использоваться не должно


class FakeTick:
    def __init__(self, bid, ask):
        self.bid = bid
        self.ask = ask


def test_end_to_end() -> None:
    print("\n[Сквозная проверка управления позицией]")

    import MetaTrader5 as mt5
    mt5.symbol_info = lambda s: types.SimpleNamespace(
        trade_stops_level=0, volume_min=0.01, volume_step=0.01)

    modified: list = []
    closed: list = []
    tm.mt5c.get_tick = lambda s: FakeTick(*test_end_to_end.price)
    tm.mt5c.modify_position = lambda t, sl, tp: modified.append((t, sl, tp))
    tm.mt5c.close_position_partial = lambda p, v: (closed.append((p.ticket, v)) or
                                                   types.SimpleNamespace(retcode=mt5.TRADE_RETCODE_DONE))

    CFG.LIVE_TRADING = True          # чтобы дойти до вызовов, а не до DRY-RUN-логов
    CFG.USE_TP_TIGHTEN = True
    CFG.USE_BREAK_EVEN_RESCUE = True
    CFG.AUTO_ADAPT_TO_SYMBOL = False  # берём пункты из настроек как есть
    CFG.USE_BREAK_EVEN = False
    CFG.USE_TRAILING_STOP = False
    CFG.USE_PROFIT_LOCK_TRAILING = False
    CFG.USE_PARTIAL_CLOSE = False
    CFG.TP_TIGHTEN_MIN_PROFIT_POINTS = 10
    CFG.TP_TIGHTEN_STEP_POINTS = 5
    CFG.BE_RESCUE_AFTER_MINUTES = 10
    CFG.BE_RESCUE_MIN_DRAWDOWN_POINTS = 20
    CFG.BE_RESCUE_EXIT_POINTS = 3

    def reset():
        tm._position_peak_points.clear()
        tm._position_trough_points.clear()
        tm._position_risk_points.clear()
        tm._position_first_seen.clear()
        tm._closed_peaks.clear()
        modified.clear()
        closed.clear()

    # --- BUY без TP: цель ставится с первого прохода
    reset()
    test_end_to_end.price = (2000.50, 2000.52)
    pos = FakePos(1, True, 2000.0, 1999.0, 0.0)
    tm.manage_open_positions("XAUUSD", 1.0, POINT, positions=[pos], learned_tp_points=100)
    check(len(modified) == 1 and abs(modified[0][2] - 2001.0) < 1e-9,
          "BUY без TP: цель 100 пт выставлена", str(modified))
    check(modified[0][1] == 1999.0, "Стоп-лосс при этом не тронут", str(modified))

    # --- Повторный проход с тем же TP: сервер зря не дёргается
    reset()
    pos = FakePos(1, True, 2000.0, 1999.0, 2001.0)
    tm.manage_open_positions("XAUUSD", 1.0, POINT, positions=[pos], learned_tp_points=100)
    check(not modified, "Цель не изменилась — обращения к серверу нет", str(modified))

    # --- Выученная цель уменьшилась, но ОСТАЁТСЯ выше риска: TP подтягивается
    # Стоп на 1999.0 при входе 2000.0 = риск 100 пт. Цель 150 пт выше риска,
    # значит пол 1R не мешает и перенос происходит.
    reset()
    pos = FakePos(1, True, 2000.0, 1999.0, 2002.0)
    tm.manage_open_positions("XAUUSD", 1.0, POINT, positions=[pos], learned_tp_points=150)
    check(len(modified) == 1 and abs(modified[0][2] - 2001.5) < 1e-9,
          "Цель уменьшилась (но выше риска) — TP подтянут ближе", str(modified))

    # --- Выученная цель НИЖЕ риска сделки: пол 1R не даёт её принять.
    # Это и есть защита от «мелкие плюсы против полноразмерных стопов».
    reset()
    pos = FakePos(1, True, 2000.0, 1999.0, 2001.0)
    tm.manage_open_positions("XAUUSD", 1.0, POINT, positions=[pos], learned_tp_points=50)
    check(not modified,
          "Цель ниже риска (50 пт против 100 пт стопа) — TP НЕ опускается", str(modified))

    # --- Выученная цель ВЫРОСЛА: TP остаётся на месте (главное правило)
    reset()
    pos = FakePos(1, True, 2000.0, 1999.0, 2000.5)
    tm.manage_open_positions("XAUUSD", 1.0, POINT, positions=[pos], learned_tp_points=300)
    check(not modified, "Цель выросла — TP НЕ отодвигается", str(modified))

    # --- Спасение в безубыток: позиция просела и вернулась к нулю
    reset()
    pos = FakePos(2, True, 2000.0, 1999.0, 2001.0)
    test_end_to_end.price = (1999.50, 1999.52)          # -50 пт
    tm.manage_open_positions("XAUUSD", 1.0, POINT, positions=[pos], learned_tp_points=100)
    check(not closed, "Свежая просевшая сделка не закрывается")

    tm._position_first_seen[2] -= 11 * 60                # состарили на 11 минут
    test_end_to_end.price = (2000.10, 2000.12)          # вернулась к +10 пт
    tm.manage_open_positions("XAUUSD", 1.0, POINT, positions=[pos], learned_tp_points=100)
    check(closed == [(2, 0.10)], "Просевшая и вернувшаяся сделка закрыта целиком", str(closed))

    # --- Та же сделка, но всё ещё в минусе: цель переносится на ноль, не закрывается
    reset()
    pos = FakePos(3, True, 2000.0, 1999.0, 2001.0)
    test_end_to_end.price = (1999.50, 1999.52)
    tm.manage_open_positions("XAUUSD", 1.0, POINT, positions=[pos], learned_tp_points=100)
    tm._position_first_seen[3] -= 11 * 60
    tm.manage_open_positions("XAUUSD", 1.0, POINT, positions=[pos], learned_tp_points=100)
    check(not closed, "Всё ещё в минусе — не закрываем")
    check(len(modified) >= 1 and abs(modified[-1][2] - (2000.0 + 3 * POINT)) < 1e-9,
          "Цель перенесена на уровень безубытка", str(modified))
    check(modified[-1][1] == 1999.0, "Стоп-лосс НЕ тронут спасением", str(modified))

    # --- SELL зеркально
    reset()
    test_end_to_end.price = (1999.48, 1999.50)
    pos = FakePos(4, False, 2000.0, 2001.0, 0.0)
    tm.manage_open_positions("XAUUSD", 1.0, POINT, positions=[pos], learned_tp_points=100)
    check(len(modified) == 1 and abs(modified[0][2] - 1999.0) < 1e-9,
          "SELL без TP: цель 100 пт выставлена зеркально", str(modified))

    # --- Выключатели действительно выключают
    reset()
    CFG.USE_TP_TIGHTEN = False
    CFG.USE_BREAK_EVEN_RESCUE = False
    test_end_to_end.price = (2000.50, 2000.52)
    pos = FakePos(5, True, 2000.0, 1999.0, 0.0)
    tm.manage_open_positions("XAUUSD", 1.0, POINT, positions=[pos], learned_tp_points=100)
    check(not modified and not closed, "Обе новые функции выключаются настройками", str(modified))
    CFG.USE_TP_TIGHTEN = True
    CFG.USE_BREAK_EVEN_RESCUE = True

    # --- Пик закрытой позиции уходит в обучение
    reset()
    test_end_to_end.price = (2000.80, 2000.82)
    pos = FakePos(6, True, 2000.0, 1999.0, 2002.0)
    tm.manage_open_positions("XAUUSD", 1.0, POINT, positions=[pos], learned_tp_points=200)
    tm.manage_open_positions("XAUUSD", 1.0, POINT, positions=[], learned_tp_points=200)
    archived = tm.pop_closed_peak(6)
    check(archived is not None and abs(archived - 80.0) < 0.01,
          "Пик +80 пт закрытой сделки доступен для обучения", str(archived))


# =====================================================================
# 7. Выученное переживает перезапуск программы
# =====================================================================
def test_partial_close() -> None:
    """Владелец: «пусть ещё делает частичное закрытие сделок для большей
    фиксации дохода».

    Механизм в программе был, но выключен. Включаем — и сразу проверяем
    честность: при минимальном лоте 0.01 половина это 0.005, такого объёма
    у брокера не существует. Молча ничего не делать здесь нельзя: человек
    будет ждать фиксации, которой физически не может произойти."""
    print("\n[Частичное закрытие: фиксируем половину прибыли]")

    import MetaTrader5 as mt5

    # Значения по умолчанию берём из ЭТАЛОНА заново: CFG к этому моменту
    # уже перенастроен предыдущими тестами
    fresh = types.ModuleType("config_fresh")
    exec((APP / "config.py.example").read_text(encoding="utf-8"), fresh.__dict__)
    check(fresh.USE_PARTIAL_CLOSE is True,
          "Частичное закрытие включено по умолчанию")
    check(0 < fresh.PARTIAL_CLOSE_PERCENT < 100,
          "Закрывается часть объёма, а не весь и не ноль",
          str(fresh.PARTIAL_CLOSE_PERCENT))

    saved = {name: getattr(CFG, name) for name in
             ("LIVE_TRADING", "USE_PARTIAL_CLOSE", "PARTIAL_CLOSE_PERCENT",
              "PARTIAL_CLOSE_TRIGGER_POINTS", "AUTO_ADAPT_TO_SYMBOL",
              "USE_BREAK_EVEN", "USE_TRAILING_STOP", "USE_PROFIT_LOCK_TRAILING",
              "USE_TP_TIGHTEN", "USE_BREAK_EVEN_RESCUE")}
    saved_info = mt5.symbol_info
    saved_tick = tm.mt5c.get_tick
    saved_modify = tm.mt5c.modify_position
    saved_close = tm.mt5c.close_position_partial

    CFG.LIVE_TRADING = True
    CFG.USE_PARTIAL_CLOSE = True
    CFG.PARTIAL_CLOSE_PERCENT = 50
    CFG.PARTIAL_CLOSE_TRIGGER_POINTS = 100
    CFG.AUTO_ADAPT_TO_SYMBOL = False
    for off in ("USE_BREAK_EVEN", "USE_TRAILING_STOP", "USE_PROFIT_LOCK_TRAILING",
                "USE_TP_TIGHTEN", "USE_BREAK_EVEN_RESCUE"):
        setattr(CFG, off, False)

    closed: list = []
    tm.mt5c.modify_position = lambda t, sl, tp: None
    tm.mt5c.close_position_partial = lambda p, v: (
        closed.append((p.ticket, round(v, 3))) or
        types.SimpleNamespace(retcode=mt5.TRADE_RETCODE_DONE))

    def run(volume, vol_min=0.01, vol_step=0.01):
        """Позиция в плюсе на 150 пунктов при пороге 100."""
        tm._partial_closed_tickets.clear()
        tm._partial_impossible_tickets.clear()
        tm._position_peak_points.clear()
        tm._position_first_seen.clear()
        closed.clear()
        mt5.symbol_info = lambda s: types.SimpleNamespace(
            trade_stops_level=0, volume_min=vol_min, volume_step=vol_step)
        run.pos = FakePos(555, True, 2000.0, 1999.0, 0.0, volume=volume)
        tm.mt5c.get_tick = lambda s: FakeTick(2001.5, 2001.5)
        tm.manage_open_positions("XAUUSD", 1.0, POINT, positions=[run.pos])

    try:
        # Лот 0.10 — половина (0.05) закрывается
        run(0.10)
        check(closed == [(555, 0.05)],
              "Половина объёма зафиксирована в плюс", str(closed))

        # Второй раз по той же сделке — не закрываем: один раз за сделку
        before = list(closed)
        tm.manage_open_positions("XAUUSD", 1.0, POINT, positions=[run.pos])
        check(closed == before, "Повторно ту же сделку не режем", str(closed))

        # Лот 0.03 при шаге 0.01: половина 0.015 округляется вниз до 0.01
        run(0.03)
        check(closed == [(555, 0.01)],
              "Объём округляется вниз до шага брокера", str(closed))

        # ГЛАВНОЕ: минимальный лот 0.01 — закрыть нечего
        run(0.01)
        check(closed == [], "Половину минимального лота брокеру не шлём",
              str(closed))
        check(555 in tm._partial_impossible_tickets,
              "Программа отметила, что закрытие невозможно")

        # ...и сказала об этом ровно один раз, а не на каждом такте
        source = (APP / "trade_manager.py").read_text(encoding="utf-8")
        block = source.split("_partial_impossible_tickets.add", 1)[1][:600]
        check("невозможно" in block, "В логе объяснено, почему не сработало")
        check("0.02" in block or "vol_min * 2" in block,
              "Сказано, с какого лота заработает")
    finally:
        for name, value in saved.items():
            setattr(CFG, name, value)
        mt5.symbol_info = saved_info
        tm.mt5c.get_tick = saved_tick
        tm.mt5c.modify_position = saved_modify
        tm.mt5c.close_position_partial = saved_close
        tm._partial_closed_tickets.clear()
        tm._partial_impossible_tickets.clear()

    # Забытые тикеты не копятся: закрытая сделка уходит из обоих наборов
    tm._partial_impossible_tickets.add(999)
    tm._partial_closed_tickets.add(999)
    tm.cleanup_peak_profit(set())
    check(999 not in tm._partial_impossible_tickets and
          999 not in tm._partial_closed_tickets,
          "После закрытия сделки её тикет забывается")


def test_symbol_auto_off() -> None:
    """Владелец: «опять минуса».

    По его настоящему отчёту (148 сделок, депозит 65) видно, откуда они:
    ОДИН инструмент — золото — дал 85% всех потерь, минус 29.56 за 37
    сделок, пока остальные пары были в плюсе. Заметить это можно было
    только вручную по отчёту. Теперь такой инструмент отключается сам."""
    print("\n[Убыточный инструмент отключается сам]")

    fresh = types.ModuleType("config_fresh")
    exec((APP / "config.py.example").read_text(encoding="utf-8"), fresh.__dict__)
    check(fresh.USE_SYMBOL_AUTO_OFF is True, "Включено по умолчанию")
    check(fresh.SYMBOL_AUTO_OFF_MIN_TRADES >= 5,
          "Судим не по двум сделкам", str(fresh.SYMBOL_AUTO_OFF_MIN_TRADES))
    check(0 < fresh.SYMBOL_AUTO_OFF_LOSS_PERCENT < 100,
          "Порог убытка — осмысленная доля счёта",
          str(fresh.SYMBOL_AUTO_OFF_LOSS_PERCENT))

    saved = {n: getattr(CFG, n, None) for n in
             ("USE_SYMBOL_AUTO_OFF", "SYMBOL_AUTO_OFF_MIN_TRADES",
              "SYMBOL_AUTO_OFF_LOSS_PERCENT", "AUTO_LEARNING_WINDOW")}
    CFG.USE_SYMBOL_AUTO_OFF = True
    CFG.SYMBOL_AUTO_OFF_MIN_TRADES = 10
    CFG.SYMBOL_AUTO_OFF_LOSS_PERCENT = 8.0
    CFG.AUTO_LEARNING_WINDOW = 20
    try:
        def state_with(profits):
            st = SymbolState(symbol="XAUUSD")
            for value in profits:
                al.record_trade_result(st, value)
            return st

        # Мало сделок — не судим, даже если минус огромный
        few = state_with([-5.0] * 3)
        check(al.symbol_auto_off_reason(few, 65.0) == "",
              "Три сделки — рано судить")

        # Ровно случай владельца: золото, минус почти половина счёта
        gold = state_with([-1.5] * 12)          # −18 при счёте 65 = 27%
        reason = al.symbol_auto_off_reason(gold, 65.0)
        check(reason != "", "Инструмент, съевший 27% счёта, отключён")
        check("возобновится" in reason,
              "Сказано, что отключение не навсегда", reason)
        check("Другие инструменты" in reason,
              "Сказано, что остальные пары работают", reason)

        # Тот же минус на большом счёте — это шум, отключать нечего
        check(al.symbol_auto_off_reason(gold, 10000.0) == "",
              "На счёте 10 000 тот же минус ничего не значит")

        # Инструмент в плюсе не трогаем никогда
        good = state_with([1.0] * 12)
        check(al.symbol_auto_off_reason(good, 65.0) == "",
              "Прибыльный инструмент не отключается")

        # Винрейт высокий, но деньги в минусе — отключаем ПО ДЕНЬГАМ:
        # десять плюсов по 0.2 не перекрывают два минуса по 5
        tricky = state_with([0.2] * 10 + [-5.0, -5.0])
        check(al.recent_win_rate(tricky) > 0.5, "Винрейт выше 50% (проверка теста)")
        check(al.symbol_auto_off_reason(tricky, 65.0) != "",
              "Судим по деньгам, а не по доле выигрышей")

        # Выключатель работает
        CFG.USE_SYMBOL_AUTO_OFF = False
        check(al.symbol_auto_off_reason(gold, 65.0) == "",
              "Выключенная настройка ничего не отключает")
        CFG.USE_SYMBOL_AUTO_OFF = True

        # Убытки выпадают из окна — инструмент возвращается сам
        recovering = state_with([-1.5] * 12)
        for _ in range(20):
            al.record_trade_result(recovering, 0.5)
        check(al.symbol_auto_off_reason(recovering, 65.0) == "",
              "Старые убытки вышли из окна — торговля возобновилась сама")

        # Ворота стоят в главном цикле, а не только в теории
        main_src = (APP / "main.py").read_text(encoding="utf-8")
        check("symbol_auto_off_reason" in main_src,
              "Проверка вызывается из главного цикла")
        gate = main_src.split("symbol_auto_off_reason", 1)[1][:250]
        check("last_reject_reason" in gate and "return" in gate,
              "Причина видна в интерфейсе, вход отменяется")
    finally:
        for name, value in saved.items():
            if value is not None:
                setattr(CFG, name, value)

    # Окно денег должно переживать перезапуск, иначе оно всегда пустое
    learning = (APP / "auto_learning.py").read_text(encoding="utf-8")
    check('"profits"' in learning.split("def save_learning_state", 1)[1][:800],
          "Деньги по сделкам сохраняются на диск")
    check("recent_profits" in learning.split("def load_learning_state", 1)[1],
          "И восстанавливаются при запуске")


def test_learning_persistence() -> None:
    print("\n[Обучение переживает перезапуск]")

    import tempfile
    tmp = tempfile.mkdtemp()
    CFG.USE_LEARNING_PERSISTENCE = True
    CFG.LEARNING_STATE_PATH = str(Path(tmp) / "learning_state.json")
    CFG.AUTO_LEARNING_WINDOW = 20

    a = SymbolState(symbol="XAUUSD")
    b = SymbolState(symbol="EURUSD")
    for pk, win in ((40, True), (60, True), (0, False), (55, True), (50, False)):
        al.record_trade_peak(a, pk)
        al.record_trade_result(a, 1.0 if win else -1.0)
    al.record_trade_peak(b, 12)
    al.record_trade_result(b, 5.0)

    check(al.save_learning_state({"XAUUSD": a, "EURUSD": b}), "Статистика сохранена в файл")
    check(Path(CFG.LEARNING_STATE_PATH).exists(), "Файл обучения создан")

    # "Перезапуск": новые пустые состояния
    a2 = SymbolState(symbol="XAUUSD")
    b2 = SymbolState(symbol="EURUSD")
    restored = al.load_learning_state({"XAUUSD": a2, "EURUSD": b2})
    check(restored == 2, "Восстановлены оба символа", str(restored))
    check(a2.recent_peaks == a.recent_peaks, "Пики восстановлены точно", str(a2.recent_peaks))
    check(a2.recent_results == a.recent_results, "Результаты восстановлены точно", str(a2.recent_results))
    check(abs(al.learned_profit_points(a2, 0.0) - al.learned_profit_points(a, 0.0)) < 1e-9,
          "Выученная цель после перезапуска та же")

    # Символ, которого не было в файле — просто копит с нуля, без ошибки
    c = SymbolState(symbol="GBPUSD")
    al.load_learning_state({"GBPUSD": c})
    check(c.recent_peaks == [] and c.recent_results == [], "Незнакомый символ начинает с нуля")

    # Уменьшили окно между запусками — хвост обрезается по новому окну
    CFG.AUTO_LEARNING_WINDOW = 3
    a3 = SymbolState(symbol="XAUUSD")
    al.load_learning_state({"XAUUSD": a3})
    check(len(a3.recent_peaks) == 3 and a3.recent_peaks == a.recent_peaks[-3:],
          "Окно уменьшили — восстанавливаются только последние записи", str(a3.recent_peaks))
    CFG.AUTO_LEARNING_WINDOW = 20

    # Испорченный файл не роняет программу
    Path(CFG.LEARNING_STATE_PATH).write_text("{это не json", encoding="utf-8")
    d = SymbolState(symbol="XAUUSD")
    check(al.load_learning_state({"XAUUSD": d}) == 0, "Испорченный файл — 0 восстановлено, без падения")
    check(d.recent_peaks == [], "После испорченного файла статистика пустая")

    # Чужая структура внутри валидного JSON — тоже не ломает
    Path(CFG.LEARNING_STATE_PATH).write_text('{"symbols": {"XAUUSD": "мусор"}}', encoding="utf-8")
    e = SymbolState(symbol="XAUUSD")
    check(al.load_learning_state({"XAUUSD": e}) == 0, "Чужой формат — тоже 0, без падения")

    # Отсутствующий файл — не ошибка
    Path(CFG.LEARNING_STATE_PATH).unlink()
    f = SymbolState(symbol="XAUUSD")
    check(al.load_learning_state({"XAUUSD": f}) == 0, "Файла нет — не ошибка, просто ноль")

    # Выключатель
    CFG.USE_LEARNING_PERSISTENCE = False
    check(al.save_learning_state({"XAUUSD": a}) is False, "USE_LEARNING_PERSISTENCE=False не сохраняет")
    check(not Path(CFG.LEARNING_STATE_PATH).exists(), "И файл не создаёт")
    CFG.USE_LEARNING_PERSISTENCE = True

    # Файл обучения не должен попасть в git
    gitignore = (APP.parent / ".gitignore").read_text(encoding="utf-8")
    check("learning_state.json" in gitignore, "learning_state.json указан в .gitignore")

    # main.py действительно загружает и сохраняет
    src = (APP / "main.py").read_text(encoding="utf-8")
    check("al.load_learning_state(sym_states)" in src, "main.py загружает статистику при старте")
    check("al.save_learning_state(sym_states)" in src, "main.py сохраняет статистику")


def test_target_never_below_own_risk() -> None:
    """САМОЕ ВАЖНОЕ ЗДЕСЬ: цель прибыли не может стать меньше собственного
    риска сделки.

    Без этого пола сжатие цели со временем делало систему убыточной по
    математике. У профиля «Истеричка» стоп = 0.5*ATR, стартовая цель =
    1.5*ATR, пол сжатия 25% -> 0.375*ATR. Через 7-8 минут сделка рисковала
    БОЛЬШЕ, чем могла выиграть (1 : 0.75). При винрейте около половины это
    гарантированный минус: мелкие плюсы против полноразмерных стопов."""
    print("\n[Цель не опускается ниже собственного риска]")

    risk = 50.0        # стоп в 50 пунктах = 1R

    # Цель ужалась до 20 пт — меньше риска. Пол обязан её поднять до 50.
    tp = tm.tighten_take_profit(True, OPEN, 2000.10, 0.0, 20, 5, POINT, 0.0, 0,
                                risk_points=risk, min_r=1.0)
    check(abs(tp - (OPEN + risk * POINT)) < 1e-9,
          "Цель поднята до 1R, а не оставлена ниже риска", f"{tp}")

    tp = tm.tighten_take_profit(False, OPEN, 1999.90, 0.0, 20, 5, POINT, 0.0, 0,
                                risk_points=risk, min_r=1.0)
    check(abs(tp - (OPEN - risk * POINT)) < 1e-9, "SELL: то же самое", f"{tp}")

    # Цель БОЛЬШЕ риска — пол не мешает
    tp = tm.tighten_take_profit(True, OPEN, 2000.10, 0.0, 150, 5, POINT, 0.0, 0,
                                risk_points=risk, min_r=1.0)
    check(abs(tp - (OPEN + 150 * POINT)) < 1e-9,
          "Цель выше риска остаётся как есть", f"{tp}")

    # Требование более осторожного соотношения
    tp = tm.tighten_take_profit(True, OPEN, 2000.10, 0.0, 20, 5, POINT, 0.0, 0,
                                risk_points=risk, min_r=2.0)
    check(abs(tp - (OPEN + 100 * POINT)) < 1e-9,
          "min_r=2 требует цель вдвое больше риска", f"{tp}")

    # Риск неизвестен (позиция без стопа) — пол не применяется, но и вреда нет
    tp = tm.tighten_take_profit(True, OPEN, 2000.10, 0.0, 20, 5, POINT, 0.0, 0,
                                risk_points=0.0, min_r=1.0)
    check(abs(tp - (OPEN + 20 * POINT)) < 1e-9,
          "Без известного риска работает по-старому", f"{tp}")

    # Полная картина «Истерички»: ATR=1.0 (100 пт), стоп 0.5*ATR = 50 пт
    atr_points = 100.0
    stop = atr_points * 0.5
    start_target = atr_points * 1.5
    for minutes in (0, 5, 10, 30, 120):
        shrunk = tm.shrunk_target_points(start_target, minutes * 60, 0.10, 0.25)
        tp = tm.tighten_take_profit(True, OPEN, 2000.10, 0.0, shrunk, 5, POINT, 0.0, 0,
                                    risk_points=stop, min_r=1.0)
        reward = (tp - OPEN) / POINT
        check(reward >= stop - 1e-6,
              f"Через {minutes} мин выигрыш ({reward:.0f} пт) не меньше риска ({stop:.0f} пт)",
              f"{reward:.1f}")

    # Настройка проброшена в реальный вызов, а не только существует
    src = (APP / "trade_manager.py").read_text(encoding="utf-8")
    check("risk_points=risk_points" in src, "Риск сделки передаётся в подтягивание цели")
    check("TP_TIGHTEN_MIN_R" in src, "Настройка минимального соотношения читается")
    cfg_text = (APP / "config.py.example").read_text(encoding="utf-8")
    check("TP_TIGHTEN_MIN_R" in cfg_text, "Настройка есть в шаблоне конфига")
    import types as _t
    fresh = _t.ModuleType("f")
    exec(cfg_text, fresh.__dict__)
    check(fresh.TP_TIGHTEN_MIN_R >= 1.0,
          "По умолчанию цель покрывает хотя бы свой стоп", str(fresh.TP_TIGHTEN_MIN_R))


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ ФИКСАЦИИ ПРИБЫЛИ")
    print("=" * 62)

    test_tp_tighten()
    test_tp_never_into_loss()
    test_target_shrink()
    test_break_even_rescue()
    test_rescue_never_touches_sl()
    test_learned_target()
    test_median()
    test_closed_peak_archive()
    test_position_age()
    test_config_params_exist()
    test_end_to_end()
    test_partial_close()
    test_symbol_auto_off()
    test_learning_persistence()
    test_target_never_below_own_risk()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
