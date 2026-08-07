#!/usr/bin/env python3
"""Тесты раскладки окна.

Просьбы владельца одним списком:
  1. Кнопки «Старт», «Пауза», «Перезапуск» — ВВЕРХУ и доступны со всех
     вкладок. Раньше старт и стоп жили на «Обзоре»: чтобы остановить бота
     с другой вкладки, надо было сначала до неё добраться.
  2. «Выход» — справа внизу, и чтобы выходил из ВСЕГО, что запускалось.
  3. Одна кнопка сохранения внизу вместо семи по разделам.
  4. Вкладку «Сделки» убрать.
  5. Боковой ползунок, чтобы не растягивать окно каждый раз.

Запуск:  python3 tests/test_window_layout.py
"""

from __future__ import annotations

import ast
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


UI = (APP / "desktop_app.py").read_text(encoding="utf-8")


def build_ui_body() -> str:
    return UI.split("def _build_ui", 1)[1].split("\n    def ", 1)[0]


def test_top_controls() -> None:
    print("\n[Управление вверху, видно со всех вкладок]")
    body = build_ui_body()

    for label, command in (("Старт", "self.start_bot"),
                           ("Пауза", "self.toggle_pause"),
                           ("Перезапуск", "self.restart_bot")):
        check(label in body, f"Кнопка «{label}» есть наверху")
        check(command in body, f"И она вызывает {command}")

    # Панель кладётся в САМО ОКНО, а не во вкладку — иначе она не была бы
    # видна с других вкладок
    top = body.split("top = ttk.Frame(", 1)
    check(len(top) == 2, "Панель управления создана")
    if len(top) == 2:
        check("self.root" in top[1][:40],
              "Панель принадлежит окну, а не вкладке", top[1][:60])
        check('side="top"' in top[1][:220], "И прижата к верху")

    # Панель должна создаваться ДО вкладок: иначе она окажется под ними
    check(body.index("top = ttk.Frame(") < body.index("self.notebook = ttk.Notebook"),
          "Панель управления выше вкладок")

    check("_refresh_top_bar" in UI, "Состояние на панели обновляется")
    check("self._refresh_top_bar()" in UI, "И делается это периодически")


def test_pause_is_not_stop() -> None:
    """Пауза и остановка — разное. На паузе открытые сделки продолжают
    вестись (трейлинг, безубыток), просто новые не открываются."""
    print("\n[Пауза — это не выключение]")
    body = UI.split("def toggle_pause", 1)[1].split("\n    def ", 1)[0]
    check("control.set_paused" in body, "Пауза ставится через общий флаг")
    check("stop_event" not in body, "Торговый цикл при этом НЕ останавливается")
    check("runtime_events.record" in body, "Пауза попадает в ленту событий")

    restart = UI.split("def restart_bot", 1)[1].split("\n    def ", 1)[0]
    check("stop_event" in restart and "start_bot" in restart,
          "Перезапуск гасит старый цикл и заводит новый")
    check("set_paused(False)" in restart,
          "И снимает паузу — иначе перезапуск не вернул бы торговлю")


def test_exit_button() -> None:
    print("\n[Выход справа внизу и закрывает всё]")
    body = build_ui_body()
    exit_line = [ln for ln in body.splitlines() if 'text="Выход"' in ln]
    check(exit_line, "Кнопка «Выход» есть")
    if exit_line:
        check("self.full_exit" in exit_line[0], "Она вызывает полный выход")
        check('side="right"' in exit_line[0], "И прижата вправо", exit_line[0].strip())
    check(body.index("bottom = ttk.Frame(") > 0, "Нижняя полоса есть")
    bottom = body.split("bottom = ttk.Frame(", 1)[1]
    check("self.root" in bottom[:40], "Нижняя полоса тоже принадлежит окну")
    check('side="bottom"' in bottom[:220], "И прижата к низу")

    quit_body = UI.split("def _hard_quit", 1)[1].split("\n    def ", 1)[0]
    check("stop_event" in quit_body, "Выход останавливает торговый цикл")
    check("accounts_tab.shutdown()" in quit_body,
          "И процессы счетов тоже — они запускаются отдельно")
    check("bridge_host.stop()" in quit_body,
          "И мост: иначе порт остался бы занят до перезагрузки")
    check("telegram_reader.stop()" in quit_body,
          "И чтение Telegram")
    check("os._exit(0)" in quit_body,
          "Процесс исчезает из Диспетчера задач, а не висит фоном")


