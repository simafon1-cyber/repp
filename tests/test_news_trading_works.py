#!/usr/bin/env python3
"""Тесты новостной торговли: почему она не работала и почему теперь работает.

ПОЧЕМУ ЭТИ ТЕСТЫ ПОЯВИЛИСЬ.

Владелец: «ни разу новостная не работала».

Причина оказалась не в календаре, не в источниках и не в порогах, а в
ПОРЯДКЕ ПРОВЕРОК. Новостной вход стоял ниже фильтра спреда. А новость всегда
расширяет спред — это её первое и самое надёжное следствие. Получалось так:

    вышла новость -> спред расширился -> сработал фильтр спреда ->
    функция вернулась -> до новостной ветки дело не дошло.

То есть новостной режим отключался ровно в ту минуту, ради которой он и
существует. Ошибка такого рода не видна ни в одном отдельном модуле: каждый
кусок правильный, неверен только их порядок. Поэтому здесь проверяется
именно порядок — и он закреплён тестом навсегда.

Запуск:  python3 tests/test_news_trading_works.py
"""

from __future__ import annotations

import re
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
for _и, _з in (("ORDER_TYPE_BUY", 0), ("ORDER_TYPE_SELL", 1),
               ("TIMEFRAME_M1", 1), ("TIMEFRAME_M5", 5), ("TIMEFRAME_M15", 15),
               ("TIMEFRAME_M30", 30), ("TIMEFRAME_H1", 60), ("TIMEFRAME_H4", 240),
               ("TIMEFRAME_D1", 1440), ("ORDER_FILLING_IOC", 1),
               ("ORDER_FILLING_FOK", 2), ("TRADE_RETCODE_DONE", 10009),
               ("TRADE_RETCODE_REQUOTE", 10004),
               ("TRADE_RETCODE_PRICE_CHANGED", 10020),
               ("TRADE_RETCODE_PRICE_OFF", 10021),
               ("POSITION_TYPE_BUY", 0), ("POSITION_TYPE_SELL", 1)):
    setattr(fake_mt5, _и, _з)
for _и in ("symbol_info", "symbol_info_tick", "order_calc_profit",
           "order_calc_margin", "copy_rates_from_pos", "positions_get",
           "account_info", "last_error", "terminal_info"):
    setattr(fake_mt5, _и, lambda *a, **k: None)
sys.modules["MetaTrader5"] = fake_mt5

import mt5_connector as mt5c   # noqa: E402
import news_calendar           # noqa: E402
import risk_manager as rm      # noqa: E402

ИСХОДНИК = (APP / "main.py").read_text(encoding="utf-8")


def позиция(шаблон: str, текст: str = "") -> int:
    """Где в исходнике стоит строка. -1 — не найдена."""
    m = re.search(шаблон, текст or ИСХОДНИК)
    return m.start() if m else -1


# =====================================================================
def test_news_check_comes_before_the_filters_news_itself_triggers() -> None:
    """ГЛАВНЫЙ ТЕСТ. Он и есть починка.

    Новость расширяет спред. Значит проверка новостного пробоя ОБЯЗАНА стоять
    выше фильтра спреда, иначе новостной режим выключается сам собой ровно
    тогда, когда должен работать."""
    print("\n[Новостной вход считается ДО фильтров, которые новость и включает]")

    новости = позиция(r"news_calendar\.detect_news_breakout")
    спред = позиция(r"if not rm\.spread_ok\(symbol, atr_value, point\)")
    ролловер = позиция(r"rm\.rollover_guard_ok")
    рынок = позиция(r"market_hours\.market_block_reason")

    check(новости > 0, "Новостной пробой в торговом цикле вообще считается")
    check(спред > 0, "Фильтр спреда на месте")
    check(новости < спред,
          "Новость считается РАНЬШЕ фильтра спреда — это и есть починка",
          f"новости на {новости}, спред на {спред}")
    check(новости < ролловер, "И раньше ролловерной паузы")
    check(новости < рынок, "И раньше защиты «рынок неликвиден»")

    # Считается РОВНО ОДИН РАЗ. Второй вызов дал бы другой ответ: за это
    # время цена ушла бы, и два ответа на один вопрос разошлись бы.
    check(ИСХОДНИК.count("news_calendar.detect_news_breakout") == 1,
          "И считается ровно один раз за проход",
          str(ИСХОДНИК.count("news_calendar.detect_news_breakout")))


