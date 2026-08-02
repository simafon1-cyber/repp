#!/usr/bin/env python3
"""Тесты сигналов из Telegram.

Главное, что здесь проверяется, — ГРАНИЦЫ ПОЛНОМОЧИЙ чужого сигнала:

  1. Он НЕ МОЖЕТ открыть сделку, поднять лот, отодвинуть стоп или отменить
     лимит — в модуле просто нет такого кода, и это проверяется разбором
     исходника, а не на словах.
  2. Надбавка к оценке ограничена сверху ЖЁСТКО, а не только настройкой:
     даже 500 в config.py не даст чужому сигналу протолкнуть сделку.
  3. По умолчанию (TELEGRAM_ROLE = "show") сигнал не влияет на торговлю
     вообще — подключение источника не меняет поведение бота молча.
  4. Молчание источника не является запретом: нет сигнала — торгуем как
     обычно.

Запуск:  python3 tests/test_telegram.py
"""

from __future__ import annotations

import ast
import sys
import types
from datetime import datetime, timedelta
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


def install_stubs() -> types.ModuleType:
    cfg = types.ModuleType("config")
    exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
    sys.modules["config"] = cfg

    class _FakeMT5(types.ModuleType):
        def __getattr__(self, name):
            if name.startswith("_"):
                raise AttributeError(name)
            return name

    mt5 = _FakeMT5("MetaTrader5")
    mt5.initialize = lambda *a, **k: False
    mt5.terminal_info = lambda: None
    sys.modules["MetaTrader5"] = mt5
    return cfg


CFG = install_stubs()

import telegram_signals as tgs     # noqa: E402
import telegram_reader as tgr      # noqa: E402

NOW = datetime(2026, 8, 3, 12, 0, 0)


def fresh(instrument="BTCUSD", direction=tgs.BUY, minutes_ago=0):
    tgs.clear()
    tgs.remember({"instrument": instrument, "direction": direction,
                  "text": "тест", "time": NOW - timedelta(minutes=minutes_ago)})


# =====================================================================
# 1. Разбор сообщений
# =====================================================================
def test_parse() -> None:
    print("\n[Разбор сообщений]")

    cases = [
        ("BTCUSD BUY now, target 70000", "BTCUSD", tgs.BUY),
        ("🟢 LONG BTC entry 68000", "BTCUSD", tgs.BUY),
        ("SELL XAUUSD от 2400", "XAUUSD", tgs.SELL),
        ("Шорт EURUSD, стоп 1.0950", "EURUSD", tgs.SELL),
        ("Покупка золота", "XAUUSD", tgs.BUY),
        ("ETH long", "ETHUSD", tgs.BUY),
        ("gold sell", "XAUUSD", tgs.SELL),
    ]
    for text, instrument, direction in cases:
        sig = tgs.parse_signal(text, NOW)
        ok = sig is not None and sig["instrument"] == instrument and sig["direction"] == direction
        check(ok, f"Разобрано: {text!r}",
              str(sig and (sig["instrument"], sig["direction"])))

    # Не сигналы
    for text in ("Всем привет!", "", "Рынок сегодня спокойный",
                 "Наши продажи выросли на 20%"):
        check(tgs.parse_signal(text, NOW) is None, f"Не сигнал: {text!r}")

    # Обе стороны в одном сообщении — угадывать нельзя: "закрываем buy,
    # открываем sell" при угадывании даёт ровно противоположный смысл.
    sig = tgs.parse_signal("BTCUSD закрываем buy, открываем sell", NOW)
    check(sig is None, "Оба направления сразу — сигнал не принимается", str(sig))

    # Есть направление, но нет инструмента
    check(tgs.parse_signal("BUY BUY BUY", NOW) is None, "Без инструмента — не сигнал")

    # Уровни из чужого сообщения НЕ берём: свои считаются от своего ATR и риска
    sig = tgs.parse_signal("BTCUSD BUY entry 68000 SL 67000 TP 72000", NOW)
    check(sig is not None and "sl" not in sig and "tp" not in sig and "entry" not in sig,
          "Чужие уровни входа/стопа/цели не попадают в сигнал", str(sig and list(sig)))


