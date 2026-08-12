#!/usr/bin/env python3
"""Тесты обхода пар по очереди и защиты от одной и той же ставки.

ОТКУДА ЗАДАЧА. Владелец: «не хочу каждый раз вписывать пары. Пусть само
подгрузится у брокера и работает по всему, на чём можно заработать за день».

Потолок в 20 пар оказался ненастоящим: вход возможен ТОЛЬКО на новом баре, а
таймфрейм M5 — бар раз в 300 секунд. Пара обходилась каждые 5 секунд, то есть
59 обходов из 60 заканчивались ничем.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ:
  1. Пара с ОТКРЫТОЙ сделкой обходится ВСЕГДА — иначе трейлинг-стоп опоздает
     и это будут реальные деньги.
  2. Остальные обходятся по кругу и НИ ОДНА не пропускается насовсем.
  3. Круг не растягивается: порция уменьшается по ФАКТИЧЕСКОМУ времени.
  4. Много пар не превращается в одну ставку, повторённую много раз.

Запуск:  python3 tests/test_scan_rotation.py
"""

from __future__ import annotations

import ast
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
        print(f"  СБОЙ {name}" + (f"  -> {detail}" if detail else ""))


cfg = types.ModuleType("config")
cfg.__file__ = str(APP / "config.py.example")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg
CFG = cfg
sys.modules["MetaTrader5"] = types.ModuleType("MetaTrader5")

import scan_rotation as sr      # noqa: E402
import symbol_picker as sp      # noqa: E402


def test_open_trades_are_never_delayed() -> None:
    """Самое важное. По открытой сделке работает трейлинг-стоп и безубыток.
    Если такая пара попадёт в общую очередь, стоп будет подтягиваться раз в
    полминуты вместо раза в пять секунд — это прямые потери."""
    print("\n[Пара с открытой сделкой обходится всегда, без очереди]")
    symbols = [f"P{i}" for i in range(30)]
    busy = {"P17", "P29"}

    for step in range(12):
        plan = sr.plan(symbols, busy, step * 3, size=3)
        missing = busy - set(plan["symbols"])
        if missing:
            check(False, f"Проход {step}: пара со сделкой пропущена",
                  str(missing))
            break
    else:
        check(True, "За 12 проходов пара со сделкой не пропущена ни разу")

    plan = sr.plan(symbols, busy, 0, size=3)
    check(len(plan["symbols"]) == 5,
          "Сделочные пары идут ДОПОЛНИТЕЛЬНО к порции, а не вместо неё",
          str(len(plan["symbols"])))
    check(len(set(plan["symbols"])) == len(plan["symbols"]),
          "Одна и та же пара не обходится дважды за проход")

    # Пара со сделкой не занимает место в очереди: очередь двигается по прочим
    check(plan["cursor"] == 3, "Очередь сдвинулась ровно на размер порции",
          str(plan["cursor"]))


def test_every_pair_gets_its_turn() -> None:
    """Пара, до которой очередь не доходит, не торгуется вообще. Это была бы
    тихая поломка: в списке она есть, в журнале ошибок нет, сделок нет."""
    print("\n[Ни одна пара не пропадает из очереди]")
    symbols = [f"P{i}" for i in range(37)]     # нарочно не делится нацело
    seen, cursor = set(), 0
    for _ in range(20):
        plan = sr.plan(symbols, set(), cursor, size=7)
        seen.update(plan["symbols"])
        cursor = plan["cursor"]
    check(seen == set(symbols), "За 20 проходов обойдены ВСЕ 37 пар",
          f"не хватило: {sorted(set(symbols) - seen)}")

    # И круг замыкается ровно, без пропуска на стыке
    cursor, order = 0, []
    for _ in range(6):
        plan = sr.plan(symbols, set(), cursor, size=7)
        order.extend(plan["symbols"])
        cursor = plan["cursor"]
    check(order[:37] == symbols, "Порядок обхода — ровно по кругу, без дыр",
          str(order[:8]))


