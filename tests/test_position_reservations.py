#!/usr/bin/env python3
"""Тесты P0-1: лимиты внутри ОДНОГО обхода символов.

ПОЧЕМУ ЭТИ ТЕСТЫ ПОЯВИЛИСЬ.

Главный цикл берёт список открытых позиций один раз за проход и отдаёт один
и тот же список всем инструментам. Но инструменты внутри этого прохода
ОТКРЫВАЮТ сделки, а список от этого не меняется:

    EURUSD открылся и занял слот
      -> следом GBPUSD видит СТАРЫЙ список
      -> и тоже проходит лимиты

Так обходились три защиты сразу: общее число сделок, общий риск и лимит
ставки на одну валюту. То есть ровно то, что бережёт депозит.

ЧЕМ ЭТИ ТЕСТЫ ОТЛИЧАЮТСЯ ОТ ОСТАЛЬНЫХ. Все прежние проверки main.py читают
ИСХОДНИК: ищут строки, считают порядок вызовов. Такая проверка не поймала бы
этот дефект никогда — каждая строка была на месте, неверна была только
последовательность во времени.

Здесь process_symbol ВЫЗЫВАЕТСЯ по-настоящему, дважды, на поддельном
терминале. И у каждого теста есть контрольная половина: с пустой книгой
резервов (то есть на старом поведении) вторая пара НЕ отклоняется. Если
починку откатить, тест упадёт.

Запуск:  python3 tests/test_position_reservations.py
"""

from __future__ import annotations

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


# =====================================================================
# ПОДДЕЛЬНЫЙ ТЕРМИНАЛ
# =====================================================================
cfg = types.ModuleType("config")
cfg.__file__ = str(APP / "config.py.example")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
cfg.LIVE_TRADING = False          # ни один ордер наружу не уходит
sys.modules["config"] = cfg


class _Информация:
    """Свойства инструмента — те же, что отдаёт терминал."""
    def __init__(self, point=0.00001):
        self.point = point
        self.digits = 5
        self.volume_min = 0.01
        self.volume_max = 100.0
        self.volume_step = 0.01
        self.trade_tick_value = 1.0
        self.trade_tick_size = point
        self.trade_contract_size = 100000.0
        self.trade_stops_level = 0
        self.spread = 2
        self.visible = True
        self.trade_mode = 4


class _Тик:
    def __init__(self, цена=1.10000, время=1_700_000_000):
        self.bid = цена
        self.ask = цена + 0.00002
        self.last = цена
        self.time = время


class _Счёт:
    def __init__(self, equity=1000.0):
        self.equity = equity
        self.balance = equity
        self.margin_free = equity
        self.login = 5054028014
        self.currency = "USD"
        self.leverage = 100


class _Позиция:
    """Открытая позиция. Полей ровно столько, сколько читает код."""
    def __init__(self, ticket, symbol, direction=1, lot=0.01,
                 price_open=1.10000, sl=1.09900, magic=0):
        self.ticket = ticket
        self.symbol = symbol
        self.type = 0 if direction == 1 else 1
        self.volume = lot
        self.price_open = price_open
        self.sl = sl
        self.tp = 0.0
        self.magic = magic
        self.profit = 0.0
        self.time = 1_700_000_000


def _свечи(n=400, старт=1.10000):
    """Ровный ряд свечей: индикаторам хватает, сигналу — нет.

    Так и задумано: нам нужно, чтобы process_symbol ДОШЁЛ до проверки
    лимитов, а не чтобы он открыл сделку. Сделку мы бронируем сами — как
    это делает главный цикл после подтверждения ордера.

    Тип возврата не случайный: терминал отдаёт структурированный массив
    numpy с именованными полями, и mt5_connector обращается к ним по имени.
    Список кортежей выглядел бы похоже и молча ломался бы."""
    import numpy as np
    тип = np.dtype([("time", "<i8"), ("open", "<f8"), ("high", "<f8"),
                    ("low", "<f8"), ("close", "<f8"), ("tick_volume", "<u8"),
                    ("spread", "<i4"), ("real_volume", "<u8")])
    ряд = []
    for i in range(n):
        цена = старт + (i % 7) * 0.00001
        ряд.append((1_700_000_000 + i * 300, цена, цена + 0.00003,
                    цена - 0.00003, цена + 0.00001, 100, 2, 0))
    return np.array(ряд, dtype=тип)


