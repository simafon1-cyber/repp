#!/usr/bin/env python3
"""Тесты оформления окна.

Жалоба владельца: «переделай внешний вид приложения, на чёрном фоне очень
плохо всё видно».

Раньше тема была одна — тёмная, и цвета в ней подбирались на глаз: серый
#9a9a9a на почти чёрном #1b1b1b, подписи #666. Такое читается только на
хорошем мониторе в тёмной комнате.

«Красиво» — вкусовщина, поэтому здесь проверяется ЧИСЛО: контраст каждой
пары «текст на фоне» по формуле WCAG. Обычный текст — не меньше 4.5,
приглушённые подписи — не меньше 3.0. Иначе «более приятный оттенок»
однажды снова вернёт нечитаемый текст, и никто этого не заметит.

Запуск:  python3 tests/test_ui_theme.py
"""

from __future__ import annotations

import re
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
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg
CFG = cfg

import ui_theme as t   # noqa: E402


def test_contrast_math() -> None:
    """Сначала убеждаемся, что сама мерка верная."""
    print("\n[Формула контраста считает правильно]")
    check(abs(t.contrast("#000000", "#ffffff") - 21.0) < 0.1,
          "Чёрное на белом — 21 (максимум)",
          f"{t.contrast('#000000', '#ffffff'):.2f}")
    check(abs(t.contrast("#777777", "#777777") - 1.0) < 0.01,
          "Цвет сам на себе — 1 (сливается полностью)")
    check(abs(t.contrast("#000", "#fff") - 21.0) < 0.1,
          "Короткая запись цвета (#fff) тоже понимается",
          f"{t.contrast('#000', '#fff'):.2f}")
    check(abs(t.contrast("#ffffff", "#000000")
              - t.contrast("#000000", "#ffffff")) < 0.01,
          "Контраст одинаков в обе стороны")

    bad = False
    try:
        t.contrast("красный", "#ffffff")
    except ValueError:
        bad = True
    check(bad, "Не-цвет отвергается понятной ошибкой")


def test_every_theme_is_readable() -> None:
    print("\n[Каждая тема читается]")
    for name, colors in t.THEMES.items():
        problems = t.contrast_problems(colors)
        check(not problems, f"Тема «{name}»: все надписи читаются",
              "; ".join(problems))
        for fg_key, bg_key, required in t.CONTRAST_RULES:
            value = t.contrast(colors[fg_key], colors[bg_key])
            check(value >= required,
                  f"  {name}: {fg_key} на {bg_key} = {value:.1f} (нужно {required})")


def test_new_theme_beats_old_one() -> None:
    """Смысл правки — стало ЛУЧШЕ, а не просто «по-другому»."""
    print("\n[Стало читаемее, чем было]")
    old_bg, old_muted, old_dim = "#1b1b1b", "#9a9a9a", "#666666"
    for theme_name in ("light", "dark"):
        colors = t.palette(theme_name)
        was = t.contrast(old_muted, old_bg)
        now = t.contrast(colors["muted"], colors["bg"])
        check(now > was, f"«{theme_name}»: подписи контрастнее прежних",
              f"было {was:.1f}, стало {now:.1f}")
        was_dim = t.contrast(old_dim, old_bg)
        now_dim = t.contrast(colors["dim"], colors["bg"])
        check(now_dim > was_dim,
              f"«{theme_name}»: мелкие пояснения контрастнее прежних",
              f"было {was_dim:.1f}, стало {now_dim:.1f}")


def test_light_is_default() -> None:
    print("\n[Светлая тема по умолчанию]")
    check(t.DEFAULT == "light", "В самом модуле по умолчанию светлая")
    check(getattr(CFG, "UI_THEME", "") == "light",
          "И в настройках программы тоже", getattr(CFG, "UI_THEME", "нет"))
    check(t.from_config(CFG)["name"] == "light",
          "Программа возьмёт светлую")

    # Тёмная никуда не делась — её можно выбрать
    check(t.palette("dark")["name"] == "dark", "Тёмная доступна по имени")
    check(t.palette("DARK")["name"] == "dark", "Регистр не важен")

    # Опечатка в настройках не должна мешать программе открыться
    check(t.palette("тёмненькая")["name"] == "light",
          "Незнакомая тема — светлая, а не падение")
    check(t.palette("")["name"] == "light", "Пустое значение — тоже светлая")


def test_colors_live_in_one_place() -> None:
    """Раньше цвета были вписаны в код в семи десятках мест — поменять
    оформление означало пройти их все руками, и вкладка «Счета» оставалась
    чёрной, даже когда остальное окно уже нет."""
    print("\n[Цвета собраны в одном месте]")

    hard = re.compile(r'"#[0-9a-fA-F]{3,6}"')
    for name in ("desktop_app.py", "accounts_tab.py"):
        src = (APP / name).read_text(encoding="utf-8")
        # Комментарии не считаем: там цвета упоминаются как пояснение
        code = "\n".join(line.split("#", 1)[0] if line.strip().startswith("#")
                         else line for line in src.splitlines())
        found = hard.findall(code)
        # Значок в трее рисуется поверх тёмной панели задач Windows и от темы
        # программы не зависит — это осознанное исключение.
        found = [c for c in found if c != '"#111111"']
        check(not found, f"{name}: цвета берутся из палитры, а не вписаны",
              ", ".join(sorted(set(found))[:6]))

    ui = (APP / "desktop_app.py").read_text(encoding="utf-8")
    check("ui_theme.apply" in ui, "Тема применяется через общий модуль")
    check("self.colors = ui_theme.from_config" in ui,
          "Палитра берётся из настроек")

    tab = (APP / "accounts_tab.py").read_text(encoding="utf-8")
    check("ui_theme.from_config" in tab,
          "Вкладка «Счета» берёт ту же палитру, а не свою")


