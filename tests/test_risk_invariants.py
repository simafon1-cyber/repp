#!/usr/bin/env python3
"""Проверка ПРАВИЛ расчёта объёма на тысячах случайных случаев.

ЧЕМ ЭТО ОТЛИЧАЕТСЯ ОТ ОСТАЛЬНЫХ ТЕСТОВ

Все прочие тесты в проекте — на примерах: «вот такой вход, вот такой ответ».
Примеры пишет человек, и он пишет те случаи, о которых подумал. Ошибка живёт
ровно там, где не подумал.

Здесь наоборот: задаются ПРАВИЛА, которые обязаны выполняться ВСЕГДА, а входы
берутся случайные — тысячи наборов депозита, цены пункта, минимального лота,
шага лота, ширины стопа и серии убытков. Если правило нарушится хоть на одном
наборе, тест печатает ИМЕННО ЭТОТ набор, и его можно разбирать руками.

ПОЧЕМУ ИМЕННО РАСЧЁТ ОБЪЁМА. Это то место, где ошибка стоит денег напрямую.
Всё остальное можно поправить следующей сборкой, а лишний ноль в объёме
списывается со счёта сразу.

ПРАВИЛА, КОТОРЫЕ ЗДЕСЬ ЗАКРЕПЛЕНЫ:
  1. Объём никогда не выходит за границы брокера (мин/макс) и всегда кратен
     шагу лота — иначе брокер просто откажет, и сделки не будет.
  2. Объём НИКОГДА не растёт после серии убытков. Это отличие честной системы
     от мартингейла, и проверять его надо не примером, а правилом.
  3. Риск в деньгах не превышает заданный процент — КРОМЕ случая, когда даже
     минимальный лот брокера дороже. Тогда программа обязана либо отказаться
     от сделки, либо громко предупредить: тихо рискнуть больше нельзя.
  4. Отказ от сделки — это ровно ноль, а не отрицательное число и не «почти
     ноль»: отрицательный объём брокер понял бы как сделку в другую сторону.

Запуск:  python3 tests/test_risk_invariants.py
"""

from __future__ import annotations

import math
import random
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
        print(f"  СБОЙ {name}" + (f"\n       {detail}" if detail else ""))


cfg = types.ModuleType("config")
cfg.__file__ = str(APP / "config.py.example")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg

mt5c = types.ModuleType("mt5_connector")
sys.modules["mt5_connector"] = mt5c
ctrl = types.ModuleType("control")
ctrl.control = types.SimpleNamespace(
    get_lot_override=lambda symbol: 0,
    get_risk_profile=lambda: None,
    get_trading_mode=lambda: "",
    is_symbol_enabled=lambda symbol: True,
    is_paused=lambda: False,
)
sys.modules["control"] = ctrl
sys.modules["MetaTrader5"] = types.ModuleType("MetaTrader5")

import logging               # noqa: E402
logging.disable(logging.WARNING)   # предупреждения о риске здесь ожидаемы

import risk_manager as rm      # noqa: E402
from state import SymbolState  # noqa: E402


def случай(rnd) -> dict:
    """Один случайный, но ПРАВДОПОДОБНЫЙ набор условий.

    Числа взяты из живого диапазона: депозит от 50 до 100 000, шаг лота 0.01
    или 0.1, цена пункта от копеек (валюта) до сотен (индексы и криптовалюта)."""
    tick_size = rnd.choice([0.00001, 0.0001, 0.001, 0.01, 0.1, 1.0])
    # Шаг лота НЕ БОЛЬШЕ минимального, и минимальный кратен шагу. У брокеров
    # это всегда так: шаг 1.0 при минимуме 0.01 означал бы, что минимальный
    # объём брокера сам себе не подходит.
    #
    # Первый же прогон этого теста выдал именно такую пару чисел и «нашёл
    # ошибку» — но ошибка была в моём генераторе, а не в программе. Ограничение
    # оставлено с этим пояснением, чтобы через месяц никто (включая меня) не
    # решил, что это подгонка под удобный результат. Отдельный случай
    # «брокер всё-таки отдал несуразицу» проверяется ниже своим тестом.
    lot_step = rnd.choice([0.01, 0.1, 1.0])
    min_lot = lot_step * rnd.choice([1, 1, 1, 2, 10])
    return {
        "equity": rnd.choice([50, 65, 100, 434, 500, 1000, 5000, 25000, 100000]),
        "min_lot": min_lot,
        "max_lot": rnd.choice([10.0, 50.0, 500.0]),
        "lot_step": lot_step,
        "tick_size": tick_size,
        "tick_value": rnd.choice([0.01, 0.1, 1.0, 10.0, 100.0]),
        "sl_dist": rnd.choice([0.0001, 0.001, 0.01, 0.5, 5.0, 50.0]),
        "losses": rnd.randint(0, 10),
    }