fake = types.ModuleType("MetaTrader5")
for _и, _з in (("TIMEFRAME_M1", 1), ("TIMEFRAME_M5", 5), ("TIMEFRAME_M15", 15),
               ("TIMEFRAME_M30", 30), ("TIMEFRAME_H1", 60), ("TIMEFRAME_H4", 240),
               ("TIMEFRAME_D1", 1440),
               ("ORDER_TYPE_BUY", 0), ("ORDER_TYPE_SELL", 1),
               ("POSITION_TYPE_BUY", 0), ("POSITION_TYPE_SELL", 1),
               ("DEAL_TYPE_BUY", 0), ("DEAL_TYPE_SELL", 1),
               ("ORDER_FILLING_IOC", 1), ("ORDER_FILLING_FOK", 2),
               ("TRADE_RETCODE_DONE", 10009), ("TRADE_RETCODE_REQUOTE", 10004),
               ("TRADE_RETCODE_PRICE_CHANGED", 10020),
               ("TRADE_RETCODE_PRICE_OFF", 10021),
               ("SYMBOL_TRADE_MODE_FULL", 4),
               ("DEAL_REASON_CLIENT", 0), ("DEAL_REASON_EXPERT", 3),
               ("DEAL_REASON_SL", 4), ("DEAL_REASON_TP", 5)):
    setattr(fake, _и, _з)

fake.symbol_info = lambda s: _Информация()
fake.symbol_info_tick = lambda s: _Тик()
fake.account_info = lambda: _Счёт()
fake.positions_get = lambda **k: []
fake.copy_rates_from_pos = lambda s, tf, a, b: _свечи()
fake.order_calc_margin = lambda *a, **k: 1.0
fake.order_calc_profit = lambda *a, **k: 1.0
fake.symbol_select = lambda *a, **k: True
fake.last_error = lambda: (0, "ok")
fake.terminal_info = lambda: None
fake.history_deals_get = lambda *a, **k: []
fake.initialize = lambda *a, **k: True
fake.shutdown = lambda: None
sys.modules["MetaTrader5"] = fake

import main as bot            # noqa: E402
import reservations           # noqa: E402
import risk_manager as rm     # noqa: E402
from state import AccountState, SymbolState   # noqa: E402


# =====================================================================
def свежий_счёт(equity=1000.0):
    сч = AccountState()
    сч.day_start_equity = equity
    сч.peak_equity = equity
    return сч


def прогнать(symbol, acc_state, positions, equity=1000.0):
    """Один вызов process_symbol. Возвращает причину отказа."""
    st = SymbolState(symbol=symbol)
    bot.process_symbol(symbol, st, acc_state, equity,
                       acc_info=_Счёт(equity), all_positions=positions)
    return st.last_reject_reason or ""


def доехал_до_лимитов(причина: str) -> bool:
    """Дошёл ли вызов до проверок лимитов вообще.

    Если process_symbol свернул раньше (нет данных, индикаторы не готовы),
    тест ничего не проверил и обязан это признать, а не засчитать успех."""
    ранние = ("Недостаточно данных", "Индикаторы не готовы")
    return not any(x in причина for x in ранние)


# =====================================================================
def test_harness_actually_reaches_the_limit_checks() -> None:
    """Сначала убедимся, что стенд вообще доезжает до проверок.

    Без этого все тесты ниже были бы зелёными по неверной причине: вызов
    сворачивался бы раньше, а мы считали бы это «не отклонено»."""
    print("\n[Стенд доезжает до проверок лимитов]")
    сч = свежий_счёт()
    причина = прогнать("EURUSD", сч, [])
    check(доехал_до_лимитов(причина),
          "process_symbol доходит до проверок, а не сворачивает раньше",
          f"причина: {причина!r}")


