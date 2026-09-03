#!/usr/bin/env python3
"""З-001 «ЗЕРКАЛО»: ВХОД В ПРОТИВОПОЛОЖНУЮ СТОРОНУ.

ОТКУДА ЭТО

Владелец, дословно: «может просто попробовать сделать все в зеркале, то
есть сигнал приходит на покупку пусть ставит на продажу, в таком виде,
сделай отдельную стратегию с название зеркало что бы я знал!». Полномочия
даны дословно: «Даю все права деле и полномочия !».

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ ПО ФАКТУ

Не «есть ли в исходнике нужная строчка», а что программа делает:
переворот происходит ровно в одном месте кода, по умолчанию выключен,
удалённо не включается, и стратегия «Зеркало» меняет ровно одну
настройку и никакую больше.

ГЛАВНОЕ, ЧЕГО ЭТИ ТЕСТЫ НЕ ГОВОРЯТ

Что зеркало помогает. Оно проверено отдельным прогоном по паспорту З-001
и НЕ помогает: теряет на всех трёх срезах. Числа лежат в
research/mirror_results.json, и отдельная проверка ниже следит, чтобы
программа не начала обещать обратное.

Запуск:  python3 tests/test_mirror.py
"""

from __future__ import annotations

import ast
import json
import os
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
import strategies as st        # noqa: E402
import remote_settings as rs   # noqa: E402
import research_manifest as rm # noqa: E402


# =====================================================================
def test_по_умолчанию_зеркало_выключено():
    """Переворот стороны — не то, что должно включаться само."""
    print("\n[По умолчанию зеркала нет]")
    эталон = (APP / "config.py.example").read_text(encoding="utf-8")
    значения = {}
    for узел in ast.parse(эталон).body:
        if (isinstance(узел, ast.Assign) and узел.targets
                and getattr(узел.targets[0], "id", "") == "MIRROR_SIGNALS"):
            значения["MIRROR_SIGNALS"] = getattr(узел.value, "value", None)
    check("MIRROR_SIGNALS" in значения,
          "Настройка MIRROR_SIGNALS есть в эталоне настроек")
    check(значения.get("MIRROR_SIGNALS") is False,
          "И по умолчанию она ВЫКЛЮЧЕНА", str(значения))


def test_переворот_ровно_в_одном_месте():
    """Два места переворота — это два разных мнения о том, куда мы идём.

    Однажды они разойдутся, и стоп встанет не с той стороны. Проверка
    слепа к комментариям: разбирается дерево кода, а не текст."""
    print("\n[Переворот сделан ровно в одном месте]")
    дерево = ast.parse((APP / "main.py").read_text(encoding="utf-8"))
    места = []
    for узел in ast.walk(дерево):
        # Ищем обращения к настройке MIRROR_SIGNALS в коде, а не в тексте.
        if (isinstance(узел, ast.Constant) and узел.value == "MIRROR_SIGNALS"):
            места.append(getattr(узел, "lineno", 0))
    check(len(места) == 1,
          "MIRROR_SIGNALS читается в торговом цикле ровно один раз",
          f"строки {места}")


def test_стратегия_зеркало_меняет_ровно_одну_настройку():
    """Меняешь два условия сразу — потом не скажешь, что подействовало."""
    print("\n[Стратегия «Зеркало» меняет одно и только одно]")
    зеркало = st.by_key("mirror")
    check(зеркало is not None, "Стратегия «Зеркало» есть в списке")
    if зеркало is None:
        return
    check(зеркало.title == "Зеркало",
          "Названа так, как просил владелец — «Зеркало»", зеркало.title)
    check(set(зеркало.params) == {"MIRROR_SIGNALS"},
          "Меняет ровно одну настройку и никакую больше",
          str(sorted(зеркало.params)))
    check(зеркало.params.get("MIRROR_SIGNALS") is True,
          "И включает именно переворот")

    # Ни один защищённый параметр не должен оказаться в стратегии.
    запрещено = set(зеркало.params) & set(st.PROTECTED_PARAMS)
    check(not запрещено, "Не трогает защиты счёта", str(запрещено))


def test_зеркало_нельзя_включить_удалённо():
    """С телефона нельзя развернуть всю торговлю в другую сторону."""
    print("\n[Удалённо зеркало не включается]")
    применить, отказы = rs.validate({"MIRROR_SIGNALS": True})
    check("MIRROR_SIGNALS" not in применить,
          "Удалённая настройка MIRROR_SIGNALS не применяется",
          str(применить))
    check(any("MIRROR_SIGNALS" in str(о) for о in отказы),
          "И сказано, что именно отброшено", str(отказы))
    check("MIRROR_SIGNALS" in rs.FORBIDDEN,
          "Названа запрещённой явно, а не «просто не разрешена»")


