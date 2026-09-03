#!/usr/bin/env python3
"""В-001 «ФИКСАЦИЯ ВВЕРХУ» И КНОПКА ПРОВЕРКИ СТРАТЕГИЙ.

ОТКУДА ЭТО

Владелец: «может, трейдинг-профиль добавит, который максимальная там
цена идёт какой-то сдвиг вниз, и чтобы он закрывал сделки, потому что
вывесела сделка плюс двадцать долларов, и она просто откатилась до минус
пять» и «И кнопку принудительная проверка стратегии».

ГЛАВНОЕ, ЧТО ЗДЕСЬ ЗАЩИЩАЕТСЯ

Не сам профиль, а ЧЕСТНОСТЬ того, что программа о нём говорит. Прогон
показал, что фиксация вверху в среднем стоит дороже, чем даёт. Программа
обязана сказать это тому, кто её выбирает, а не молчать.

И отдельно — что отозванный прогон остался отозванным: файл с прежним
«ГИПОТЕЗА ПОДТВЕРЖДЕНА» никуда не делся, но рядом лежит запись, почему
он недействителен.

ЧЕГО ЭТИ ТЕСТЫ НЕ ПРОВЕРЯЮТ

Что профиль работает у настоящего брокера. Терминала здесь нет.

Запуск:  python3 tests/test_peak_lock.py
"""

from __future__ import annotations

import ast
import json
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


import run_baseline              # noqa: E402
import strategies as st          # noqa: E402
import research_manifest as rm   # noqa: E402


# =====================================================================
def test_фиксация_прибыли_включена_насовсем():
    """Решение владельца 03.09.2026: «Фиксация прибыли должна быть на
    постоянной основе».

    Не профиль, не кнопка, не выбор — заводская настройка. Проверяется
    эталон config.py.example: именно из него берутся значения при новой
    установке."""
    print("\n[Фиксация прибыли — заводская настройка, а не выбор]")
    значения = {}
    for узел in ast.parse((APP / "config.py.example").read_text(
            encoding="utf-8")).body:
        if isinstance(узел, ast.Assign) and узел.targets:
            имя = getattr(узел.targets[0], "id", "")
            if имя:
                try:
                    значения[имя] = ast.literal_eval(узел.value)
                except Exception:  # noqa: BLE001
                    pass

    check(значения.get("USE_SIMPLE_EXIT") is False,
          "Простой выход выключен — иначе программа не трогает сделку вовсе",
          str(значения.get("USE_SIMPLE_EXIT")))
    check(значения.get("USE_PROFIT_LOCK_TRAILING") is True,
          "Стоп идёт за пиком прибыли")
    check(значения.get("USE_TIERED_PROFIT_LOCK") is True,
          "И запирает тем больше, чем выше был пик")
    доля = значения.get("PROFIT_LOCK_START_R_FRACTION")
    check(доля == 0.5,
          "Запирать начинаем с ПОЛОВИНЫ риска, а не с целого", str(доля))
    ступени = значения.get("PROFIT_LOCK_TIERS")
    check(isinstance(ступени, list) and ступени and ступени[0][0] <= 0.5,
          "Первая ступень начинается не позже половины риска", str(ступени))


def test_выбора_фиксации_в_списке_стратегий_больше_нет():
    """Владелец: «кнопка по фиксации прибыли не нужна».

    Два пути к одному и тому же — способ однажды включить не то."""
    print("\n[Фиксация не выбирается стратегией]")
    check(st.by_key("peak_lock") is None,
          "Профиля «Фиксация вверху» в списке нет")
    check("Фиксация вверху" not in st.titles(включая_черновики=True),
          "И названия такого нет", str(st.titles(включая_черновики=True)))
    check("peak_lock" not in st.ПАСПОРТА,
          "И паспорт к несуществующей стратегии не привязан")
    # Сам паспорт остаётся: это запись исследования, а не настройка.
    check((ROOT / "preregistration" / "strategy_peak_exit.json").exists(),
          "Паспорт В-001 остался как запись исследования")