def test_single_save_button() -> None:
    print("\n[Одна кнопка сохранения вместо семи]")
    buttons = re.findall(r'ttk\.Button\([^)]*text="[^"]*Сохранит[^"]*"', UI)
    check(len(buttons) == 1,
          "Кнопка сохранения в программе ровно одна", str(len(buttons)))

    body = build_ui_body()
    save_line = [ln for ln in body.splitlines() if "Сохранить все настройки" in ln]
    check(save_line, "И она внизу, на общей полосе")

    saver = UI.split("def save_everything", 1)[1].split("\n    def ", 1)[0]
    for part in ("save_broker_settings", "save_advanced_params",
                 "save_profile_fields", "save_market_context",
                 "save_sources", "save_system_settings"):
        check(part in saver, f"Сохраняет раздел: {part}")
    check("settings_backup.save()" in saver,
          "И обновляет постоянную копию настроек")
    check("problems" in saver,
          "О неудачах сообщает, а не проглатывает их молча")

    # Тихий режим: разделы не должны показывать по своему окошку
    for fname in ("save_broker_settings", "save_sources", "save_system_settings"):
        head = UI.split(f"def {fname}(", 1)[1].split(")", 1)[0]
        check("silent" in head, f"{fname} умеет тихий режим", head)


def test_positions_tab_removed() -> None:
    print("\n[Вкладки «Сделки» больше нет]")
    tabs = UI.split("self.tab_frames = {", 1)[1].split("}", 1)[0]
    check('"Сделки"' not in tabs, "Её нет в списке вкладок окна")
    for keep in ("Обзор", "Счета", "Символы", "Настройка", "Система"):
        check(f'"{keep}"' in tabs, f"Вкладка «{keep}» на месте")

    # Открытые позиции не потерялись: они видны на вкладке «Счета»
    accounts = (APP / "accounts_tab.py").read_text(encoding="utf-8")
    check("ОТКРЫТЫЕ ПОЗИЦИИ" in accounts.upper(),
          "Позиции по-прежнему видны на вкладке «Счета»")


def test_scrollbars() -> None:
    print("\n[Боковой ползунок вместо растягивания окна]")
    check("def _scrollable" in UI, "Прокрутка есть")
    helper = UI.split("def _scrollable", 1)[1].split("\n    def ", 1)[0]
    check("Scrollbar" in helper, "И это настоящая полоса прокрутки")
    check("mousewheel" in helper.lower() or "MouseWheel" in helper,
          "Колесо мыши тоже крутит")

    used = UI.count("self._scrollable(parent)")
    check(used >= 8, f"Прокрутка стоит на большинстве вкладок: {used}", str(used))
    for tab in ("_build_tab_broker", "_build_tab_sources", "_build_tab_system",
                "_build_tab_news", "_build_tab_config", "_build_tab_overview"):
        body = UI.split(f"def {tab}", 1)[1].split("\n    def ", 1)[0]
        check("_scrollable(parent)" in body, f"{tab}: прокрутка включена")

    match = re.search(r'geometry\("(\d+)x(\d+)"\)', UI)
    check(match is not None, "Размер окна задан")
    if match:
        width = int(match.group(1))
        check(width >= 1000,
              f"Окно по умолчанию достаточно широкое: {width}", str(width))


def test_startup_retries() -> None:
    """Владелец: «календарь MT5 не всегда работает», «мост не всегда сразу
    включается». Обе вещи зависят от терминала, который после запуска
    Windows готов не сразу — одна попытка при старте это лотерея."""
    print("\n[Мост и календарь дожимаются повторами]")
    check("STARTUP_RETRIES" in UI, "Число попыток задано")
    check("STARTUP_RETRY_MS" in UI, "И пауза между ними")

    bridge = UI.split("def _start_bridge_if_enabled", 1)[1].split("\n    def ", 1)[0]
    check("attempt" in bridge, "Мост считает попытки")
    check("attempt + 1" in bridge, "И пробует снова")
    check("runtime_events.record" in bridge,
          "О результате остаётся запись в ленте событий")

    news = UI.split("def _ensure_news_source", 1)[1].split("\n    def ", 1)[0]
    check("attempt + 1" in news, "Календарь тоже пробует несколько раз")
    check("ensure_ready" in news, "И заодно ставит сервис, если его нет")
    check("self.root.after(4000, self._ensure_news_source)" in UI,
          "Проверка календаря запускается при старте программы")


def test_syntax_is_valid() -> None:
    """После стольких правок разметки файл обязан оставаться рабочим."""
    print("\n[Файл окна разбирается как Python]")
    ok = True
    try:
        ast.parse(UI)
    except SyntaxError as e:
        ok = False
        check(False, "desktop_app.py разбирается", str(e))
    if ok:
        check(True, "desktop_app.py разбирается")


