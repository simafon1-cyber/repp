#!/usr/bin/env python3
"""Тесты PHASE 1: закрытая свеча, точный расчёт денег, защита после перезапуска.

Все четыре проверки здесь — про ДЕФЕКТЫ, а не про торговые идеи. Ни один порог,
вес или настройка стратегии не затрагиваются.

  1. ЗАКРЫТАЯ СВЕЧА. Программа брала бары через copy_rates_from_pos(..., 0, n),
     где позиция 0 — ТЕКУЩАЯ, ещё не закрытая свеча. Вся торговая логика при
     этом написана в расчёте на последнюю ЗАКРЫТУЮ (так же, как в советнике:
     SIGNAL_SHIFT 1). Из-за сдвига решение принималось по свече возрастом
     несколько секунд, у которой максимум и минимум почти совпадают: жёсткое
     подтверждение по свече не проходило, откат+пробой не начислялся, фильтр
     истощения не срабатывал никогда, ATR был занижен.

  2. ТОЧНЫЙ РАСЧЁТ ДЕНЕГ. Риск считался приближением (расстояние/размер тика
     * цена тика). На золоте и кроссах оно расходится с действительностью.
     Советник давно спрашивает точную сумму у терминала (OrderCalcProfit),
     Python-версия — нет, хотя вызов доступен.

  3. СВОБОДНЫЕ СРЕДСТВА. Маржа не проверялась вовсе: брокер молча отклонял
     ордер, и снаружи это выглядело как «сделки нет без причины».

  4. ЗАЩИТА ПОСЛЕ ПЕРЕЗАПУСКА. Пик счёта жил в памяти процесса, и перезапуск
     обнулял его — вместе с запретом по просадке.

Запуск:  python3 tests/test_closed_bar_and_risk.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import types
from datetime import datetime, timedelta
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

# Заглушка MetaTrader: настоящий терминал в проверках недоступен, а поведение
# программы обязано быть предсказуемым и без него.
fake_mt5 = types.ModuleType("MetaTrader5")
fake_mt5.ORDER_TYPE_BUY = 0
fake_mt5.ORDER_TYPE_SELL = 1
fake_mt5.TIMEFRAME_M1 = 1
fake_mt5.TIMEFRAME_M5 = 5
fake_mt5.TIMEFRAME_M15 = 15
fake_mt5.TIMEFRAME_M30 = 30
fake_mt5.TIMEFRAME_H1 = 60
fake_mt5.TIMEFRAME_H4 = 240
fake_mt5.TIMEFRAME_D1 = 1440
fake_mt5.ORDER_FILLING_IOC = 1
fake_mt5.ORDER_FILLING_FOK = 2
fake_mt5.TRADE_RETCODE_REQUOTE = 10004
fake_mt5.TRADE_RETCODE_PRICE_CHANGED = 10020
fake_mt5.TRADE_RETCODE_PRICE_OFF = 10021
fake_mt5.TRADE_RETCODE_DONE = 10009
sys.modules["MetaTrader5"] = fake_mt5

import mt5_connector as mt5c    # noqa: E402
import risk_manager as rm       # noqa: E402
import risk_state               # noqa: E402
from state import AccountState  # noqa: E402


# =====================================================================
# 1. ЗАКРЫТАЯ СВЕЧА
# =====================================================================
def test_forming_bar_is_dropped() -> None:
    """Последняя строка обязана быть последней ЗАКРЫТОЙ свечой."""
    print("\n[Формирующаяся свеча не попадает в расчёт]")

    запрошено = {}

    def fake_copy(symbol, tf, start, count):
        # Ровно то, что отдаёт MetaTrader: позиция 0 — ТЕКУЩАЯ свеча, она
        # идёт ПОСЛЕДНЕЙ в возвращаемом массиве (от старых к новым).
        запрошено["count"] = count
        запрошено["start"] = start
        база = 1700000000
        строки = []
        for i in range(count):
            строки.append({
                "time": база + i * 300,
                "open": 1.0 + i * 0.001, "high": 1.002 + i * 0.001,
                "low": 0.998 + i * 0.001, "close": 1.001 + i * 0.001,
                "tick_volume": 100 + i,
            })
        # Последняя — «живая»: только что открылась, диапазон почти нулевой
        строки[-1].update(high=строки[-1]["open"] + 0.00001,
                          low=строки[-1]["open"] - 0.00001,
                          close=строки[-1]["open"], tick_volume=1)
        return строки

    saved = fake_mt5.copy_rates_from_pos if hasattr(fake_mt5, "copy_rates_from_pos") else None
    fake_mt5.copy_rates_from_pos = fake_copy
    try:
        df = mt5c.get_rates_df("EURUSD", "M5", count=50)
        check(df is not None, "Свечи получены")
        check(len(df) == 50, "Строк ровно столько, сколько просили",
              str(len(df)))
        check(запрошено["count"] == 51,
              "У терминала запрошено НА ОДНУ БОЛЬШЕ — лишняя отбрасывается",
              str(запрошено.get("count")))
        check(запрошено["start"] == 0, "Начало по-прежнему с позиции 0")

        # ГЛАВНОЕ: последняя строка — не та «живая» свеча.
        последняя = df.iloc[-1]
        диапазон = float(последняя["high"] - последняя["low"])
        check(диапазон > 0.001,
              "Последняя свеча ПОЛНОЦЕННАЯ, а не только что открывшаяся",
              f"диапазон {диапазон}")
        check(int(последняя["tick_volume"]) > 1,
              "И объём у неё настоящий, а не один тик",
              str(последняя["tick_volume"]))

        # Время последней строки на один период меньше времени живой свечи.
        живая = fake_copy("EURUSD", 5, 0, 51)[-1]
        живое_время = datetime.utcfromtimestamp(живая["time"])
        check(последняя["time"].to_pydatetime() < живое_время,
              "Время последней строки СТАРШЕ времени текущего бара",
              f"{последняя['time']} vs {живое_время}")
        check((живое_время - последняя["time"].to_pydatetime())
              == timedelta(minutes=5),
              "Ровно на один бар M5, а не на два и не на ноль")

        # Одна-единственная свеча не должна превращаться в пустоту.
        fake_mt5.copy_rates_from_pos = lambda s, t, st, c: [
            {"time": 1700000000, "open": 1.0, "high": 1.1, "low": 0.9,
             "close": 1.05, "tick_volume": 10}]
        one = mt5c.get_rates_df("EURUSD", "M5", count=1)
        check(one is not None and len(one) == 1,
              "Единственная свеча отдаётся, а не теряется")

        fake_mt5.copy_rates_from_pos = lambda s, t, st, c: None
        check(mt5c.get_rates_df("EURUSD", "M5", count=10) is None,
              "Нет данных — честный None, а не пустая таблица")
    finally:
        if saved is not None:
            fake_mt5.copy_rates_from_pos = saved
        else:
            del fake_mt5.copy_rates_from_pos


def test_signal_bar_matches_the_advisor() -> None:
    """Договорённость должна совпадать с советником, иначе Python и MQL5 —
    две разные стратегии под одним именем."""
    print("\n[Договорённость о сигнальной свече совпадает с советником]")
    mql = (ROOT / "ai_scalper_pro" / "Config.mqh").read_text(encoding="utf-8")
    check("#define SIGNAL_SHIFT 1" in mql,
          "В советнике сигнальная свеча — последняя ЗАКРЫТАЯ")
    check("#define PULLBACK_SHIFT 2" in mql,
          "А свеча отката — на бар раньше сигнальной")

    src = (APP / "mt5_connector.py").read_text(encoding="utf-8")
    добыча = src.split("def get_rates_df", 1)[1].split("\ndef ", 1)[0]
    код = [l for l in добыча.splitlines() if not l.strip().startswith("#")]
    строки = "\n".join(код)
    check("count + 1" in строки, "Python запрашивает на свечу больше")
    check("iloc[:-1]" in строки, "И отбрасывает последнюю (формирующуюся)")
    check("SIGNAL_SHIFT" in добыча,
          "Причина записана рядом с кодом, со ссылкой на советник")


# =====================================================================
# 2. ТОЧНЫЙ РАСЧЁТ ДЕНЕГ
# =====================================================================
class FakeInfo:
    def __init__(self, tick_value=1.0, tick_size=0.00001,
                 volume_min=0.01, volume_max=100.0, volume_step=0.01):
        self.trade_tick_value = tick_value
        self.trade_tick_size = tick_size
        self.volume_min = volume_min
        self.volume_max = volume_max
        self.volume_step = volume_step


class FakeTick:
    def __init__(self, bid=1900.0, ask=1900.5):
        self.bid = bid
        self.ask = ask


def test_money_asked_from_terminal() -> None:
    """Точную сумму спрашиваем у терминала, приближение — только в запасе."""
    print("\n[Деньги считает терминал, а не приближённая формула]")

    вызовы = []

    def fake_calc_profit(order_type, symbol, lot, price_open, price_close):
        вызовы.append((order_type, symbol, lot, price_open, price_close))
        return 123.45

    fake_mt5.order_calc_profit = fake_calc_profit
    fake_mt5.symbol_info_tick = lambda s: FakeTick()
    try:
        money = rm.money_per_distance("XAUUSD", 1, 4.5, 1.0)
        check(money == 123.45, "Взята сумма от терминала, а не формула",
              str(money))
        check(len(вызовы) == 1, "Терминал спрошен ровно один раз")
        тип, символ, лот, откр, закр = вызовы[0]
        check(тип == fake_mt5.ORDER_TYPE_BUY,
              "Спрошено как про покупку — как в MQL5 MoneyPerDistance(1,...)")
        check(лот == 1.0, "Про ОДИН лот: дальше объём считается делением")
        check(откр == 1900.5, "От цены ASK (цена покупки)")
        check(abs(закр - 1905.0) < 1e-9,
              "Целевая цена выше на ширину стопа — как в советнике", str(закр))

        # Отрицательный ответ терминала берётся по модулю: MQL5 делает так же.
        fake_mt5.order_calc_profit = lambda *a: -77.0
        check(rm.money_per_distance("XAUUSD", 1, 4.5, 1.0) == 77.0,
              "Знак не важен — берётся величина")

        # Терминал не ответил -> запасной путь через цену тика.
        fake_mt5.order_calc_profit = lambda *a: None
        info = FakeInfo(tick_value=1.0, tick_size=0.01)
        fallback = rm.money_risk_per_lot("XAUUSD", 4.5, info)
        check(abs(fallback - 450.0) < 1e-9,
              "Запасной путь считает по цене тика, как раньше", str(fallback))

        # Терминал сломался -> тоже запасной путь, а не падение.
        def взрыв(*a):
            raise RuntimeError("терминал не отвечает")
        fake_mt5.order_calc_profit = взрыв
        check(rm.money_per_distance("XAUUSD", 1, 4.5, 1.0) == 0.0,
              "Ошибка терминала не роняет расчёт")
        check(abs(rm.money_risk_per_lot("XAUUSD", 4.5, info) - 450.0) < 1e-9,
              "И уводит на запасной путь")

        # Мусор на входе.
        fake_mt5.order_calc_profit = fake_calc_profit
        check(rm.money_per_distance("XAUUSD", 1, 0, 1.0) == 0.0,
              "Нулевое расстояние — ноль, а не выдумка")
        check(rm.money_per_distance("XAUUSD", 1, 4.5, 0) == 0.0,
              "Нулевой объём — ноль")
    finally:
        fake_mt5.order_calc_profit = lambda *a: None


def test_lot_uses_exact_money() -> None:
    """Объём обязан считаться от ТОЧНОЙ суммы, иначе весь смысл теряется."""
    print("\n[Объём считается от точной суммы]")

    from state import SymbolState
    st = SymbolState(symbol="XAUUSD")

    fake_mt5.symbol_info = lambda s: FakeInfo(tick_value=1.0, tick_size=0.01)
    fake_mt5.symbol_info_tick = lambda s: FakeTick()
    CFG.USE_RISK_BASED_LOT = True

    try:
        # Терминал говорит: один лот на этом стопе теряет 100 денег.
        fake_mt5.order_calc_profit = lambda *a: -100.0
        lot_exact = rm.calc_lot("XAUUSD", 4.5, equity=10000.0, sym_state=st)

        # Приближение по цене тика дало бы 450 на лот — то есть в 4.5 раза
        # больше, и объём вышел бы в 4.5 раза меньше.
        fake_mt5.order_calc_profit = lambda *a: None
        lot_fallback = rm.calc_lot("XAUUSD", 4.5, equity=10000.0, sym_state=st)

        check(lot_exact > lot_fallback,
              "Точный расчёт и приближение дают РАЗНЫЙ объём — ради этого всё",
              f"точно {lot_exact}, приближённо {lot_fallback}")
        check(lot_exact > 0 and lot_fallback > 0, "Оба варианта дают сделку")

        # И проверим саму величину: риск профиля от 10000 при 100 на лот.
        profile = rm.get_profile()
        ожидаемо = 10000.0 * profile["risk_percent"] / 100.0 / 100.0
        ожидаемо = int(ожидаемо / 0.01) * 0.01
        check(abs(lot_exact - ожидаемо) < 1e-9,
              "Объём = риск в деньгах / потеря на лот, округлённый вниз",
              f"{lot_exact} против {ожидаемо}")
    finally:
        fake_mt5.order_calc_profit = lambda *a: None


def test_open_risk_uses_same_math() -> None:
    """Риск открытых позиций и риск новой сделки обязаны считаться ОДИНАКОВО:
    их складывают и сравнивают с общим потолком."""
    print("\n[Открытый риск считается тем же способом]")
    src = (APP / "risk_manager.py").read_text(encoding="utf-8")
    кусок = src.split("def get_open_risk_percent", 1)[1].split("\ndef ", 1)[0]
    check("money_risk_per_lot" in кусок,
          "Открытый риск считает та же функция")
    check("trade_tick_value" not in кусок,
          "Своей копии приближённой формулы там больше нет")

    main_src = (APP / "main.py").read_text(encoding="utf-8")
    кусок2 = main_src.split("new_trade_risk_pct = 0.0", 1)[1][:400]
    check("rm.money_risk_per_lot" in кусок2,
          "И риск новой сделки — та же функция")


# =====================================================================
# 3. СВОБОДНЫЕ СРЕДСТВА
# =====================================================================
class FakeAccount:
    def __init__(self, margin_free=1000.0, equity=1000.0, login=5054028014):
        self.margin_free = margin_free
        self.equity = equity
        self.login = login


def test_margin_check_blocks_only_impossible_orders() -> None:
    """Проверка маржи отсекает ровно то, что брокер и так не примет."""
    print("\n[Свободные средства]")
    fake_mt5.symbol_info_tick = lambda s: FakeTick()

    fake_mt5.order_calc_margin = lambda *a: 500.0
    reason = rm.margin_block_reason("XAUUSD", 1, 0.10, account=FakeAccount(1000.0))
    check(reason == "", "Средств хватает — не мешаем", reason)

    fake_mt5.order_calc_margin = lambda *a: 1500.0
    reason = rm.margin_block_reason("XAUUSD", 1, 0.10, account=FakeAccount(1000.0))
    check(reason != "", "Средств не хватает — сделка отменяется")
    check("1500" in reason and "1000" in reason,
          "И названы оба числа: сколько нужно и сколько есть", reason)

    # ПРИ ЛЮБОМ СОМНЕНИИ ПРОПУСКАЕМ. Иначе неудачный вспомогательный запрос
    # останавливал бы торговлю целиком.
    def взрыв(*a):
        raise RuntimeError("терминал не отвечает")
    fake_mt5.order_calc_margin = взрыв
    check(rm.margin_block_reason("XAUUSD", 1, 0.10, account=FakeAccount(1.0)) == "",
          "Ошибка терминала НЕ блокирует торговлю")

    fake_mt5.order_calc_margin = lambda *a: None
    check(rm.margin_block_reason("XAUUSD", 1, 0.10, account=FakeAccount(1.0)) == "",
          "Нет ответа — не блокируем")

    fake_mt5.order_calc_margin = lambda *a: 1500.0
    check(rm.margin_block_reason("XAUUSD", 1, 0.10, account=FakeAccount(0.0)) == "",
          "Нет данных о свободных средствах — не блокируем")
    check(rm.margin_block_reason("XAUUSD", 1, 0.0, account=FakeAccount(1.0)) == "",
          "Нулевой объём проверять нечего")

    main_src = (APP / "main.py").read_text(encoding="utf-8")
    check("rm.margin_block_reason" in main_src,
          "Проверка подключена к торговому циклу")
    место = main_src.index("rm.margin_block_reason")
    открытие = main_src.index("tm.execute_market_order")
    check(место < открытие, "И стоит ДО отправки ордера")


# =====================================================================
# 4. ЗАЩИТА ПОСЛЕ ПЕРЕЗАПУСКА
# =====================================================================
def test_drawdown_guard_survives_restart() -> None:
    """Пик счёта обязан пережить перезапуск — иначе лимит просадки снимается
    сам собой, и владелец видит это как «поработал пару часов и всё»."""
    print("\n[Пик счёта переживает перезапуск]")
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "risk_state.json")
        login = 5054028014

        # Счёт вырос до 500, потом просел до 450.
        было = AccountState(day_start_equity=500.0, peak_equity=500.0,
                            last_trade_day=datetime.now(), trades_today=3)
        check(risk_state.save(было, login, path) is True, "Состояние записано")

        # ПЕРЕЗАПУСК: новое состояние заводится от ТЕКУЩЕГО эквити 450.
        стало = AccountState(day_start_equity=450.0, peak_equity=450.0,
                             last_trade_day=datetime.now())
        note = risk_state.load(стало, login, path)
        check(стало.peak_equity == 500.0,
              "Пик восстановлен, а не обнулён текущим эквити",
              str(стало.peak_equity))
        check(стало.day_start_equity == 500.0,
              "И начало дня тоже — иначе дневной лимит считался бы заново",
              str(стало.day_start_equity))
        check(стало.trades_today == 3, "И число сделок за день")
        check("500" in note, "Что восстановлено — сказано словами", note)

        # И теперь запрет по просадке ДЕЙСТВИТЕЛЬНО работает после перезапуска.
        CFG.USE_MAX_DRAWDOWN_LIMIT = True
        profile = rm.get_profile()
        limit = float(profile.get("max_drawdown_pct", 0) or 0)
        if limit > 0:
            просевшее = 500.0 * (1 - limit / 100.0) - 1.0
            check(rm.max_drawdown_hit(стало, просевшее) is True,
                  f"Просадка ниже {limit}% от пика 500 — вход закрыт")
            # А без восстановления пик был бы 450, и та же цифра прошла бы.
            наивное = AccountState(peak_equity=450.0)
            check(rm.max_drawdown_hit(наивное, просевшее) is False,
                  "Без восстановления та же просадка НЕ ловилась бы — "
                  "ровно эта дыра и чинится")

        # Пик никогда не опускается: сохранённое меньше текущего — берём текущее.
        выше = AccountState(peak_equity=900.0)
        risk_state.load(выше, login, path)
        check(выше.peak_equity == 900.0, "Меньший сохранённый пик не понижает текущий")

        # Вчерашнее начало дня сегодня не применяется.
        вчерашнее = {"version": 1, "accounts": {str(login): {
            "peak_equity": 500.0, "day_start_equity": 500.0,
            "day": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
            "trades_today": 9}}}
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(вчерашнее, f)
        новый_день = AccountState(day_start_equity=450.0, peak_equity=450.0)
        risk_state.load(новый_день, login, path)
        check(новый_день.peak_equity == 500.0, "Пик — переносится через сутки")
        check(новый_день.day_start_equity == 450.0,
              "А вчерашнее начало дня — НЕ переносится",
              str(новый_день.day_start_equity))
        check(новый_день.trades_today == 0, "И вчерашние сделки не считаются")


def test_accounts_do_not_mix() -> None:
    """У каждого счёта свой пик. Чужой пик закрыл бы торговлю на ровном месте."""
    print("\n[Счета не смешиваются]")
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "risk_state.json")
        risk_state.save(AccountState(peak_equity=1000.0), 111, path)
        risk_state.save(AccountState(peak_equity=200.0), 222, path)

        первый = AccountState(peak_equity=0.0)
        risk_state.load(первый, 111, path)
        check(первый.peak_equity == 1000.0, "Первый счёт получил свой пик")

        второй = AccountState(peak_equity=0.0)
        risk_state.load(второй, 222, path)
        check(второй.peak_equity == 200.0, "Второй — свой, а не чужой",
              str(второй.peak_equity))
        check(risk_state.save(AccountState(peak_equity=1.0), 0, path) is False,
              "Без номера счёта состояние не хранится")


def test_broken_file_never_stops_the_program() -> None:
    """Нечитаемый вспомогательный файл не повод не торговать."""
    print("\n[Битый файл не мешает работать]")
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "risk_state.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("это не json")
        st = AccountState(peak_equity=300.0)
        check(risk_state.load(st, 111, path) == "", "Мусор читается как «нечего восстанавливать»")
        check(st.peak_equity == 300.0, "И текущее состояние не испорчено")
        check(risk_state.save(st, 111, path) is True, "Файл перезаписывается заново")

        check(risk_state.load(AccountState(), 111, os.path.join(folder, "нет")) == "",
              "Отсутствие файла — не ошибка")


def test_file_is_written_only_when_changed() -> None:
    """Главный цикл крутится каждые несколько секунд — писать файл каждый раз
    значит изнашивать диск без пользы."""
    print("\n[Файл пишется только при изменении]")
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "risk_state.json")
        st = AccountState(peak_equity=100.0, day_start_equity=100.0,
                          last_trade_day=datetime.now())
        check(risk_state.save_if_changed(st, 777, path) is True, "Первый раз — пишем")
        check(risk_state.save_if_changed(st, 777, path) is False,
              "Ничего не изменилось — не пишем")
        st.peak_equity = 150.0
        check(risk_state.save_if_changed(st, 777, path) is True,
              "Пик вырос — пишем")

        # Файл защищён от перезаписи обновлением программы.
        up_src = (APP / "updater.py").read_text(encoding="utf-8")
        protected = up_src.split("PROTECTED = {", 1)[1].split("}", 1)[0]
        check('"risk_state.json"' in protected,
              "Обновление программы не затирает состояние защиты")


# =====================================================================
# 5. LIVE_TRADING
# =====================================================================
def test_live_trading_off_by_default() -> None:
    """Свежепоставленная программа не должна начинать торговать сама."""
    print("\n[Реальная торговля включается осознанно]")
    example = (APP / "config.py.example").read_text(encoding="utf-8")
    строка = [l for l in example.splitlines() if l.startswith("LIVE_TRADING")]
    check(строка and "False" in строка[0],
          "В образце настроек реальная торговля выключена",
          строка[0] if строка else "строки нет")

    # И это должно быть включаемо БЕЗ блокнота: иначе владелец, не
    # программист, останется с молчащей программой и без способа её завести.
    ui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    params = ui.split("ADVANCED_PARAMS = [", 1)[1].split("\n]", 1)[0]
    check('"LIVE_TRADING"' in params,
          "Переключатель есть в настройках программы")
    import param_help
    check(param_help.has_help("LIVE_TRADING"),
          "И у него есть объяснение по кнопке «?»")

    # Удалённо включить торговлю по-прежнему нельзя (это правило старше).
    remote = (APP / "remote_settings.py").read_text(encoding="utf-8")
    check("включить торговлю удалённо нельзя" in remote,
          "Удалённо включить торговлю по-прежнему нельзя")


if __name__ == "__main__":
    print("=" * 62)
    print("ТЕСТЫ: ЗАКРЫТАЯ СВЕЧА, ТОЧНЫЕ ДЕНЬГИ, ЗАЩИТА ПОСЛЕ ПЕРЕЗАПУСКА")
    print("=" * 62)
    test_forming_bar_is_dropped()
    test_signal_bar_matches_the_advisor()
    test_money_asked_from_terminal()
    test_lot_uses_exact_money()
    test_open_risk_uses_same_math()
    test_margin_check_blocks_only_impossible_orders()
    test_drawdown_guard_survives_restart()
    test_accounts_do_not_mix()
    test_broken_file_never_stops_the_program()
    test_file_is_written_only_when_changed()
    test_live_trading_off_by_default()
    print()
    print("=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    sys.exit(1 if failed else 0)