def test_second_symbol_is_blocked_by_total_position_cap() -> None:
    """ПЕРВЫЙ ДЕФЕКТ. Общее число одновременных сделок.

    Первая пара открылась в этом же проходе. Вторая обязана её увидеть."""
    print("\n[Лимит общего числа сделок: вторая пара видит первую]")
    было = cfg.MAX_SIMULTANEOUS_POSITIONS
    cfg.MAX_SIMULTANEOUS_POSITIONS = 1
    try:
        # КОНТРОЛЬ: книга пуста — это старое поведение. Вторая пара проходит.
        сч = свежий_счёт()
        причина = прогнать("GBPUSD", сч, [])
        check("MAX_SIMULTANEOUS_POSITIONS" not in причина,
              "Контроль: при пустой книге лимит не срабатывает",
              f"причина: {причина!r}")

        # А теперь EURUSD открылся в этом же проходе.
        сч = свежий_счёт()
        сч.reservations.забронировать("EURUSD", 1, 0.0)
        причина = прогнать("GBPUSD", сч, [])
        check("MAX_SIMULTANEOUS_POSITIONS" in причина,
              "Вторая пара ОТКЛОНЕНА по общему числу сделок",
              f"причина: {причина!r}")
    finally:
        cfg.MAX_SIMULTANEOUS_POSITIONS = было


def test_second_symbol_is_blocked_by_total_risk() -> None:
    """ВТОРОЙ ДЕФЕКТ. Общий риск по счёту.

    Риск бронируется в ДЕНЬГАХ: следующая сделка считает свой процент от
    того же капитала, и складывать проценты от разных знаменателей нельзя."""
    print("\n[Лимит общего риска: вторая пара видит риск первой]")
    профиль = rm.get_profile()
    было = профиль["max_total_risk_pct"]
    был_порог = профиль["min_score_to_trade"]
    # Потолок намеренно ЩЕДРЫЙ, а бронь равна ему целиком. Так тест не
    # зависит от того, сколько риска берёт сама сделка на поддельном
    # инструменте: собственный риск меньше потолка (контроль проходит), а
    # «потолок + собственный риск» больше потолка при любом ненулевом
    # риске (вторая половина отклоняется). Подгонять число под стенд не
    # пришлось бы, но оно бы сломалось от любой правки поддельного тика.
    профиль["max_total_risk_pct"] = 50.0
    # Проверка общего риска стоит ПОСЛЕ отбора по силе сигнала — иначе
    # незачем считать риск сделки, которой не будет. Значит до неё надо
    # доехать: опускаем порог входа, чтобы ровные свечи стенда его прошли.
    # Это настройка ТЕСТА, а не программы: в finally она возвращается.
    профиль["min_score_to_trade"] = 0.0
    try:
        # КОНТРОЛЬ: книга пуста — риска нет, лимит молчит.
        сч = свежий_счёт()
        причина = прогнать("GBPUSD", сч, [], equity=1000.0)
        check("общий риск" not in причина.lower(),
              "Контроль: при пустой книге общий риск не превышен",
              f"причина: {причина!r}")

        # EURUSD открылся и забрал 500$ риска при счёте 1000$ — ровно
        # потолок. Любая следующая сделка с ненулевым риском его перевалит.
        сч = свежий_счёт()
        сч.reservations.забронировать("EURUSD", 1, 500.0)
        check(abs(сч.reservations.риск_процент(1000.0) - 50.0) < 1e-9,
              "Забронированный риск считается в процентах верно",
              str(сч.reservations.риск_процент(1000.0)))
        причина = прогнать("GBPUSD", сч, [], equity=1000.0)
        check("общий риск" in причина.lower(),
              "Вторая пара ОТКЛОНЕНА по общему риску",
              f"причина: {причина!r}")
    finally:
        профиль["max_total_risk_pct"] = было
        профиль["min_score_to_trade"] = был_порог


def test_second_symbol_is_blocked_by_currency_exposure() -> None:
    """ТРЕТИЙ ДЕФЕКТ. Ставка на одну валюту.

    EURUSD и GBPUSD — это в основном одна и та же ставка против доллара."""
    print("\n[Лимит валютной экспозиции: вторая пара видит первую]")
    было = cfg.MAX_POSITIONS_PER_CURRENCY
    cfg.MAX_POSITIONS_PER_CURRENCY = 1
    try:
        # КОНТРОЛЬ: книга пуста — по доллару никого нет.
        сч = свежий_счёт()
        причина = прогнать("GBPUSD", сч, [])
        check("валют" not in причина.lower() and "USD" not in причина,
              "Контроль: при пустой книге валютный лимит не срабатывает",
              f"причина: {причина!r}")

        сч = свежий_счёт()
        сч.reservations.забронировать("EURUSD", 1, 0.0)
        причина = прогнать("GBPUSD", сч, [])
        check(причина and ("валют" in причина.lower() or "USD" in причина),
              "Вторая пара ОТКЛОНЕНА по ставке на одну валюту",
              f"причина: {причина!r}")
    finally:
        cfg.MAX_POSITIONS_PER_CURRENCY = было


