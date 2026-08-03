#!/usr/bin/env python3
"""Тесты автоматической установки в MetaTrader 5.

Смысл: пользователь не должен ставить ничего отдельно. Программа сама
копирует советники и сервис календаря в терминал и вызывает компилятор.

Главное, что проверяется:
  1. Терминалы находятся, а служебные папки Common и Community — нет.
  2. Файлы попадают именно туда, куда нужно: советники в Experts, сервис
     календаря в Services. Перепутать = терминал их не увидит.
  3. Ошибки не проглатываются: нет исходников, нет терминала, нет
     MetaEditor — про каждый случай сказано понятным текстом.
  4. Исходники .mq5 действительно кладутся внутрь .exe при сборке.

Запуск:  python3 tests/test_mt5_install.py
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
        print(f"  СБОЙ {name}" + (f"  -> {detail}" if detail else ""))


cfg = types.ModuleType("config")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg

import mt5_install as mi   # noqa: E402


def make_terminal(root: str, name: str) -> str:
    path = os.path.join(root, name)
    os.makedirs(os.path.join(path, "MQL5"), exist_ok=True)
    return path


# =====================================================================
# 1. Поиск терминалов
# =====================================================================
def test_find_terminals() -> None:
    print("\n[Поиск терминалов]")

    tmp = tempfile.mkdtemp()
    make_terminal(tmp, "A1B2C3D4E5F6")
    make_terminal(tmp, "F6E5D4C3B2A1")
    # Служебные папки MetaQuotes — не терминалы
    make_terminal(tmp, "Common")
    make_terminal(tmp, "Community")
    # Папка без MQL5 — тоже не терминал
    os.makedirs(os.path.join(tmp, "Просто папка"), exist_ok=True)

    found = mi.find_terminals([tmp])
    names = sorted(os.path.basename(p) for p in found)
    check(names == ["A1B2C3D4E5F6", "F6E5D4C3B2A1"],
          "Найдены оба терминала, служебные папки пропущены", str(names))

    check(mi.find_terminals([os.path.join(tmp, "нет такой")]) == [],
          "Несуществующая папка — пустой список, без падения")
    check(mi.find_terminals([]) == [], "Пустой список путей — пустой результат")

    # Один и тот же терминал не должен попасть дважды
    check(len(mi.find_terminals([tmp, tmp])) == 2,
          "Повторный путь не удваивает список", str(mi.find_terminals([tmp, tmp])))


# =====================================================================
# 2. Копирование — главное, чтобы не перепутать папки
# =====================================================================
def test_copy_layout() -> None:
    print("\n[Куда копируются файлы]")

    tmp = tempfile.mkdtemp()
    terminal = make_terminal(tmp, "TESTTERM")

    copied, errors = mi.copy_files(terminal)
    check(copied > 0, "Файлы скопированы", f"{copied} шт, ошибки: {errors}")
    check(not errors, "Без ошибок", str(errors))

    experts = os.path.join(terminal, "MQL5", "Experts")
    services = os.path.join(terminal, "MQL5", "Services")

    # Сервис ДОЛЖЕН лежать в Services: в Experts терминал его не покажет
    # в разделе «Сервисы» Навигатора, и календарь просто не заработает.
    check(os.path.exists(os.path.join(services, "CalendarExport.mq5")),
          "Сервис календаря лежит в Services")
    check(not os.path.exists(os.path.join(experts, "CalendarExport.mq5")),
          "Сервис НЕ попал в Experts — там терминал его не увидит")

    check(os.path.exists(os.path.join(experts, "AI_Scalper_Pro.mq5")),
          "Советник AI Scalper Pro в Experts")
    check(os.path.exists(os.path.join(experts, "DualGuardEA.mq5")),
          "Советник DualGuard в Experts")
    check(not os.path.exists(os.path.join(services, "AI_Scalper_Pro.mq5")),
          "Советник не попал в Services")

    # Заголовки .mqh нужны рядом с советником, иначе он не соберётся
    for header in ("RiskManager.mqh", "TradeManager.mqh", "SignalEngine.mqh",
                   "Config.mqh", "NewsAI.mqh"):
        check(os.path.exists(os.path.join(experts, header)),
              f"Заголовок {header} рядом с советником")

    # Папка Files нужна сервису календаря для записи файла
    check(os.path.isdir(os.path.join(terminal, "MQL5", "Files")),
          "Папка Files создана — сервису календаря есть куда писать")

    # Повторный запуск не должен ломаться на уже существующих файлах
    copied2, errors2 = mi.copy_files(terminal)
    check(copied2 == copied and not errors2,
          "Повторная установка проходит так же, без ошибок", str(errors2))


def test_layout_matches_repo() -> None:
    print("\n[Список файлов совпадает с репозиторием]")

    for folder, items in mi.LAYOUT.items():
        for subdir, name in items:
            path = ROOT / subdir / name
            check(path.exists(), f"{subdir}/{name} существует в репозитории")

    # Всё, что компилируем, должно быть в списке копирования — иначе будем
    # собирать файл, которого нет
    copy_pairs = set()
    for folder, items in mi.LAYOUT.items():
        for _subdir, name in items:
            copy_pairs.add((folder, name))
    for folder, name in mi.COMPILE:
        check((folder, name) in copy_pairs,
              f"Компилируемый {folder}/{name} есть в списке копирования")

    # Компилируются только .mq5: .mqh — заголовки, отдельно их не собирают
    for _folder, name in mi.COMPILE:
        check(name.endswith(".mq5"), f"Компилируется только .mq5: {name}")


# =====================================================================
# 3. Ошибки не проглатываются
# =====================================================================
def test_error_paths() -> None:
    print("\n[Понятные отказы]")

    saved = mi.find_terminals
    mi.find_terminals = lambda roots=None: []
    try:
        report = mi.install_all()
        check(report["terminals"] == 0, "Терминалов нет — ноль в отчёте")
        check("MetaTrader" in report["text"], "Сказано, что терминал не найден", report["text"])
    finally:
        mi.find_terminals = saved

    saved_src = mi.sources_available
    mi.sources_available = lambda: False
    try:
        report = mi.install_all()
        check(bool(report["errors"]), "Нет исходников — это ошибка, а не тишина")
        check(".mq5" in report["text"], "Сказано, каких файлов не хватает", report["text"])
    finally:
        mi.sources_available = saved_src

    # Нет MetaEditor — файлы копируются, но об этом честно сообщается
    tmp = tempfile.mkdtemp()
    make_terminal(tmp, "NOEDITOR")
    saved = mi.find_terminals
    saved_me = mi.find_metaeditor
    mi.find_terminals = lambda roots=None: [os.path.join(tmp, "NOEDITOR")]
    mi.find_metaeditor = lambda t: ""
    try:
        report = mi.install_all()
        check(report["copied"] > 0, "Файлы всё равно скопированы")
        check(report["compiled"] == 0, "Собрано ноль")
        check(any("MetaEditor" in e for e in report["errors"]),
              "Сказано, что нужно собрать вручную", str(report["errors"]))
        check("F7" in " ".join(report["errors"]),
              "Подсказано, какую клавишу нажать")
    finally:
        mi.find_terminals = saved
        mi.find_metaeditor = saved_me


def test_describe() -> None:
    print("\n[Отчёт человеческим языком]")

    text = mi.describe({"terminals": 2, "copied": 26, "compiled": 6, "errors": []})
    check("2" in text and "26" in text, "Числа на месте", text)
    check("ошибок нет" in text, "Успех назван успехом", text)

    text = mi.describe({"terminals": 1, "copied": 13, "compiled": 0,
                        "errors": ["a", "b", "c", "d", "e"]})
    check("и ещё 2" in text, "Длинный список ошибок сокращается", text)

    text = mi.describe({"terminals": 0, "copied": 0, "compiled": 0,
                        "errors": ["Терминал не найден"]})
    check(text == "Терминал не найден", "Без терминалов — только причина", text)


# =====================================================================
# 4. Всё едет вместе с программой
# =====================================================================
def test_bundled_with_exe() -> None:
    print("\n[Всё внутри программы, ставить отдельно нечего]")

    wf = (ROOT / ".github" / "workflows" / "build-exe.yml").read_text(encoding="utf-8")
    check('--add-data "../mql5;mql5"' in wf,
          "Исходники mql5 кладутся внутрь .exe")
    check('--add-data "../ai_scalper_pro;ai_scalper_pro"' in wf,
          "Исходники советника AI Scalper Pro тоже")
    check("--hidden-import mt5_install" in wf, "Модуль установки виден сборщику")
    check("--collect-all telethon" in wf,
          "telethon внутри — pip install отдельно не нужен")

    bat = (APP / "build_exe.bat").read_bytes().decode("ascii", errors="replace")
    check("add-data" in bat, "Локальная сборка тоже кладёт исходники внутрь")
    check("telethon" in bat, "И telethon тоже")

    # bundled_root должен уметь работать и в .exe, и из исходников
    src = (APP / "mt5_install.py").read_text(encoding="utf-8")
    check("_MEIPASS" in src, "Учитывается распаковка PyInstaller")
    check((ROOT / "mql5" / "CalendarExport.mq5").exists(),
          "Из исходников путь тоже верный")
    check(mi.sources_available() is True, "Исходники найдены при запуске из репозитория")


def test_auto_install_wiring() -> None:
    print("\n[Автоустановка при первом запуске]")

    gui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    tree = ast.parse(gui)
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    check("_auto_install_into_mt5_once" in funcs, "Автоустановка есть")
    check("install_into_mt5" in funcs, "Есть и ручная кнопка")
    check("_auto_install_into_mt5_once" in gui.split("def _auto_install")[0],
          "Автоустановка вызывается при запуске")

    auto = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_auto_install_into_mt5_once":
            auto = ast.get_source_segment(gui, node)
    check(auto is not None, "Тело автоустановки найдено")
    if auto:
        check("is_installed()" in auto,
              "Повторно не переустанавливает — проверяет, что уже стоит")
        check("silent=True" in auto,
              "При автозапуске не показывает окно «готово» поверх экрана")
        check("sources_available()" in auto,
              "Без исходников молча ничего не делает")

    manual = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "install_into_mt5":
            manual = ast.get_source_segment(gui, node)
    check(manual is not None and "threading.Thread" in manual,
          "Установка идёт в фоне — окно не замирает на время компиляции")


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ УСТАНОВКИ В METATRADER")
    print("=" * 62)

    test_find_terminals()
    test_copy_layout()
    test_layout_matches_repo()
    test_error_paths()
    test_describe()
    test_bundled_with_exe()
    test_auto_install_wiring()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
