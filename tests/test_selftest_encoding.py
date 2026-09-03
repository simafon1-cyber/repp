#!/usr/bin/env python3
"""САМОПРОВЕРКА СБОРКИ НЕ ПАДАЕТ ИЗ-ЗА КОДИРОВКИ.

ОТКУДА ЭТО — И ЭТО МОЯ ОШИБКА

Сборка 85 не собралась. Владелец написал: «Сборка не собралась».

Причина: я добавил в самопроверку сборки печать русских слов и названий
стратегий. На сборочной машине Windows вывод перенаправляется в файл с
кодировкой cp1252 — русских букв в ней нет вовсе:

    UnicodeEncodeError: 'charmap' codec can't encode characters

Печать бросала исключение. Программа собрана ОКОННОЙ, поэтому Windows
показывала окно с ошибкой, нажать в нём «ОК» было некому, и сборка
убивалась по трёхминутному сроку. Дважды. Итог — шесть минут тишины и
провал без внятной причины.

Обиднее всего, ЧТО именно упало: проверка, добавленная чтобы ловить
ошибки ДО владельца, сама стала ошибкой, доехавшей до сборки.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ ПО ФАКТУ

Берётся НАСТОЯЩАЯ selftest из исходника и выполняется с потоком вывода,
который русских букв не умеет — как консоль сборочной машины. Проверяется
не «есть ли в коде нужная строчка», а что вызов возвращает код и не
бросает исключение.

ЧЕГО ЭТОТ ТЕСТ НЕ ПРОВЕРЯЕТ

Что сборка на Windows пройдёт. Здесь Linux; cp1252 изображается
подделкой потока.

Запуск:  python3 tests/test_selftest_encoding.py
"""

from __future__ import annotations

import ast
import io
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
APP = ROOT / "ai_scalper_standalone"
sys.path.insert(0, str(APP))
sys.path.insert(0, str(BASE))

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


import run_baseline            # noqa: E402


class ПотокБезРусского(io.TextIOBase):
    """Поток, который умеет только латиницу — как консоль сборки Windows.

    Ведёт себя ровно как cp1252: русская буква -> UnicodeEncodeError."""

    def __init__(self):
        self.написано = []

    def write(self, текст):
        текст.encode("cp1252")     # бросит, если есть кириллица
        self.написано.append(текст)
        return len(текст)

    def flush(self):
        pass


def _запустить_самопроверку(поток=None):
    """Выполнить НАСТОЯЩУЮ selftest отдельно от модуля.

    Функция берётся из дерева кода и исполняется в чистом пространстве
    имён — так же, как в test_system. Это и есть проверка того, что
    самопроверка САМОДОСТАТОЧНА: понадобится ей сосед по модулю — здесь
    сразу вылезет NameError.

    ВАЖНО: подделка tkinter обязана стоять и во время ВЫЗОВА, а не только
    во время разбора. Сняли раньше — и самопроверка честно ответит «нет
    модуля tkinter», а тест решит, что дело в кодировке.

    Возвращает (код, события окна, что напечатано, упавшее исключение)."""
    исходник = (APP / "desktop_app.py").read_text(encoding="utf-8")
    функция = next(у for у in ast.walk(ast.parse(исходник))
                   if isinstance(у, ast.FunctionDef) and у.name == "selftest")

    события = []

    class Окно:
        def withdraw(self):
            события.append("скрыто")

        def update_idletasks(self):
            события.append("отрисовано")

        def destroy(self):
            события.append("закрыто")

    поддельный = types.ModuleType("tkinter")
    поддельный.Tk = lambda: (события.append("создано"), Окно())[1]

    копия = types.ModuleType("single_instance")
    копия.process_alive = lambda pid: False

    место = {"single_instance": копия}
    был_tk = sys.modules.get("tkinter")
    был_вывод = sys.stdout
    sys.modules["tkinter"] = поддельный
    if поток is not None:
        sys.stdout = поток
    код, упало = None, None
    try:
        exec(compile(ast.Module(body=[функция], type_ignores=[]),
                     "desktop_app.py", "exec"), место)
        код = место["selftest"]()
    except Exception as e:  # noqa: BLE001
        упало = f"{type(e).__name__}: {e}"
    finally:
        sys.stdout = был_вывод
        if был_tk is None:
            sys.modules.pop("tkinter", None)
        else:
            sys.modules["tkinter"] = был_tk
    напечатано = "".join(поток.написано) if поток is not None else ""
    return код, события, напечатано, упало