def test_reservations_are_cleared_each_pass() -> None:
    """Резерв живёт РОВНО один проход.

    Если бы он пережил проход, свежий снимок посчитал бы одну и ту же
    сделку дважды — и программа сама себе запретила бы торговать."""
    print("\n[Резерв живёт ровно один проход]")
    книга = reservations.Книга()
    книга.забронировать("EURUSD", 1, 5.0)
    книга.забронировать("XAUUSD", -1, 7.0)
    check(книга.сколько() == 2, "Две брони записаны")
    книга.очистить()
    check(книга.сколько() == 0, "После очистки книга пуста")
    check(книга.риск_денег() == 0.0, "И риск обнулился")

    исходник = (APP / "main.py").read_text(encoding="utf-8")
    поз_снимок = исходник.find("all_positions = mt5c.get_open_positions()")
    поз_очистка = исходник.find("acc_state.reservations.очистить()")
    check(поз_снимок != -1 and поз_очистка != -1 and поз_очистка > поз_снимок,
          "Очистка стоит сразу ПОСЛЕ получения свежего снимка")
    # Комментарии выкидываем: слово «reservations» в пояснении — не чтение
    # резервов. Проверка, которая этого не различает, ловит собственный текст.
    между = "\n".join(
        строка.split("#", 1)[0]
        for строка in исходник[поз_снимок:поз_очистка].splitlines())
    check("reservations" not in между,
          "И между ними никто резервы не читает", между.strip())


def test_reservation_is_made_only_after_the_broker_confirms() -> None:
    """Бронь ставится ТОЛЬКО на подтверждённый ордер.

    Бронь на неподтверждённую сделку означала бы, что программа сама себе
    запретила торговать из-за ордера, которого не существует."""
    print("\n[Бронь — только на подтверждённый ордер]")
    исходник = (APP / "main.py").read_text(encoding="utf-8")
    поз_ордер = исходник.find("ok = tm.execute_market_order(")
    поз_бронь = исходник.find("acc_state.reservations.забронировать(")
    check(поз_ордер != -1 and поз_бронь != -1 and поз_бронь > поз_ордер,
          "Бронь идёт после отправки ордера")
    кусок = исходник[поз_ордер:поз_бронь]
    check("if ok:" in кусок, "И только внутри ветки успеха")


def test_reservation_counts_per_symbol_too() -> None:
    """Слоты хеджа считаются по ОДНОМУ инструменту, а не по всем сразу."""
    print("\n[Счёт брони по инструменту]")
    книга = reservations.Книга()
    книга.забронировать("EURUSD", 1, 1.0)
    книга.забронировать("EURUSD", -1, 1.0)
    книга.забронировать("XAUUSD", 1, 1.0)
    check(книга.сколько() == 3, "Всего три брони")
    check(книга.сколько("EURUSD") == 2, "По EURUSD — две")
    check(книга.сколько("XAUUSD") == 1, "По золоту — одна")
    check(книга.сколько("USDJPY") == 0, "По незнакомой паре — ноль")
    check(книга.символы() == ["EURUSD", "XAUUSD"],
          "Инструменты без повторов и в порядке появления",
          str(книга.символы()))


def test_reservations_can_only_overcount_never_under() -> None:
    """Резерв ошибается только в безопасную сторону.

    Он ставится сразу после подтверждения и не спрашивает терминал заново.
    Значит недосчитать он не может — только посчитать лишнее, если сделка
    мгновенно закрылась. В худшем случае мы не откроем сделку, которую
    могли бы. Потерять на этом нельзя."""
    print("\n[Резерв ошибается только в безопасную сторону]")
    книга = reservations.Книга()
    check(книга.риск_процент(0) == 0.0,
          "При нулевом капитале возвращается ноль, а не деление на ноль")
    check(книга.риск_процент(-5) == 0.0,
          "При отрицательном — тоже ноль")
    check(книга.риск_процент("не число") == 0.0,
          "И на мусоре не падает")
    книга.забронировать("EURUSD", 1, -100.0)
    check(книга.риск_денег() == 0.0,
          "Отрицательный риск не уменьшает занятое",
          str(книга.риск_денег()))


