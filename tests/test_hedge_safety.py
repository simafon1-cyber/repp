#!/usr/bin/env python3
"""Тесты безопасности хеджа: тип счёта и целостность двух ног.

ПОЧЕМУ ЭТИ ТЕСТЫ ПОЯВИЛИСЬ.

Настройка hedge_both_directions открывает по одному инструменту сразу BUY и
SELL. Внешний аудит нашёл здесь две дыры, и обе подтвердились по коду.

ПЕРВАЯ: тип счёта не проверялся ВООБЩЕ. Поиск по всему проекту не находил
ни NETTING, ни RETAIL_HEDGING, ни margin_mode.

Счета MT5 бывают двух видов, и это свойство счёта у брокера, а не настройка
программы:

    RETAIL_HEDGING   можно держать покупку и продажу одновременно
    RETAIL_NETTING   позиция по инструменту всегда ОДНА

На неттинговом счёте вторая заявка не создаёт вторую позицию, а закрывает
или разворачивает первую. Вместо хеджа вышла бы закрытая сделка — а
стратегия и учёт риска ожидают совсем другого.

Запуск:  python3 tests/test_hedge_safety.py
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
cfg.LIVE_TRADING = False
sys.modules["config"] = cfg

НЕТТИНГ, БИРЖА, ХЕДЖ = 0, 1, 2


class _Информация:
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
    """Счёт брокера. margin_mode — то самое поле, которого никто не читал."""
    def __init__(self, equity=1000.0, margin_mode=ХЕДЖ):
        self.equity = equity
        self.balance = equity
        self.margin_free = equity
        self.login = 5054028014
        self.currency = "USD"
        self.leverage = 100
        if margin_mode is not None:
            self.margin_mode = margin_mode


def _свечи(n=400, старт=1.10000):
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
               ("ACCOUNT_MARGIN_MODE_RETAIL_NETTING", НЕТТИНГ),
               ("ACCOUNT_MARGIN_MODE_EXCHANGE", БИРЖА),
               ("ACCOUNT_MARGIN_MODE_RETAIL_HEDGING", ХЕДЖ)):
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
sys.modules["MetaTrader5"] = fake

import execution as ex        # noqa: E402
import main as bot            # noqa: E402
import mt5_connector as mt5c  # noqa: E402
import risk_manager as rm     # noqa: E402
from state import AccountState, SymbolState   # noqa: E402

# ФАЙЛ ИНЦИДЕНТА (И2) — ВО ВРЕМЕННУЮ ПАПКУ.
#
# Отметка об остановке пишется рядом с программой. Тесты не должны
# оставлять её в рабочей папке: следующий запуск программы увидел бы
# чужой инцидент и отказался торговать.
import tempfile as _врем                                   # noqa: E402
import incident as _инцидент                               # noqa: E402
from control import control                                # noqa: E402

_ПАПКА_ИНЦИДЕНТА = _врем.mkdtemp(prefix="incident_test_")
_инцидент.путь = (lambda folder="":
                  __import__('os').path.join(_ПАПКА_ИНЦИДЕНТА, "incident.json"))


def _очистить_инцидент():
    """Снять остановку между проверками.

    Обычная кнопка «снять паузу» инцидент теперь НЕ снимает — в этом весь
    смысл И2. Значит, тесты обязаны снимать его явно, иначе первый же
    инцидент остановил бы все проверки после себя."""
    control.снять_инцидент("тест", "очистка между проверками")
    control.set_paused(False)


ИСХОДНИК_MAIN = (APP / "main.py").read_text(encoding="utf-8")


def прогнать(symbol, acc_info, equity=1000.0):
    сч = AccountState()
    сч.day_start_equity = equity
    сч.peak_equity = equity
    st = SymbolState(symbol=symbol)
    bot.process_symbol(symbol, st, сч, equity,
                       acc_info=acc_info, all_positions=[])
    return st.last_reject_reason or ""


# =====================================================================
def test_hedging_account_is_allowed() -> None:
    print("\n[Хедж-счёт: встречные позиции разрешены]")
    check(mt5c.hedging_block_reason(_Счёт(margin_mode=ХЕДЖ)) == "",
          "На hedging-счёте запрета нет")
    check(mt5c.account_margin_mode(_Счёт(margin_mode=ХЕДЖ)) == ХЕДЖ,
          "Режим счёта читается")


def test_netting_account_is_blocked_with_a_readable_reason() -> None:
    """Главный случай. На неттинге вторая заявка закрыла бы первую."""
    print("\n[Неттинговый счёт: встречные позиции запрещены]")
    причина = mt5c.hedging_block_reason(_Счёт(margin_mode=НЕТТИНГ))
    check(причина != "", "Запрет есть")
    check("netting" in причина.lower() or "неттинг" in причина.lower(),
          "И в причине названо, какой это счёт", причина)
    check("одна" in причина.lower() or "ОДНА" in причина,
          "Сказано, что позиция может быть только одна", причина)
    check("хедж" in причина.lower(),
          "И названа настройка, которую надо выключить", причина)


def test_exchange_account_is_blocked_too() -> None:
    print("\n[Биржевой счёт: тоже запрещён]")
    причина = mt5c.hedging_block_reason(_Счёт(margin_mode=БИРЖА))
    check(причина != "", "Запрет есть")
    check("бирж" in причина.lower(), "И счёт назван биржевым", причина)


def test_unknown_account_type_is_blocked_not_assumed() -> None:
    """ОСТОРОЖНОСТЬ НЕСИММЕТРИЧНА, И ЭТО НАМЕРЕННО.

    Ошибка «запретили зря» стоит одной неоткрытой сделки. Ошибка
    «разрешили зря» — закрытой или развёрнутой позиции, которую никто не
    просил трогать. Поэтому при любом сомнении запрещаем."""
    print("\n[Неизвестный тип счёта: запрет, а не предположение]")

    check(mt5c.account_margin_mode(_Счёт(margin_mode=None)) is None,
          "Счёт без поля margin_mode даёт None, а не ноль")
    причина = mt5c.hedging_block_reason(_Счёт(margin_mode=None))
    check(причина != "", "И встречные позиции запрещены", причина)
    check("не удалось узнать" in причина.lower(),
          "Причина честно говорит «не знаю», а не выдумывает тип", причина)

    check(mt5c.account_margin_mode(None) is not None
          or mt5c.hedging_block_reason(None) != "",
          "Отсутствие счёта тоже не открывает дорогу хеджу")

    # Незнакомое число — не повод считать его хеджем.
    причина = mt5c.hedging_block_reason(_Счёт(margin_mode=99))
    check(причина != "", "Незнакомый режим запрещён", причина)
    check("99" in причина, "И число показано человеку", причина)


def test_broken_terminal_does_not_open_the_door() -> None:
    """Если в терминале нет самой константы — тоже запрет."""
    print("\n[Терминал без нужной константы: запрет]")
    было = fake.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING
    del fake.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING
    try:
        причина = mt5c.hedging_block_reason(_Счёт(margin_mode=ХЕДЖ))
        check(причина != "",
              "Без константы хедж запрещён даже на правильном счёте", причина)
    finally:
        fake.ACCOUNT_MARGIN_MODE_RETAIL_HEDGING = было
    check(mt5c.hedging_block_reason(_Счёт(margin_mode=ХЕДЖ)) == "",
          "После возврата константы всё снова работает")


def test_trading_refuses_hedge_on_netting_account() -> None:
    """ПОВЕДЕНЧЕСКАЯ ПРОВЕРКА. Не текст в исходнике, а вызов.

    Профиль просит хедж, счёт неттинговый — сделки быть не должно."""
    print("\n[Торговля: хедж на неттинге не открывается]")
    профиль = rm.get_profile()
    был_хедж = профиль.get("hedge_both_directions", False)
    был_порог = профиль["min_score_to_trade"]
    профиль["hedge_both_directions"] = True
    профиль["min_score_to_trade"] = 0.0
    try:
        # КОНТРОЛЬ: на хедж-счёте этой причины быть не должно.
        причина = прогнать("EURUSD", _Счёт(margin_mode=ХЕДЖ))
        check("одна позиция" not in причина.lower()
              and "netting" not in причина.lower(),
              "Контроль: на hedging-счёте запрета по типу счёта нет",
              f"причина: {причина!r}")

        причина = прогнать("EURUSD", _Счёт(margin_mode=НЕТТИНГ))
        check("netting" in причина.lower() or "неттинг" in причина.lower(),
              "На неттинговом счёте сделка ОТКЛОНЕНА с понятной причиной",
              f"причина: {причина!r}")
    finally:
        профиль["hedge_both_directions"] = был_хедж
        профиль["min_score_to_trade"] = был_порог


def test_refusal_is_not_a_silent_switch_to_one_side() -> None:
    """Молча переключиться на одну сторону НЕЛЬЗЯ.

    Это была бы тихая подмена решения, которое настроил владелец: он просил
    две ноги, а получил бы одну и не узнал бы об этом."""
    print("\n[Отказ, а не тихая подмена на одну сторону]")
    кусок = ИСХОДНИК_MAIN[
        ИСХОДНИК_MAIN.find("нельзя_хедж = mt5c.hedging_block_reason"):]
    кусок = кусок[:400]
    check("return" in кусок, "После запрета управление возвращается")
    check("hedge_directions = [1, -1]" not in кусок.split("return")[0],
          "И до return хедж не назначается")
    check("direction, score" not in кусок.split("return")[0],
          "И одна сторона вместо хеджа не подставляется")


def test_check_happens_before_lot_and_risk_are_computed() -> None:
    """Проверка стоит ДО расчёта лота и риска на две ноги.

    Иначе программа считала бы объём и риск для ног, которых быть не может,
    и могла бы отказать по «нехватке средств» вместо настоящей причины."""
    print("\n[Проверка идёт до расчёта лота и риска]")
    поз_проверка = ИСХОДНИК_MAIN.find("нельзя_хедж = mt5c.hedging_block_reason")
    поз_лот = ИСХОДНИК_MAIN.find("lot = rm.calc_lot(")
    поз_риск = ИСХОДНИК_MAIN.find("new_trade_risk_money = per_lot * lot")
    check(поз_проверка != -1, "Проверка на месте")
    check(поз_проверка < поз_лот, "Она раньше расчёта лота")
    check(поз_проверка < поз_риск, "И раньше расчёта риска")


def test_no_extra_terminal_call_for_the_check() -> None:
    """Тип счёта берётся из уже полученного acc_info.

    Лишний запрос к терминалу на каждую пару — это та самая задержка, из-за
    которой снимок позиций и стал одним на проход."""
    print("\n[Лишнего обращения к терминалу нет]")
    check("mt5c.hedging_block_reason(acc_info)" in ИСХОДНИК_MAIN,
          "В проверку передаётся уже полученный acc_info")

    # А сама функция умеет и без него — для вызовов со стороны.
    вызовов = {"n": 0}
    было = fake.account_info

    def считать():
        вызовов["n"] += 1
        return было()

    fake.account_info = считать
    try:
        mt5c.hedging_block_reason(_Счёт(margin_mode=ХЕДЖ))
        check(вызовов["n"] == 0,
              "С переданным счётом терминал не опрашивается",
              str(вызовов["n"]))
        mt5c.hedging_block_reason(None)
        check(вызовов["n"] == 1,
              "Без него — ровно один запрос", str(вызовов["n"]))
    finally:
        fake.account_info = было


# =====================================================================
# ЦЕЛОСТНОСТЬ ДВУХ НОГ (P0-2)
# =====================================================================
def _хедж_профиль():
    профиль = rm.get_profile()
    было = (профиль.get("hedge_both_directions", False),
            профиль["min_score_to_trade"])
    профиль["hedge_both_directions"] = True
    профиль["min_score_to_trade"] = 0.0
    return профиль, было


def test_order_now_returns_a_state_not_a_yes_no() -> None:
    """Раньше отправка ордера возвращала True/False, потом номер позиции.

    Ни того, ни другого не хватало. «Номер или ноль» — это по-прежнему
    ответ на вопрос «получилось?», а у заявки четыре исхода: полное,
    частичное, отказ и «неизвестно» (см. execution.py). Теперь отсюда
    возвращается состояние, а не да/нет."""
    print("\n[Отправка ордера возвращает состояние заявки]")
    import trade_manager as tm
    check(hasattr(tm, "TICKET_НЕИЗВЕСТЕН"),
          "Есть метка «открылось, но номер неизвестен»")

    итог = tm.execute_market_order("EURUSD", 1, 0.01, 0.001, 0.002, 5.0, 0.00001)
    check(isinstance(итог, ex.Итог), "Возвращается Итог, а не число",
          type(итог).__name__)
    check(итог.статус in (ex.ПОЛНОЕ, ex.ЧАСТИЧНОЕ, ex.ОТКАЗ, ex.НЕИЗВЕСТНО),
          "И его статус — одно из четырёх состояний", итог.статус)

    исходник = (APP / "trade_manager.py").read_text(encoding="utf-8")
    начало = исходник.find("def execute_market_order")
    конец = исходник.find("\n# Сколько раз пытаться", начало + 10)
    тело = исходник[начало:конец]
    check("return True" not in тело,
          "Из отправки больше не возвращается True")
    check("return 0" not in тело,
          "И голый ноль тоже: он не отличает отказ от «не знаю»")
    check("ex.отказ(" in тело, "Отказ возвращается явным Итогом")


def test_half_filled_hedge_closes_the_filled_leg() -> None:
    """ГЛАВНЫЙ СЛУЧАЙ. Первая нога прошла, вторая нет — первую закрыть."""
    print("\n[Половинчатый хедж: исполненная нога закрывается]")
    import trade_manager as tm
    профиль, было = _хедж_профиль()
    отправлено, закрыто = [], []
    ордер, нога = tm.execute_market_order, tm.close_leg

    def подделка_ордера(symbol, direction, lot, sl_dist, tp_dist, score, point):
        отправлено.append(direction)
        if len(отправлено) == 1:
            return ex.полное(111, lot)
        return ex.отказ("вторая нога срывается", lot)

    def подделка_закрытия(symbol, ticket, direction=0, volume=0.0):
        закрыто.append(ticket)
        return ex.полное(ticket, volume)

    tm.execute_market_order = подделка_ордера
    tm.close_leg = подделка_закрытия
    try:
        причина = прогнать("EURUSD", _Счёт(margin_mode=ХЕДЖ))
        check(len(отправлено) == 2, "Отправлены обе ноги", str(отправлено))
        check(закрыто == [111],
              "Исполненная нога ЗАКРЫТА компенсацией", str(закрыто))
        check("не собрался" in причина or "не исполнилась" in причина,
              "И причина названа словами", f"причина: {причина!r}")
    finally:
        tm.execute_market_order, tm.close_leg = ордер, нога
        профиль["hedge_both_directions"], профиль["min_score_to_trade"] = было


def test_failed_compensation_stops_new_entries() -> None:
    """ХУДШИЙ СЛУЧАЙ. Нога висит, закрыть не смогли.

    Своим стопом она защищена, но это уже случайность, а не задумка.
    Открывать что-то ещё в таком состоянии нельзя."""
    print("\n[Компенсация не удалась: новые входы остановлены]")
    import trade_manager as tm
    from control import control
    профиль, было = _хедж_профиль()
    ордер, нога = tm.execute_market_order, tm.close_leg
    отправлено = []

    tm.execute_market_order = (
        lambda *a, **k: (отправлено.append(1),
                         ex.полное(222, 0.01) if len(отправлено) == 1
                         else ex.отказ("вторая нога срывается"))[1])
    # Закрыть не удалось — и это ОТКАЗ, а не частичное закрытие.
    tm.close_leg = lambda *a, **k: ex.отказ("брокер не принял закрытие")

    _очистить_инцидент()
    try:
        причина = прогнать("EURUSD", _Счёт(margin_mode=ХЕДЖ))
        check(control.is_paused(),
              "Торговля ПРИОСТАНОВЛЕНА — новых входов не будет")
        check("компенсация не удалась" in причина.lower(),
              "И причина сказана прямо", f"причина: {причина!r}")
    finally:
        _очистить_инцидент()
        tm.execute_market_order, tm.close_leg = ордер, нога
        профиль["hedge_both_directions"], профиль["min_score_to_trade"] = было


def test_first_leg_failing_needs_no_compensation() -> None:
    """Если сорвалась ПЕРВАЯ нога, закрывать нечего — и не надо."""
    print("\n[Сорвалась первая нога: закрывать нечего]")
    import trade_manager as tm
    профиль, было = _хедж_профиль()
    ордер, нога = tm.execute_market_order, tm.close_leg
    закрыто = []
    tm.execute_market_order = lambda *a, **k: ex.отказ("первая нога сорвалась")
    tm.close_leg = lambda *a, **k: закрыто.append(1) or ex.полное(1, 0.01)
    try:
        прогнать("EURUSD", _Счёт(margin_mode=ХЕДЖ))
        check(закрыто == [], "Компенсация не вызывалась", str(закрыто))
    finally:
        tm.execute_market_order, tm.close_leg = ордер, нога
        профиль["hedge_both_directions"], профиль["min_score_to_trade"] = было


def test_successful_hedge_is_not_disturbed() -> None:
    """Обе ноги прошли — ничего закрывать не надо."""
    print("\n[Полный хедж: компенсация не срабатывает]")
    import trade_manager as tm
    профиль, было = _хедж_профиль()
    ордер, нога = tm.execute_market_order, tm.close_leg
    закрыто = []
    tm.execute_market_order = lambda *a, **k: ex.полное(333, 0.01)
    tm.close_leg = lambda *a, **k: закрыто.append(1) or ex.полное(1, 0.01)
    try:
        причина = прогнать("EURUSD", _Счёт(margin_mode=ХЕДЖ))
        check(закрыто == [], "Компенсация не вызывалась", str(закрыто))
        check(причина == "OK", "Сделка засчитана как открытая", repr(причина))
    finally:
        tm.execute_market_order, tm.close_leg = ордер, нога
        профиль["hedge_both_directions"], профиль["min_score_to_trade"] = было


def test_partial_hedge_is_no_longer_reported_as_ok() -> None:
    """Строки «OK (частично, хедж)» больше не существует.

    Она и была всей прежней реакцией на половинчатый хедж: программа
    сообщала об успехе там, где на счету оставалась чужая позиция."""
    print("\n[«OK (частично, хедж)» больше нет]")
    # Комментарии выкидываем: упоминание старой строки в пояснении — это
    # история правки, а не поведение. Проверка, которая их не различает,
    # ловит собственный текст (уже попадался в тестах резервов).
    без_комментариев = "\n".join(
        строка.split("#", 1)[0] for строка in ИСХОДНИК_MAIN.splitlines())
    check("частично, хедж" not in без_комментариев,
          "Ложного успеха в исполняемом коде не осталось")
    # И положительно: обе честные причины на месте.
    check("Хедж не собрался" in без_комментариев,
          "Есть причина «хедж не собрался, первая нога закрыта»")
    check("компенсация не удалась" in без_комментариев,
          "И причина «компенсация не удалась»")


def test_dry_run_never_sends_a_closing_order() -> None:
    """В режиме проверки закрывать нечего: позиции не существует."""
    print("\n[Проверочный режим не шлёт приказ на закрытие]")
    import trade_manager as tm
    было = cfg.LIVE_TRADING
    cfg.LIVE_TRADING = False
    звонки = []
    настоящий = mt5c.close_position_partial
    mt5c.close_position_partial = lambda *a, **k: звонки.append(1)
    try:
        итог = tm.close_leg("EURUSD", 123, direction=1, volume=0.01)
        check(итог.статус == ex.ПОЛНОЕ,
              "Закрытие в проверочном режиме считается успешным", итог.статус)
        check(звонки == [], "И наружу ничего не отправлено", str(звонки))
    finally:
        mt5c.close_position_partial = настоящий
        cfg.LIVE_TRADING = было


if __name__ == "__main__":
    print("=" * 62)
    print("БЕЗОПАСНОСТЬ ХЕДЖА: тип счёта")
    print("=" * 62)
    test_hedging_account_is_allowed()
    test_netting_account_is_blocked_with_a_readable_reason()
    test_exchange_account_is_blocked_too()
    test_unknown_account_type_is_blocked_not_assumed()
    test_broken_terminal_does_not_open_the_door()
    test_trading_refuses_hedge_on_netting_account()
    test_refusal_is_not_a_silent_switch_to_one_side()
    test_check_happens_before_lot_and_risk_are_computed()
    test_no_extra_terminal_call_for_the_check()
    test_order_now_returns_a_state_not_a_yes_no()
    test_half_filled_hedge_closes_the_filled_leg()
    test_failed_compensation_stops_new_entries()
    test_first_leg_failing_needs_no_compensation()
    test_successful_hedge_is_not_disturbed()
    test_partial_hedge_is_no_longer_reported_as_ok()
    test_dry_run_never_sends_a_closing_order()
    print()
    print("=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    sys.exit(1 if failed else 0)