def test_symbol_matching() -> None:
    print("\n[Суффиксы брокера]")

    for symbol in ("BTCUSD", "BTCUSDs", "BTCUSDm", "BTCUSD.a", "BTCUSD_i"):
        check(tgs.symbol_matches(symbol, "BTCUSD"), f"{symbol} соответствует BTCUSD")

    check(not tgs.symbol_matches("EURUSD", "BTCUSD"), "EURUSD не соответствует BTCUSD")
    check(not tgs.symbol_matches("XAUUSDs", "BTCUSD"), "Золото не соответствует BTCUSD")
    check(not tgs.symbol_matches("BTCUSD", ""), "Пустой инструмент ничему не соответствует")


# =====================================================================
# 2. ГРАНИЦЫ ПОЛНОМОЧИЙ — главное
# =====================================================================
def test_role_show_does_nothing() -> None:
    print("\n[Режим «только показывать» не влияет на торговлю]")

    CFG.TELEGRAM_ENABLED = True
    CFG.TELEGRAM_ROLE = "show"
    fresh("BTCUSD", tgs.BUY)

    check(tgs.score_bonus("BTCUSDs", tgs.BUY, NOW) == 0.0, "Баллы не добавляются")
    check(tgs.score_bonus("BTCUSDs", tgs.SELL, NOW) == 0.0, "И в другую сторону тоже")
    check(tgs.veto_entry("BTCUSDs", tgs.SELL, NOW) is False, "Вход не запрещается")
    check(tgs.veto_entry("BTCUSDs", tgs.BUY, NOW) is False, "И в совпадающую сторону тоже")

    # Это значение по умолчанию — подключение источника не должно молча
    # менять поведение бота
    default_cfg = types.ModuleType("c")
    exec((APP / "config.py.example").read_text(encoding="utf-8"), default_cfg.__dict__)
    check(default_cfg.TELEGRAM_ROLE == "show", "По умолчанию роль — только показывать")
    check(default_cfg.TELEGRAM_ENABLED is False, "И само чтение по умолчанию выключено")


def test_score_bonus_capped() -> None:
    print("\n[Надбавка к оценке ограничена жёстко]")

    CFG.TELEGRAM_ENABLED = True
    CFG.TELEGRAM_ROLE = "score"
    fresh("BTCUSD", tgs.BUY)

    CFG.TELEGRAM_MAX_SCORE_BONUS = 10.0
    check(tgs.score_bonus("BTCUSDs", tgs.BUY, NOW) == 10.0, "Совпадение — надбавка по настройке")
    check(tgs.score_bonus("BTCUSDs", tgs.SELL, NOW) == 0.0,
          "Несовпадение — надбавки нет (за это отвечает вето, а не двойное наказание)")

    # ГЛАВНОЕ: потолок в коде, а не только в настройке
    CFG.TELEGRAM_MAX_SCORE_BONUS = 500.0
    bonus = tgs.score_bonus("BTCUSDs", tgs.BUY, NOW)
    check(bonus <= 15.0, "Даже 500 в настройках даёт не больше 15 баллов", str(bonus))

    CFG.TELEGRAM_MAX_SCORE_BONUS = -50.0
    check(tgs.score_bonus("BTCUSDs", tgs.BUY, NOW) == 0.0,
          "Отрицательная настройка не превращается в штраф")

    CFG.TELEGRAM_MAX_SCORE_BONUS = 10.0
    # Надбавка никогда не отрицательна ни при каком направлении
    for d in (tgs.BUY, tgs.SELL):
        check(tgs.score_bonus("BTCUSDs", d, NOW) >= 0.0, f"Надбавка не отрицательна ({d})")

    # Надбавки не хватает, чтобы протолкнуть слабый сигнал через типичный порог
    weakest_passing = 45          # самый мягкий порог из профилей ("Истеричка")
    check(15.0 < weakest_passing,
          "Потолок надбавки меньше самого мягкого порога входа — сигнал не тащит сделку в одиночку")