def посчитать(case: dict) -> tuple:
    """Вернуть (объём, состояние). Подставляем брокера, всё остальное живое."""
    info = types.SimpleNamespace(
        volume_min=case["min_lot"], volume_max=case["max_lot"],
        volume_step=case["lot_step"], trade_tick_size=case["tick_size"],
        trade_tick_value=case["tick_value"], point=case["tick_size"],
        trade_stops_level=0)
    rm._symbol_info = lambda symbol, _i=info: _i
    st = SymbolState(symbol="TEST")
    st.consecutive_losses = case["losses"]
    lot = rm.calc_lot("TEST", case["sl_dist"], case["equity"], st)
    return lot, st


ПРОГОНОВ = 4000


def test_broker_limits_always_respected() -> None:
    """Объём вне границ брокера — это отказ в исполнении. Сделки просто не
    будет, а человек будет гадать, почему бот молчит."""
    print(f"\n[Границы брокера соблюдаются — {ПРОГОНОВ} случайных наборов]")
    rnd = random.Random(20260813)
    плохие = []
    вне_шага = []
    for _ in range(ПРОГОНОВ):
        case = случай(rnd)
        lot, _ = посчитать(case)
        if lot == 0.0:
            continue                       # отказ от сделки — проверяется ниже
        if lot < case["min_lot"] - 1e-9 or lot > case["max_lot"] + 1e-9:
            плохие.append((case, lot))
        # Кратность шагу: считаем в целых шагах, чтобы не ловить дробную пыль
        шагов = lot / case["lot_step"]
        if abs(шагов - round(шагов)) > 1e-6:
            вне_шага.append((case, lot))

    check(not плохие, "Объём всегда внутри границ брокера",
          f"пример: {плохие[0][0]} -> {плохие[0][1]}" if плохие else "")
    check(not вне_шага, "И всегда кратен шагу лота",
          f"пример: {вне_шага[0][0]} -> {вне_шага[0][1]}" if вне_шага else "")


def test_volume_never_grows_after_losses() -> None:
    """ГЛАВНОЕ ПРАВИЛО ЧЕСТНОЙ СИСТЕМЫ. Рост объёма после убытка — это
    мартингейл: он красиво отыгрывает мелкие просадки и уносит счёт целиком
    на первой длинной серии. Проверять это примером мало: правило должно
    держаться на любых числах."""
    print(f"\n[Объём не растёт после убытков — {ПРОГОНОВ} наборов]")
    rnd = random.Random(777)
    нарушения = []
    сравнений = 0
    for _ in range(ПРОГОНОВ):
        case = случай(rnd)
        case["losses"] = 0
        без_убытков, _ = посчитать(case)
        for losses in (1, 3, 5, 10):
            case_l = dict(case, losses=losses)
            после, _ = посчитать(case_l)
            if после == 0.0 or без_убытков == 0.0:
                continue
            сравнений += 1
            if после > без_убытков + 1e-9:
                нарушения.append((case_l, без_убытков, после))

    check(сравнений > 1000, "Сравнений набралось достаточно", str(сравнений))
    check(not нарушения,
          "После серии убытков объём НИКОГДА не больше исходного",
          (f"пример: {нарушения[0][0]}: было {нарушения[0][1]}, "
           f"стало {нарушения[0][2]}") if нарушения else "")


