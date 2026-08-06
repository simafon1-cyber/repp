#!/usr/bin/env python3
"""Тесты самоналадки источника новостей.

Владелец: «там всё запущено и прописано, проверь и автоматизируй, пусть сам
всё запускает».

Главное, что здесь проверяется:
  1. Программа сама СТАВИТ и СОБИРАЕТ сервис календаря, если его нет.
  2. Свежий файл календаря — единственное доказательство, что сервис
     реально запущен. Установленный и собранный сервис ещё ничего не
     значит: он может лежать и не работать.
  3. Программа не обещает того, чего не может: первый запуск сервиса
     делается в терминале руками, и текст говорит об этом прямо, а не
     «сейчас всё само заработает».
  4. В чужой терминал без нужды не лезем: чиним только когда включён
     новостной режим.

Запуск:  python3 tests/test_news_autostart.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
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
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg
CFG = cfg


class _FakeMT5(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return name


sys.modules["MetaTrader5"] = _FakeMT5("MetaTrader5")

import news_autostart as na   # noqa: E402
import mt5_install            # noqa: E402


def make_terminal(root: Path, installed: bool, compiled: bool) -> str:
    """Поддельная папка терминала с сервисом или без."""
    services = root / "MQL5" / "Services"
    services.mkdir(parents=True, exist_ok=True)
    if installed:
        (services / na.SERVICE_SOURCE).write_text("// сервис", encoding="utf-8")
    if compiled:
        (services / na.SERVICE_COMPILED).write_bytes(b"\x00")
    return str(root)


def test_service_state() -> None:
    print("\n[Состояние сервиса в терминале]")
    with tempfile.TemporaryDirectory() as tmp:
        empty = make_terminal(Path(tmp) / "empty", False, False)
        state = na.service_state(empty)
        check(state["installed"] is False, "Пустой терминал: сервиса нет")
        check(state["compiled"] is False, "И собранного тоже нет")

        half = make_terminal(Path(tmp) / "half", True, False)
        state = na.service_state(half)
        check(state["installed"] is True, "Исходник сервиса виден")
        check(state["compiled"] is False,
              "Но не собран — значит терминал его не запустит")

        full = make_terminal(Path(tmp) / "full", True, True)
        state = na.service_state(full)
        check(state["installed"] and state["compiled"],
              "Установлен и собран")


def test_calendar_freshness() -> None:
    """Свежесть файла — ЕДИНСТВЕННОЕ доказательство, что сервис работает."""
    print("\n[Свежесть календаря]")
    import news_providers as npv

    saved = npv.mt5_calendar_path
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "calendar_export.json"
        npv.mt5_calendar_path = lambda: str(path)
        try:
            state = na.calendar_state()
            check(state["exists"] is False, "Файла нет — и это видно")
            check(state["fresh"] is False, "Несуществующий файл не свежий")

            path.write_text("{}", encoding="utf-8")
            state = na.calendar_state()
            check(state["exists"] and state["fresh"],
                  "Только что записанный файл — свежий")

            old = time.time() - npv.MT5_CALENDAR_MAX_AGE_SECONDS - 60
            os.utime(path, (old, old))
            state = na.calendar_state()
            check(state["exists"] is True, "Старый файл на месте")
            check(state["fresh"] is False,
                  "Но он протух — сервис остановлен или терминал закрыт")
            check(state["age_seconds"] > npv.MT5_CALENDAR_MAX_AGE_SECONDS,
                  "Возраст файла посчитан")

            # Путь узнать не удалось (нет связи с терминалом) — не падаем
            npv.mt5_calendar_path = lambda: (_ for _ in ()).throw(
                RuntimeError("нет связи с терминалом"))
            state = na.calendar_state()
            check(state["error"] != "", "Нет связи — сказано, а не падение",
                  state["error"])
        finally:
            npv.mt5_calendar_path = saved


def test_check_and_texts() -> None:
    print("\n[Проверка цепочки и понятный ответ]")
    import news_providers as npv

    saved_find = mt5_install.find_terminals
    saved_path = npv.mt5_calendar_path
    with tempfile.TemporaryDirectory() as tmp:
        cal = Path(tmp) / "calendar_export.json"
        npv.mt5_calendar_path = lambda: str(cal)
        try:
            # 1. Терминалов нет вовсе
            mt5_install.find_terminals = lambda: []
            state = na.check()
            check(state["terminals"] == [], "Терминалы не найдены")
            check("не найден" in na.describe(state),
                  "Так и сказано", na.describe(state))

            # 2. Терминал есть, сервиса нет -> надо ставить, и это делает сама
            term = make_terminal(Path(tmp) / "t1", False, False)
            mt5_install.find_terminals = lambda: [term]
            state = na.check()
            check(state["needs_install"] is True, "Видно, что надо ставить")
            check(state["ready"] is False, "И готовности нет")
            check("поставит и соберёт его сама" in na.describe(state),
                  "Обещано ровно то, что программа умеет", na.describe(state))

            # 3. Поставлен и собран, но ни разу не запускался
            term = make_terminal(Path(tmp) / "t2", True, True)
            mt5_install.find_terminals = lambda: [term]
            state = na.check()
            check(state["needs_install"] is False, "Ставить больше нечего")
            check(state["needs_manual_start"] is True,
                  "Остался ручной шаг — первый запуск сервиса")
            text = na.describe(state)
            check("Навигатор" in text and "Запустить" in text,
                  "Сказано, куда нажать", text)
            check("сам" in text and "при каждом старте" in text,
                  "И что дальше это делать не придётся", text)

            # 4. Всё работает
            cal.write_text("{}", encoding="utf-8")
            state = na.check()
            check(state["ready"] is True, "Свежий календарь — цепочка готова")
            check("работает" in na.describe(state),
                  "Так и написано", na.describe(state))

            # 5. Файл протух — виноват не «нет новостей», а остановленный сервис
            old = time.time() - 7200
            os.utime(cal, (old, old))
            state = na.check()
            check(state["ready"] is False, "Протухший календарь не считается")
            check("остановлен" in na.describe(state),
                  "Названа настоящая причина", na.describe(state))
        finally:
            mt5_install.find_terminals = saved_find
            npv.mt5_calendar_path = saved_path


def test_repair_installs_itself() -> None:
    print("\n[Программа ставит сервис сама]")
    saved_find = mt5_install.find_terminals
    saved_install = mt5_install.install_all
    calls = []

    with tempfile.TemporaryDirectory() as tmp:
        try:
            # Сервиса нет -> install_all вызывается
            term = make_terminal(Path(tmp) / "t", False, False)
            mt5_install.find_terminals = lambda: [term]
            mt5_install.install_all = lambda progress=None: (
                calls.append("install") or
                {"copied": 3, "compiled": 3, "errors": []})
            done = na.repair()
            check(calls == ["install"], "Установка запущена сама", str(calls))
            check(any("скопирован" in d for d in done),
                  "Отчитались, что сделали", str(done))

            # Уже стоит -> повторно не ставим
            calls.clear()
            term = make_terminal(Path(tmp) / "t2", True, True)
            mt5_install.find_terminals = lambda: [term]
            na.repair()
            check(calls == [], "Установленный сервис заново не ставим")

            # Терминалов нет -> ничего не трогаем
            calls.clear()
            mt5_install.find_terminals = lambda: []
            check(na.repair() == [], "Без терминала делать нечего")
            check(calls == [], "И установку не запускаем")
        finally:
            mt5_install.find_terminals = saved_find
            mt5_install.install_all = saved_install


def test_only_when_news_mode() -> None:
    """В чужой терминал без нужды не лезем."""
    print("\n[Чиним только когда новости нужны]")
    saved_find = mt5_install.find_terminals
    saved_install = mt5_install.install_all
    saved_mode = CFG.TRADING_MODE
    calls = []

    with tempfile.TemporaryDirectory() as tmp:
        term = make_terminal(Path(tmp) / "t", False, False)
        mt5_install.find_terminals = lambda: [term]
        mt5_install.install_all = lambda progress=None: (
            calls.append("install") or {"copied": 1, "compiled": 1, "errors": []})
        try:
            CFG.TRADING_MODE = CFG.TradingMode.SCALPING
            na.reset_checks()
            state = na.ensure_ready(force=True)
            check(state["news_mode"] is False, "Новостной режим выключен")
            check(calls == [], "В терминал не полезли", str(calls))

            CFG.TRADING_MODE = CFG.TradingMode.NEWS_TRADING
            na.reset_checks()
            na.ensure_ready(force=True)
            check(calls == ["install"], "Новостной режим — ставим сами", str(calls))

            # Режим BOTH тоже считается новостным
            calls.clear()
            CFG.TRADING_MODE = CFG.TradingMode.BOTH
            na.reset_checks()
            check(na.check()["news_mode"] is True, "Режим BOTH тоже новостной")

            # Проверка не гоняется на каждом вызове
            calls.clear()
            CFG.TRADING_MODE = CFG.TradingMode.NEWS_TRADING
            na.reset_checks()
            na.ensure_ready(force=True)
            first = len(calls)
            na.ensure_ready()          # сразу следом, без force
            check(len(calls) == first,
                  "Повторная проверка не долбит терминал", str(calls))
        finally:
            mt5_install.find_terminals = saved_find
            mt5_install.install_all = saved_install
            CFG.TRADING_MODE = saved_mode
            na.reset_checks()


def test_honest_about_manual_step() -> None:
    """Нельзя обещать «запустится само» там, где это технически невозможно:
    человек будет ждать сделок, которых не будет."""
    print("\n[Про ручной шаг сказано честно]")
    src = (APP / "news_autostart.py").read_text(encoding="utf-8")
    check("нет способа запустить сервис снаружи" in src,
          "В коде объяснено, почему запуск не автоматизирован")

    ui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    check("news_autostart.ensure_ready" in ui,
          "Проверка вызывается при запуске программы")
    check("fix_news_source" in ui, "Есть кнопка «Проверить и починить»")
    check("news_source_var" in ui, "Состояние источника видно на вкладке")


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ: ИСТОЧНИК НОВОСТЕЙ НАЛАЖИВАЕТСЯ САМ")
    print("=" * 62)

    test_service_state()
    test_calendar_freshness()
    test_check_and_texts()
    test_repair_installs_itself()
    test_only_when_news_mode()
    test_honest_about_manual_step()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