def test_slice_is_computed_from_the_bar_not_guessed() -> None:
    print("\n[Размер порции считается из времени круга]")
    # 120 пар, круг 5 с, обойти всё за 30 с -> 6 проходов -> 20 за проход
    check(sr.planned_slice(120, 5, 30) == 20, "120 пар -> 20 за проход",
          str(sr.planned_slice(120, 5, 30)))
    check(sr.planned_slice(300, 5, 30) == 50, "300 пар -> 50 за проход",
          str(sr.planned_slice(300, 5, 30)))
    # Остаток тоже должен быть обойдён: 37/6 = 6.16 -> берём 7, а не 6
    check(sr.planned_slice(37, 5, 30) == 7,
          "Остаток округляется ВВЕРХ — иначе хвост списка не обходится",
          str(sr.planned_slice(37, 5, 30)))
    check(sr.planned_slice(4, 5, 30) >= 2, "Маленький список не дробится в пыль",
          str(sr.planned_slice(4, 5, 30)))
    check(sr.planned_slice(0, 5, 30) == 0, "Пустой список — нечего обходить")
    check(sr.planned_slice(50, 5, 5) == 50,
          "Если обойти надо за один проход — берём всех")
    # Если время посчитать НЕ ПО ЧЕМУ, надо обойти всех, а не двоих: иначе
    # кривая настройка тихо оставила бы почти весь список без торговли
    check(sr.planned_slice(50, 0, 30) == 50,
          "Круг задан нулём — обходим весь список, а не обрезаем его",
          str(sr.planned_slice(50, 0, 30)))
    check(sr.planned_slice(50, 5, 0) == 50,
          "Время прокрутки задано нулём — тоже весь список",
          str(sr.planned_slice(50, 5, 0)))
    check(sr.planned_slice(50, -1, -1) == 50, "Отрицательные значения — так же")
    check(sr.planned_slice("мусор", 5, 30) >= sr.MIN_SLICE, "Мусор не роняет")


def test_slice_shrinks_when_the_pass_is_actually_slow() -> None:
    """40 мс на пару — это моя ОЦЕНКА. На медленном компьютере или медленном
    брокере она окажется неправдой, и платить будет владелец растянутым
    кругом. Поэтому решает замеренное время, а не оценка."""
    print("\n[Порция подстраивается под фактическое время, а не под оценку]")
    poll = 5.0
    budget = poll * sr.BUDGET_FRACTION       # 2.5 с

    slow = sr.adjust_slice(current=20, last_pass_seconds=5.0,
                           poll_seconds=poll, total=200)
    check(slow < 20, "Проход занял 5 с при бюджете 2.5 — порция уменьшена",
          str(slow))
    check(slow <= 10, "Уменьшена пропорционально перерасходу, а не на единицу",
          str(slow))

    fast = sr.adjust_slice(current=20, last_pass_seconds=0.4,
                           poll_seconds=poll, total=200)
    check(fast > 20, "Время есть — порция растёт", str(fast))
    check(fast == 21, "Растёт осторожно, по одной паре", str(fast))

    steady = sr.adjust_slice(current=20, last_pass_seconds=budget * 0.8,
                             poll_seconds=poll, total=200)
    check(steady == 20, "У границы бюджета размер не дёргается", str(steady))

    floor = sr.adjust_slice(current=2, last_pass_seconds=99.0,
                            poll_seconds=poll, total=200)
    check(floor >= sr.MIN_SLICE,
          "Даже при жутких тормозах список продолжает прокручиваться",
          str(floor))

    check(sr.adjust_slice(current=20, last_pass_seconds=0.1, poll_seconds=poll,
                          total=20) == 20,
          "Больше, чем есть пар, не берём")

    # Сжатие должно сходиться: повторный медленный проход не зациклится
    size = 50
    for _ in range(10):
        size = sr.adjust_slice(size, 5.0, poll, 200)
    check(size >= sr.MIN_SLICE, "Многократное сжатие не уходит в ноль",
          str(size))