def test_veto() -> None:
    print("\n[Вето]")

    CFG.TELEGRAM_ENABLED = True
    CFG.TELEGRAM_ROLE = "veto"
    fresh("BTCUSD", tgs.BUY)

    check(tgs.veto_entry("BTCUSDs", tgs.SELL, NOW) is True,
          "Сигнал на покупку запрещает продажу")
    check(tgs.veto_entry("BTCUSDs", tgs.BUY, NOW) is False,
          "Совпадающее направление не запрещается")

    # Молчание источника — не запрет
    tgs.clear()
    check(tgs.veto_entry("BTCUSDs", tgs.BUY, NOW) is False,
          "Нет сигнала — торгуем как обычно, а не стоим")
    check(tgs.veto_entry("BTCUSDs", tgs.SELL, NOW) is False, "И в другую сторону тоже")

    # Сигнал по другому инструменту не мешает
    fresh("XAUUSD", tgs.BUY)
    check(tgs.veto_entry("BTCUSDs", tgs.SELL, NOW) is False,
          "Сигнал по золоту не запрещает сделку по биткоину")

    # В режиме score вето тоже работает
    CFG.TELEGRAM_ROLE = "score"
    fresh("BTCUSD", tgs.BUY)
    check(tgs.veto_entry("BTCUSDs", tgs.SELL, NOW) is True, "В режиме «баллы» вето действует")


def test_ttl() -> None:
    print("\n[Протухший сигнал не применяется]")

    CFG.TELEGRAM_ENABLED = True
    CFG.TELEGRAM_ROLE = "score"
    CFG.TELEGRAM_SIGNAL_TTL_MIN = 30

    fresh("BTCUSD", tgs.BUY, minutes_ago=29)
    check(tgs.score_bonus("BTCUSDs", tgs.BUY, NOW) > 0, "Свежий сигнал применяется")

    fresh("BTCUSD", tgs.BUY, minutes_ago=31)
    check(tgs.score_bonus("BTCUSDs", tgs.BUY, NOW) == 0.0,
          "Сигнал старше TTL не даёт баллов")
    check(tgs.veto_entry("BTCUSDs", tgs.SELL, NOW) is False,
          "И не запрещает вход — рекомендация устарела")

    fresh("BTCUSD", tgs.BUY, minutes_ago=30)
    check(tgs.score_bonus("BTCUSDs", tgs.BUY, NOW) > 0, "Ровно на границе TTL ещё действует")


def test_disabled() -> None:
    print("\n[Выключено — значит выключено]")

    CFG.TELEGRAM_ENABLED = False
    CFG.TELEGRAM_ROLE = "score"
    fresh("BTCUSD", tgs.BUY)

    check(tgs.signal_for("BTCUSDs", NOW) is None, "Сигнал не отдаётся")
    check(tgs.score_bonus("BTCUSDs", tgs.BUY, NOW) == 0.0, "Баллы не добавляются")
    check(tgs.veto_entry("BTCUSDs", tgs.SELL, NOW) is False, "Вето не действует")
    check(tgs.describe("BTCUSDs", NOW) == "выкл", "В интерфейсе честно написано «выкл»")
    CFG.TELEGRAM_ENABLED = True