def test_стратегия_не_снимает_фиксацию_молча():
    """Стратегия может выключить фиксацию. Молчать об этом нельзя.

    Проверяется по дереву кода: перед применением стратегии, которая
    ставит USE_SIMPLE_EXIT=True, человека обязаны спросить отдельно."""
    print("\n[О снятии фиксации предупреждают отдельно]")
    текст = (APP / "desktop_app.py").read_text(encoding="utf-8")
    дерево = ast.parse(текст)
    обработчик = None
    for узел in ast.walk(дерево):
        if isinstance(узел, ast.FunctionDef) and узел.name == "_apply_strategy":
            обработчик = узел
    check(обработчик is not None, "Обработчик применения стратегии найден")
    if обработчик is None:
        return
    тело = ast.unparse(обработчик)
    check("USE_SIMPLE_EXIT" in тело,
          "Применение стратегии смотрит на простой выход")
    check("ВЫКЛЮЧИТ фиксацию прибыли" in тело,
          "И предупреждает человека словами")
    check(тело.count("askyesno") >= 2,
          "Спрашивают отдельно, а не одним общим вопросом",
          str(тело.count("askyesno")))

    # С-001 действительно снимает фиксацию — иначе предупреждать не о чем.
    c001 = st.by_key("c001_simple")
    check(c001 is not None and c001.params.get("USE_SIMPLE_EXIT") is True,
          "С-001 действительно выключает ведение сделки")


def test_перенос_включает_фиксацию_в_старых_настройках():
    """Решение обязано доехать до УЖЕ УСТАНОВЛЕННОЙ программы.

    config.py не перезаписывается при обновлении. Без одноразового
    переноса владелец поставил бы новую сборку и снова увидел бы, как
    плюс превращается в минус."""
    print("\n[Решение доезжает до установленной программы]")
    import os
    import tempfile
    import types
    import config_migrate as cm

    with tempfile.TemporaryDirectory() as d:
        путь = os.path.join(d, "config.py")
        # Настройки, в которых фиксация выключена простым выходом.
        with open(путь, "w", encoding="utf-8") as f:
            f.write("USE_SIMPLE_EXIT = True\n"
                    "USE_PROFIT_LOCK_TRAILING = False\n"
                    "PROFIT_LOCK_START_R_FRACTION = 1.0\n"
                    "SYMBOLS = ['EURUSD']\n")
        пояснения = cm.apply_one_time(путь)
        mod = types.ModuleType("z")
        exec(open(путь, encoding="utf-8").read(), mod.__dict__)

        check(mod.USE_SIMPLE_EXIT is False, "Простой выход снят")
        check(mod.USE_PROFIT_LOCK_TRAILING is True, "Фиксация включена")
        check(mod.PROFIT_LOCK_START_R_FRACTION == 0.5,
              "И начинается с половины риска",
              str(mod.PROFIT_LOCK_START_R_FRACTION))
        check(any("постоянной основе" in x for x in пояснения),
              "Человеку объяснили, что изменилось", str(пояснения)[:80])
        check(any("стоит дороже, чем даёт" in x for x in пояснения),
              "И честно сказали цену этой защиты", str(пояснения)[:80])

        # Владелец передумал — программа не спорит.
        with open(путь, "w", encoding="utf-8") as f:
            f.write("USE_SIMPLE_EXIT = True\n"
                    "MIGRATED_PROFIT_LOCK_ALWAYS = True\n"
                    "SYMBOLS = ['EURUSD']\n")
        cm.apply_one_time(путь)
        mod2 = types.ModuleType("z2")
        exec(open(путь, encoding="utf-8").read(), mod2.__dict__)
        check(mod2.USE_SIMPLE_EXIT is True,
              "Вернул простой выход сам — повторно не переключаем")


