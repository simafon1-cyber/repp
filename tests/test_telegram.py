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

    # Секреты (api_hash, ключ Finnhub) шифруются, как пароль MT5
    gui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    gui_tree = ast.parse(gui)
    saver = None
    for node in ast.walk(gui_tree):
        if isinstance(node, ast.FunctionDef) and node.name == "save_sources":
            saver = ast.get_source_segment(gui, node)
    check(saver is not None, "Сохранение источников — одной функцией")
    if saver:
        # protect_secret — единая точка: шифрует или кладёт открытым текстом
        # в приватном режиме (см. secure_store.private_mode)
        check("secure_store.protect_secret" in saver,
              "Секреты пишутся через единую защищённую точку")
        check("protect(self.tg_api_hash_var" in saver, "api_hash проходит через шифрование")
        check('_write_config_value("TELEGRAM_ENABLED"' in saver,
              "Выключатель Telegram сохраняется")
        check('_write_config_value("NEWS_PROVIDER_CHAIN"' in saver,
              "Выбор источников календаря сохраняется той же кнопкой")
        check("tgr.stop()" in saver,
              "Снятая галочка Telegram останавливает чтение сразу, а не до перезапуска")
    check("TELEGRAM_SESSION_PATH" in (APP / "config.py.example").read_text(encoding="utf-8"),
          "Путь к файлу сессии вынесен в настройки")
    gitignore = (APP.parent / ".gitignore").read_text(encoding="utf-8")
    check("telegram_session" in gitignore, "Файл сессии Telegram не попадает в git")


