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
    check(used >= 5, f"Прокрутка стоит на насыщенных вкладках: {used}", str(used))
    for tab in ("_build_tab_broker", "_build_tab_sources", "_build_tab_system",
                "_build_tab_news"):
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
    test_syntax_is_valid()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