def test_паспорт_запечатан_и_сетка_объявлена_заранее():
    print("\n[Паспорт В-001 запечатан, сетка объявлена до расчёта]")
    путь = ROOT / "preregistration" / "strategy_peak_exit.json"
    check(путь.exists(), "Паспорт на месте", str(путь))
    if not путь.exists():
        return
    п = json.loads(путь.read_text(encoding="utf-8"))
    check(rm.хеш_поля(п, "хеш_паспорта") == п.get("хеш_паспорта"),
          "Печать сходится с содержимым")
    сетка = (п.get("заранее_объявленная_сетка") or {}).get("доля_отката_от_пика")
    check(сетка == [0.30, 0.50, 0.70],
          "Сетка из трёх долей объявлена в паспорте", str(сетка))
    check("ПЕССИМИСТИЧНОЕ" in str(п.get("договорённость_внутри_бара", "")),
          "Договорённость внутри бара — против гипотезы, а не за неё")
    # Паспорт ЗАПЕЧАТАН: править его под тест нельзя ни одной буквой,
    # иначе печать перестанет сходиться. Сравнение без учёта регистра.
    оговорка = str(п.get("oos_проверка", "")).lower()
    check("уже третий раз" in оговорка,
          "Оговорка про третье использование срезов записана заранее",
          оговорка[:80])


def test_отозванный_прогон_остался_отозванным():
    """Собственную ошибку прятать нельзя.

    Файл с прежним «ГИПОТЕЗА ПОДТВЕРЖДЕНА» остаётся на месте, а рядом
    лежит запись, почему он недействителен, — с числами."""
    print("\n[Отозванный прогон никуда не спрятан]")
    прежний = ROOT / "research" / "peak_exit_results.json"
    отзыв = ROOT / "research" / "peak_exit_ОТОЗВАН.json"
    check(прежний.exists(), "Прежний результат НЕ удалён", str(прежний))
    check(отзыв.exists(), "Рядом лежит отзыв", str(отзыв))
    if not (прежний.exists() and отзыв.exists()):
        return
    о = json.loads(отзыв.read_text(encoding="utf-8"))
    check("недействител" in str(о.get("почему_он_недействителен", "")).lower()
          or bool(о.get("почему_он_недействителен")),
          "Сказано, почему прежний прогон недействителен")
    замер = о.get("замер_дефекта_на_train") or {}
    check(float(замер.get("вклад_в_среднее_по_всем_выходам_R", 0)) > 0.1,
          "Дефект измерен числом, а не описан словами", str(замер))
    check("ТОЛЬКО train и validation" in str(
              (о.get("пересчёт_после_исправления") or {}).get("какие_срезы", "")),
          "И сказано, что OOS повторно не открывался")
    числа = ((о.get("пересчёт_после_исправления") or {}).get("числа") or {})
    отрицательные = []
    for срез, доли in числа.items():
        for доля, свод in доли.items():
            отрицательные.append(float(свод.get("среднее_r", 0)) < 0)
    check(отрицательные and all(отрицательные),
          "После исправления все доли на всех срезах отрицательны",
          str(отрицательные))


def test_кнопка_проверки_стратегий_есть_и_ничего_не_применяет():
    """Владелец: «И кнопку принудительная проверка стратегии».

    Проверяется по дереву кода: обработчик кнопки не пишет настройки и
    не переключает активную стратегию."""
    print("\n[Кнопка проверки стратегий есть и ничего не включает]")
    текст = (APP / "desktop_app.py").read_text(encoding="utf-8")
    дерево = ast.parse(текст)

    обработчик = None
    for узел in ast.walk(дерево):
        if (isinstance(узел, ast.FunctionDef)
                and узел.name == "_проверить_стратегии_сейчас"):
            обработчик = узел
    check(обработчик is not None, "Обработчик кнопки есть")
    if обработчик is None:
        return

    опасное = []
    for узел in ast.walk(обработчик):
        if isinstance(узел, ast.Call):
            имя = (getattr(узел.func, "attr", None)
                   or getattr(узел.func, "id", ""))
            if имя in ("_write_config_value", "start_bot", "_apply_strategy",
                       "set_risk_profile"):
                опасное.append(имя)
    check(not опасное, "Кнопка ничего не применяет и не запускает",
          str(опасное))
    check("обновить" in ast.unparse(обработчик),
          "Зато список стратегий она действительно обновляет")

    строки = [у.value for у in ast.walk(дерево)
              if isinstance(у, ast.Constant) and isinstance(у.value, str)]
    check("Проверить стратегии сейчас" in строки,
          "Подпись кнопки на месте")


def main() -> int:
    print("=" * 70)
    print("В-001 «ФИКСАЦИЯ ВВЕРХУ» И КНОПКА ПРОВЕРКИ СТРАТЕГИЙ")
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