def test_overview_has_no_duplicates() -> None:
    """Владелец: «переделай вкладку обзор, убери лишнее», «чтобы ничего не
    повторялось».

    На «Обзоре» было три кнопки управления (Старт, Стоп, Полный выход) —
    ровно те же, что теперь на постоянных панелях сверху и снизу. Была своя
    строка состояния — она же в верхней панели. Счёт и статистика лежали в
    двух отдельных рамках, хотя это одно и то же: про счёт."""
    print("\n[На «Обзоре» ничего не повторяется]")
    body = UI.split("def _build_tab_overview", 1)[1].split("\n    def ", 1)[0]

    for gone, where in (('text="▶  Старт"', "верхней панели"),
                        ('text="■  Стоп"', "верхней панели"),
                        ("Полный выход", "нижней полосе")):
        check(gone not in body,
              f"Кнопки «{gone}» на «Обзоре» больше нет — она на {where}")

    check(body.count("ttk.Label(parent, textvariable=self.status_var") == 0,
          "Второй строки состояния нет — она в верхней панели")
    check("self.status_var" in body,
          "Но сама переменная осталась: её читает остальной код")

    check(body.count("LabelFrame") <= 5,
          "Рамок немного, страница не разваливается",
          str(body.count("LabelFrame")))
    check("self.info_var" in body and "self.stats_var" in body,
          "Счёт и статистика на месте")
    account_block = body.split('text=" Счёт "', 1)[1].split("LabelFrame", 1)[0]
    check("self.info_var" in account_block and "self.stats_var" in account_block,
          "И лежат в ОДНОЙ рамке, а не в двух")

    check("trade_warning_slot" in body,
          "Под предупреждение зарезервировано место")
    check("pack_forget()" in UI,
          "Пустая рамка «Внимание» прячется, а не висит на пол-экрана")


def test_every_tab_is_reachable() -> None:
    """Прокрутка должна быть везде, где содержимое не помещается. Но НЕ там,
    где стоит большая таблица или текст со своей прокруткой: внешняя рамка
    отняла бы у них высоту, и таблица схлопнулась бы в одну строку."""
    print("\n[Прокрутка там, где нужна, и не там, где вредна]")

    tabs = re.findall(r"    def (_build_tab_\w+)\(self, parent", UI)
    check(len(tabs) >= 15, f"Вкладок разобрано: {len(tabs)}", str(len(tabs)))

    # ГЛАВНОЕ ПРАВИЛО: у вкладки не может быть ДВУХ полос прокрутки сразу.
    # Своя прокрутка (у длинного текста, у списка параметров) и добавленная
    # снаружи дают два ползунка рядом и ломают расчёт высоты — я на этом
    # уже попался, добавляя прокрутку всем подряд.
    doubled = []
    long_without_scroll = []
    for name in tabs:
        body = UI.split(f"def {name}", 1)[1].split("\n    def ", 1)[0]
        scrolls = "_scrollable(parent)" in body
        own_scroll = "Scrollbar(" in body
        if scrolls and own_scroll:
            doubled.append(name)
        # Длинная вкладка-форма без всякой прокрутки — то, из-за чего окно
        # приходилось растягивать
        heavy = "Treeview(" in body or "tk.Text(" in body
        if (not scrolls and not own_scroll and not heavy
                and len(body.splitlines()) >= 30):
            long_without_scroll.append(name)

    check(not doubled, "Ни у одной вкладки нет двух полос прокрутки",
          ", ".join(doubled))
    check(not long_without_scroll,
          "Длинные вкладки-формы прокручиваются", ", ".join(long_without_scroll))

    scrolled = [n for n in tabs
                if "_scrollable(parent)" in UI.split(f"def {n}", 1)[1].split("\n    def ", 1)[0]]
    check(len(scrolled) >= 8,
          f"Прокрутка стоит на большинстве вкладок: {len(scrolled)}",
          ", ".join(scrolled))


def test_tab_names_fit() -> None:
    """Вкладок полтора десятка. Длинные имена не помещались в строку, и
    названия обрезались: «Как пользоват», «Сигналы ...»."""
    print("\n[Названия вкладок помещаются]")
    tabs = UI.split("self.tab_frames = {", 1)[1].split("}", 1)[0]
    names = re.findall(r'"([^"]+)":', tabs)
    check(names, "Названия вкладок найдены")
    longest = max(names, key=len) if names else ""
    check(len(longest) <= 10,
          f"Самое длинное название короткое: «{longest}» ({len(longest)})",
          longest)
    check(len(names) <= 15, f"Вкладок не больше 15: {len(names)}", str(len(names)))

    # Скрываемые в простом режиме вкладки должны называться ТАК ЖЕ, иначе
    # переключение режима перестало бы их находить
    advanced = re.search(r"ADVANCED_TAB_NAMES = \[([^\]]+)\]", UI)
    check(advanced is not None, "Список вкладок продвинутого режима найден")
    if advanced:
        adv_names = re.findall(r'"([^"]+)"', advanced.group(1))
        missing = [n for n in adv_names if n not in names]
        check(not missing,
              "Все они есть среди вкладок окна", ", ".join(missing))


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ: РАСКЛАДКА ОКНА")
    print("=" * 62)

    test_top_controls()
    test_pause_is_not_stop()
    test_exit_button()
    test_single_save_button()
    test_positions_tab_removed()
    test_scrollbars()
    test_startup_retries()
    test_overview_has_no_duplicates()
    test_every_tab_is_reachable()
    test_tab_names_fit()
    test_syntax_is_valid()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