def test_empty_book_is_false() -> None:
    """Пустая книга — это ложь, а не «объект существует»."""
    print("\n[Пустая книга читается как «резервов нет»]")
    книга = reservations.Книга()
    check(not книга, "Пустая книга ложна")
    книга.забронировать("EURUSD", 1, 0.0)
    check(bool(книга), "Непустая — истинна")


def test_symbols_are_merged_not_replaced() -> None:
    """Объединение снимка и брони не теряет ни тех, ни других."""
    print("\n[Снимок и бронь объединяются, а не заменяют друг друга]")
    книга = reservations.Книга()
    книга.забронировать("GBPUSD", 1, 0.0)
    итог = reservations.объединить_символы(["EURUSD"], книга)
    check(итог == ["EURUSD", "GBPUSD"], "Оба инструмента на месте", str(итог))

    книга2 = reservations.Книга()
    книга2.забронировать("EURUSD", 1, 0.0)
    итог2 = reservations.объединить_символы(["EURUSD"], книга2)
    check(итог2 == ["EURUSD"], "Повтор не удваивается", str(итог2))
    check(reservations.объединить_символы(None, книга2) == ["EURUSD"],
          "Пустой снимок не ломает объединение")
    check(reservations.объединить_символы(["EURUSD"], None) == ["EURUSD"],
          "Отсутствие книги не ломает объединение")


def test_reservations_never_reach_position_management() -> None:
    """Бронь НЕ ДОЛЖНА попадать в ведение открытых позиций.

    Это самый опасный способ сломать такую починку. Соблазнительно
    подмешать «поддельные позиции» прямо в общий список — тогда все
    проверки заработали бы сами, без единой правки. Но этот же список
    уходит в manage_open_positions, и та начала бы двигать стоп у тикета,
    которого не существует.

    Поэтому книга резервов хранит СЧЁТЧИКИ, а не позиции: подмешать её в
    список позиций физически нечем."""
    print("\n[Бронь не попадает в ведение позиций]")
    книга = reservations.Книга()
    книга.забронировать("EURUSD", 1, 10.0)

    # У книги нет ничего, что можно было бы принять за позицию.
    for опасное in ("как_позиции", "позиции", "positions", "tickets"):
        check(not hasattr(книга, опасное),
              f"У книги нет метода «{опасное}» — подмешать нечего")

    # А в исходнике список позиций не переприсваивается внутри разбора пары:
    # в ведение уходит ровно то, что пришло от терминала.
    исходник = (APP / "main.py").read_text(encoding="utf-8")
    начало = исходник.find("def process_symbol")
    конец = исходник.find("\ndef ", начало + 10)
    тело = исходник[начало:конец]
    check("all_positions = " not in тело,
          "Внутри разбора пары список позиций не переписывается")
    check("positions=all_positions" in тело,
          "И в ведение уходит именно он")


def test_each_account_has_its_own_book() -> None:
    """У каждого счёта своя книга.

    Общая на всех означала бы, что сделка на одном счёте запрещает
    торговлю на другом."""
    print("\n[У каждого счёта своя книга]")
    a, b = AccountState(), AccountState()
    a.reservations.забронировать("EURUSD", 1, 10.0)
    check(a.reservations.сколько() == 1, "У первого счёта бронь есть")
    check(b.reservations.сколько() == 0, "У второго счёта её нет")


if __name__ == "__main__":
    print("=" * 62)
    print("P0-1: лимиты внутри одного обхода символов")
    print("=" * 62)
    test_harness_actually_reaches_the_limit_checks()
    test_second_symbol_is_blocked_by_total_position_cap()
    test_second_symbol_is_blocked_by_total_risk()
    test_second_symbol_is_blocked_by_currency_exposure()
    test_reservations_are_cleared_each_pass()
    test_reservation_is_made_only_after_the_broker_confirms()
    test_reservation_counts_per_symbol_too()
    test_reservations_can_only_overcount_never_under()
    test_empty_book_is_false()
    test_symbols_are_merged_not_replaced()
    test_reservations_never_reach_position_management()
    test_each_account_has_its_own_book()
    print()
    print("=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    sys.exit(1 if failed else 0)
