#!/usr/bin/env python3
"""Тесты скрытия окна терминала MetaTrader.

ОТКУДА ЗАДАЧА. Владелец: «можно ли сделать, чтобы не открывался сам терминал
MetaTrader, чтобы оно подключалось к учётной записи и работало... чтобы я мог
просто ввести учётные данные и работать в этой же программе».

ЧТО ВОЗМОЖНО. Программа и так запускает терминал сама и сама входит в счёт:
логин, пароль и сервер передаются прямо в mt5.initialize. Остаётся убрать с
экрана окно — терминал при этом работает полностью.

ЧТО НЕВОЗМОЖНО, и это надо было сказать прямо. Совсем без терминала работать
нельзя. Терминал И ЕСТЬ соединение с брокером: он держит защищённый канал и
исполняет приказы, а наша программа с брокером не разговаривает вовсе.
Протокол закрытый, MetaQuotes его не публикует.

ГЛАВНОЕ, ЧТО ПРОВЕРЯЕТСЯ ЗДЕСЬ, — не «окно спряталось», а чтобы человек не
остался без терминала: спрятанное окно исчезает и с панели задач, вернуть его
мышью нельзя.

Запуск:  python3 tests/test_hidden_terminal.py
"""

from __future__ import annotations

import ast
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
        print(f"  СБОЙ {name}" + (f"  -> {detail}" if detail else ""))


cfg = types.ModuleType("config")
cfg.__file__ = str(APP / "config.py.example")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg
CFG = cfg


class _FakeMT5(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return name


sys.modules["MetaTrader5"] = _FakeMT5("MetaTrader5")

import mt5_connector as mt5c      # noqa: E402

UI = (APP / "desktop_app.py").read_text(encoding="utf-8")
CONN = (APP / "mt5_connector.py").read_text(encoding="utf-8")


def test_not_windows_is_a_quiet_no_op() -> None:
    """Программа собирается и проверяется на Linux, а работает на Windows.
    На чужой системе скрытие обязано просто ничего не делать — не падать."""
    print("\n[Не Windows — просто ничего не делаем]")
    check(mt5c.set_terminal_visible(False) == 0, "Скрытие: 0 окон, без ошибки")
    check(mt5c.set_terminal_visible(True) == 0, "Показ: то же самое")
    check(mt5c.hide_terminal() == 0, "hide_terminal не падает")
    check(mt5c.show_terminal() == 0, "show_terminal не падает")


def test_there_is_a_way_back() -> None:
    """САМОЕ ВАЖНОЕ. Спрятанное окно исчезает и с панели задач: мышью его не
    достать. Без обратного пути человек остался бы без MetaTrader совсем и
    решил бы, что программа его сломала."""
    print("\n[Окно всегда можно вернуть]")
    check(hasattr(mt5c, "show_terminal"), "Обратная команда существует")

    # 1) Кнопка в окне
    check("show_mt5_terminal" in UI, "В окне есть обработчик показа")
    check('text="Показать терминал"' in UI, "И кнопка для человека")
    tab = UI.split("def _build_tab_system", 1)[1][:3000]
    check("self.show_mt5_terminal" in tab,
          "Кнопка стоит на вкладке «Система»")

    # 2) Возврат при выходе из программы — на случай, если про кнопку забыли
    quit_body = UI.split("def _hard_quit", 1)[1].split("\n    def ", 1)[0]
    check("show_terminal()" in quit_body,
          "При выходе окно возвращается на экран обязательно")

    # И имя должно существовать: ровно на этом я уже обжигался с tgr
    tree = ast.parse(UI)
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                modules.add(a.asname or a.name.split(".")[0])
    check("mt5c" in modules,
          "Модуль подключён под тем именем, под которым вызывается",
          str(sorted(m for m in modules if "mt5" in m)))


def test_hides_only_after_successful_login() -> None:
    """Пока вход не удался, терминал может показывать ошибку или просить
    пароль. Спрятать его в этот момент означало бы спрятать от человека саму
    причину, по которой ничего не работает."""
    print("\n[Прячем только после успешного входа]")
    body = CONN.split("def connect(", 1)[1].split("\ndef ", 1)[0]
    check("hide_terminal()" in body, "Скрытие вызывается из подключения")
    check(body.index("account_info()") < body.index("hide_terminal()"),
          "И только ПОСЛЕ проверки, что счёт действительно доступен")
    check(body.index("raise RuntimeError") < body.index("hide_terminal()"),
          "Неудачное подключение до скрытия просто не доходит")


def test_setting_exists_and_is_explained() -> None:
    print("\n[Настройка и честное объяснение]")
    check(CFG.MT5_HIDE_TERMINAL is True,
          "Скрытие включено — владелец просил именно этого")

    example = (APP / "config.py.example").read_text(encoding="utf-8")
    block = example.split("MT5_HIDE_TERMINAL", 1)[0][-2000:]
    check("панел" in block and "задач" in block,
          "Предупреждено, что окно пропадёт и с панели задач")
    check("закрыт" in block,
          "И сказано, почему нельзя обойтись совсем без терминала")


def test_honest_about_impossibility() -> None:
    """Обещать «встроим брокера в программу» нельзя. Терминал — это и есть
    связь с брокером, протокол закрытый."""
    print("\n[Про невозможное сказано прямо, а не обойдено]")
    head = CONN.split("def set_terminal_visible", 1)[0][-2500:]
    for word in ("закрыт", "брокер", "протокол"):
        check(word in head, f"В коде объяснено: «{word}»")

    tab = UI.split("def _build_tab_system", 1)[1][:3000]
    check("закрытый" in tab or "закрыт" in tab,
          "И человеку в окне это тоже написано, а не только в коде")


def test_window_class_is_the_terminal_only() -> None:
    """Прятать что попало нельзя: перепутанный класс окна убрал бы с экрана
    чужую программу."""
    print("\n[Прячется именно терминал, а не что попало]")
    check(mt5c._MT5_WINDOW_CLASSES, "Класс окна задан")
    check(all("MetaTrader" in c or "MetaQuotes" in c
              for c in mt5c._MT5_WINDOW_CLASSES),
          "И это окна MetaTrader", str(mt5c._MT5_WINDOW_CLASSES))
    body = CONN.split("def set_terminal_visible", 1)[1].split("\ndef ", 1)[0]
    check("GetClassNameW" in body,
          "Окна отбираются по классу, а не по заголовку — заголовок у "
          "терминала меняется вместе со счётом и языком")
    check("_MT5_WINDOW_CLASSES" in body, "Сверка идёт со списком классов")


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ: СКРЫТОЕ ОКНО ТЕРМИНАЛА")
    print("=" * 62)

    test_not_windows_is_a_quiet_no_op()
    test_there_is_a_way_back()
    test_hides_only_after_successful_login()
    test_setting_exists_and_is_explained()
    test_honest_about_impossibility()
    test_window_class_is_the_terminal_only()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