def test_money_guards_still_come_first() -> None:
    """Новость не даёт права обойти защиту денег. Всё, что бережёт счёт,
    обязано остаться ВЫШЕ новостной ветки."""
    print("\n[Защита денег по-прежнему выше новостей]")
    новости = позиция(r"news_calendar\.detect_news_breakout")

    for шаблон, имя in (
            (r"rm\.trading_block_reason", "приостановка торговли по счёту"),
            (r"al\.symbol_auto_off_reason", "самоотключение убыточной пары"),
            (r"rm\.count_open_positions\(symbol", "лимит сделок по паре"),
            (r"MAX_SIMULTANEOUS_POSITIONS", "общий лимит сделок"),
            (r"rm\.blocked_symbol_reason", "пара отключена вручную")):
        место = позиция(шаблон)
        check(0 < место < новости,
              f"Выше новостей: {имя}", f"{имя} на {место}, новости на {новости}")


def test_news_has_its_own_spread_ceiling_and_it_exists() -> None:
    """Снять потолок спреда совсем было бы не починкой, а новой бедой: вход
    при спреде в пятьдесят раз шире обычного отдаёт стоп брокеру ещё до того,
    как цена куда-то пошла."""
    print("\n[У новостного входа свой потолок спреда — но он есть]")
    было_спред = mt5c.get_spread_points
    try:
        CFG.USE_SPREAD_FILTER = True
        CFG.MAX_SPREAD_POINTS = 30
        CFG.AUTO_ADAPT_TO_SYMBOL = False
        CFG.NEWS_MAX_SPREAD_MULT = 3.0

        mt5c.get_spread_points = lambda s: 20
        check(rm.spread_ok("EURUSD", 0.0, 0.00001),
              "Обычный спред проходит обе проверки")
        check(rm.news_spread_ok("EURUSD", 0.0, 0.00001), "И новостную тоже")

        mt5c.get_spread_points = lambda s: 60      # вдвое шире обычного потолка
        check(not rm.spread_ok("EURUSD", 0.0, 0.00001),
              "Расширенный новостью спред обычную проверку НЕ проходит")
        check(rm.news_spread_ok("EURUSD", 0.0, 0.00001),
              "А новостную проходит — иначе новость отменяла бы сама себя")

        mt5c.get_spread_points = lambda s: 200     # в шесть раз шире
        check(not rm.news_spread_ok("EURUSD", 0.0, 0.00001),
              "Но чудовищный спред не проходит и новостную — потолок ЕСТЬ")

        # 0 = потолка нет вовсе. Это осознанный выбор, а не значение по умолчанию.
        CFG.NEWS_MAX_SPREAD_MULT = 0
        check(rm.news_spread_ok("EURUSD", 0.0, 0.00001),
              "NEWS_MAX_SPREAD_MULT = 0 снимает потолок совсем")
        CFG.NEWS_MAX_SPREAD_MULT = 3.0

        # Выключенный фильтр спреда выключает обе проверки одинаково.
        CFG.USE_SPREAD_FILTER = False
        check(rm.news_spread_ok("EURUSD", 0.0, 0.00001),
              "При выключенном фильтре спреда новостная проверка не мешает")
        CFG.USE_SPREAD_FILTER = True
    finally:
        mt5c.get_spread_points = было_спред


def test_settings_exist_and_are_sane() -> None:
    print("\n[Настройки новостной торговли на месте и осмысленны]")
    example = (APP / "config.py.example").read_text(encoding="utf-8")

    check("NEWS_MAX_SPREAD_MULT" in example,
          "Потолок спреда для новостей есть в настройках")
    check(getattr(CFG, "NEWS_MAX_SPREAD_MULT", 0) > 1.0,
          "И он шире обычного, иначе смысла в нём нет",
          str(getattr(CFG, "NEWS_MAX_SPREAD_MULT", 0)))

    check(not hasattr(CFG, "TRADING_MODE"),
          "Выбора режима торговли больше нет — новостной вход работает всегда")
    check("TradingMode" not in ИСХОДНИК,
          "И торговый цикл о режимах ничего не знает")
    check(getattr(CFG, "NEWS_HARD_BLOCK_WINDOW_MIN", 1) == 0,
          "Паузы вокруг новостей нет — иначе она сама и запрещала бы вход")
    check(getattr(CFG, "USE_NEWS_FILTER", False) is True,
          "Источник новостей включён")
    check(str(getattr(CFG, "NEWS_TRADE_MIN_IMPACT", "")).lower()
          in ("low", "medium", "high"),
          "Порог важности задан понятным словом")

    # Новая настройка обязана попадать в старые config.py при обновлении —
    # иначе у тех, кто уже пользуется программой, её просто не будет.
    import config_migrate
    без_неё = example.replace("NEWS_MAX_SPREAD_MULT = 3.0", "")
    пропущенные = config_migrate.missing_keys(без_неё, example)
    check("NEWS_MAX_SPREAD_MULT" in пропущенные,
          "И она дописывается в существующие настройки при обновлении")


