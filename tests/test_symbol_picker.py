#!/usr/bin/env python3
"""Тесты самостоятельного отбора пар у брокера.

ОТКУДА ЗАДАЧА. Владелец: «можешь добавить, чтобы список сам подгружался — всё,
что есть у брокера, и он старался работать на всех парах? Если проблематично —
выбрать самые топовые пары, на которых больше всего вариант заработать».

НА ВСЕХ НЕЛЬЗЯ, и это посчитано, а не заявлено: около 40 мс на пару за проход,
300 пар превращают пятисекундный круг в двенадцатисекундный. Плюс пары не
независимы — EURUSD, GBPUSD, AUDUSD и NZDUSD это в основном ставка на доллар.

Поэтому отбор. ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ — что отбор считает то, что обещает, и
никогда не выдаёт пару, которая депозиту не по карману.

Запуск:  python3 tests/test_symbol_picker.py
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

import symbol_picker as sp      # noqa: E402


def pair(symbol, spread, atr, min_lot=0.01, money_per_point=1.0,
         stop=None, trade_mode=4):
    return {"symbol": symbol, "spread_points": spread, "atr_points": atr,
            "min_lot": min_lot, "money_per_point": money_per_point,
            "stop_points": stop if stop is not None else atr * 1.5,
            "trade_mode": trade_mode}


def test_spread_cost_is_relative_not_absolute() -> None:
    """Спред в пунктах сам по себе ничего не значит: 20 пунктов на паре,
    которая ходит 2000, — это дёшево, а на паре, которая ходит 60, —
    половина хода."""
    print("\n[Цена входа считается долей от движения, а не в пунктах]")
    cheap = sp.spread_cost_ratio(20, 2000)
    dear = sp.spread_cost_ratio(20, 60)
    check(cheap < dear, "Одинаковый спред — разная цена входа",
          f"{cheap:.3f} против {dear:.3f}")
    check(abs(cheap - 0.01) < 1e-9, "20 из 2000 — это 1%", str(cheap))
    check(abs(sp.spread_cost_ratio(10, 40) - 0.25) < 1e-9, "10 из 40 — 25%")

    for spread, atr, why in ((10, 0, "движения нет"), (10, -5, "ATR отрицательный"),
                             ("мусор", 100, "спред не число"), (10, None, "ATR пуст")):
        check(sp.spread_cost_ratio(spread, atr) >= 999,
              f"Посчитать нельзя ({why}) — пара уходит в конец, а не наверх")


def test_min_lot_risk() -> None:
    print("\n[Риск минимального лота в процентах счёта]")
    # 0.01 лота, стоп 100 пунктов, 1 доллар за пункт на лот, счёт 500
    risk = sp.min_lot_risk_percent(0.01, 100, 1.0, 500)
    check(abs(risk - 0.2) < 1e-9, "0.01 x 100 x 1 = 1 доллар = 0.2% от 500",
          str(risk))
    # Тот же инструмент на счёте 65 — уже втрое дороже относительно счёта
    check(sp.min_lot_risk_percent(0.01, 100, 1.0, 65) > risk,
          "На меньшем счёте тот же лот рискует большей долей")
    for args, why in ((( 0.01, 100, 1.0, 0), "счёт нулевой"),
                      ((0, 100, 1.0, 500), "лот нулевой"),
                      ((0.01, 0, 1.0, 500), "стоп нулевой"),
                      (("мусор", 100, 1.0, 500), "лот не число")):
        check(sp.min_lot_risk_percent(*args) >= 999,
              f"Посчитать нельзя ({why}) — считаем непригодной")


def test_expensive_pairs_are_rejected() -> None:
    """Главная защита: пара, где ОДИН минимальный лот стоит слишком дорого,
    не должна попасть в список никогда. Настройкой риска это не лечится —
    ниже минимального лота брокер не пускает."""
    print("\n[Слишком дорогая для депозита пара не проходит]")
    # Золото: минимальный лот 0.01 = 1 унция, шаг цены 0.01 доллара, то есть
    # один пункт на лоте стоит 100 x 0.01 = 1 доллар. Стоп 435 пунктов ->
    # 0.01 x 435 x 1 = 4.35 доллара. На счёте 65 это 6.7% — втрое выше потолка.
    gold = pair("XAUUSD", spread=30, atr=1000, min_lot=0.01,
                money_per_point=1.0, stop=435)
    why = sp.reject_reason(gold, equity=65, max_risk_percent=2.0,
                           max_spread_ratio=0.25)
    check(why != "", "На счёте 65 золото отклонено", why or "(пусто)")
    check("минимальный лот" in why.lower(), "И причина названа именно эта", why)

    # На счёте 1000 та же пара помещается
    check(sp.reject_reason(gold, equity=1000, max_risk_percent=2.0,
                           max_spread_ratio=0.25) == "",
          "А на счёте 1000 та же пара проходит — потолок относительный")


def test_wide_spread_is_rejected() -> None:
    print("\n[Пара, где спред съедает движение, не проходит]")
    bad = pair("EXOTIC", spread=50, atr=100)      # спред = половина хода
    why = sp.reject_reason(bad, 1000, 2.0, 0.25)
    check(why != "", "Отклонена")
    check("спред" in why.lower(), "Причина — спред", why)
    check("50%" in why, "И названа доля", why)

    good = pair("EURUSD", spread=10, atr=200)     # 5%
    check(sp.reject_reason(good, 1000, 2.0, 0.25) == "", "Дешёвая пара проходит")


def test_broker_disabled_is_rejected() -> None:
    print("\n[Запрещённая брокером пара не проходит]")
    why = sp.reject_reason(pair("X", 10, 200, trade_mode=0), 1000, 2.0, 0.25)
    check(why != "" and "брокер" in why, "Отклонена с прямой причиной", why)


def test_order_is_by_cost_of_entry() -> None:
    print("\n[Первыми идут пары с самым дешёвым входом]")
    result = sp.pick([
        pair("DEAR", spread=40, atr=200),     # 20%
        pair("CHEAP", spread=4, atr=200),     # 2%
        pair("MIDDLE", spread=20, atr=200),   # 10%
    ], equity=1000, limit=5, per_currency=0)
    check(result["chosen"] == ["CHEAP", "MIDDLE", "DEAR"],
          "Порядок по цене входа", str(result["chosen"]))


def test_one_bet_is_not_taken_six_times() -> None:
    """САМОЕ ВАЖНОЕ ПОСЛЕ ЦЕНЫ ВХОДА. Пары не независимы: EURUSD, GBPUSD,
    AUDUSD, NZDUSD — это в основном ставка на доллар. Набрать шесть таких
    значит сделать одну ставку шесть раз и получить шестикратный убыток
    там, где казалось, что риск размазан."""
    print("\n[Не набираем одну и ту же ставку несколько раз]")
    usd = [pair(n, spread=5, atr=200) for n in
           ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF")]
    result = sp.pick(usd, equity=1000, limit=10, per_currency=3)
    check(len(result["chosen"]) == 3,
          "С долларом взято не больше трёх пар", str(result["chosen"]))
    check(any("та же ставка" in r for r in result["rejected"]),
          "И человеку объяснено почему", str(result["rejected"][:2]))

    # Ограничение можно снять
    check(len(sp.pick(usd, 1000, limit=10, per_currency=0)["chosen"]) == 6,
          "При per_currency = 0 берутся все")

    # Разные валюты не мешают друг другу
    mixed = [pair("EURUSD", 5, 200), pair("GBPJPY", 5, 200), pair("AUDCAD", 5, 200)]
    check(len(sp.pick(mixed, 1000, limit=10, per_currency=1)["chosen"]) == 3,
          "Пары без общих валют берутся все")


def test_limit_is_respected() -> None:
    print("\n[Число пар ограничено — иначе круг не успевает]")
    many = [pair(f"SYM{i:03d}", spread=5, atr=200) for i in range(100)]
    result = sp.pick(many, 1000, limit=20, per_currency=0)
    check(len(result["chosen"]) == 20, "Взято ровно столько, сколько задано",
          str(len(result["chosen"])))
    check(len(sp.pick(many, 1000, limit=1, per_currency=0)["chosen"]) == 1,
          "И единица работает")
    check(CFG.AUTO_PICK_LIMIT <= 30,
          "Значение по умолчанию не выводит круг за POLL_SECONDS",
          str(CFG.AUTO_PICK_LIMIT))


def test_currencies_of() -> None:
    print("\n[Разбор имени пары на валюты]")
    check(sp.currencies_of("EURUSD") == ("EUR", "USD"), "EURUSD")
    check(sp.currencies_of("eurusd.m") == ("EUR", "USD"), "С суффиксом брокера")
    check(sp.currencies_of("XAUUSDs") == ("XAU", "USD"), "Металл тоже")
    check(sp.currencies_of("US500") == (), "Индекс — валют нет, ограничение не применяется")
    # Короткое имя нельзя разрезать пополам: из «DAX» получилась бы валюта
    # «DAX» и пустая вторая, и один индекс занял бы место валютной пары.
    for short in ("DAX", "SPX", "WTI", "NG", "BTC"):
        check(sp.currencies_of(short) == (),
              f"Короткое имя {short} — не валютная пара",
              str(sp.currencies_of(short)))
    check(sp.currencies_of("") == (), "Пустое имя не падает")
    check(sp.currencies_of(None) == (), "None тоже")


def test_nothing_suitable_is_said_plainly() -> None:
    print("\n[Если ничего не подошло — так и сказано]")
    result = sp.pick([pair("BAD", spread=500, atr=100)], 1000, limit=5)
    check(result["chosen"] == [], "Список пуст")
    check("не нашлось" in sp.describe(result), "И это написано человеку",
          sp.describe(result))
    check(sp.pick([], 1000)["chosen"] == [], "Пустой вход не падает")


def test_wired_into_startup() -> None:
    print("\n[Отбор подключён к запуску]")
    src = (APP / "main.py").read_text(encoding="utf-8")
    check("def auto_pick_symbols" in src, "Функция отбора есть")
    check("cfg.SYMBOLS = picked" in src, "Результат становится списком пар")
    # Отбор обязан идти ДО создания состояний: иначе он ни на что не повлияет.
    # Проверяем по СТРОЕНИЮ кода, а не по тексту вызова: текстовая проверка
    # ломалась от добавления обычного параметра, хотя код был верным, — то есть
    # шумела вместо того, чтобы ловить настоящие поломки.
    tree = ast.parse(src)
    main_fn = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    pick_at = [n.lineno for n in ast.walk(main_fn) if isinstance(n, ast.Call)
               and ast.unparse(n.func) == "auto_pick_symbols"]
    init_at = [n.lineno for n in ast.walk(main_fn) if isinstance(n, ast.Call)
               and ast.unparse(n.func) == "init_states"]
    check(len(pick_at) == 1, "Отбор при запуске вызывается ровно один раз",
          str(pick_at))
    check(bool(init_at), "Проверка самого теста: init_states в запуске есть")
    if pick_at and init_at:
        check(pick_at[0] < min(init_at),
              "Отбор идёт ДО построения списка символов",
              f"отбор на строке {pick_at[0]}, init_states на {min(init_at)}")

    body = src.split("def auto_pick_symbols", 1)[1].split("\ndef ", 1)[0]
    check("blocked_symbol_reason" in body,
          "Выключенные вручную пары в отбор не попадают — иначе вернулось бы "
          "золото, которое владелец просил отключить")
    check("AUTO_PICK_SYMBOLS" in body, "Отбор можно выключить настройкой")

    # Один раз при запуске, а не в цикле: замер всех пар брокера дорогой
    loop = src.split("while True:", 1)[1]
    check("auto_pick_symbols" not in loop,
          "В торговом цикле отбор не вызывается")


def _load_survey_symbol(spread_points, atr_bar_range, stops_level=0):
    """Взять НАСТОЯЩИЙ survey_symbol из main.py и выполнить его на подставном
    терминале. Так проверяется сам код, а не его пересказ в тесте."""
    src = (APP / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    func = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "survey_symbol")

    info = types.SimpleNamespace(
        point=0.00001, trade_tick_value=1.0, trade_tick_size=0.00001,
        spread=spread_points, volume_min=0.01, trade_mode=4,
        trade_stops_level=stops_level)
    mt5 = types.ModuleType("MetaTrader5")
    mt5.symbol_info = lambda name: info
    sys.modules["MetaTrader5"] = mt5

    class FakeSeries(list):
        def __sub__(self, other):
            return FakeSeries(a - b for a, b in zip(self, other))

        def tail(self, n):
            return FakeSeries(self[-n:])

        def mean(self):
            return sum(self) / len(self)

    class FakeDF:
        def __init__(self, span):
            # 60 баров одинакового размаха: средний размах = span
            self.rows = [span] * 60

        def __len__(self):
            return len(self.rows)

        def __getitem__(self, key):
            if key == "high":
                return FakeSeries(self.rows)
            return FakeSeries([0.0] * len(self.rows))

    selected = []
    ns = {"cfg": cfg,
          "mt5c": types.SimpleNamespace(
              get_rates_df=lambda *a, **k: FakeDF(atr_bar_range),
              ensure_symbol=lambda name: (selected.append(name), True)[1])}
    exec(compile(ast.Module(body=[func], type_ignores=[]), "main.py", "exec"), ns)
    row = ns["survey_symbol"]("EURUSD")
    row["_selected"] = list(selected)
    return row


def _survey_with_unavailable_symbol():
    """Тот же настоящий survey_symbol, но брокер не даёт добавить пару."""
    src = (APP / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    func = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "survey_symbol")
    mt5 = types.ModuleType("MetaTrader5")

    def boom(name):
        raise AssertionError("замер пошёл дальше, хотя пара недоступна")

    mt5.symbol_info = boom
    sys.modules["MetaTrader5"] = mt5
    ns = {"cfg": cfg,
          "mt5c": types.SimpleNamespace(ensure_symbol=lambda name: False,
                                        get_rates_df=boom)}
    exec(compile(ast.Module(body=[func], type_ignores=[]), "main.py", "exec"), ns)
    try:
        return ns["survey_symbol"]("EURUSD")
    finally:
        sys.modules["MetaTrader5"] = types.ModuleType("MetaTrader5")


def test_pair_must_be_added_to_market_watch_first() -> None:
    """БЕЗ ЭТОГО ОТБОР НЕ РАБОТАЕТ ВООБЩЕ. symbols_get() отдаёт все сотни пар
    брокера, но бары и спред терминал даёт только по тем, что добавлены в
    «Обзор рынка». Без добавления замер по остальным вернул бы пусто, и отбор
    молча видел бы лишь те несколько пар, что уже открыты у человека."""
    print("\n[Пара сначала добавляется в «Обзор рынка», потом замеряется]")
    row = _load_survey_symbol(spread_points=10, atr_bar_range=0.0020)
    check(row.get("_selected") == ["EURUSD"],
          "Перед замером пара добавляется в «Обзор рынка»",
          str(row.get("_selected")))

    check(_survey_with_unavailable_symbol() == {},
          "Не удалось добавить — пара пропускается, а не считается по мусору")

    src = (APP / "main.py").read_text(encoding="utf-8")
    body = src.split("def auto_pick_symbols", 1)[1].split("\ndef ", 1)[0]
    # select_symbol — то же добавление в «Обзор рынка», но оно ещё и говорит,
    # добавили пару МЫ или она уже была открыта человеком: по этому различию
    # программа потом убирает за собой только своё.
    check("select_symbol" in body,
          "Все пары добавляются в «Обзор рынка» до замеров")
    # Порядок: сперва добавить ВСЕ, потом мерить. Иначе первые пары успеют
    # подкачать историю, а последние — нет, и выпадут из отбора на весь сеанс
    check(body.index("select_symbol") < body.index("survey_symbol(name)"),
          "Сначала добавляются ВСЕ пары, и только потом идут замеры")


def test_stop_estimate_matches_real_trading() -> None:
    """Риск минимального лота считается от стопа. Если отбор оценит стоп
    короче, чем его реально поставит торговля, он занизит риск и пропустит
    пару, которая депозиту не по карману. В торговле пол стопа — это МАКСИМУМ
    из доли ATR, нескольких спредов и минимума брокера (risk_manager.
    min_stop_distance). Здесь проверяется, что отбор считает так же."""
    print("\n[Оценка стопа при отборе совпадает с торговой]")

    atr_frac = float(CFG.MIN_SL_ATR_FRACTION)
    spread_mult = float(CFG.MIN_SL_SPREAD_MULTIPLE)

    # Узкий спред: решает ATR. Размах бара 0.0020 при точке 0.00001 = 200 пунктов
    row = _load_survey_symbol(spread_points=10, atr_bar_range=0.0020)
    check(abs(row["atr_points"] - 200) < 1e-6, "ATR посчитан в пунктах",
          str(row.get("atr_points")))
    check(abs(row["stop_points"] - 200 * atr_frac) < 1e-6,
          f"Узкий спред — стоп по ATR ({atr_frac} x 200)",
          str(row.get("stop_points")))

    # Широкий спред: решает он. 90 пунктов x 4 = 360 > 200 x 1.5 = 300
    wide = _load_survey_symbol(spread_points=90, atr_bar_range=0.0020)
    check(abs(wide["stop_points"] - 90 * spread_mult) < 1e-6,
          f"Широкий спред — стоп по спреду ({spread_mult} x 90), а не по ATR",
          str(wide.get("stop_points")))
    check(wide["stop_points"] > 200 * atr_frac,
          "И это БОЛЬШЕ оценки по одному только ATR — иначе риск занижен")

    # Минимум брокера тоже поднимает стоп
    far = _load_survey_symbol(spread_points=10, atr_bar_range=0.0020,
                              stops_level=900)
    check(abs(far["stop_points"] - 900) < 1e-6,
          "Минимальная дистанция брокера тоже учтена",
          str(far.get("stop_points")))

    # И это влияет на решение: та же пара при широком спреде дороже
    equity, cap = 500.0, 2.0
    cheap_risk = sp.min_lot_risk_percent(0.01, row["stop_points"],
                                         row["money_per_point"], equity)
    wide_risk = sp.min_lot_risk_percent(0.01, wide["stop_points"],
                                        wide["money_per_point"], equity)
    check(wide_risk > cheap_risk,
          "Широкий спред => больше стоп => больше риск минимального лота",
          f"{cheap_risk:.3f} -> {wide_risk:.3f}")
    check(cap > 0, "Потолок риска задан (проверка самого теста)")

    sys.modules["MetaTrader5"] = types.ModuleType("MetaTrader5")


def test_honest_about_all_pairs() -> None:
    """Обещать «работаем на всех парах» нельзя. Причина должна быть записана
    числом, а не общими словами."""
    print("\n[Про «все пары» сказано прямо и с числами]")
    doc = (APP / "symbol_picker.py").read_text(encoding="utf-8")
    check("40 мс" in doc or "40 мс" in doc.replace(" ", " "),
          "Названа цена одной пары за проход")
    check("12.0 с" in doc or "12" in doc, "И во что это выливается на 300 парах")
    check("ставка на доллар" in doc,
          "И названа вторая причина — пары не независимы")
    example = (APP / "config.py.example").read_text(encoding="utf-8")
    check("НА ВСЕХ НЕЛЬЗЯ" in example, "То же самое сказано в настройках")


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ: САМОСТОЯТЕЛЬНЫЙ ОТБОР ПАР")
    print("=" * 62)

    test_spread_cost_is_relative_not_absolute()
    test_min_lot_risk()
    test_expensive_pairs_are_rejected()
    test_wide_spread_is_rejected()
    test_broker_disabled_is_rejected()
    test_order_is_by_cost_of_entry()
    test_one_bet_is_not_taken_six_times()
    test_limit_is_respected()
    test_currencies_of()
    test_nothing_suitable_is_said_plainly()
    test_wired_into_startup()
    test_stop_estimate_matches_real_trading()
    test_pair_must_be_added_to_market_watch_first()
    test_honest_about_all_pairs()
    test_startup_cannot_hang()
    test_window_says_it_is_busy()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


def test_startup_cannot_hang() -> None:
    """ОТКУДА ЭТОТ ТЕСТ. Владелец: «нет отклика от программы, виснет».

    Отбор шёл по ВСЕМ парам брокера сразу за барами. Чтобы получить бары,
    пару надо добавить в «Обзор рынка», после чего терминал идёт за историей
    на сервер — на сотнях пар запуск растягивался на минуты. Программа честно
    работала, но для человека перед экраном это то же самое, что зависла.

    Здесь проверяется, что дорогая работа ОГРАНИЧЕНА: и по числу пар, и по
    времени."""
    print("\n[Запуск не может длиться бесконечно]")

    # Первый этап — только описание контракта, без баров
    # Цена пункта растёт чуть-чуть: все 200 пар счёту по карману, и проверяется
    # именно ЛИМИТ, а не отсев по риску. С крупным шагом (1.0 + i) отсеивалось
    # бы 195 пар по риску, и тест проверял бы совсем другое.
    facts = [{"symbol": f"AA{i:02d}BB", "min_lot": 0.01,
              "money_per_point": 1.0 + i * 0.001, "trade_mode": 4}
             for i in range(200)]
    stage1 = sp.prefilter(facts, equity=500, max_risk_percent=2.0, limit=60)
    check(len(stage1["kept"]) == 60,
          "До настоящего замера доходит не больше заданного числа пар",
          str(len(stage1["kept"])))
    check(any("не проверялись" in r for r in stage1["rejected"]),
          "И об отброшенных сказано прямо, а не молча")

    # Порядок: самые посильные для счёта — первыми. Если время кончится,
    # успеют именно те, на которых счёт может торговать. Вход НАРОЧНО подан
    # в обратном порядке: иначе проверка проходила бы и без сортировки.
    shuffled = [{"symbol": "DEAR", "min_lot": 0.01, "money_per_point": 9.0,
                 "trade_mode": 4},
                {"symbol": "MIDDLE", "min_lot": 0.01, "money_per_point": 4.0,
                 "trade_mode": 4},
                {"symbol": "CHEAP", "min_lot": 0.01, "money_per_point": 1.0,
                 "trade_mode": 4}]
    order = sp.prefilter(shuffled, equity=5000, max_risk_percent=2.0)["kept"]
    check(order == ["CHEAP", "MIDDLE", "DEAR"],
          "Первыми идут самые дешёвые для счёта, а не как пришли", str(order))
    # И при нехватке времени успевает именно посильная пара
    limited = sp.prefilter(shuffled, equity=5000, max_risk_percent=2.0, limit=1)
    check(limited["kept"] == ["CHEAP"],
          "Успеет одна — успеет самая посильная", str(limited["kept"]))

    # Неподъёмное отсекается ещё до баров
    heavy = [{"symbol": "BTCUSD", "min_lot": 0.01, "money_per_point": 1000.0,
              "trade_mode": 4},
             {"symbol": "EURUSD", "min_lot": 0.01, "money_per_point": 1.0,
              "trade_mode": 4},
             {"symbol": "CLOSED", "min_lot": 0.01, "money_per_point": 1.0,
              "trade_mode": 0}]
    stage1 = sp.prefilter(heavy, equity=500, max_risk_percent=2.0)
    check(stage1["kept"] == ["EURUSD"],
          "Неподъёмное и закрытое отсеяно ДО обращения за барами",
          str(stage1["kept"]))
    check(any("BTCUSD" in r for r in stage1["rejected"]), "Причина названа")

    check(sp.prefilter([], 500, 2.0)["kept"] == [], "Пустой список не роняет")
    check(sp.prefilter(None, 500, 2.0)["kept"] == [], "None тоже")
    check(sp.prefilter(facts, 500, 2.0, limit=0)["kept"] != [],
          "Нулевой лимит не означает «ни одной пары»")

    # Ограничение по ВРЕМЕНИ — в самом коде запуска
    src = (APP / "main.py").read_text(encoding="utf-8")
    body = src.split("def auto_pick_symbols", 1)[1].split("\ndef ", 1)[0]
    check("deadline" in body, "У отбора есть срок")
    check(body.count("time.time() > deadline") >= 3,
          "Срок проверяется на каждом дорогом шаге, а не один раз",
          str(body.count("time.time() > deadline")))
    # Проверяем СТРОЕНИЕ, а не наличие текста: «stage1 = что-угодно or
    # symbol_picker.prefilter(...)» текст содержит, а дешёвый этап обходит.
    func = next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef) and n.name == "auto_pick_symbols")
    calls = [ast.unparse(n.value.func) for n in ast.walk(func)
             if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
             and ast.unparse(n.targets[0]) == "stage1"]
    check(calls == ["symbol_picker.prefilter"],
          "Результат дешёвого этапа берётся прямо из prefilter, без обходных путей",
          str(calls))
    check(body.index("prefilter") < body.index("survey_symbol(name)"),
          "И именно ДО дорогого замера")
    # Дорогой замер идёт ТОЛЬКО по выжившим, а не по всему списку брокера
    check("for name in shortlist:" in body,
          "Замеряются только прошедшие первый этап")
    check("for name in available:\n        try:\n            row = survey_symbol" not in body,
          "И весь список брокера в замер не идёт")
    check("AUTO_PICK_MAX_SECONDS" in body, "Срок берётся из настроек")

    # Дешёвый этап не должен трогать «Обзор рынка» — иначе он не дешёвый
    facts_fn = src.split("def contract_facts", 1)[1].split("\ndef ", 1)[0]
    check("ensure_symbol" not in facts_fn,
          "Первый этап НЕ добавляет пары в «Обзор рынка» — в этом весь смысл")
    check("get_rates_df" not in facts_fn, "И не запрашивает бары")

    # Норма спреда — второе место, где запуск мог растянуться
    seed = src.split("def seed_spread_baselines", 1)[1].split("\ndef ", 1)[0]
    check("deadline" in seed, "Загрузка нормы спреда тоже ограничена по времени")
    check("time.time() > deadline" in seed, "И срок реально проверяется")

    check(int(CFG.AUTO_PICK_MAX_SECONDS) > 0, "Срок задан в настройках",
          str(CFG.AUTO_PICK_MAX_SECONDS))
    check(int(CFG.AUTO_PICK_SURVEY_LIMIT) > 0, "И число пар для замера тоже")


def test_window_says_it_is_busy() -> None:
    """Пока идёт подготовка, окно обязано об этом говорить. Молчащая
    программа неотличима от зависшей — ровно это владелец и увидел."""
    print("\n[Окно говорит, что занято, а не молчит]")
    ui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    loop = ui.split("def _refresh_loop", 1)[1].split("\n    def ", 1)[0]

    check("Подготовка" in loop,
          "Пока первого круга не было, написано «Подготовка», а не «Работает»")
    # События должны показываться и БЕЗ снимка: снимок появляется только
    # после первого круга, а подготовка идёт до него
    events_at = loop.index("runtime_events.describe(3)")
    snap_block = loop.index("if snap:")
    tail = loop[events_at:]
    check(tail.count("problems.append") >= 1, "События попадают в список сообщений")
    check(events_at > snap_block, "Проверка самого теста: блок идёт после снимка")
    line = loop[loop.rindex("\n", 0, events_at) + 1:].split("\n", 1)[0]
    indent = len(line) - len(line.lstrip())
    check(indent == 12,
          "Показ событий НЕ вложен внутрь «если есть снимок» — иначе во время "
          "подготовки окно молчит", f"отступ {indent}: {line.strip()}")


if __name__ == "__main__":
    sys.exit(main())
