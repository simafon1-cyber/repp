#!/usr/bin/env python3
"""Тесты живучести торгового цикла.

Жалоба владельца: «он перестаёт открывать сделки через некоторое время,
работает пару часов и всё, потом надо перезапуск приложения».

Причина нашлась в main.py. Вызов быстрого монитора позиций стоял СНАРУЖИ
try/except главного цикла, а внешний перехват ловил только KeyboardInterrupt.
Любая неожиданная ошибка внутри монитора улетала мимо всей защиты: цикл
выходил через finally, поток умирал, окно продолжало показывать «Работает»,
и сделки не открывались до перезапуска программы.

Что здесь проверяется:
  1. Ни один вызов внутри цикла не остался снаружи защиты от ошибок.
  2. У цикла есть ПУЛЬС — отметка о пройденном круге. Живого потока мало:
     поток может застрять внутри зависшего запроса к терминалу, и снаружи
     это выглядит точно так же — сделок нет.
  3. Сторож поднимает цикл сам, но НЕ трогает его, когда человек сам нажал
     «Стоп».

Запуск:  python3 tests/test_bot_alive.py
"""

from __future__ import annotations

import ast
import sys
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
# main.py при импорте запоминает путь к config, чтобы замечать правки на
# лету — подставляем эталон вместе с путём к нему.
cfg.__file__ = str(APP / "config.py.example")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg


class _FakeMT5(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return name


sys.modules["MetaTrader5"] = _FakeMT5("MetaTrader5")

import main as bot   # noqa: E402


def test_nothing_escapes_the_loop() -> None:
    """Внутри while True не должно остаться ни одного вызова, чья ошибка
    выходит наружу: одна такая ошибка выключает бота до перезапуска."""
    print("\n[Из главного цикла ошибка не может улететь наружу]")

    src = (APP / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    main_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            main_fn = node
    check(main_fn is not None, "Функция main найдена")
    if main_fn is None:
        return

    loops = [n for n in ast.walk(main_fn) if isinstance(n, ast.While)]
    check(loops, "Главный цикл найден")
    if not loops:
        return
    loop = max(loops, key=lambda n: len(list(ast.walk(n))))

    # Каждый оператор тела цикла обязан лежать внутри try — либо сам быть try
    unguarded = []
    for stmt in loop.body:
        if isinstance(stmt, (ast.Try, ast.If, ast.Pass)):
            continue
        unguarded.append(type(stmt).__name__ + f" (строка {stmt.lineno})")
    check(not unguarded,
          "Каждый шаг цикла защищён от ошибок", "; ".join(unguarded))

    # Монитор позиций — то самое место, где была дыра
    calls = [n for n in ast.walk(loop)
             if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "_fast_position_monitor"]
    check(calls, "Монитор позиций вызывается из цикла")
    for call in calls:
        inside = any(call.lineno >= t.lineno and call.lineno <= (t.end_lineno or t.lineno)
                     for t in ast.walk(loop) if isinstance(t, ast.Try))
        check(inside, "Монитор позиций вызывается ВНУТРИ try/except")

    # И внешний перехват обязан ловить не только Ctrl+C
    handlers = []
    for node in ast.walk(main_fn):
        if isinstance(node, ast.Try):
            for h in node.handlers:
                name = getattr(h.type, "id", "") or getattr(
                    getattr(h.type, "attr", None), "__str__", lambda: "")()
                handlers.append(name)
    check("Exception" in handlers,
          "Аварийный выход цикла ловится и попадает в лог, а не уходит молча",
          str(handlers))


def test_heartbeat() -> None:
    """Пульс — доказательство, что цикл РАБОТАЕТ, а не просто существует."""
    print("\n[Пульс торгового цикла]")

    check(hasattr(bot, "last_heartbeat"), "Пульс есть")
    check(hasattr(bot, "seconds_since_heartbeat"), "И его возраст считается")

    saved = bot._heartbeat["at"]
    try:
        bot._heartbeat["at"] = 0.0
        check(bot.seconds_since_heartbeat() == 0.0,
              "Цикл ещё ни разу не отработал — не считаем это молчанием")

        bot._heartbeat["at"] = time.time()
        check(bot.seconds_since_heartbeat() < 1.0,
              "Только что пройденный круг — свежий пульс")

        bot._heartbeat["at"] = time.time() - 600
        check(599 < bot.seconds_since_heartbeat() < 601,
              "Молчание считается в секундах",
              str(bot.seconds_since_heartbeat()))
    finally:
        bot._heartbeat["at"] = saved

    # Пульс должен ставиться в КОНЦЕ круга, а не в начале: иначе он значил бы
    # «круг начался», а зависший круг так и остался бы незамеченным
    src = (APP / "main.py").read_text(encoding="utf-8")
    body = src.split("while True:", 1)[1].split("except KeyboardInterrupt", 1)[0]
    check('_heartbeat["at"] = time.time()' in body,
          "Пульс ставится внутри цикла")
    before_beat = body.split('_heartbeat["at"] = time.time()', 1)[0]
    check("process_symbol" in before_beat,
          "И ставится ПОСЛЕ обхода символов, а не до него")


def test_watchdog_decision() -> None:
    print("\n[Сторож: когда поднимать цикл заново]")
    limit = 180

    check(bot.watchdog_reason(True, False, 0, limit) != "",
          "Поток умер — поднимаем заново")
    check("завершился" in bot.watchdog_reason(True, False, 0, limit),
          "И называем причину", bot.watchdog_reason(True, False, 0, limit))

    check(bot.watchdog_reason(True, True, 600, limit) != "",
          "Поток жив, но молчит 10 минут — тоже поднимаем")
    check("600" in bot.watchdog_reason(True, True, 600, limit),
          "Сказано, сколько именно молчал",
          bot.watchdog_reason(True, True, 600, limit))

    check(bot.watchdog_reason(True, True, 5, limit) == "",
          "Работающий цикл не трогаем")
    check(bot.watchdog_reason(True, True, limit, limit) == "",
          "Ровно на пороге ещё не паникуем")

    # ГЛАВНОЕ: нажатый человеком «Стоп» — не поломка
    check(bot.watchdog_reason(False, False, 99999, limit) == "",
          "Человек нажал «Стоп» — сторож молчит и не запускает бота сам")
    check(bot.watchdog_reason(False, True, 0, limit) == "",
          "И при остановке живого потока тоже")

    check(bot.watchdog_reason(True, True, 99999, 0) == "",
          "Нулевой порог = сторож по молчанию выключен")


def test_watchdog_wired_into_program() -> None:
    print("\n[Сторож подключён к программе]")
    ui = (APP / "desktop_app.py").read_text(encoding="utf-8")

    check("_watchdog_tick" in ui, "Сторож есть")
    check("self._watchdog_tick()" in ui,
          "И вызывается периодически из обновления окна")
    check("watchdog_reason" in ui, "Решение принимает проверенная функция")
    check("_bot_should_run" in ui, "Намерение «бот должен работать» хранится")

    stop = ui.split("def stop_bot", 1)[1][:300]
    check("_bot_should_run = False" in stop,
          "«Стоп» снимает намерение — сторож не поднимет бота обратно")

    body = ui.split("def _watchdog_tick", 1)[1].split("\n    def ", 1)[0]
    check("start_bot()" in body, "При поломке цикл поднимается заново")
    check("stop_event.set()" in body,
          "Зависший поток сначала просят остановиться")

    # Интерфейс из чужого потока не трогаем — это само по себе источник
    # зависаний, а модальное окно с ошибкой раньше некому было закрыть
    run = ui.split("def _run_bot", 1)[1].split("\n    def ", 1)[0]
    check("messagebox" not in run,
          "Торговый поток не открывает модальных окон")
    check("self.root.after" in run,
          "Всё, что нужно показать, передаётся в поток окна")


def test_runtime_events() -> None:
    """Владелец писал одно и то же несколько раз: «останавливается,
    перезапустил — сделки пошли». Каждый раз разбирать приходилось по коду:
    от самой программы следов не оставалось — всё уходило в scalper.log,
    который никто не открывает, а окно показывало то же «Работает»."""
    print("\n[Лента происшествий: что случилось с программой]")

    import runtime_events as ev
    import tempfile, os as _os

    saved_dir = ev.app_dir
    with tempfile.TemporaryDirectory() as tmp:
        ev.app_dir = lambda: tmp
        ev.clear()
        try:
            check(ev.describe() == "", "Пока ничего не случилось — молчим")

            ev.record("связь", "потеряна связь с терминалом MT5")
            ev.record("сторож", "поток завершился — цикл перезапущен")
            text = ev.describe()
            check("связь" in text and "сторож" in text,
                  "Оба события видны", text)
            check(text.index("сторож") < text.index("связь"),
                  "Свежее событие показано первым")

            check(_os.path.exists(ev.path()),
                  "Лента сохраняется файлом — переживёт закрытие окна")

            # Перезапуск программы: события прошлого запуска читаются обратно
            ev._events.clear()
            check(ev.describe() == "", "После очистки памяти пусто")
            loaded = ev.load()
            check(len(loaded) == 2,
                  "События прошлого запуска прочитаны", str(len(loaded)))
            check("сторож" in ev.describe(), "И снова видны в окне")

            # Лента не растёт бесконечно
            for i in range(ev.MAX_EVENTS + 20):
                ev.record("тест", f"событие {i}")
            check(len(ev.recent(999)) <= ev.MAX_EVENTS,
                  "Старые события вытесняются", str(len(ev.recent(999))))

            # Испорченный файл не должен ронять программу
            with open(ev.path(), "w", encoding="utf-8") as f:
                f.write("{не json")
            check(ev.load() == [], "Испорченный файл — пустая лента, без падения")
        finally:
            ev.app_dir = saved_dir
            ev._events.clear()

    # События действительно записываются там, где происходят
    src = (APP / "main.py").read_text(encoding="utf-8")
    check("runtime_events.record" in src, "Главный цикл пишет события")
    for marker in ('"связь"', '"ошибка"', '"остановка"'):
        check(marker in src, f"Записывается событие {marker}")

    ui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    check('runtime_events.record("сторож"' in ui,
          "Сторож отмечает свои перезапуски")
    check("runtime_events.describe(3)" in ui,
          "И лента показывается на вкладке «Обзор»")
    check("runtime_events.load()" in ui,
          "События прошлого запуска подхватываются при старте")


def main_run() -> int:
    print("=" * 62)
    print("ТЕСТЫ: ТОРГОВЫЙ ЦИКЛ НЕ УМИРАЕТ МОЛЧА")
    print("=" * 62)

    test_nothing_escapes_the_loop()
    test_heartbeat()
    test_watchdog_decision()
    test_watchdog_wired_into_program()
    test_runtime_events()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main_run())