def test_table_rows_are_not_cramped() -> None:
    """В таблицах здесь почти всё содержимое программы — строки должны быть
    достаточно высокими, чтобы цифры не слипались."""
    print("\n[Таблицы читаются]")
    src = (APP / "ui_theme.py").read_text(encoding="utf-8")
    match = re.search(r"rowheight=(\d+)", src)
    check(match is not None, "Высота строки задана")
    if match:
        height = int(match.group(1))
        check(height >= 24, f"Строка не тесная: {height} точек", str(height))


def test_проверяются_все_видимые_пары():
    """Правила контраста были НЕПОЛНЫМИ, и это важнее, чем кажется.

    Цвета во всех трёх темах были в порядке — но десять пар, которые
    человек видит каждый день, никем не проверялись. Значит, завтрашняя
    правка оттенка «чтобы красивее» могла сделать нечитаемой таблицу
    позиций, и ни один тест бы не возразил.

    Особенно про card и row_alt: прибыль и убыток в таблице рисуются НЕ
    на фоне окна, а на фоне карточки и чередующейся строки. Проверялись
    же они только на фоне окна.

    Наоборот: убрать эти пары из CONTRAST_RULES — тест падает."""
    print("\n[Правила контраста покрывают то, что видно]")
    пары = {(fg, bg) for fg, bg, _ in t.CONTRAST_RULES}
    обязательные = [
        ("fg", "row_alt", "текст в чередующейся строке таблицы"),
        ("muted", "card", "подпись внутри карточки"),
        ("dim", "card", "второстепенное внутри карточки"),
        ("profit", "card", "прибыль в таблице позиций"),
        ("loss", "card", "убыток в таблице позиций"),
        ("profit", "row_alt", "прибыль в чередующейся строке"),
        ("loss", "row_alt", "убыток в чередующейся строке"),
        ("warning", "card", "предупреждение в карточке"),
        ("fg", "tab_bg", "надпись на неактивной вкладке"),
        ("muted", "heading", "подпись в шапке таблицы"),
    ]
    for fg, bg, что in обязательные:
        check((fg, bg) in пары, f"Проверяется: {что}")

    # И каждый цвет палитры, на котором вообще может лежать текст,
    # обязан быть чьим-то фоном хотя бы в одном правиле.
    фоны = {bg for _, bg, _ in t.CONTRAST_RULES}
    for цвет in ("bg", "card", "row_alt", "heading", "tab_active", "tab_bg"):
        check(цвет in фоны, f"Фон «{цвет}» участвует хотя бы в одной проверке")


def test_черновик_стратегии_не_попадает_в_список_выбора():
    """Стратегия в общем списке выглядит как готовый вариант.

    С-001 таким не является: ни заверённого паспорта, ни демо-приёмки,
    прибыльность не проверена. Проверяется ФАКТ: что окно положит в
    выпадающий список.

    Наоборот: вернуть titles() к выдаче всех стратегий — тест падает."""
    print("\n[Черновик стратегии не предлагается наравне]")
    import strategies
    видимые = strategies.titles()
    черновые = [s.title for s in strategies.STRATEGIES
                if strategies.черновик(s)]
    check(черновые, "Черновики в наборе вообще есть", str(черновые))
    for имя in черновые:
        check(имя not in видимые, f"«{имя}» не в списке выбора")
    check(len(видимые) > 0, "А рабочие стратегии в списке остались",
          str(len(видимые)))

    # И окно берёт список именно этой функцией, а не своим перебором.
    import ast
    исходник = (APP / "desktop_app.py").read_text(encoding="utf-8")
    дерево = ast.parse(исходник)
    свой_перебор = []
    for узел in ast.walk(дерево):
        if isinstance(узел, ast.Attribute) and узел.attr == "STRATEGIES":
            свой_перебор.append(ast.unparse(узел))
    check(len(свой_перебор) <= 1,
          "Окно не перебирает список стратегий само",
          str(свой_перебор))


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ: ОФОРМЛЕНИЕ ОКНА ЧИТАЕТСЯ")
    print("=" * 62)

    test_contrast_math()
    test_every_theme_is_readable()
    test_new_theme_beats_old_one()
    test_light_is_default()
    test_colors_live_in_one_place()
    test_table_rows_are_not_cramped()
    test_проверяются_все_видимые_пары()
    test_черновик_стратегии_не_попадает_в_список_выбора()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
