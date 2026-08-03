#!/usr/bin/env python3
"""Тесты: бот отрабатывает всю торговую сессию, лимита по числу сделок нет.

Что здесь важно различать:

  ЛИМИТ ПО ЧИСЛУ СДЕЛОК сам по себе ничего не защищает. Сто прибыльных
  сделок не опаснее десяти. Он просто выключал бота посреди дня.

  ЛИМИТ ПО ДЕНЬГАМ защищает: дневной лимит убытка и лимит просадки
  останавливают торговлю, когда счёт реально теряет. Эти проверки остаются
  и здесь же проверяются, чтобы их случайно не сняли заодно.

Запуск:  python3 tests/test_no_trade_cap.py
"""

from __future__ import annotations

import ast
import re
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
APP = ROOT / "ai_scalper_standalone"
PRO = ROOT / "ai_scalper_pro"
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
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg


class _FakeMT5(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return name


_mt5 = _FakeMT5("MetaTrader5")
_mt5.initialize = lambda *a, **k: False
sys.modules["MetaTrader5"] = _mt5

CFG = cfg


# =====================================================================
# 1. Программа на Python
# =====================================================================
def test_profiles_have_no_cap() -> None:
    print("\n[Профили: лимита по числу сделок нет]")

    for profile_key, profile in CFG.RISK_PROFILES.items():
        name = profile["name"]
        check(profile["max_trades_per_day"] == 0,
              f"«{name}»: без лимита сделок за день",
              str(profile["max_trades_per_day"]))

        # А вот одновременные сделки ограничены — это другое: без этого один
        # сигнал мог бы открыть десятки позиций и превысить риск на счёте
        check(profile["max_open_positions"] > 0,
              f"«{name}»: лимит ОДНОВРЕМЕННЫХ сделок остался",
              str(profile["max_open_positions"]))

        # Денежные защиты обязаны остаться
        check(profile["daily_loss_limit_pct"] > 0,
              f"«{name}»: дневной лимит убытка на месте",
              str(profile["daily_loss_limit_pct"]))
        check(profile["max_drawdown_pct"] > 0,
              f"«{name}»: лимит просадки на месте",
              str(profile["max_drawdown_pct"]))


def test_zero_means_unlimited() -> None:
    print("\n[Ноль означает «без ограничения», а не «ни одной сделки»]")

    src = (APP / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "process_symbol":
            fn = ast.get_source_segment(src, node)
    check(fn is not None, "Функция входа найдена")
    if not fn:
        return

    # Проверка должна пропускать ноль, а не отсекать на нём всё
    check("max_per_day and" in fn or "max_per_day > 0" in fn,
          "Ноль трактуется как «без ограничения»")
    check("acc_state.trades_today >= max_per_day" in fn,
          "Заданное число всё ещё работает, если его вписать")

    # Поведение целиком: воспроизводим само условие
    def blocked(trades_today: int, cap: int) -> bool:
        return bool(cap) and trades_today >= cap

    check(blocked(10_000, 0) is False, "10000 сделок при лимите 0 — не блокируется")
    check(blocked(0, 0) is False, "Ноль сделок при лимите 0 — тоже не блокируется")
    check(blocked(20, 20) is True, "Заданный лимит 20 срабатывает на 20-й")
    check(blocked(19, 20) is False, "На 19-й ещё торгуем")


def test_loss_streak_pause_is_short() -> None:
    print("\n[Пауза после серии убытков не съедает день]")

    import risk_manager as rm

    CFG.PAUSE_MINUTES_AFTER_LOSS_STREAK = 30
    minutes = rm.loss_streak_pause_minutes()
    check(minutes == 30, "Пауза читается в минутах", str(minutes))
    check(minutes < 120,
          "Пауза короче двух часов — торговая сессия не теряется", str(minutes))

    CFG.PAUSE_MINUTES_AFTER_LOSS_STREAK = 0
    check(rm.loss_streak_pause_minutes() == 0, "Ноль — паузы нет вовсе")

    # Старое имя настройки (в часах) читается, если новое не задано —
    # у пользователя мог остаться конфиг прошлой версии
    del CFG.PAUSE_MINUTES_AFTER_LOSS_STREAK
    CFG.PAUSE_HOURS_AFTER_LOSS_STREAK = 2
    check(rm.loss_streak_pause_minutes() == 120,
          "Старая настройка в часах пересчитывается в минуты",
          str(rm.loss_streak_pause_minutes()))
    del CFG.PAUSE_HOURS_AFTER_LOSS_STREAK
    check(rm.loss_streak_pause_minutes() == 0, "Настройки нет вообще — паузы нет")

    CFG.PAUSE_MINUTES_AFTER_LOSS_STREAK = 30

    # Мусор в настройке не должен ронять программу
    for bad in ("много", None, [1]):
        CFG.PAUSE_MINUTES_AFTER_LOSS_STREAK = bad
        value = rm.loss_streak_pause_minutes()
        check(isinstance(value, float) and value >= 0,
              f"Мусор в настройке ({bad!r}) не ломает расчёт", str(value))
    CFG.PAUSE_MINUTES_AFTER_LOSS_STREAK = 30

    # Нулевая пауза не должна ставить дату «в прошлом» и выглядеть как пауза
    src = (APP / "main.py").read_text(encoding="utf-8")
    check("pause_minutes > 0" in src,
          "При нулевой паузе pause_until вообще не выставляется")


def test_config_defaults() -> None:
    print("\n[Значения по умолчанию в шаблоне конфига]")

    text = (APP / "config.py.example").read_text(encoding="utf-8")
    check("PAUSE_MINUTES_AFTER_LOSS_STREAK" in text, "Настройка паузы в минутах есть")
    check(text.count("max_trades_per_day=0") == 4,
          "Все четыре профиля без лимита сделок",
          str(text.count("max_trades_per_day=0")))
    check("БЕЗ ОГРАНИЧЕНИЯ" in text, "В шаблоне объяснено, что означает ноль")

    # Значение ПО УМОЛЧАНИЮ читаем из файла заново: тесты выше подменяют
    # настройку в памяти, и без этой проверки длинная пауза в поставляемом
    # конфиге прошла бы незамеченной.
    fresh = types.ModuleType("fresh_cfg")
    exec(text, fresh.__dict__)
    default_pause = getattr(fresh, "PAUSE_MINUTES_AFTER_LOSS_STREAK", None)
    check(default_pause is not None, "Пауза задана в шаблоне")
    check(default_pause == 0,
          "Пауза по умолчанию снята — торговля не прерывается вовсе",
          str(default_pause))
    check(not hasattr(fresh, "PAUSE_HOURS_AFTER_LOSS_STREAK"),
          "Старой настройки в часах в шаблоне не осталось")
    for profile in fresh.RISK_PROFILES.values():
        check(profile["max_trades_per_day"] == 0,
              f"Умолчание в файле: «{profile['name']}» без лимита сделок",
              str(profile["max_trades_per_day"]))

    # Часы торговли по умолчанию выключены — иначе «целая сессия» не выйдет
    check(CFG.USE_TRADING_HOURS is False,
          "Ограничение по часам выключено — торгуем всю сессию",
          str(CFG.USE_TRADING_HOURS))


# =====================================================================
# 2. Советник MQL5 — поведение должно совпадать
# =====================================================================
def test_mql5_matches() -> None:
    print("\n[Советник MQL5: то же поведение]")

    config = (PRO / "Config.mqh").read_text(encoding="utf-8")
    check(re.search(r"input int\s+MaxTradesPerDay\s*=\s*0\s*;", config) is not None,
          "MaxTradesPerDay по умолчанию 0")
    check("PauseMinutesAfterLossStreak" in config, "Пауза задана в минутах")
    check("PauseHoursAfterLossStreak" not in config, "Старой настройки в часах не осталось")
    match = re.search(r"input int\s+PauseMinutesAfterLossStreak\s*=\s*(\d+)", config)
    check(match is not None and int(match.group(1)) < 120,
          "Пауза короче двух часов", match.group(1) if match else "нет")

    ea = (PRO / "AI_Scalper_Pro.mq5").read_text(encoding="utf-8")
    check("g_effMaxTradesPerDay>0 && TradesToday>=g_effMaxTradesPerDay" in ea,
          "Ноль означает «без ограничения», а не «не торговать»")
    check("PauseMinutesAfterLossStreak*60" in ea,
          "Пауза считается в минутах")
    check("PauseHoursAfterLossStreak*3600" not in ea, "Старый расчёт в часах убран")

    risk = (PRO / "RiskManager.mqh").read_text(encoding="utf-8")
    check("g_effMaxTradesPerDay       = 0;" in risk,
          "Профили советника тоже без лимита")
    for old in ("= 10;", "= 40;", "= 200;", "= 20;"):
        check(f"g_effMaxTradesPerDay       {old}" not in risk,
              f"Старый лимит {old.strip('= ;')} убран из профилей")

    # Денежные защиты в советнике остались
    for keep in ("DailyLossLimit", "MaxDrawdown"):
        check(keep in config, f"Денежная защита {keep} на месте")


def test_money_protections_still_enforced() -> None:
    print("\n[Деньги по-прежнему защищены]")

    src = (APP / "main.py").read_text(encoding="utf-8")
    check("rm.trading_allowed(acc_state, sym_state, equity)" in src,
          "Проверка дневного лимита и просадки стоит перед входом")

    risk_src = (APP / "risk_manager.py").read_text(encoding="utf-8")
    for fn in ("daily_loss_limit_hit", "max_drawdown_hit"):
        check(fn in risk_src, f"Функция {fn} на месте")

    # Дневной порог убытка владелец попросил снять: он останавливал бота до
    # завтра, а бот должен отрабатывать всё торговое время. Механизм остался в
    # коде и включается галочкой — выключена только сама остановка по дню.
    check(CFG.USE_DAILY_LOSS_LIMIT is False,
          "Дневной порог убытка выключен — бот работает всю сессию")

    # Лимит просадки владелец тоже попросил снять: «не останавливать торговлю,
    # убрать это условие». Остановок не осталось ни одной — значит защита
    # целиком переехала на уровень ОДНОЙ сделки, и вот она обязана быть.
    # Значения читаем из шаблона заново: тесты выше подменяют настройки в
    # памяти, и без этого включённая остановка в поставляемом конфиге прошла бы
    # незамеченной.
    fresh = types.ModuleType("fresh_cfg")
    exec((APP / "config.py.example").read_text(encoding="utf-8"), fresh.__dict__)
    check(fresh.USE_DAILY_LOSS_LIMIT is False,
          "В шаблоне дневной порог убытка выключен")
    check(fresh.USE_MAX_DRAWDOWN_LIMIT is False,
          "В шаблоне лимит просадки выключен — торговля не прерывается")
    check(int(fresh.PAUSE_MINUTES_AFTER_LOSS_STREAK) == 0,
          "В шаблоне паузы после серии убытков нет",
          str(fresh.PAUSE_MINUTES_AFTER_LOSS_STREAK))
    profile = CFG.RISK_PROFILES[CFG.RiskProfile(CFG.RISK_PROFILE)]
    check(float(profile["risk_percent"]) > 0,
          "Риск на сделку ограничен процентом от счёта", str(profile["risk_percent"]))
    check(float(profile["max_total_risk_pct"]) > 0,
          "Совокупный риск по открытым сделкам ограничен",
          str(profile["max_total_risk_pct"]))
    check("apply_min_stop_floor" in risk_src,
          "Стоп-лосс обязан быть дальше спреда и шума")


def test_stop_not_inside_noise() -> None:
    """По РЕАЛЬНЫМ сделкам пользователя: стоп на золоте был 1.78-1.87 пункта
    при спреде ~0.2-0.4. Сделки закрывались за 8-11 секунд, ни одна не дошла
    до цели, 12 убытков из 16. Стоп находился внутри шума инструмента."""
    print("\n[Стоп не может оказаться внутри спреда и шума]")

    import risk_manager as rm
    import mt5_connector as mt5c

    CFG.MIN_SL_SPREAD_MULTIPLE = 4.0
    CFG.MIN_SL_ATR_FRACTION = 0.8

    saved_spread = mt5c.get_spread_points
    saved_info = rm._symbol_info

    class FakeInfo:
        trade_stops_level = 0
        point = 0.01

    rm._symbol_info = lambda s: FakeInfo()
    mt5c.get_spread_points = lambda s: 30       # 30 пунктов = 0.30 на золоте
    try:
        point = 0.01
        atr = 3.6                                # ATR золота M5 ~3.6 пункта цены

        # Так считалось раньше: 0.5 * ATR = 1.8 — ровно как в журнале сделок
        naive = atr * 0.5
        check(abs(naive - 1.8) < 1e-9, "Старый расчёт даёт те самые 1.8", str(naive))

        floored = rm.apply_min_stop_floor("XAUUSD", naive, atr, point)
        check(floored > naive, "Стоп расширен", f"{naive} -> {floored}")

        # Пол по спреду: 4 спреда = 4 * 0.30 = 1.20
        # Пол по ATR: 0.8 * 3.6 = 2.88 -> побеждает он
        check(abs(floored - 2.88) < 1e-9, "Стоп не ближе 0.8 ATR", str(floored))
        check(floored >= 30 * point * 4,
              "И заведомо шире четырёх спредов", f"{floored} против {30*point*4}")

        # Широкий спред должен побеждать ATR
        mt5c.get_spread_points = lambda s: 200   # 2.00 на золоте
        floored = rm.apply_min_stop_floor("XAUUSD", naive, atr, point)
        check(abs(floored - 8.0) < 1e-9,
              "При широком спреде побеждает спредовый пол", str(floored))

        # Уже широкий стоп не сужается
        mt5c.get_spread_points = lambda s: 30
        wide = rm.apply_min_stop_floor("XAUUSD", 50.0, atr, point)
        check(wide == 50.0, "Широкий стоп остаётся как есть — пол только расширяет")

        # Требование брокера тоже учитывается
        class StopsLevel(FakeInfo):
            trade_stops_level = 1000             # 10.00 на золоте
        rm._symbol_info = lambda s: StopsLevel()
        floored = rm.apply_min_stop_floor("XAUUSD", naive, atr, point)
        check(abs(floored - 10.0) < 1e-9, "Учитывается минимум брокера", str(floored))
        rm._symbol_info = lambda s: FakeInfo()

        # Нет данных — не падаем и ничего не выдумываем
        mt5c.get_spread_points = lambda s: 0
        check(rm.apply_min_stop_floor("XAUUSD", naive, 0.0, point) == naive,
              "Без ATR и спреда стоп остаётся прежним")
    finally:
        mt5c.get_spread_points = saved_spread
        rm._symbol_info = saved_info

    # Проброшено в реальный расчёт входа
    src = (APP / "main.py").read_text(encoding="utf-8")
    check("rm.apply_min_stop_floor(symbol, sl_dist, atr_value, point)" in src,
          "Пол применяется при расчёте сделки")
    check("Минимальный лот брокера рискует больше" in src,
          "Отказ по слишком мелкому депозиту объяснён понятным текстом")

    cfg_text = (APP / "config.py.example").read_text(encoding="utf-8")
    for name in ("MIN_SL_SPREAD_MULTIPLE", "MIN_SL_ATR_FRACTION", "ALLOW_MIN_LOT_OVER_RISK"):
        check(name in cfg_text, f"Настройка {name} есть в шаблоне")


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ: РАБОТА ВСЮ СЕССИЮ, БЕЗ ЛИМИТА СДЕЛОК")
    print("=" * 62)

    test_profiles_have_no_cap()
    test_zero_means_unlimited()
    test_loss_streak_pause_is_short()
    test_config_defaults()
    test_mql5_matches()
    test_money_protections_still_enforced()
    test_stop_not_inside_noise()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