# =====================================================================
def test_поток_подделки_действительно_не_умеет_русский():
    """Если бы умел, все проверки ниже ничего не значили."""
    print("\n[Подделка потока ведёт себя как консоль сборки]")
    поток = ПотокБезРусского()
    try:
        поток.write("Зеркало")
        check(False, "Русская буква в такой поток не проходит", "прошла")
    except UnicodeEncodeError:
        check(True, "Русская буква в такой поток не проходит")
    поток.write("SELFTEST OK")
    check(поток.написано == ["SELFTEST OK"],
          "А латиница проходит", str(поток.написано))


def test_настоящая_самопроверка_переживает_cp1252():
    """ГЛАВНАЯ проверка. Ровно то, что уронило сборку 85.

    Наоборот: вернуть внутрь самопроверки голый print — она падает
    исключением, и эта проверка это видит."""
    print("\n[Настоящая самопроверка переживает поток без кириллицы]")
    код, события, напечатано, упало = _запустить_самопроверку(
        ПотокБезРусского())
    check(упало is None,
          "Самопроверка не упала на потоке без кириллицы", str(упало))
    check(код == 0, "И ответила «годно»", str(код))
    check("создано" in события and "закрыто" in события,
          "Окно при этом действительно создавалось", str(события))
    check("SELFTEST OK" in напечатано,
          "И в вывод попало понятное слово", напечатано[:80])


def test_самопроверка_самодостаточна():
    """Ей нельзя опираться на соседей по модулю.

    Это последняя проверка перед тем, как сборка уедет к человеку.
    Сломается сосед — и проверка не отработает вовсе. Именно на этом она
    и упала: печать была вынесена отдельной функцией модуля, а
    выполнялась самопроверка в одиночку."""
    print("\n[Самопроверка не опирается на соседей]")
    код, _, _, упало = _запустить_самопроверку()
    check(упало is None or "NameError" not in упало,
          "Выполняется в одиночку, без модуля вокруг", str(упало))
    check(код == 0, "И отвечает «годно»", str(код))


def test_печать_внутри_самопроверки_защищена():
    """Голая печать возвращает ту же беду.

    Разбор дерева кода: все вызовы print внутри selftest обязаны лежать
    ВНУТРИ вложенной защищённой функции печати, а не в теле проверки."""
    print("\n[Внутри самопроверки нет незащищённой печати]")
    дерево = ast.parse((APP / "desktop_app.py").read_text(encoding="utf-8"))
    selftest = None
    for узел in ast.walk(дерево):
        if isinstance(узел, ast.FunctionDef) and узел.name == "selftest":
            selftest = узел
    check(selftest is not None, "Самопроверка найдена")
    if selftest is None:
        return

    вложенные = [у for у in selftest.body if isinstance(у, ast.FunctionDef)]
    check(bool(вложенные),
          "Внутри есть вложенная функция печати",
          str([у.name for у in вложенные]))
    защищённые = set()
    for у in вложенные:
        защищённые |= {getattr(в, "lineno", 0) for в in ast.walk(у)}

    голые = [getattr(у, "lineno", 0) for у in ast.walk(selftest)
             if isinstance(у, ast.Call)
             and getattr(у.func, "id", "") == "print"
             and getattr(у, "lineno", 0) not in защищённые]
    check(not голые,
          "Ни одного print в теле самопроверки — только через защищённую печать",
          f"строки {голые}")


def test_падение_самопроверки_даёт_код_а_не_окно():
    """Сборка 85 висела по три минуты дважды: оконная программа при
    падении показывает окно, а нажать «ОК» на сборочной машине некому."""
    print("\n[Падение даёт код, а не зависшее окно]")
    дерево = ast.parse((APP / "desktop_app.py").read_text(encoding="utf-8"))
    main = None
    for узел in ast.walk(дерево):
        if isinstance(узел, ast.FunctionDef) and узел.name == "main":
            main = узел
    check(main is not None, "main найдена")
    if main is None:
        return
    check("--selftest" in ast.unparse(main), "Ключ --selftest разбирается")

    защищён = False
    for узел in ast.walk(main):
        if not isinstance(узел, ast.Try):
            continue
        if "selftest()" not in ast.unparse(узел):
            continue
        for обработчик in узел.handlers:
            if getattr(обработчик.type, "id", "") in ("BaseException",
                                                      "Exception"):
                защищён = True
    check(защищён,
          "Вызов самопроверки обёрнут перехватом — падение даёт код, а не окно")


def main() -> int:
    print("=" * 70)
    print("САМОПРОВЕРКА СБОРКИ НЕ ПАДАЕТ ИЗ-ЗА КОДИРОВКИ")
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