def test_rotation_is_wired_into_the_loop() -> None:
    print("\n[Прокрутка подключена к торговому циклу]")
    src = (APP / "main.py").read_text(encoding="utf-8")
    check("import scan_rotation" in src, "Модуль подключён")

    loop = src.split("while True:", 1)[1]
    check("scan_rotation.plan" in loop, "План обхода строится в цикле")
    check("_open_symbols(all_positions)" in loop,
          "Пары с открытой сделкой отмечены как обязательные")
    check("scan_rotation.adjust_slice" in loop,
          "Размер порции пересчитывается по факту каждый проход")

    # Обход ДОЛЖЕН идти по плану, а не по всему словарю
    check("for sym, st in sym_states.items():" not in loop,
          "Старый обход всех пар подряд убран")
    check("for sym in step[\"symbols\"]:" in loop, "Обходятся только пары из плана")

    # Время замеряется вокруг самого обхода, а не вокруг всего круга
    body = loop.split("_scan_started = time.time()", 1)
    check(len(body) == 2, "Замер времени начинается перед обходом")
    check("_scan[\"spent\"] = time.time() - _scan_started" in loop,
          "И заканчивается сразу после него")

    # Указатель очереди обязан сохраняться между проходами, иначе программа
    # вечно обходила бы первые N пар, а хвост списка не торговался бы никогда
    check("_scan = {" in src, "Состояние очереди живёт между проходами")
    check("_scan[\"cursor\"] = step[\"cursor\"]" in loop,
          "Указатель очереди сохраняется — иначе хвост списка не торгуется")


def test_same_bet_is_not_opened_many_times() -> None:
    """Пар стало много, и главная опасность — не их число, а то, что они не
    независимы. Четыре сделки против доллара это ОДНА ставка, и стопы по ним
    сработают вместе."""
    print("\n[Одна и та же ставка не открывается много раз]")
    sys.modules.pop("risk_manager", None)
    sys.modules["mt5_connector"] = types.ModuleType("mt5_connector")
    ctrl = types.ModuleType("control")
    ctrl.control = types.SimpleNamespace()
    sys.modules["control"] = ctrl
    import risk_manager as rm

    # Три сделки против доллара уже открыты, потолок 3
    open_now = ["EURUSD", "GBPUSD", "AUDUSD"]
    why = rm.currency_exposure_reason("NZDUSD", open_now, 3)
    check(why != "", "Четвёртая долларовая пара не открывается", why or "(пусто)")
    check("USD" in why, "Названа именно та валюта, которая набрана", why)
    check("MAX_POSITIONS_PER_CURRENCY" in why, "Названа настройка", why)

    # А пара без доллара — можно: это ДРУГАЯ ставка
    check(rm.currency_exposure_reason("EURGBP", open_now, 3) == "",
          "Пара без набранной валюты открывается — это другая ставка")

    check(rm.currency_exposure_reason("NZDUSD", open_now, 0) == "",
          "0 = ограничение выключено")
    check(rm.currency_exposure_reason("NZDUSD", [], 3) == "",
          "Сделок нет — ограничивать нечего")
    check(rm.currency_exposure_reason("US500", open_now, 3) == "",
          "У индекса валют нет — ограничение к нему не применяется")

    # Суффикс брокера не должен обманывать защиту
    check(rm.currency_exposure_reason("NZDUSDm", ["EURUSD.a", "GBPUSDs", "AUDUSD"], 3) != "",
          "Суффиксы брокера не обходят защиту — иначе она молчала бы у половины брокеров")

    check(rm.currency_exposure_reason("NZDUSD", ["EURUSD", "GBPUSD"], 3) == "",
          "Пока потолок не набран — открывать можно")

    # Проверяем СТРОЕНИЕ кода, а не наличие текста: поиском по строке легко
    # обмануться — «same_bet = "" or (rm.currency_exposure_reason(...))» текст
    # содержит, а защиту отключает. Ровно на этом мой первый тест и попался.
    src = (APP / "main.py").read_text(encoding="utf-8")
    func = next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef) and n.name == "process_symbol")

    guarded = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        called = ast.unparse(node.value.func)
        if called != "rm.currency_exposure_reason":
            continue
        # Результат обязан попасть прямо в переменную, без «или пусто» рядом
        guarded.append(ast.unparse(node.targets[0]))
    check(len(guarded) == 1,
          "Ответ защиты попадает в переменную напрямую, без обходных выражений",
          str(guarded))

    if guarded:
        name = guarded[0]
        returns = [n for n in ast.walk(func)
                   if isinstance(n, ast.If) and ast.unparse(n.test) == name
                   and any(isinstance(b, ast.Return) for b in n.body)]
        check(len(returns) == 1,
              "И если защита сработала — вход прекращается (return)",
              str(len(returns)))

    check(src.index("rm.currency_exposure_reason(") < src.index("direction = 0"),
          "Проверка идёт ДО принятия решения о входе")
    check(int(CFG.MAX_POSITIONS_PER_CURRENCY) > 0,
          "И по умолчанию она включена",
          str(CFG.MAX_POSITIONS_PER_CURRENCY))


