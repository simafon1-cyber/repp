#!/usr/bin/env python3
"""Тесты: вторая копия программы не запускается, автозапуск чинит сам себя,
рамка «Внимание» не занимает пол-экрана, тем стало три.

ОТКУДА ЗАДАЧИ — всё из сообщений владельца за один вечер:
  «при запуске программы включается две»
  «не работает автозапуск программы с windows»
  «уменьши окно Внимание, пусть будет небольшим с ползунком, и красным
   выделяется только критические ошибки»
  «сделай выбор тем, и добавь тему в стиле macOS»

Запуск:  python3 tests/test_single_instance.py
"""

from __future__ import annotations

import ast
import os
import sys
import tempfile
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
sys.modules["MetaTrader5"] = types.ModuleType("MetaTrader5")

import single_instance as si     # noqa: E402
import ui_theme                  # noqa: E402


def test_second_copy_does_not_start() -> None:
    """Две копии — это не просто неудобно. Обе подключаются к одному
    терминалу, обе ведут одни и те же позиции и обе двигают стоп-лосс,
    каждая считая, что она одна."""
    print("\n[Вторая копия не запускается]")
    with tempfile.TemporaryDirectory() as folder:
        lock = os.path.join(folder, "running.lock")

        check(si.acquire(lock) is True, "Первая копия замок берёт")
        check(si.read_owner(lock) == os.getpid(), "И записывает свой номер")

        # Чужой ЖИВОЙ процесс — вторую копию не пускаем
        with open(lock, "w", encoding="utf-8") as f:
            f.write("4242")
        check(si.acquire(lock, alive=lambda pid: True) is False,
              "Копия уже работает — вторая не запускается")
        check(si.read_owner(lock) == 4242,
              "И чужой замок не перехватывается", str(si.read_owner(lock)))

        # Процесса нет — замок брошенный, забираем. Иначе один аварийный выход
        # запер бы человека снаружи от собственной программы навсегда.
        check(si.acquire(lock, alive=lambda pid: False) is True,
              "Брошенный замок забирается — иначе программу не открыть уже НИКОГДА")
        check(si.read_owner(lock) == os.getpid(), "Теперь замок наш")

        si.release(lock)
        check(not os.path.exists(lock), "Свой замок отпускается при выходе")

        # Чужой замок не удаляем: его хозяин ещё работает
        with open(lock, "w", encoding="utf-8") as f:
            f.write("4242")
        si.release(lock)
        check(os.path.exists(lock), "Чужой замок не трогаем")


def test_lock_never_blocks_the_program() -> None:
    """Замок — вспомогательная вещь. Если он не пишется, программа обязана
    работать: человеку нужна торговля, а не наши файлы."""
    print("\n[Замок не мешает работать]")
    bad = os.path.join(tempfile.gettempdir(), "нет-такой-папки", "x.lock")
    check(si.acquire(bad) is True, "Не удалось записать замок — всё равно работаем")

    check(si.read_owner(os.path.join(tempfile.gettempdir(), "нет-файла")) == 0,
          "Нет файла — хозяина нет")
    with tempfile.TemporaryDirectory() as folder:
        lock = os.path.join(folder, "l")
        with open(lock, "w", encoding="utf-8") as f:
            f.write("не число")
        check(si.read_owner(lock) == 0, "Мусор в замке = хозяина нет")
        check(si.acquire(lock) is True, "И он не мешает запуску")

    check(si.process_alive(0) is False, "Нулевой номер процесса — не живой")
    check(si.process_alive(-5) is False, "Отрицательный — тоже")
    check(si.process_alive("мусор") is False, "Мусор — тоже")
    check(si.process_alive(os.getpid()) is True, "Свой процесс — живой")