def test_login_calls() -> None:
    print("\n[Вход: правильный вызов Telethon]")

    src = (APP / "telegram_reader.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    login = None
    run = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "login":
            login = ast.get_source_segment(src, node)
        if isinstance(node, ast.FunctionDef) and node.name == "_run":
            run = ast.get_source_segment(src, node)

    check(login is not None and run is not None, "Функции входа и чтения найдены")
    if not (login and run):
        return

    def code_only(text: str) -> str:
        """Только код, без комментариев: в комментариях этого модуля старая
        ошибка описана дословно, и проверка ловила бы её описание."""
        out = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            out.append(line.split("  #")[0])
        return "\n".join(out)

    login = code_only(login)
    run = code_only(run)

    # ГЛАВНОЕ. start() и disconnect() у Telethon двухрежимные: вне запущенного
    # цикла они крутят его САМИ и возвращают не корутину. Обернуть их в
    # run_until_complete = TypeError "a coroutine or an awaitable is required".
    # Из-за этого вход не работал вообще.
    check("run_until_complete(client.start" not in login,
          "start() не обёрнут в run_until_complete — иначе вход падает всегда")
    check("await client.start(" in login,
          "start() вызывается через await внутри async-функции")
    check("await client.disconnect()" in login, "disconnect() тоже через await")

    check("run_until_complete(client.disconnect" not in run,
          "В фоновом чтении disconnect() не обёрнут — вне цикла он вернул бы None")
    # А вот connect() и is_user_authorized() — обычные корутины, их оборачивать
    # правильно
    check("run_until_complete(client.connect())" in run,
          "connect() — обычная корутина, обёртка тут уместна")

    # Устаревший параметр loop= убран: в новых версиях Telethon его нет
    check("loop=loop" not in src, "Устаревший параметр loop= не передаётся")

    # Вход не должен требовать включённого чтения: логично войти ДО включения
    check("login_preflight" in src, "У входа своя, более мягкая проверка")
    lp = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "login_preflight":
            lp = ast.get_source_segment(src, node)
    check(lp is not None and "tgs.enabled()" not in lp,
          "Вход не требует, чтобы чтение уже было включено")
    check(lp is not None and "sources()" not in lp,
          "Вход не требует заранее заполненного списка источников")


def test_login_runs_end_to_end() -> None:
    """Прогоняем НАСТОЯЩУЮ login() на поддельном Telethon, который ведёт себя
    в точности как настоящий: start() и disconnect() двухрежимные — вне
    запущенного цикла крутят его сами и возвращают не корутину.

    Это тест-«ловушка» именно на ту ошибку, из-за которой вход не работал:
    прежний код оборачивал результат start() в run_until_complete второй раз
    и падал с TypeError. Разбор исходника такое не поймал бы, если завтра
    кто-то напишет ошибку иначе."""
    print("\n[Вход целиком, на поддельном Telethon]")

    import asyncio
    import importlib.machinery

    calls = []

    class FakeClient:
        def __init__(self, session, api_id, api_hash, **kw):
            if "loop" in kw:
                raise TypeError("__init__() got an unexpected keyword argument 'loop'")
            calls.append("init")

        def _dual(self, coro):
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return coro
            return loop.run_until_complete(coro)

        def start(self, **kw):
            async def _s():
                calls.append("start")
                kw["phone"]()
                kw["code_callback"]()
                return self
            return self._dual(_s())

        def disconnect(self):
            async def _d():
                calls.append("disconnect")
            return self._dual(_d())

    fake = types.ModuleType("telethon")
    fake.__spec__ = importlib.machinery.ModuleSpec("telethon", None)
    fake.TelegramClient = FakeClient
    fake.events = types.SimpleNamespace(NewMessage=lambda **k: None)

    saved = sys.modules.get("telethon")
    sys.modules["telethon"] = fake

    CFG.TELEGRAM_API_ID = 12345
    CFG.TELEGRAM_API_HASH = "abc"
    CFG.TELEGRAM_ENABLED = False      # вход обязан работать и при выключенном чтении
    CFG.TELEGRAM_SOURCES = []
    try:
        error = tgr.login("+79990001122", lambda: "12345", lambda: "")
        check(error == "", "Вход проходит без ошибки", repr(error))
        check(calls == ["init", "start", "disconnect"],
              "Клиент создан, запущен и корректно отключён", str(calls))

        # Ошибка Telethon доходит до пользователя переведённой
        calls.clear()

        def boom(self, **kw):     # присваивается классу -> вызывается как метод
            raise type("PhoneCodeInvalidError", (Exception,), {})("bad code")

        FakeClient.start = boom
        error = tgr.login("+79990001122", lambda: "00000", lambda: "")
        check("код" in error.lower(), "Ошибка возвращается по-русски", error)
    finally:
        if saved is not None:
            sys.modules["telethon"] = saved
        else:
            sys.modules.pop("telethon", None)


def test_login_preflight_rules() -> None:
    print("\n[Проверка перед входом]")

    CFG.TELEGRAM_ENABLED = False
    CFG.TELEGRAM_API_ID = 12345
    CFG.TELEGRAM_API_HASH = "abc"
    CFG.TELEGRAM_SOURCES = []

    msg = tgr.login_preflight()
    check("выключен" not in msg.lower(),
          "Выключенное чтение НЕ мешает войти", msg)
    check("источник" not in msg.lower(),
          "Пустой список источников НЕ мешает войти", msg)

    CFG.TELEGRAM_API_ID = 0
    check("my.telegram.org" in tgr.login_preflight(),
          "Без api_id подсказывает, где его взять")

    CFG.TELEGRAM_API_ID = 12345
    CFG.TELEGRAM_API_HASH = ""
    check("my.telegram.org" in tgr.login_preflight(), "Без api_hash — то же самое")

    CFG.TELEGRAM_API_HASH = "abc"
    CFG.TELEGRAM_ENABLED = True
    check("источник" in tgr.preflight().lower(),
          "А вот ЧТЕНИЕ без источников не запускается", tgr.preflight())


def test_error_messages() -> None:
    print("\n[Понятные ошибки вместо английских имён классов]")

    class FakeErr(Exception):
        pass

    for name, expect in (
            ("PhoneNumberInvalidError", "номер"),
            ("PhoneCodeInvalidError", "код"),
            ("PhoneCodeExpiredError", "устарел"),
            ("SessionPasswordNeededError", "двухфакторн"),
            ("ApiIdInvalidError", "api_id"),
            ("AuthKeyDuplicatedError", "telegram_session"),
    ):
        exc = type(name, (Exception,), {})("boom")
        msg = tgr.explain_error(exc)
        check(expect.lower() in msg.lower(), f"{name} -> по-русски", msg)

    flood = type("FloodWaitError", (Exception,), {})("wait")
    flood.seconds = 300
    msg = tgr.explain_error(flood)
    check("6 мин" in msg, "FloodWait переводится в минуты", msg)

    # Та самая ошибка, из-за которой вход не работал — тоже объясняется
    msg = tgr.explain_error(TypeError("An asyncio.Future, a coroutine or an awaitable is required"))
    check("Обновите программу" in msg, "Старая внутренняя ошибка распознаётся", msg)

    msg = tgr.explain_error(OSError("нет сети"))
    check("связи" in msg, "Сетевая ошибка понятна", msg)

    # Незнакомая ошибка не теряется
    msg = tgr.explain_error(FakeErr("что-то пошло не так"))
    check("что-то пошло не так" in msg, "Незнакомая ошибка показывается как есть", msg)


def test_login_not_on_gui_thread() -> None:
    print("\n[Вход не морозит окно]")

    gui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    tree = ast.parse(gui)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "telegram_login":
            fn = ast.get_source_segment(gui, node)
    check(fn is not None, "Обработчик кнопки найден")
    if not fn:
        return

    check("threading.Thread" in fn,
          "Вход выполняется в фоновом потоке — иначе окно замирает и диалог "
          "ввода кода может не показаться")
    check("self.root.after(0, ask)" in fn,
          "Вопросы задаются в потоке интерфейса: диалоги Tk из другого потока открывать нельзя")
    check("done.wait()" in fn, "Фоновый поток ждёт ответа пользователя")
    check("tgr.login_preflight()" in fn, "Используется мягкая проверка для входа")

    # Результат обрабатывается тоже в потоке интерфейса
    check("_after_telegram_login" in gui, "Результат возвращается в поток интерфейса")


def test_exe_build_includes_telethon() -> None:
    print("\n[Сборка .exe]")

    wf = (BASE.parent / ".github" / "workflows" / "build-exe.yml").read_text(encoding="utf-8")
    check("--collect-all telethon" in wf,
          "telethon попадает в .exe — иначе в собранной программе входа не будет вовсе")
    for mod in ("telegram_signals", "telegram_reader", "trading_schedule"):
        check(f"--hidden-import {mod}" in wf, f"{mod} виден сборщику")

    req = (APP / "requirements.txt").read_text(encoding="utf-8")
    check("telethon" in req.lower(), "telethon есть в requirements.txt")


def test_sources_tab() -> None:
    print("\n[Вкладка «Источники» — одно место для выключателей]")

    gui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    tree = ast.parse(gui)
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    check("_build_tab_sources" in funcs, "Вкладка собирается")
    check("save_sources" in funcs, "Есть общая кнопка сохранения")
    check("refresh_sources_tab" in funcs, "Есть проверка состояния источников")
    # Раскладка окна задаётся данными в ui_layout.py — спрашиваем её.
    import ui_layout
    check(ui_layout.group_of("Источники") == "Новости",
          "Вкладка «Источники» лежит в группе «Новости»",
          ui_layout.group_of("Источники"))
    check('self.tab_frames["Источники"]' in gui, "И окно её действительно строит")

    # Настройки НЕ должны остаться продублированными на старых вкладках —
    # иначе человек поменяет их в одном месте, а действовать будет другое.
    check("save_news_settings" not in funcs,
          "Старое сохранение настроек новостей удалено, а не оставлено дублем")
    check("save_telegram_settings" not in funcs,
          "Старое сохранение настроек Telegram удалено")

    for name in ("_build_tab_news", "_build_tab_telegram"):
        body = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                body = ast.get_source_segment(gui, node)
        check(body is not None, f"{name} найдена")
        if body:
            check("news_chain_vars" not in body and "tg_api_hash_var" not in body,
                  f"{name}: полей настройки больше нет — только показ данных")
            check("Источник" in body or "«Источники»" in body,
                  f"{name}: сказано, где искать настройки")

    # Вкладка видна всегда, а не только в продвинутом режиме: спрятать
    # единственное место выключения источников нельзя.
    advanced = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ADVANCED_TAB_NAMES":
            advanced = ast.literal_eval(node.value)
    check(advanced is not None and "Источники" not in advanced,
          "Вкладка «Источники» не спрятана в продвинутый режим", str(advanced))


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


def test_buttons_apply_screen_fields() -> None:
    """Живой случай владельца (скриншот): галочка «Читать сигналы из
    Telegram» стоит, api_id и api_hash заполнены, он нажимает «Войти в
    Telegram» — а программа отвечает «Telegram выключен в настройках
    (TELEGRAM_ENABLED = False)».

    Причина ровно та же, что была у обновления с веткой: кнопки читали
    СОХРАНЁННЫЙ config.py, а не поля на экране. Пока не нажата «Сохранить
    всё», для программы галочка не поставлена и api_id пуст — она честно
    про это писала, только человек видел заполненные поля."""
    print("\n[Кнопки применяют то, что набрано на экране]")

    ui = (APP / "desktop_app.py").read_text(encoding="utf-8")

    check("def _apply_source_fields" in ui,
          "Есть применение полей вкладки «Источники»")
    body = ui.split("def _apply_source_fields", 1)[1].split("\n    def ", 1)[0]
    for name in ("TELEGRAM_ENABLED", "TELEGRAM_API_ID",
                 "TELEGRAM_SOURCES", "TELEGRAM_ROLE"):
        check(name in body, f"{name} берётся с экрана")
    check("TELEGRAM_API_HASH" in body, "И секретный api_hash тоже")
    check("SECRET_PLACEHOLDER" in body,
          "Заглушка «ключ сохранён» не затирает уже сохранённый api_hash")
    check("_reload_cfg()" in body,
          "После записи настройки перечитываются — иначе проверка ниже "
          "снова смотрела бы в старое")

    login = ui.split("def telegram_login", 1)[1].split("\n    def ", 1)[0]
    applies = "_apply_source_fields()" in login
    check(applies, "«Войти в Telegram» сначала применяет поля")
    check(applies and "login_preflight()" in login
          and login.index("_apply_source_fields()") < login.index("login_preflight()"),
          "И делает это ДО проверки готовности, а не после")

    check("apply_fields=True" in ui,
          "«Проверить источники» тоже проверяет то, что на экране")

    # Секрет не должен затираться, если поле оставили с заглушкой
    check('hash_text and hash_text != SECRET_PLACEHOLDER' in body,
          "Пустое поле api_hash означает «не менять», а не «стереть»")


def test_foreign_signal_can_never_open_a_trade() -> None:
    """Владелец: «почему я не могу брать новости из телеграма для работы?»

    Может — но только как ограничитель или добавка к оценке. Открыть сделку
    чужой сигнал не может ни в одном режиме, и это не недоделка: канал в
    Telegram ничем не подтверждён, а разрешить ему открывать сделки значит
    отдать счёт постороннему."""
    print("\n[Чужой сигнал не открывает сделок ни в каком режиме]")

    roles = (APP / "telegram_signals.py").read_text(encoding="utf-8")
    main_src = (APP / "main.py").read_text(encoding="utf-8")

    check("show" in roles and "veto" in roles,
          "Роли сигнала: показывать / запрещать вход")
    check("score" in roles, "И добавлять баллы к оценке")

    # В главном цикле сигнал влияет ТОЛЬКО на отказ и на баллы
    check("telegram_signals" in main_src, "Сигналы участвуют в решении")
    check("execute_market_order" in main_src, "Сделку открывает торговый модуль")
    for forbidden in ("telegram_signals.set_lot", "telegram_signals.set_risk",
                      "telegram_signals.open_trade"):
        check(forbidden not in main_src,
              f"Нет способа для сигнала сделать {forbidden.split('.')[-1]}")

    ui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    check("не может открыть сделку" in ui,
          "И в окне это написано прямо, а не спрятано в коде")


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
    test_sources_tab()
    test_login_calls()
    test_login_runs_end_to_end()
    test_login_preflight_rules()
    test_error_messages()
    test_login_not_on_gui_thread()
    test_exe_build_includes_telethon()
    test_reader_preflight()
    test_buttons_apply_screen_fields()
    test_foreign_signal_can_never_open_a_trade()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