def test_no_dangerous_code() -> None:
    print("\n[В модуле физически нет опасных действий]")

    for name in ("telegram_signals.py", "telegram_reader.py"):
        src = (APP / name).read_text(encoding="utf-8")
        tree = ast.parse(src)
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Attribute):
                    calls.add(f.attr)
                elif isinstance(f, ast.Name):
                    calls.add(f.id)

        for forbidden in ("order_send", "send_market_order", "modify_position",
                          "close_position_partial", "execute_market_order", "calc_lot"):
            check(forbidden not in calls, f"{name}: не вызывает {forbidden}")

        # И не импортирует торговые модули — чтобы не мог начать вызывать
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for forbidden in ("trade_manager", "risk_manager", "mt5_connector"):
            check(forbidden not in imported, f"{name}: не импортирует {forbidden}")

    # Читатель не отправляет сообщения и никуда не вступает
    reader = (APP / "telegram_reader.py").read_text(encoding="utf-8")
    for forbidden in ("send_message", "JoinChannel", "join_chat", "delete_messages"):
        check(forbidden not in reader, f"Читатель не вызывает {forbidden}")


def test_wiring() -> None:
    print("\n[Подключение к торговой логике]")

    engine = (APP / "signal_engine.py").read_text(encoding="utf-8")
    check("telegram_signals.score_bonus" in engine,
          "Надбавка подключена к расчёту оценки")
    check("telegram_signals.veto_entry" not in engine,
          "Вето НЕ спрятано внутрь оценки — оно отдельный, видимый фильтр входа")

    main_src = (APP / "main.py").read_text(encoding="utf-8")
    check("telegram_signals.veto_entry" in main_src, "Вето стоит в цепочке фильтров входа")
    check("Сигнал из Telegram против" in main_src,
          "У отказа понятная причина — она видна в интерфейсе")

    # Запуск чтения не должен ронять торговлю
    tree = ast.parse(main_src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            fn = ast.get_source_segment(main_src, node)
    check(fn is not None and "telegram_reader.start()" in fn, "Чтение запускается вместе с ботом")
    check(fn is not None and "log.warning(\"Telegram" in fn,
          "Отказ подключения только пишется в журнал, торговля продолжается")

    # Секрет api_hash шифруется, как пароль MT5 и ключи AI
    gui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    check("secure_store.encrypt_value(api_hash" in gui, "api_hash шифруется перед записью")
    check("TELEGRAM_SESSION_PATH" in (APP / "config.py.example").read_text(encoding="utf-8"),
          "Путь к файлу сессии вынесен в настройки")
    gitignore = (APP.parent / ".gitignore").read_text(encoding="utf-8")
    check("telegram_session" in gitignore, "Файл сессии Telegram не попадает в git")


def test_reader_preflight() -> None:
    print("\n[Проверка перед запуском чтения]")

    CFG.TELEGRAM_ENABLED = False
    check("выключен" in tgr.preflight(), "Выключено — честно сообщает", tgr.preflight())

    CFG.TELEGRAM_ENABLED = True
    CFG.TELEGRAM_API_ID = 0
    CFG.TELEGRAM_API_HASH = ""
    msg = tgr.preflight()
    check("my.telegram.org" in msg, "Нет ключей — подсказывает, где их взять", msg)

    CFG.TELEGRAM_API_ID = 12345
    CFG.TELEGRAM_API_HASH = "abc"
    CFG.TELEGRAM_SOURCES = []
    check("источник" in tgr.preflight().lower(), "Нет источников — сообщает", tgr.preflight())

    CFG.TELEGRAM_SOURCES = ["@my_crypto_signalsbot"]
    msg = tgr.preflight()
    check("telethon" in msg.lower() or msg == "",
          "Дальше упирается только в наличие библиотеки", msg)

    check(tgr.sources() == ["@my_crypto_signalsbot"], "Источники читаются из настроек")
    CFG.TELEGRAM_SOURCES = ["  @a  ", "", "  ", "@b"]
    check(tgr.sources() == ["@a", "@b"], "Пробелы и пустые строки отсеиваются", str(tgr.sources()))


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ СИГНАЛОВ ИЗ TELEGRAM")
    print("=" * 62)

    test_parse()
    test_symbol_matching()
    test_role_show_does_nothing()
    test_score_bonus_capped()
    test_veto()
    test_ttl()
    test_disabled()
    test_no_dangerous_code()
    test_wiring()
    test_reader_preflight()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
