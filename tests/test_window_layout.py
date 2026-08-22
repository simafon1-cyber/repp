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
import builtins
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


import ui_layout  # noqa: E402

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
    check("stop_event" in restart, "Перезапуск гасит старый цикл")
    # Новый цикл заводится НЕ здесь, а после того, как старый действительно
    # остановился: соединение с терминалом одно на всю программу, и старый
    # цикл, завершаясь, его закрывает. Запусти мы новый раньше — он остался бы
    # без связи. Подробности и проверка решения — в test_bot_alive.py.
    check("_restart_when_stopped" in restart,
          "И ЖДЁТ его остановки, а не запускает новый вслепую")
    check("after(600" not in restart,
          "Прежней глухой паузы в 600 мс больше нет")
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
    check("tgr.stop()" in quit_body,
          "И чтение Telegram")
    check("os._exit(0)" in quit_body,
          "Процесс исчезает из Диспетчера задач, а не висит фоном")

    # А ВОТ ЭТО — главное. Проверка выше ищет текст, и она годами проходила
    # на строке `telegram_reader.stop()`, которой НЕ СУЩЕСТВОВАЛО: модуль
    # импортирован как tgr, значит вызов давал NameError. Ошибку молча
    # съедал `except Exception: pass` вокруг, и чтение Telegram на выходе не
    # останавливалось вообще.
    #
    # Поэтому здесь проверяем не текст, а РАЗРЕШИМОСТЬ имени: каждое имя, у
    # которого в _hard_quit что-то вызывается, обязано существовать — либо
    # как импорт модуля, либо как локальная переменная.
    tree = ast.parse(UI)
    quit_fn = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_hard_quit")
    module_names = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                module_names.add(a.asname or a.name.split(".")[0])
    local_names = {t.id for n in ast.walk(quit_fn) if isinstance(n, ast.Assign)
                   for t in n.targets if isinstance(t, ast.Name)}
    local_names |= {a.arg for a in quit_fn.args.args}

    unresolved = []
    for n in ast.walk(quit_fn):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name)):
            base = n.func.value.id
            if base not in module_names and base not in local_names and base != "self":
                unresolved.append(f"{base}.{n.func.attr}() (строка {n.lineno})")
    check(not unresolved,
          "Все вызовы в «Выходе» обращаются к существующим именам",
          "; ".join(unresolved))


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
    страницы = ui_layout.all_pages()
    check("Сделки" not in страницы, "Её нет в списке страниц окна")
    for keep in ("Обзор", "Счета", "Символы", "Настройка", "Система"):
        check(keep in страницы, f"Страница «{keep}» на месте")

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

    # РАНЬШЕ ЗДЕСЬ ПРОВЕРЯЛОСЬ pack_forget(): пустая рамка обязана была
    # прятаться, чтобы не висеть на пол-экрана. Требование правильное, но
    # оно решало не ту беду.
    #
    # Прячущаяся рамка означала, что при отсутствии сообщений человек
    # видит ПУСТОЕ МЕСТО. А пустое место он читает как «программа
    # молчит» — и однажды принял это за зависание. Причина молчания
    # (происшествие, позиция без стопа, пауза, проверочный режим) лежала
    # при этом в журнале.
    #
    # Теперь рамка не прячется, потому что не бывает пустой: в ней
    # всегда есть хотя бы строка «торговля разрешена» (ui_status.собрать
    # никогда не возвращает пустой список — проверяется в
    # test_ui_status.py). А от «пол-экрана» защищает не пряталка, а
    # ФИКСИРОВАННАЯ ВЫСОТА с ползунком.
    рамка = UI.split("self.trade_warning_frame = ", 1)[1].split("\n    def ", 1)[0]
    высота = re.search(r"trade_warning_text = tk\.Text\([^)]*?height=(\d+)",
                       рамка, re.S)
    check(высота is not None, "У рамки задана высота, а не «сколько влезет»")
    if высота:
        check(int(высота.group(1)) <= 8,
              "И высота небольшая — рамка не займёт пол-экрана",
              высота.group(1))
    check("yscrollcommand" in рамка,
          "Длинное уезжает под ползунок, а не растягивает страницу")


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
        # ВАЖНАЯ ОГОВОРКА. Правило про две полосы — про полосы СТРАНИЦЫ.
        # Небольшое окошко фиксированной высоты со своим ползунком внутри
        # прокручиваемой страницы — это другое: страница прокручивается
        # целиком, а окошко только внутри себя. Именно так сделана рамка
        # «Внимание»: у владельца она разрослась на пол-экрана, и высоту
        # пришлось ограничить, а длинное убрать под ползунок.
        bounded_widget = "height=" in body and "tk.Text(" in body
        if scrolls and own_scroll and not bounded_widget:
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
    """Полоса вкладок обязана ПОМЕЩАТЬСЯ В ОКНО. Числом, а не на глаз.

    Владелец: «вкладки, кнопки выходят за границы, приходится увеличивать
    окно». Причина была арифметической: четырнадцать вкладок в один ряд шире
    минимальной ширины окна. Раньше это чинили сокращением названий и
    проверяли по скриншоту — и на другом мониторе всё разъезжалось заново.
    Теперь ширина считается в пикселях, и добавить пятнадцатую вкладку молча
    больше нельзя: тест упадёт до сборки."""
    print("\n[Полоса вкладок помещается в окно]")
    верх = ui_layout.group_names()
    ширина = ui_layout.strip_width(верх)
    check(ui_layout.fits(верх),
          f"Полоса {ширина} px помещается в окно "
          f"{ui_layout.МИН_ШИРИНА_ОКНА} px", str(ширина))

    # Старая раскладка из четырнадцати вкладок НЕ помещалась — тест обязан
    # это видеть, иначе он ничего не проверяет.
    старая = ["Обзор", "Брокер", "Символы", "Счета", "Лог", "Equity",
              "Настройка", "Календарь", "Новости", "Источники", "Система",
              "Сигналы", "Чат", "Помощь"]
    check(not ui_layout.fits(старая),
          "И прежние 14 вкладок он честно считает не влезающими",
          str(ui_layout.strip_width(старая)))
    check(len(верх) < len(старая), "Верхних вкладок стало меньше",
          f"{len(старая)} -> {len(верх)}")

    check(not ui_layout.problems(), "Претензий к раскладке нет",
          "; ".join(ui_layout.problems()))

    # ЗАПАС ОБЯЗАН БЫТЬ. «Впритык» на своём мониторе означает «не помещается»
    # на чужом: ширина знака зависит от шрифта системы и масштаба экрана.
    # Здесь берётся полоса, которая занимает окно ровно под завязку, и она
    # обязана считаться не влезающей.
    впритык = ["W" * ((ui_layout.МИН_ШИРИНА_ОКНА - ui_layout.ЗАПАС_ОКНА
                       - ui_layout.ОТСТУП_ВКЛАДКИ) // ui_layout.ЗНАК_ПИКСЕЛЕЙ)]
    check(not ui_layout.fits(впритык),
          "Полоса «ровно по краю» считается не влезающей",
          str(ui_layout.strip_width(впритык)))
    # А половина окна — влезает, иначе проверка была бы просто запретом.
    половина = ["W" * (ui_layout.МИН_ШИРИНА_ОКНА // (2 * ui_layout.ЗНАК_ПИКСЕЛЕЙ))]
    check(ui_layout.fits(половина), "Половина окна — помещается",
          str(ui_layout.strip_width(половина)))

    # Каждая группа либо одиночная (и тогда без второго ряда вкладок), либо
    # с несколькими страницами. Путаница здесь оставила бы страницу без окна.
    check(ui_layout.is_single("Обзор"), "«Обзор» — одна страница, без ряда")
    check(not ui_layout.is_single("Новости"),
          "«Новости» — несколько страниц, со вторым рядом",
          str(ui_layout.pages("Новости")))
    check(ui_layout.group_of("Календарь") == "Новости",
          "«Календарь» лежит в группе «Новости»",
          ui_layout.group_of("Календарь"))
    check(ui_layout.group_of("Такой нет") == "",
          "Несуществующая страница не приписана никуда")

    # Второй ряд вкладок ЖИВЁТ ВНУТРИ первого и потому уже его на поля окна.
    # Если про это забыть, страницы группы разъедутся ровно тем же способом,
    # каким разъезжались верхние вкладки. Проверяем не число, а само
    # различие: обязана найтись ширина, которая наверху помещается, а внутри
    # группы уже нет.
    разошлись = any(
        ui_layout.fits(["Ш" * n], ui_layout.МИН_ШИРИНА_ОКНА)
        and not ui_layout.fits(["Ш" * n],
                               ui_layout.МИН_ШИРИНА_ОКНА - ui_layout.ЗАПАС_ОКНА)
        for n in range(1, 200))
    check(разошлись, "Второй ряд вкладок считается уже первого")

    # Раскладка сама себя ругает, когда с ней что-то не так — иначе
    # problems() был бы украшением, а не проверкой.
    тесно = ui_layout.problems(window_px=200)
    check(any("не помещается" in x for x in тесно),
          "В узком окне раскладка жалуется на полосу вкладок",
          "; ".join(тесно))
    пусто = ui_layout.problems(hidden=ui_layout.pages("Помощь"))
    check(any("без страниц" in x for x in пусто),
          "И на группу, у которой спрятали все страницы",
          "; ".join(пусто))

    # НИ ОДНА СТРАНИЦА НЕ ПОТЕРЯЛАСЬ при группировке.
    страницы = ui_layout.all_pages()
    пропало = [n for n in старая if n not in страницы]
    check(not пропало, "Все прежние вкладки остались доступны",
          ", ".join(пропало))
    check(len(страницы) == len(set(страницы)),
          "И ни одна не лежит сразу в двух группах")
    for имя in страницы:
        check(bool(ui_layout.group_of(имя)), f"«{имя}» приписана к группе")

    # Скрываемые в простом режиме страницы должны называться ТАК ЖЕ, иначе
    # переключение режима перестало бы их находить.
    advanced = re.search(r"ADVANCED_TAB_NAMES = \[([^\]]+)\]", UI)
    check(advanced is not None, "Список вкладок продвинутого режима найден")
    adv_names = re.findall(r'"([^"]+)"', advanced.group(1)) if advanced else []
    missing = [n for n in adv_names if n not in страницы]
    check(not missing, "Все они есть среди страниц окна", ", ".join(missing))

    # И в простом режиме ни одна группа не остаётся пустой: вкладка без
    # единственной страницы — тупик, человек нажимает и попадает в никуда.
    check(not ui_layout.problems(hidden=adv_names),
          "В простом режиме пустых вкладок не остаётся",
          "; ".join(ui_layout.problems(hidden=adv_names)))


def test_window_builds_pages_it_declares() -> None:
    """Окно обязано строить ровно те страницы, которые объявлены в раскладке.
    Иначе появится вкладка-пустышка или страница, до которой не добраться."""
    print("\n[Окно строит все объявленные страницы]")
    body = build_ui_body()
    for имя in ui_layout.all_pages():
        check(f'self.tab_frames["{имя}"]' in body,
              f"Страница «{имя}» строится")
    # Страницы берутся из ui_layout, а не переписаны в окне вторым списком.
    check("ui_layout.group_names()" in body,
          "Список вкладок берётся из раскладки, а не продублирован")
    check("self.tab_books" in body,
          "Запомнено, в каком ряду лежит каждая страница")


def _bound_names(node) -> set:
    """Имена, которые эта область видимости заводит у себя."""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                out.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            out.update(n.names)
    return out


def test_no_undefined_names() -> None:
    """Имя используется, но нигде не заведено — это NameError при вызове.

    ЗАЧЕМ ОТДЕЛЬНАЯ ПРОВЕРКА. В «Выходе» годами стояло `telegram_reader.stop()`
    при том, что модуль импортирован как `tgr`. Вызов давал NameError, его
    молча съедал `except Exception: pass` вокруг, и чтение Telegram при выходе
    не останавливалось вообще. Тест на это был — но он искал ТЕКСТ
    «telegram_reader.stop()» в исходнике и потому исправно подтверждал
    несуществующий вызов.

    Такие ошибки не видны при обычном запуске: они прячутся в ветках, куда
    доходят редко (выход, экран входа, обработка отказа). Отсюда правило:
    имена проверяются целиком по программе, а не по одной функции."""
    print("\n[Все имена в программе существуют]")
    builtin_names = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "_"}
    problems = []
    for path in sorted(APP.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            check(False, f"{path.name} разбирается", str(e))
            continue
        module_names = _bound_names(tree) | builtin_names

        def report(node, visible):
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load)
                        and sub.id not in visible):
                    problems.append(f"{path.name}:{sub.lineno} — {sub.id}")

        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Всё, что заводится где угодно внутри функции, считаем видимым:
                # нам важно не место присваивания, а существует ли имя вообще.
                report(n, module_names | _bound_names(n))
        for n in tree.body:
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                report(n, module_names)

    check(not problems, "Ни одного неизвестного имени во всей программе",
          "; ".join(sorted(set(problems))[:8]))


def test_no_self_outside_classes() -> None:
    """Обычная функция не может обращаться к self — это гарантированный
    NameError при первом же вызове.

    Так была устроена ловушка в _show_login(): функция уровня модуля брала
    цвета из self.colors. Экран входа выключен по умолчанию, поэтому никто
    туда не заходил, — а программа сама предлагает его включить, и включение
    роняло её на старте, ещё до появления окна.

    Проверка идёт по ВСЕМ файлам программы, а не только по окну: ошибка не
    специфична для интерфейса, просто там её цена выше всего."""
    print("\n[Ни одна функция вне класса не обращается к self]")
    problems = []
    for path in sorted(APP.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            check(False, f"{path.name} разбирается", str(e))
            continue
        inside_class = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        inside_class.add(id(sub))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if id(node) in inside_class:
                continue
            if any(a.arg == "self" for a in node.args.args):
                continue          # функция сама принимает self — законно
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name) and sub.id == "self":
                    problems.append(f"{path.name}:{sub.lineno} в {node.name}()")
                    break
    check(not problems,
          "Ни одного обращения к self вне класса", "; ".join(problems))


def test_кнопки_закрытия_говорят_правду() -> None:
    """САМАЯ СЕРЬЁЗНАЯ находка аудита интерфейса.

    С ревизии bc3dd08 массовые кнопки закрывают ТОЛЬКО сделки бота
    (CLOSE_BOT_POSITIONS_ONLY). А окно подтверждения по-прежнему обещало
    закрыть «АБСОЛЮТНО ВСЕ открытые позиции счёта, и сделки бота, и
    открытые вручную».

    То есть окно ВРАЛО. Человек читает «закроется всё», нажимает «да»,
    уходит — и его ручная сделка остаётся открытой, хотя он уверен в
    обратном. Это не «непонятно», это неверно.

    Проверяется ФАКТ: какой текст соберётся при включённой настройке.

    Наоборот: вернуть жёсткий текст «АБСОЛЮТНО ВСЕ позиции счёта» —
    тест падает."""
    print("\n[Кнопки закрытия говорят правду]")
    исходник = (APP / "desktop_app.py").read_text(encoding="utf-8")

    # Слепо к комментариям: разбираем дерево кода и смотрим ТОЛЬКО
    # строковые значения, которые реально попадут человеку на экран.
    tree = ast.parse(исходник)
    строки_окна = []
    for узел in ast.walk(tree):
        if not isinstance(узел, ast.FunctionDef):
            continue
        if узел.name not in ("close_all_positions", "close_profitable_positions",
                             "close_losing_positions", "_что_закроется"):
            continue
        # ДОКУМЕНТАЦИЮ ФУНКЦИИ НЕ СЧИТАЕМ. Она — пояснение для того, кто
        # читает код, а не текст на экране. Первая версия этой проверки
        # брала все строки подряд и падала на объяснении «раньше здесь
        # было написано АБСОЛЮТНО ВСЕ» — то есть ловила мой же
        # комментарий вместо дефекта. Правило проекта требует слепоты к
        # пояснениям, и оно написано ровно про такие случаи.
        тело = узел.body[1:] if (узел.body and isinstance(узел.body[0], ast.Expr)
                                 and isinstance(узел.body[0].value, ast.Constant)
                                 and isinstance(узел.body[0].value.value, str)
                                 ) else узел.body
        for кусок in тело:
            for вн in ast.walk(кусок):
                if isinstance(вн, ast.Constant) and isinstance(вн.value, str):
                    строки_окна.append(вн.value)
    весь = " ".join(строки_окна)

    check(строки_окна, "Тексты кнопок закрытия найдены")
    check("АБСОЛЮТНО ВСЕ" not in весь,
          "Обещания «закрыть АБСОЛЮТНО ВСЕ позиции счёта» больше нет",
          весь[:100])
    # Обещание «закроются и ручные» само по себе не запрещено: оно
    # ВЕРНО, когда настройка выключена. Запрещено обещать это ВСЕГДА.
    # Поэтому проверяется не наличие фразы, а то, что рядом с ней есть и
    # противоположная — то есть текст ветвится по настройке.
    check("НЕ закроются" in весь,
          "Есть текст для случая «ручные не трогаем»")
    check("Отменить нельзя" in весь or "нельзя отменить" in весь,
          "И предупреждение о необратимости на месте", весь[:80])
    check("сделки бота" in весь,
          "Сказано, что закрываются сделки бота", весь[:100])
    check("вручную" in весь and "НЕ закроются" in весь,
          "И прямо сказано, что ручные сделки НЕ закроются")

    # Текст обязан ЗАВИСЕТЬ от настройки, а не быть написан навсегда.
    # Настройка читается через getattr(cfg, "ИМЯ", ...) — то есть имя
    # приходит СТРОКОЙ, а не как обращение к полю. Искать надо и там.
    def настройка_читается(дерево) -> bool:
        for у in ast.walk(дерево):
            if isinstance(у, ast.Call) and isinstance(у.func, ast.Name) \
                    and у.func.id == "getattr":
                for арг in у.args:
                    if isinstance(арг, ast.Constant) \
                            and арг.value == "CLOSE_BOT_POSITIONS_ONLY":
                        return True
            if isinstance(у, ast.Attribute) and у.attr == "CLOSE_BOT_POSITIONS_ONLY":
                return True
        return False

    check(настройка_читается(tree),
          "Текст берётся из настройки, а не написан на бумаге")

    # И то же самое на вкладке «Счета», где кнопки реально видны.
    счета = (APP / "accounts_tab.py").read_text(encoding="utf-8")
    дерево_счетов = ast.parse(счета)
    check(настройка_читается(дерево_счетов),
          "На вкладке «Счета» текст тоже берётся из настройки")
    # Подтверждение проверяется У КАЖДОЙ из трёх кнопок отдельно.
    #
    # Первая версия искала askyesno «где-нибудь в файле» — и проходила,
    # даже когда все три обработчика закрывали позиции молча: askyesno
    # оставался у соседней кнопки «Закрыть всё на всех счетах».
    # Проверка, которая не различает исправное и сломанное, проверкой не
    # является. Поймано собственной проверкой «наоборот».
    for имя, что_зовёт in (("_close_all", "close_all"),
                           ("_close_profit", "close_profitable"),
                           ("_close_loss", "close_losing")):
        функция = next((у for у in ast.walk(дерево_счетов)
                        if isinstance(у, ast.FunctionDef) and у.name == имя), None)
        check(функция is not None, f"Обработчик {имя} найден")
        if функция is None:
            continue
        # Вызов закрытия обязан лежать ВНУТРИ условия, а не сам по себе.
        под_условием = []
        for узел in ast.walk(функция):
            if not isinstance(узел, ast.If):
                continue
            for вн in ast.walk(узел):
                if isinstance(вн, ast.Call) and isinstance(вн.func, ast.Attribute) \
                        and вн.func.attr == что_зовёт:
                    под_условием.append(имя)
        check(под_условием,
              f"{имя}: закрытие идёт только после подтверждения")
        спрашивает = any(
            isinstance(у, ast.Call) and isinstance(у.func, ast.Attribute)
            and у.func.attr in ("_спросить", "askyesno")
            for у in ast.walk(функция))
        check(спрашивает, f"{имя}: вопрос человеку задаётся")


def test_состояние_торговли_видно_на_обзоре() -> None:
    """Причина молчания программы должна быть на экране, а не в журнале.

    Раньше «Работает» и отсутствие сделок выглядели как зависание:
    происшествие, позиция без стопа и пауза попадали максимум в ленту
    последних трёх событий, откуда их вытесняло следующее сообщение.

    Наоборот: убрать вызов _показать_состояние_торговли — тест падает."""
    print("\n[Состояние торговли видно на «Обзоре»]")
    исходник = (APP / "desktop_app.py").read_text(encoding="utf-8")
    tree = ast.parse(исходник)

    имена_функций = {у.name for у in ast.walk(tree)
                     if isinstance(у, ast.FunctionDef)}
    check("_состояние_торговли" in имена_функций,
          "Сбор состояния торговли существует")

    зовут = {у.func.attr for у in ast.walk(tree)
             if isinstance(у, ast.Call) and isinstance(у.func, ast.Attribute)}
    check("_состояние_торговли" in зовут,
          "И вызывается из цикла обновления")

    # Рамка обязана быть ВИДНА ВСЕГДА, в отличие от «Внимания», которое
    # прячется. Прячущаяся рамка состояния бессмысленна: молчание и есть
    # то, что человек принимает за поломку.
    прячут = re.findall(r"trade_state_\w*\.pack_forget", исходник)
    check(not прячут, "Рамка состояния нигде не прячется", str(прячут))

    # И решение о содержимом принимает отдельный проверяемый модуль.
    check("ui_status" in исходник, "Содержимое решает ui_status")


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
    test_window_builds_pages_it_declares()
    test_syntax_is_valid()
    test_no_undefined_names()
    test_no_self_outside_classes()
    test_no_dead_code()
    test_кнопки_закрытия_говорят_правду()
    test_состояние_торговли_видно_на_обзоре()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


def test_no_dead_code() -> None:
    """Функция, которую никто не вызывает, — это не «запас на будущее», а
    мусор: её читают, в ней ищут ошибки, её правят при переименованиях, и она
    молча устаревает. Проверка постоянная, чтобы мёртвый код не накапливался
    заново.

    Обработчики веб-страниц и подобное вызываются НЕ по имени, а через
    декоратор (@app.route, @app.before_request) — их отсекаем по наличию
    декоратора, иначе проверка ругалась бы на живой код."""
    print("\n[Мёртвого кода нет]")
    defined = {}
    for path in sorted(APP.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.decorator_list or node.name.startswith("__"):
                continue
            defined[node.name] = path.name

    used = set()
    for path in sorted(APP.glob("*.py")) + sorted(BASE.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                used.add(node.value.strip())

    dead = sorted(f"{where}:{name}" for name, where in defined.items()
                  if name not in used)
    check(not dead, "Ни одной функции, которую никто не вызывает",
          "; ".join(dead[:8]))
    check(len(defined) > 100, "Проверка действительно прошла по программе",
          str(len(defined)))


if __name__ == "__main__":
    sys.exit(main())
