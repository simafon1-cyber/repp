#!/usr/bin/env python3
"""ЧЁРНЫЕ ОКНА: НИ ОДНА ВНЕШНЯЯ КОМАНДА НЕ ПОКАЗЫВАЕТ КОНСОЛЬ.

ОТКУДА ЭТО

Владелец, дословно: «Программу постоянно открывают какие-то командную
строку и окна остаются активными, постоянно висят, может, до двадцати
тридцати окон доходить».

ПОЧЕМУ ЭТО СЛУЧИЛОСЬ

Программа собрана оконной, своей консоли у неё нет. Каждой запущенной
консольной команде Windows выдаёт консоль — то есть окно. Команд у нас
несколько, и зовутся они часто: права на файл настроек переписываются
при каждом сохранении. Отсюда десятки окон.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ

Разбором дерева кода, а не текста: каждый вызов subprocess во всём
пакете обязан передавать добавку quiet_run.без_окна(). Проверка слепа к
комментариям — она смотрит на сам вызов.

ПОЧЕМУ ПРОВЕРКА, А НЕ ПРОСТО ПРАВКА

Правку легко сделать один раз и потерять со следующим вызовом. Здесь
запрет действует на будущее: добавит кто-нибудь новый subprocess без
добавки — проверка упадёт до сборки, а не после установки у владельца.

ЧЕГО ЭТОТ ТЕСТ НЕ ПРОВЕРЯЕТ

Что окон действительно не стало. Здесь Linux, консоли Windows тут нет
вовсе, и увидеть окно невозможно. Проверяется, что флаг передаётся —
а не что Windows его послушалась.

Запуск:  python3 tests/test_no_console_windows.py
"""

from __future__ import annotations

import ast
import sys
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


import quiet_run  # noqa: E402


# Способы запустить чужую программу. Всем нужна добавка.
ЗАПУСКИ = {"run", "Popen", "call", "check_call", "check_output"}

# ЕДИНСТВЕННОЕ ИСКЛЮЧЕНИЕ, и оно названо вслух.
#
# Установщик новой версии запускается с DETACHED_PROCESS: он обязан
# пережить закрытие самой программы, ради которого и запускается.
# Microsoft прямо пишет, что CREATE_NO_WINDOW НЕЛЬЗЯ сочетать с
# DETACHED_PROCESS. Консоли у отделённого процесса и так нет.
ИСКЛЮЧЕНИЯ = {("updater.py", "cmd")}


def _вызовы_subprocess(дерево):
    """Все вызовы subprocess.<что-то> в дереве кода."""
    for узел in ast.walk(дерево):
        if not isinstance(узел, ast.Call):
            continue
        ф = узел.func
        if not isinstance(ф, ast.Attribute) or ф.attr not in ЗАПУСКИ:
            continue
        if not (isinstance(ф.value, ast.Name) and ф.value.id == "subprocess"):
            continue
        yield узел


def _есть_добавка(узел) -> bool:
    """Передана ли **quiet_run.без_окна() или явные creationflags."""
    for к in узел.keywords:
        # **quiet_run.без_окна() — раскрытие словаря, arg is None
        if к.arg is None:
            текст = ast.dump(к.value)
            if "без_окна" in текст or "без_окна" in ast.unparse(к.value):
                return True
        if к.arg == "creationflags":
            return True
    return False


def _первый_аргумент(узел) -> str:
    """Чем запускают — для понятного сообщения об ошибке."""
    if not узел.args:
        return "?"
    а = узел.args[0]
    if isinstance(а, ast.List) and а.elts:
        первый = а.elts[0]
        if isinstance(первый, ast.Constant):
            return str(первый.value)
    try:
        return ast.unparse(а)[:30]
    except Exception:  # noqa: BLE001
        return "?"


# =====================================================================
def test_каждый_запуск_прячет_окно():
    print("\n[Каждая внешняя команда запускается без окна]")
    всего = 0
    беды = []
    for файл in sorted(APP.glob("*.py")):
        дерево = ast.parse(файл.read_text(encoding="utf-8"))
        for узел in _вызовы_subprocess(дерево):
            всего += 1
            чем = _первый_аргумент(узел)
            if (файл.name, чем) in ИСКЛЮЧЕНИЯ:
                continue
            if not _есть_добавка(узел):
                беды.append(f"{файл.name}:{узел.lineno} запускает «{чем}» "
                            f"без quiet_run.без_окна()")
    check(всего >= 6, f"Вызовы внешних команд найдены ({всего} шт.)",
          str(всего))
    check(not беды, "Ни один вызов не показывает чёрное окно",
          "; ".join(беды[:3]))


def test_помощник_молчит_на_чужой_платформе():
    """Флаги Windows на Linux — верный способ получить непонятную ошибку."""
    print("\n[На не-Windows добавки нет]")
    if sys.platform == "win32":
        добавка = quiet_run.без_окна()
        check("creationflags" in добавка, "На Windows флаг передаётся")
        check(добавка.get("creationflags") == quiet_run.CREATE_NO_WINDOW,
              "И это именно CREATE_NO_WINDOW")
    else:
        check(quiet_run.без_окна() == {},
              "На этой системе добавка пустая", str(quiet_run.без_окна()))
    check(quiet_run.CREATE_NO_WINDOW == 0x08000000,
          "Значение флага то самое, что у Windows",
          hex(quiet_run.CREATE_NO_WINDOW))


def test_у_каждой_команды_есть_предел_ожидания():
    """Зависшая команда не имеет права подвесить программу.

    ЭТО НАСТОЯЩИЙ ДЕФЕКТ, найденный по дороге: у git rev-parse в
    trade_journal и в прогоне исследования таймаута не было ВОВСЕ.
    check_output ждёт столько, сколько потребуется, — то есть вечно."""
    print("\n[У каждой команды есть предел ожидания]")
    беды = []
    for файл in sorted(APP.glob("*.py")):
        дерево = ast.parse(файл.read_text(encoding="utf-8"))
        for узел in _вызовы_subprocess(дерево):
            ф = узел.func.attr
            if ф == "Popen":
                continue      # Popen не ждёт — ему предел не нужен
            if (файл.name, _первый_аргумент(узел)) in ИСКЛЮЧЕНИЯ:
                continue
            if not any(к.arg == "timeout" for к in узел.keywords):
                беды.append(f"{файл.name}:{узел.lineno} ждёт без предела")
    check(not беды, "Ни одна команда не ждёт без предела",
          "; ".join(беды[:3]))


def main() -> int:
    print("=" * 70)
    print("ЧЁРНЫЕ ОКНА: НИ ОДНА КОМАНДА НЕ ПОКАЗЫВАЕТ КОНСОЛЬ")
    print("=" * 70)
    for имя, ф in sorted(globals().items()):
        if имя.startswith("test_") and callable(ф):
            print(f"\n--- {имя}")
            try:
                ф()
            except Exception as e:  # noqa: BLE001
                check(False, f"{имя} доработала до конца",
                      f"{type(e).__name__}: {str(e).splitlines()[0]}")
    print("\n" + "=" * 70)
    print(f"Пройдено: {passed}   Сбоев: {failed}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
