#!/usr/bin/env python3
"""Тесты: почему нет сделок, сохранность настроек, автовход в счёт.

Три жалобы владельца, разобранные здесь:

  1. «Перезапустил программу и начала открывать сделки, до этого затишье».
     Две из трёх защит, запрещающих вход, — ЗАЩЁЛКИ, живущие только в
     памяти: лимит просадки считается от пика, накопленного С МОМЕНТА
     ЗАПУСКА, а пауза после серии убытков лежит в состоянии символа.
     Перезапуск обнуляет и то и другое — и выглядит это как «программа
     подвисла, помог перезапуск». Сама защита правильная; неправильно было
     МОЛЧАТЬ о ней и выдавать одну фразу на три разные причины.

  2. «Сбиваются последние настройки». config.py ищется РЯДОМ С .exe.
     Запустили свежескачанную сборку из «Загрузок» — рядом настроек нет,
     программа создаёт заводские.

  3. «И автоматический вход в счёт». Счёт добавляли на вкладке «Счета», а
     торговый цикл входил только по полям вкладки «Брокер».

Запуск:  python3 tests/test_silence_and_settings.py
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
        print(f"  СБОЙ {name}" + (f"  -> {detail}" if detail else ""))


cfg = types.ModuleType("config")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg
CFG = cfg


class _FakeMT5(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return name


sys.modules["MetaTrader5"] = _FakeMT5("MetaTrader5")

import risk_manager as rm          # noqa: E402
import settings_backup as sb       # noqa: E402
from state import AccountState, SymbolState   # noqa: E402


# =====================================================================
# 1. Почему нет сделок
# =====================================================================
def test_block_reason_is_specific() -> None:
    print("\n[Причина запрета названа по имени, а не «лимит/просадка/пауза»]")

    saved = {n: getattr(CFG, n) for n in
             ("USE_DAILY_LOSS_LIMIT", "USE_MAX_DRAWDOWN_LIMIT", "RISK_PROFILE")}
    acc = AccountState()
    sym = SymbolState(symbol="EURUSD")
    try:
        CFG.USE_DAILY_LOSS_LIMIT = False
        CFG.USE_MAX_DRAWDOWN_LIMIT = False

        check(rm.trading_block_reason(acc, sym, 100.0) == "",
              "Ничего не сработало — вход разрешён")
        check(rm.trading_allowed(acc, sym, 100.0) is True,
              "И старая проверка согласна")

        # --- Просадка: защёлка, снимаемая перезапуском ---
        CFG.USE_MAX_DRAWDOWN_LIMIT = True
        acc.peak_equity = 100.0
        limit = CFG.RISK_PROFILES[CFG.RISK_PROFILE]["max_drawdown_pct"]
        equity = 100.0 * (1 - (limit + 5) / 100.0)
        reason = rm.trading_block_reason(acc, sym, equity)
        check("просадк" in reason.lower(), "Названа просадка", reason)
        check("100" in reason, "Назван максимум, от которого считается", reason)
        check("перезапуск" in reason.lower(),
              "Сказано главное: перезапуск снимает запрет — и это не решение",
              reason)
        check("USE_MAX_DRAWDOWN_LIMIT" in reason,
              "Названа настройка, которой это выключается", reason)
        check(rm.trading_allowed(acc, sym, equity) is False,
              "Вход при этом действительно запрещён")

        # Перезапуск = новый AccountState с нулевым пиком. Именно поэтому
        # после перезапуска «сделки снова пошли».
        fresh = AccountState()
        check(rm.trading_block_reason(fresh, sym, equity) == "",
              "После перезапуска запрета нет — пик обнулился")
        CFG.USE_MAX_DRAWDOWN_LIMIT = False

        # --- Пауза после серии убытков ---
        sym.pause_until = datetime.now() + timedelta(minutes=20)
        reason = rm.trading_block_reason(acc, sym, 100.0)
        check("пауза" in reason.lower(), "Названа пауза", reason)
        check("PAUSE_MINUTES_AFTER_LOSS_STREAK" in reason,
              "И настройка, которой она снимается", reason)
        sym.pause_until = None

        # --- Дневной лимит ---
        CFG.USE_DAILY_LOSS_LIMIT = True
        acc.day_start_equity = 100.0
        deep = 100.0 * (1 - (CFG.RISK_PROFILES[CFG.RISK_PROFILE]["daily_loss_limit_pct"] + 5) / 100.0)
        reason = rm.trading_block_reason(acc, sym, deep)
        check("дневной" in reason.lower(), "Назван дневной лимит", reason)
        check("торгового дня" in reason,
              "Сказано, когда он сам сбросится", reason)
    finally:
        for name, value in saved.items():
            setattr(CFG, name, value)


def test_reason_reaches_the_screen() -> None:
    print("\n[Причина доходит до главной вкладки]")
    src = (APP / "main.py").read_text(encoding="utf-8")
    check("rm.trading_block_reason(" in src,
          "Главный цикл спрашивает КОНКРЕТНУЮ причину")
    check("(лимит/просадка/пауза)" not in src,
          "Прежней общей фразы на три причины больше нет")

    ui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    check("_silence_reasons" in ui, "Причины собираются для показа")
    body = ui.split("def _silence_reasons", 1)[1].split("\n    def ", 1)[0]
    check("reject_reason" in body, "Берутся причины отказа по парам")
    check('startswith("OK")' in body,
          "Успешные проходы за причину молчания не считаются")
    check("[:3]" in body, "Показываем главные причины, а не простыню")
    # Раньше это была надпись (trade_warning_var), теперь — небольшое окошко
    # с ползунком: список из сотен отобранных пар растягивал надпись на
    # пол-экрана. Проверяем сам ФАКТ вывода, а не способ.
    #
    # И проверяем его ПО ДЕРЕВУ КОДА, а не по точному тексту вызова.
    # Прежняя запись искала строку "_show_warnings(problems)" — то есть
    # ровно один способ написать вызов. Стоило добавить второй аргумент
    # (строку состояния торговли), и проверка упала, хотя причины молчания
    # доходят до «Обзора» ровно как раньше. Это проверка способа, а не
    # факта — против собственного пояснения выше.
    import ast as _ast
    _дерево = _ast.parse(ui)
    _доходят = False
    for _у in _ast.walk(_дерево):
        if not (isinstance(_у, _ast.Call)
                and isinstance(_у.func, _ast.Attribute)
                and _у.func.attr == "_show_warnings"):
            continue
        _имена = {_а.id for _а in _у.args if isinstance(_а, _ast.Name)}
        if "problems" in _имена:
            _доходят = True
    check(_доходят, "И выводятся на вкладку «Обзор»")
    check("def _show_warnings" in ui, "Вывод собран в одном месте")


# =====================================================================
# 2. Настройки не теряются
# =====================================================================
def test_settings_survive_new_folder() -> None:
    print("\n[Настройки переживают запуск из другой папки]")

    saved_storage = sb.storage_dir
    with tempfile.TemporaryDirectory() as store, \
            tempfile.TemporaryDirectory() as first, \
            tempfile.TemporaryDirectory() as second:
        sb.storage_dir = lambda: store
        try:
            # Первый компьютер/папка: настройки есть, копия делается
            original = Path(first) / "config.py"
            original.write_text("MT5_LOGIN = 110486921\nSYMBOLS = ['EURUSD']\n",
                                encoding="utf-8")
            made = sb.save(str(original))
            check(bool(made), "Копия настроек создана", made)
            check(os.path.exists(sb.backup_path()), "И лежит в постоянной папке")

            # Вторая папка (скачали новую сборку в «Загрузки») — своего
            # config.py нет
            target = Path(second) / "config.py"
            restored = sb.restore_if_missing(str(target))
            check(bool(restored), "Настройки восстановлены на новом месте")
            check(target.read_text(encoding="utf-8") ==
                  original.read_text(encoding="utf-8"),
                  "И это ТЕ ЖЕ настройки, а не заводские")

            # ГЛАВНОЕ: существующий config.py не трогается никогда
            target.write_text("MT5_LOGIN = 999\n", encoding="utf-8")
            check(sb.restore_if_missing(str(target)) == "",
                  "Существующие настройки не перезаписываются")
            check("999" in target.read_text(encoding="utf-8"),
                  "И остаются ровно такими, какими их оставил человек")

            # Копии нет — обычный первый запуск, ничего не происходит
            os.remove(sb.backup_path())
            missing = Path(second) / "нет.py"
            check(sb.restore_if_missing(str(missing)) == "",
                  "Без копии восстанавливать нечего — это не ошибка")
            check(sb.save(str(missing)) == "",
                  "И сохранять несуществующий файл тоже нечего")
        finally:
            sb.storage_dir = saved_storage


def test_backup_folder_is_stable() -> None:
    """Папка копии не должна зависеть от того, откуда запущен .exe — иначе
    смысла в ней нет."""
    print("\n[Копия лежит в постоянной папке пользователя]")
    saved_frozen = getattr(sys, "frozen", None)
    saved_exe = sys.executable
    try:
        where = []
        for path in ("C:/Downloads/AI_Scalper_Pro.exe",
                     "D:/Программы/AI_Scalper/AI_Scalper_Pro.exe"):
            sys.frozen = True
            sys.executable = path
            where.append(sb.storage_dir())
            check(sb.app_dir() == os.path.dirname(path),
                  f"Рабочая папка следует за .exe: {sb.app_dir()}")
        check(where[0] == where[1],
              "А папка копии одна и та же, откуда бы ни запускали", str(where))
    finally:
        if saved_frozen is None:
            sys.frozen = False
            del sys.frozen
        else:
            sys.frozen = saved_frozen
        sys.executable = saved_exe

    ui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    check("settings_backup.restore_if_missing()" in ui,
          "Восстановление вызывается при запуске программы")
    check("settings_backup.save()" in ui,
          "И копия обновляется после изменения настроек")


# =====================================================================
# 3. Автоматический вход в счёт
# =====================================================================
def test_auto_login_uses_accounts_tab() -> None:
    print("\n[Автовход берёт счёт со вкладки «Счета»]")
    import mt5_connector as mt5c
    import accounts as accounts_module

    src = (APP / "mt5_connector.py").read_text(encoding="utf-8")
    check("def auto_login_account" in src, "Автовход есть")
    body = src.split("def connect(", 1)[1][:900]
    check("auto_login_account()" in body, "И используется при подключении")
    check("if login <= 0:" in body,
          "Только когда на вкладке «Брокер» логин не заполнен")

    # Пустой список счетов — работаем как раньше, без выдумок
    saved_store = accounts_module.AccountStore
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "accounts.json"

        class _Store(accounts_module.AccountStore):
            def __init__(self, path=None):
                super().__init__(empty)

        accounts_module.AccountStore = _Store
        try:
            check(mt5c.auto_login_account() is None,
                  "Счетов нет — автовходу неоткуда взяться")

            # Настроенный счёт — берётся
            real = accounts_module.AccountStore(empty)
            acc = accounts_module.Account(name="Демо", login=110486921,
                                          server="MetaQuotes-Demo",
                                          password="пароль")
            real.add(acc, "", getattr(CFG, "SECURITY_SALT", "") or "")
            picked = mt5c.auto_login_account()
            check(picked is not None, "Настроенный счёт найден")
            if picked:
                check(picked.login == 110486921, "И это он", str(picked.login))

            # Выключенный счёт не берём
            acc.enabled = False
            real.save("", getattr(CFG, "SECURITY_SALT", "") or "")
            check(mt5c.auto_login_account() is None,
                  "Выключенный счёт для автовхода не годится")
        finally:
            accounts_module.AccountStore = saved_store


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ: ПОЧЕМУ НЕТ СДЕЛОК, НАСТРОЙКИ, АВТОВХОД")
    print("=" * 62)

    test_block_reason_is_specific()
    test_reason_reaches_the_screen()
    test_settings_survive_new_folder()
    test_backup_folder_is_stable()
    test_auto_login_uses_accounts_tab()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