def test_loss_multiplier_shrinks_and_is_capped() -> None:
    """ДВА РАЗНЫХ ПРАВИЛА, и проверять их надо порознь.

    Тест «объём не растёт после убытков» держался НЕ на формуле, а на
    ограничении min(1.0, ...) сверху. Мутационная проверка это показала:
    поломка формулы на «1.0 + 0.7 * ratio» тест не роняла — ограничение
    гасило её. То есть я проверял правило, которое выполняется по другой
    причине, и не заметил бы, если бы формула однажды перевернулась.

    Поэтому здесь порознь:
      * множитель РЕАЛЬНО УБЫВАЕТ с ростом серии убытков (это формула);
      * и НИКОГДА не больше единицы, даже при бессмысленном счётчике (это
        ограничение — вторая линия обороны).
    """
    print("\n[Множитель риска убывает и не превышает единицу]")
    st = SymbolState(symbol="TEST")

    значения = []
    for losses in range(0, int(cfg.MAX_CONSECUTIVE_LOSSES) + 1):
        st.consecutive_losses = losses
        значения.append(rm.loss_streak_risk_multiplier(st))

    check(all(a >= b - 1e-12 for a, b in zip(значения, значения[1:])),
          "С каждой новой потерей множитель не растёт", str(значения))
    check(значения[0] > значения[-1] + 1e-9,
          "И к концу серии он ЗАМЕТНО меньше, чем в начале — иначе формула "
          "ничего не делает", f"{значения[0]} -> {значения[-1]}")
    check(all(v <= 1.0 + 1e-12 for v in значения),
          "Больше единицы не бывает никогда", str(значения))
    check(all(v >= cfg.MIN_LOSS_STREAK_RISK_MULTIPLIER - 1e-12 for v in значения),
          "И ниже заданного дна тоже", str(значения))

    # Счётчик убытков теоретически может оказаться отрицательным (сбой,
    # ручная правка состояния). Формула тогда даёт больше единицы — то есть
    # УВЕЛИЧИЛА бы объём. Ограничение сверху обязано это погасить.
    for мусор in (-1, -5, -1000):
        st.consecutive_losses = мусор
        v = rm.loss_streak_risk_multiplier(st)
        check(v <= 1.0 + 1e-12,
              f"Отрицательный счётчик убытков ({мусор}) не увеличивает объём",
              str(v))


def test_risk_is_capped_or_loudly_explained() -> None:
    """Риск выше настроенного допустим ровно в одном случае: минимальный лот
    брокера сам по себе дороже. Ниже него опуститься нельзя — это решение
    брокера, а не наше. Но тогда программа ОБЯЗАНА сказать об этом: тихо
    рискнуть больше, чем человек разрешил, нельзя."""
    print(f"\n[Превышение риска либо запрещено, либо названо вслух — {ПРОГОНОВ} наборов]")
    rnd = random.Random(31337)
    молча = []
    profile = rm.get_profile()
    for _ in range(ПРОГОНОВ):
        case = случай(rnd)
        lot, st = посчитать(case)
        if lot <= 0:
            continue
        loss_per_lot = (case["sl_dist"] / case["tick_size"]) * case["tick_value"]
        риск = lot * loss_per_lot
        разрешено = case["equity"] * profile["risk_percent"] / 100.0
        if риск > разрешено * 1.01:
            # Допустимо, только если объём упёрся в минимальный лот брокера
            упёрлись = abs(lot - case["min_lot"]) < 1e-9
            if not упёрлись or not st.last_risk_warning:
                молча.append((case, lot, риск, разрешено, st.last_risk_warning))

    check(not молча,
          "Риск выше настроенного бывает только на минимальном лоте И всегда "
          "с предупреждением",
          (f"пример: {молча[0][0]}\n       объём {молча[0][1]}, риск "
           f"{молча[0][2]:.2f} при разрешённых {молча[0][3]:.2f}, "
           f"предупреждение: {молча[0][4]!r}") if молча else "")


def test_refusal_is_exactly_zero() -> None:
    """Отказ от сделки — это ровно ноль. Отрицательный объём брокер понял бы
    как сделку в ДРУГУЮ сторону, а «почти ноль» он отвергнет, и человек
    увидит молчание вместо объяснения."""
    print(f"\n[Отказ — это ровно ноль — {ПРОГОНОВ} наборов]")
    rnd = random.Random(4242)
    странные = []
    отказов = 0
    saved = getattr(cfg, "ALLOW_MIN_LOT_OVER_RISK", True)
    try:
        cfg.ALLOW_MIN_LOT_OVER_RISK = False      # режим строгого отказа
        for _ in range(ПРОГОНОВ):
            case = случай(rnd)
            lot, st = посчитать(case)
            if lot < 0:
                странные.append((case, lot))
            if lot == 0.0:
                отказов += 1
                if not st.last_risk_warning:
                    странные.append((case, "отказ без объяснения"))
            elif 0 < lot < case["min_lot"] - 1e-9:
                странные.append((case, lot))
    finally:
        cfg.ALLOW_MIN_LOT_OVER_RISK = saved

    check(отказов > 0, "Отказы в выборке встретились", str(отказов))
    check(not странные, "Объём либо ноль с объяснением, либо не меньше минимального",
          f"пример: {странные[0]}" if странные else "")