def test_guard_is_wired_into_startup() -> None:
    print("\n[Защита подключена к запуску]")
    src = (APP / "desktop_app.py").read_text(encoding="utf-8")
    main_fn = next(n for n in ast.walk(ast.parse(src))
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = {ast.unparse(n.func): n.lineno for n in ast.walk(main_fn)
             if isinstance(n, ast.Call)}
    check("single_instance.acquire" in calls, "Замок берётся при запуске")
    check("multiprocessing.freeze_support" in calls, "Проверка теста: freeze_support на месте")
    if "single_instance.acquire" in calls and "multiprocessing.freeze_support" in calls:
        check(calls["single_instance.acquire"] > calls["multiprocessing.freeze_support"],
              "ПОСЛЕ freeze_support: дочерние процессы счетов до замка не "
              "доходят, и он их не касается")
    check("atexit.register" in calls, "И отпускается при выходе")


def test_autostart_repairs_its_own_path() -> None:
    """Владелец: «не работает автозапуск программы с windows». Галочка стоит,
    запись есть — а программа не стартует. Windows про это молчит."""
    print("\n[Автозапуск чинит свой путь сам]")
    src = (APP / "desktop_app.py").read_text(encoding="utf-8")
    ns = {"os": os}
    func = next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef) and n.name == "autostart_needs_repair")
    exec(compile(ast.Module(body=[func], type_ignores=[]), "desktop_app.py", "exec"), ns)
    needs = ns["autostart_needs_repair"]

    same = needs(r'"C:\App\prog.exe"', r"C:\App\prog.exe")
    check(same == "", "Путь верный — ничего не трогаем", same)
    check(needs(r'"C:\Downloads\prog.exe"', r"C:\App\prog.exe") != "",
          "Путь устарел — чиним")
    why = needs(r'"C:\Downloads\prog.exe"', r"C:\App\prog.exe")
    check("Downloads" in why and "App" in why,
          "И человеку названы оба места", why)
    check(needs("", r"C:\App\prog.exe") == "",
          "Записи нет — автозапуск выключен, не навязываемся")
    check(needs(r'"C:\App\prog.exe"', "") == "", "Неизвестно куда — не трогаем")

    # Чинится при КАЖДОМ запуске, а не по кнопке: человек про поломку не знает
    main_fn = next(n for n in ast.walk(ast.parse(src))
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [ast.unparse(n.func) for n in ast.walk(main_fn) if isinstance(n, ast.Call)]
    check("repair_autostart" in calls, "Проверка идёт при каждом запуске")

    # А неудача включения обязана доходить до человека, а не в журнал
    toggle = src.split("def _toggle_autostart", 1)[1].split("\n    def ", 1)[0]
    check("showwarning" in toggle, "Не получилось — человеку сказано")
    check("autostart_var.set(False)" in toggle,
          "И галочка снимается обратно: стоящая галочка при неработающем "
          "автозапуске — это обман")


def test_warning_box_is_small_and_not_all_red() -> None:
    """Владелец прислал снимок: рамка «Внимание» заняла пол-экрана и была
    целиком красной — туда попал список из 497 отобранных пар. Настоящие
    предупреждения в нём потерялись, а красный цвет перестал что-то значить."""
    print("\n[Рамка «Внимание» небольшая, красным — только важное]")
    src = (APP / "desktop_app.py").read_text(encoding="utf-8")

    build = src.split("Что мешает торговать", 1)[1].split("---------- Действия", 1)[0]
    check("tk.Text(" in build, "Это окошко с текстом, а не растущая надпись")
    check("height=6" in build, "Высота ограничена", build[:60])
    check("Scrollbar" in build, "И есть ползунок")

    # Цвет по важности — проверяем ВЫЗОВОМ
    cls = next(n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.ClassDef) and n.name == "App")
    # ВЫПОЛНЯЕМ настоящую функцию из программы. Повторять её правило в тесте
    # нельзя: тогда тест проверяет свою копию, а не программу — на этом он
    # уже попался, пропустив «красить красным всегда».
    func = next(n for n in cls.body
                if isinstance(n, ast.FunctionDef) and n.name == "_warning_severity")
    func.decorator_list = []          # @staticmethod вне класса не нужен
    words = next(n for n in cls.body
                 if isinstance(n, ast.Assign)
                 and ast.unparse(n.targets[0]) == "CRITICAL_WORDS")
    ns = {}
    exec(compile(ast.Module(body=[words], type_ignores=[]), "x", "exec"), ns)
    ns["App"] = types.SimpleNamespace(CRITICAL_WORDS=ns["CRITICAL_WORDS"])
    exec(compile(ast.Module(body=[func], type_ignores=[]), "x", "exec"), ns)
    severity = ns["_warning_severity"]
    check(len(ns["CRITICAL_WORDS"]) > 5,
          "Список важных слов прочитан из кода, а не переписан в тест",
          str(len(ns["CRITICAL_WORDS"])))

    check(severity("Выбрано 497 пар из 12538 у брокера") == "обычное",
          "Список пар — не красный: это не предупреждение")
    check(severity("Замерено пар: 641 из 11571") == "обычное",
          "Ход замеров — тоже не красный")
    for bad in ("потеряна связь с терминалом MT5",
                "сбой в главном цикле",
                "не удалось открыть сделку",
                "торговый цикл аварийно завершился"):
        check(severity(bad) == "важно", f"А это красным: {bad[:40]}")

    # Длинная строка обязана подрезаться, иначе рамка снова разрастётся
    show = src.split("def _show_warnings", 1)[1].split("\n    def ", 1)[0]
    check("> 300" in show, "Слишком длинная строка подрезается")
    check("Символы" in show, "И сказано, где смотреть полностью")

    # Список пар в окно больше не попадает — только в журнал
    main_src = (APP / "main.py").read_text(encoding="utf-8")
    body = main_src.split("def auto_pick_symbols", 1)[1].split("\ndef ", 1)[0]
    record = [l for l in body.splitlines() if "runtime_events.record" in l
              or "names=False" in l]
    check(any("names=False" in l for l in record),
          "В окно уходит короткая строка без перечисления пар", str(record)[:150])