def test_explainer_names_the_real_reason() -> None:
    """Владелец: «я не заметил за ним этого». Заметить было нельзя — при
    отсутствии входа программа молчала. Объяснение обязано называть причину,
    а не разводить руками."""
    print("\n[Программа объясняет, что именно с новостями сейчас]")
    было_события = news_calendar._get_events
    try:
        # Источник не отвечает.
        news_calendar._get_events = lambda: ([], "сервис не запущен")
        текст = news_calendar.explain_news_entry("EURUSD")
        check("не отвечает" in текст, "Молчащий источник назван", текст[:70])

        # Источник ответил, но пусто.
        news_calendar._get_events = lambda: ([], None)
        текст = news_calendar.explain_news_entry("EURUSD")
        check("событий" in текст, "Пустой календарь назван отдельно", текст[:70])

        # Событие есть, но по чужой валюте.
        from datetime import datetime, timedelta
        чужое = [{"time": datetime.now() - timedelta(minutes=5),
                  "currency": "JPY", "event": "Ставка Банка Японии",
                  "impact": "high", "actual": "", "estimate": "", "prev": ""}]
        news_calendar._get_events = lambda: (чужое, None)
        текст = news_calendar.explain_news_entry("EURUSD")
        check("событий в календаре нет" in текст,
              "Событие по чужой валюте не выдаётся за своё", текст[:80])

        # Своё событие, но впереди — говорим, через сколько.
        впереди = [{"time": datetime.now() + timedelta(minutes=40),
                    "currency": "USD", "event": "Nonfarm Payrolls",
                    "impact": "high", "actual": "", "estimate": "", "prev": ""}]
        news_calendar._get_events = lambda: (впереди, None)
        текст = news_calendar.explain_news_entry("EURUSD")
        check("через" in текст and "мин" in текст,
              "Сказано, через сколько ближайшая новость", текст[:90])

        # Источник выключен совсем — это тоже причина, и её надо назвать.
        было_фильтр = CFG.USE_NEWS_FILTER
        CFG.USE_NEWS_FILTER = False
        текст = news_calendar.explain_news_entry("EURUSD")
        check("выключены" in текст, "Выключенный источник назван прямо", текст[:70])
        CFG.USE_NEWS_FILTER = было_фильтр
    finally:
        news_calendar._get_events = было_события


def test_low_confidence_is_reported_not_silent() -> None:
    """Раньше «пробой есть, но уверенности не хватило» выглядело точно так
    же, как «новостей нет» — то есть никак. Разные причины должны читаться
    по-разному."""
    print("\n[Нехватка уверенности не выглядит как «новостей нет»]")
    check("уверенность" in ИСХОДНИК and "ниже порога" in ИСХОДНИК,
          "В торговом цикле такая причина называется словами")
    место_причины = позиция(r"уверенность \{news_conf")
    check(место_причины > 0, "И она попадает в причину отказа по паре")


def test_news_never_bypasses_the_hard_market_block() -> None:
    """Смягчена ровно одна часть защиты «рынок неликвиден» — та, что ловит
    широкий спред, то есть саму новость. Жёсткие признаки (брокер запретил
    торговлю, цена замерла) с новостью не связаны и остаются."""
    print("\n[Новость не отменяет жёсткий запрет торговли]")
    кусок = ИСХОДНИК[позиция(r"market_hours\.market_block_reason"):]
    кусок = кусок[:1200]
    check("thin_ratio" in кусок and "not news_ready" in кусок,
          "Снят только признак «спред намного шире обычного»")
    check("trade_mode" in кусок,
          "Запрет брокера по-прежнему передаётся в проверку")
    check("dead_seconds" in кусок,
          "И признак замершей цены тоже — он с новостью не связан")


def test_state_can_tell_source_failure_from_no_news() -> None:
    print("\n[Отказ источника отличим от отсутствия новостей]")
    from state import SymbolState
    st = SymbolState(symbol="EURUSD")
    check(hasattr(st, "last_news_error"),
          "У пары есть отдельное поле про сбой календаря")
    check(st.last_news_error == "",
          "И по умолчанию оно пустое — сбоя нет")
    check("last_news_error" in ИСХОДНИК,
          "Торговый цикл его заполняет при сбое источника")


if __name__ == "__main__":
    print("=" * 62)
    print("ТЕСТЫ: НОВОСТНАЯ ТОРГОВЛЯ РАБОТАЕТ")
    print("=" * 62)
    test_news_check_comes_before_the_filters_news_itself_triggers()
    test_money_guards_still_come_first()
    test_news_has_its_own_spread_ceiling_and_it_exists()
    test_settings_exist_and_are_sane()
    test_explainer_names_the_real_reason()
    test_low_confidence_is_reported_not_silent()
    test_news_never_bypasses_the_hard_market_block()
    test_state_can_tell_source_failure_from_no_news()
    print()
    print("=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    sys.exit(1 if failed else 0)
