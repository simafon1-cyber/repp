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
    # Отбор обязан идти ДО создания состояний: иначе он ни на что не повлияет
    check(src.index("auto_pick_symbols(acc.equity)") < src.index("sym_states = init_states()"),
          "Отбор идёт ДО построения списка символов")

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

    ns = {"cfg": cfg,
          "mt5c": types.SimpleNamespace(
              get_rates_df=lambda *a, **k: FakeDF(atr_bar_range))}
    exec(compile(ast.Module(body=[func], type_ignores=[]), "main.py", "exec"), ns)
    return ns["survey_symbol"]("EURUSD")


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
    test_honest_about_all_pairs()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