def test_three_themes_and_all_readable() -> None:
    """Владелец: «сделай выбор тем, и добавь тему в стиле macOS»."""
    print("\n[Тем три, и все читаемые]")
    check(set(ui_theme.THEMES) >= {"light", "dark", "macos"},
          "Есть светлая, тёмная и в стиле macOS", str(list(ui_theme.THEMES)))
    check(len(ui_theme.choices()) == 3, "Все три предлагаются на выбор")
    for key, title in ui_theme.choices():
        check(key in ui_theme.THEMES, f"«{title}» — существующая тема")

    # Контраст — не вкусовщина, а число. Иначе «красивая тема» однажды снова
    # сделает текст нечитаемым.
    for key, title in ui_theme.choices():
        p = ui_theme.palette(key)
        check(ui_theme.contrast(p["fg"], p["bg"]) >= 4.5,
              f"«{title}»: основной текст читается",
              f"{ui_theme.contrast(p['fg'], p['bg']):.1f}")
        check(ui_theme.contrast(p["muted"], p["bg"]) >= 3.0,
              f"«{title}»: подписи читаются",
              f"{ui_theme.contrast(p['muted'], p['bg']):.1f}")
        for money in ("profit", "loss"):
            check(ui_theme.contrast(p[money], p["bg"]) >= 3.0,
                  f"«{title}»: цифры денег ({money}) читаются",
                  f"{ui_theme.contrast(p[money], p['bg']):.1f}")

    check(ui_theme.palette("macos")["name"] == "macos", "Тема отдаётся по имени")
    check(ui_theme.palette("такой нет")["name"] == ui_theme.DEFAULT,
          "Опечатка в настройке не роняет окно")

    # Выбор темы должен быть В ОКНЕ, а не только в файле настроек
    src = (APP / "desktop_app.py").read_text(encoding="utf-8")
    check("ui_theme.choices()" in src, "Список тем берётся из одного места")
    check("def _save_theme_choice" in src, "Выбор сохраняется")
    save = src.split("def _save_theme_choice", 1)[1].split("\n    def ", 1)[0]
    check("UI_THEME" in save, "Именно в настройку темы")
    check("следующем запуске" in save,
          "И честно сказано, что применится после перезапуска — обещать "
          "мгновенную перекраску и не сделать её хуже")


if __name__ == "__main__":
    print("=" * 62)
    print("ТЕСТЫ: ОДНА КОПИЯ, АВТОЗАПУСК, РАМКА «ВНИМАНИЕ», ТЕМЫ")
    print("=" * 62)
    test_second_copy_does_not_start()
    test_lock_never_blocks_the_program()
    test_guard_is_wired_into_startup()
    test_autostart_repairs_its_own_path()
    test_warning_box_is_small_and_not_all_red()
    test_three_themes_and_all_readable()
    print()
    print("=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    sys.exit(1 if failed else 0)