def test_inconsistent_broker_limits_do_not_break_us() -> None:
    """А что если брокер всё-таки отдаст несуразицу — минимум 0.01 при шаге
    1.0? Такого быть не должно, но «не должно» и «не бывает» — разные вещи.
    Программа обязана вернуть минимальный лот: он по определению исполним,
    что бы брокер ни писал в шаге."""
    print("\n[Несуразные границы брокера не ломают расчёт]")
    info = types.SimpleNamespace(
        volume_min=0.01, volume_max=50.0, volume_step=1.0,
        trade_tick_size=0.001, trade_tick_value=1.0, point=0.001,
        trade_stops_level=0)
    rm._symbol_info = lambda symbol, _i=info: _i
    lot = rm.calc_lot("TEST", 0.001, 50, SymbolState(symbol="TEST"))
    check(lot >= 0, "Объём неотрицательный", str(lot))
    check(lot == 0.0 or lot >= info.volume_min - 1e-9,
          "И либо отказ, либо не меньше минимального у брокера", str(lot))


def test_nothing_crashes_on_broker_nonsense() -> None:
    """Брокер иногда отдаёт ноль или мусор в описании инструмента. Расчёт
    объёма обязан это пережить: падение здесь останавливает торговлю целиком."""
    print("\n[Мусор от брокера не роняет расчёт]")
    беды = []
    for tick_size, tick_value, sl in (
            (0, 1.0, 0.001), (0.0001, 0, 0.001), (0.0001, 1.0, 0),
            (0.0001, 1.0, -1), (0, 0, 0), (-0.1, -1.0, -1)):
        case = {"equity": 500, "min_lot": 0.01, "max_lot": 10.0,
                "lot_step": 0.01, "tick_size": tick_size or 0.0001,
                "tick_value": tick_value, "sl_dist": sl, "losses": 0}
        info = types.SimpleNamespace(
            volume_min=0.01, volume_max=10.0, volume_step=0.01,
            trade_tick_size=tick_size, trade_tick_value=tick_value,
            point=tick_size, trade_stops_level=0)
        rm._symbol_info = lambda symbol, _i=info: _i
        try:
            lot = rm.calc_lot("TEST", sl, 500, SymbolState(symbol="TEST"))
            if lot is None or (isinstance(lot, float) and math.isnan(lot)) or lot < 0:
                беды.append((tick_size, tick_value, sl, lot))
        except Exception as e:      # noqa: BLE001
            беды.append((tick_size, tick_value, sl, f"{type(e).__name__}: {e}"))
    check(not беды, "Ни один мусорный набор не роняет расчёт",
          str(беды[:2]) if беды else "")

    # И нулевой депозит тоже: он бывает при обрыве связи с терминалом
    info = types.SimpleNamespace(
        volume_min=0.01, volume_max=10.0, volume_step=0.01,
        trade_tick_size=0.0001, trade_tick_value=1.0, point=0.0001,
        trade_stops_level=0)
    rm._symbol_info = lambda symbol, _i=info: _i
    try:
        lot = rm.calc_lot("TEST", 0.001, 0, SymbolState(symbol="TEST"))
        check(lot >= 0, "Нулевой депозит не даёт отрицательный объём", str(lot))
    except Exception as e:      # noqa: BLE001
        check(False, "Нулевой депозит не роняет расчёт", f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    print("=" * 62)
    print("ПРАВИЛА РАСЧЁТА ОБЪЁМА НА СЛУЧАЙНЫХ ДАННЫХ")
    print("=" * 62)
    test_broker_limits_always_respected()
    test_volume_never_grows_after_losses()
    test_loss_multiplier_shrinks_and_is_capped()
    test_risk_is_capped_or_loudly_explained()
    test_refusal_is_exactly_zero()
    test_inconsistent_broker_limits_do_not_break_us()
    test_nothing_crashes_on_broker_nonsense()
    print()
    print("=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    sys.exit(1 if failed else 0)