def test_паспорт_зеркала_запечатан():
    """Без сходящейся печати черновик включать нельзя."""
    print("\n[Паспорт З-001 запечатан и сходится]")
    путь = ROOT / "preregistration" / "strategy_mirror.json"
    check(путь.exists(), "Паспорт на месте", str(путь))
    if not путь.exists():
        return
    п = json.loads(путь.read_text(encoding="utf-8"))
    check(rm.хеш_поля(п, "хеш_паспорта") == п.get("хеш_паспорта"),
          "Печать сходится с содержимым", str(п.get("хеш_паспорта"))[:16])
    можно, почему = st.паспорт_заверен("mirror")
    check(можно, "Программа считает паспорт заверённым", почему)
    check("mirror" in st.ЧЕРНОВИКИ,
          "И «Зеркало» помечено черновиком, а не готовой стратегией")


def test_программа_не_обещает_прибыли_от_зеркала():
    """ГЛАВНАЯ проверка честности.

    Зеркало измерено и НЕ помогает. Программа обязана говорить это в
    лицо тому, кто её выбирает, а не молчать.

    Наоборот: убрать из предупреждения слова про потери — проверка
    падает."""
    print("\n[Программа не обещает прибыли от зеркала]")
    зеркало = st.by_key("mirror")
    if зеркало is None:
        check(False, "Стратегия «Зеркало» есть")
        return
    текст = f"{зеркало.idea} {зеркало.when} {зеркало.caution}".lower()
    check("не помогает" in текст or "теряет" in текст,
          "Сказано, что зеркало не помогает", зеркало.caution[:60])
    check("издержки" in текст,
          "И названа настоящая причина — издержки", зеркало.caution[:60])
    for обещание in ("прибыльн", "заработ", "выгодн"):
        плохо = обещание in текст and "не " not in текст
        check(not плохо, f"Нет обещания «{обещание}…»")


def test_результат_прогона_записан_и_отрицателен():
    """Числа должны лежать в репозитории, а не в переписке."""
    print("\n[Результат прогона записан]")
    путь = ROOT / "research" / "mirror_results.json"
    check(путь.exists(), "Файл результата на месте", str(путь))
    if not путь.exists():
        return
    р = json.loads(путь.read_text(encoding="utf-8"))
    check(р.get("решение") == "ГИПОТЕЗА ОТКЛОНЕНА",
          "Решение записано: гипотеза отклонена", str(р.get("решение")))
    for срез in ("train", "validation", "oos"):
        часть = (р.get(срез) or {}).get("зеркало") or {}
        среднее = часть.get("среднее_r")
        check(isinstance(среднее, float) and среднее < 0,
              f"Зеркало на срезе «{срез}» теряет", str(среднее))
    check(bool(р.get("оговорка")),
          "Записана оговорка про уже виденные данные")


def test_зеркало_видно_в_СОБРАННОЙ_программе():
    """Жалоба владельца: «у меня также самая не появилась стратегия зеркала».

    Черновик показывается в списке, только если рядом лежит его паспорт и
    печать сходится. У собранной программы паспорта лежат ВНУТРИ .exe, и
    ищутся они по другому пути — через sys._MEIPASS. Забудь строку
    --add-data в сборке, и стратегия молча исчезнет у человека, хотя на
    машине разработчика будет на месте.

    Здесь этот путь и проверяется: подставляется _MEIPASS с копией папки
    pasportов, как это делает PyInstaller."""
    print("\n[Зеркало видно и в собранной программе]")
    import importlib
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as д:
        shutil.copytree(ROOT / "preregistration",
                        os.path.join(д, "preregistration"))
        было = getattr(sys, "_MEIPASS", None)
        sys._MEIPASS = д
        try:
            importlib.reload(st)
            check(st._корень_данных() == д,
                  "Паспорта ищутся внутри собранной программы",
                  st._корень_данных())
            можно, почему = st.паспорт_заверен("mirror")
            check(можно, "Паспорт «Зеркала» найден и печать сходится", почему)
            check("Зеркало" in st.titles(),
                  "И «Зеркало» есть в списке выбора", str(st.titles()))
        finally:
            if было is None:
                delattr(sys, "_MEIPASS")
            else:
                sys._MEIPASS = было
            importlib.reload(st)


def test_сборка_падает_если_паспорта_не_попали_внутрь():
    """Пропажу паспорта обязана поймать САМОПРОВЕРКА СБОРКИ, а не владелец.

    Проверяется по дереву кода: в selftest есть проверка паспортов и
    списка стратегий, и она возвращает ненулевой код."""
    print("\n[Самопроверка сборки ловит пропажу паспорта]")
    import ast as _ast
    текст = (APP / "desktop_app.py").read_text(encoding="utf-8")
    дерево = _ast.parse(текст)
    selftest = None
    for узел in _ast.walk(дерево):
        if isinstance(узел, _ast.FunctionDef) and узел.name == "selftest":
            selftest = узел
    check(selftest is not None, "Самопроверка сборки есть")
    if selftest is None:
        return
    тело = _ast.unparse(selftest)
    check("паспорт_заверен" in тело,
          "Она проверяет, что паспорта на месте и печать сходится")
    check("titles" in тело,
          "И что стратегии действительно попали в список")
    check("return 1" in тело,
          "И валит сборку, а не пишет предупреждение")


def main() -> int:
    print("=" * 70)
    print("З-001 «ЗЕРКАЛО»: ВХОД В ПРОТИВОПОЛОЖНУЮ СТОРОНУ")
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