def test_no_artificial_cap_on_the_list() -> None:
    """Владелец просил не ограничивать список руками. Потолок снят — но он не
    должен превратиться в «одну пару» из-за того, что ноль где-то понят как
    единица."""
    print("\n[Потолок на число пар снят по-настоящему]")
    check(int(CFG.AUTO_PICK_LIMIT) == 0, "AUTO_PICK_LIMIT = 0 (без потолка)",
          str(CFG.AUTO_PICK_LIMIT))

    many = [{"symbol": f"AA{i:02d}BB", "spread_points": 10 + i,
             "atr_points": 200, "min_lot": 0.01, "money_per_point": 1.0,
             "stop_points": 300, "trade_mode": 4} for i in range(40)]
    result = sp.pick(many, equity=1000, limit=0, per_currency=0)
    check(len(result["chosen"]) == 40, "Ноль означает ВСЕ подходящие пары",
          str(len(result["chosen"])))

    result = sp.pick(many, equity=1000, limit=5, per_currency=0)
    check(len(result["chosen"]) == 5, "Ненулевой потолок по-прежнему работает",
          str(len(result["chosen"])))

    # Ограничение по валютам в СПИСКЕ выключено — оно перенесено на сделки
    check(int(CFG.AUTO_PICK_PER_CURRENCY) == 0,
          "Список по валютам не режется — смотреть пары ничего не стоит")

    # ...но дорогие пары обязаны отсеиваться и без потолка
    pricey = many + [{"symbol": "XXXYYY", "spread_points": 500,
                      "atr_points": 200, "min_lot": 0.01,
                      "money_per_point": 1.0, "stop_points": 300,
                      "trade_mode": 4}]
    result = sp.pick(pricey, equity=1000, limit=0, per_currency=0)
    check("XXXYYY" not in result["chosen"],
          "Снятый потолок не отменяет отсев дорогих пар")


def test_explanation_has_numbers() -> None:
    """Почему потолок был ненастоящим, должно быть записано числами: иначе
    через месяц никто (включая меня) не вспомнит, на чём основано решение."""
    print("\n[Причина записана числами, а не на словах]")
    doc = (APP / "scan_rotation.py").read_text(encoding="utf-8")
    check("300 секунд" in doc, "Названа длина бара M5")
    check("59" in doc or "пятьдесят девять" in doc,
          "Названо, сколько обходов уходило впустую")
    check("трейлинг" in doc.lower(),
          "Названа причина, по которой сделочные пары идут без очереди")
    cfgtext = (APP / "config.py.example").read_text(encoding="utf-8")
    check("SCAN_ROTATE_SECONDS" in cfgtext, "Настройка есть в config")
    check("MAX_POSITIONS_PER_CURRENCY" in cfgtext, "И защита от одной ставки")


def test_describe_tells_the_real_interval() -> None:
    print("\n[Человеку сказано, как часто на деле проверяется пара]")
    line = sr.describe(120, 20, 5)
    check("120" in line, "Названо число пар", line)
    check("30" in line, "И реальный интервал проверки одной пары", line)
    check(sr.describe(0, 0, 5) != "", "Пустой список не роняет строку")


if __name__ == "__main__":
    print("=" * 62)
    print("ТЕСТЫ: ОБХОД МНОГИХ ПАР ПО ОЧЕРЕДИ")
    print("=" * 62)
    test_open_trades_are_never_delayed()
    test_every_pair_gets_its_turn()
    test_slice_is_computed_from_the_bar_not_guessed()
    test_slice_shrinks_when_the_pass_is_actually_slow()
    test_rotation_is_wired_into_the_loop()
    test_same_bet_is_not_opened_many_times()
    test_no_artificial_cap_on_the_list()
    test_explanation_has_numbers()
    test_describe_tells_the_real_interval()
    print()
    print("=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    sys.exit(1 if failed else 0)
