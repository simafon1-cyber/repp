#!/usr/bin/env python3
"""Тесты расписания работы бота (вкладка «Календарь»).

Главное, что здесь проверяется: расписание НЕ является вторым, отдельным
набором правил. Оно обязано совпадать с реальными фильтрами входа
(risk_manager.trading_hours_ok, news_calendar.is_high_impact_event_near) и
брать те же настройки. Расхождение = программа обещает человеку одно, а
делает другое.

Запуск:  python3 tests/test_schedule.py
"""

from __future__ import annotations

import ast
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
APP = BASE.parent / "ai_scalper_standalone"
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


def install_stubs() -> types.ModuleType:
    cfg = types.ModuleType("config")
    exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
    sys.modules["config"] = cfg

    class _FakeMT5(types.ModuleType):
        def __getattr__(self, name):
            if name.startswith("_"):
                raise AttributeError(name)
            return name

    mt5 = _FakeMT5("MetaTrader5")
    mt5.initialize = lambda *a, **k: False
    mt5.terminal_info = lambda: None
    sys.modules["MetaTrader5"] = mt5
    return cfg


CFG = install_stubs()

import news_calendar as nc        # noqa: E402
import risk_manager as rm         # noqa: E402
import trading_schedule as ts     # noqa: E402


SYMBOLS = ["EURUSD", "XAUUSD"]
# Понедельник, чтобы выходные не мешали проверять остальное
MONDAY = datetime(2026, 8, 3, 12, 0, 0)


def ev(minutes_from, impact="high", currency="USD", name="Nonfarm Payrolls", base=MONDAY):
    return {"time": base + timedelta(minutes=minutes_from), "currency": currency,
            "event": name, "impact": impact, "actual": "", "estimate": "", "prev": ""}


# =====================================================================
# 1. Расписание совпадает с реальными фильтрами входа
# =====================================================================
def test_matches_real_filters() -> None:
    print("\n[Расписание совпадает с реальными фильтрами]")

    CFG.USE_NEWS_FILTER = True
    CFG.NEWS_HARD_BLOCK_WINDOW_MIN = 30

    check(ts.hard_block_minutes() == CFG.NEWS_HARD_BLOCK_WINDOW_MIN,
          "Окно блокировки берётся из НАСТОЯЩЕЙ настройки, а не задано отдельно")

    # Сопоставление символ -> валюты должно быть одной функцией на всю программу
    check(ts.symbol_codes("XAUUSDm") == nc._symbol_codes("XAUUSDm"),
          "Разбор символа — та же функция, что у фильтра новостей",
          f"{ts.symbol_codes('XAUUSDm')} vs {nc._symbol_codes('XAUUSDm')}")

    # Часы торговли: сверяем ts.within_trading_hours с настоящей rm.trading_hours_ok
    # для КАЖДОГО часа суток и для обычного диапазона, и для диапазона через полночь.
    import unittest.mock as mock
    for start, end in ((8, 20), (22, 6), (0, 24), (5, 5)):
        CFG.USE_TRADING_HOURS = True
        CFG.TRADING_START_HOUR = start
        CFG.TRADING_END_HOUR = end
        mismatch = []
        for hour in range(24):
            moment = MONDAY.replace(hour=hour)
            with mock.patch.object(rm, "datetime") as fake:
                fake.now.return_value = moment
                real = rm.trading_hours_ok()
            mine = ts.within_trading_hours(moment)
            if real != mine:
                mismatch.append((hour, real, mine))
        check(not mismatch, f"Часы {start}..{end}: расписание совпадает с фильтром входа",
              str(mismatch))

    CFG.USE_TRADING_HOURS = False
    check(all(ts.within_trading_hours(MONDAY.replace(hour=h)) for h in range(24)),
          "Ограничение выключено — работает круглосуточно")


def test_block_window_matches_news_filter() -> None:
    print("\n[Окно блокировки совпадает с фильтром новостей]")

    CFG.USE_NEWS_FILTER = True
    CFG.USE_TRADING_HOURS = False
    CFG.NEWS_HARD_BLOCK_WINDOW_MIN = 30
    CFG.NEWS_PROVIDER_CHAIN = ["стенд"]

    event = ev(0)          # важная новость ровно "сейчас"

    # Подсовываем фильтру новостей ровно те же события через провайдера-заглушку
    import news_providers as npv
    npv.PROVIDERS["стенд"] = lambda k, f, t: [event]
    npv.KEYLESS_PROVIDERS.add("стенд")

    import unittest.mock as mock
    mismatch = []
    for offset in (-45, -31, -30, -15, 0, 15, 30, 31, 45):
        moment = MONDAY + timedelta(minutes=offset)
        npv._CACHE.clear()
        with mock.patch.object(nc, "datetime") as fake:
            fake.now.return_value = moment
            real_blocked = nc.is_high_impact_event_near("EURUSD", CFG.NEWS_HARD_BLOCK_WINDOW_MIN)
        rows = ts.build_schedule(["EURUSD"], [event], moment, hours_ahead=24)
        mine_blocked = any(r["active_now"] and r["action"] == ts.ACTION_BLOCK for r in rows)
        if real_blocked != mine_blocked:
            mismatch.append((offset, real_blocked, mine_blocked))

    check(not mismatch,
          "На всех смещениях -45..+45 мин расписание и фильтр говорят одно и то же",
          str(mismatch))

    npv.PROVIDERS.pop("стенд", None)
    npv.KEYLESS_PROVIDERS.discard("стенд")
    npv._CACHE.clear()


# =====================================================================
# 2. Построение расписания
# =====================================================================
def test_build_schedule() -> None:
    print("\n[Построение расписания]")

    CFG.USE_NEWS_FILTER = True
    CFG.NEWS_HARD_BLOCK_WINDOW_MIN = 30

    rows = ts.build_schedule(SYMBOLS, [ev(60)], MONDAY)
    check(len(rows) == 1, "Важная новость попала в расписание", str(len(rows)))
    if rows:
        r = rows[0]
        check(r["action"] == ts.ACTION_BLOCK, "Важная новость блокирует вход")
        check(r["start"] == MONDAY + timedelta(minutes=30), "Начало окна = событие минус 30 мин")
        check(r["end"] == MONDAY + timedelta(minutes=90), "Конец окна = событие плюс 30 мин")
        check(set(r["symbols"]) == {"EURUSD", "XAUUSD"},
              "USD-новость затрагивает обе пары", str(r["symbols"]))
        check(not r["active_now"], "Будущее окно не помечено как идущее")

    # Средняя новость только ужесточает порог
    rows = ts.build_schedule(SYMBOLS, [ev(60, impact="medium")], MONDAY)
    check(rows and rows[0]["action"] == ts.ACTION_PENALTY,
          "Средняя новость не блокирует, а ужесточает порог")

    # Слабая не влияет вовсе
    rows = ts.build_schedule(SYMBOLS, [ev(60, impact="low")], MONDAY)
    check(rows == [], "Слабая новость в расписание не попадает")

    # Не наши валюты отбрасываются
    rows = ts.build_schedule(["EURUSD"], [ev(60, currency="JPY")], MONDAY)
    check(rows == [], "Новость по чужой валюте отброшена — не засоряет список")

    rows = ts.build_schedule(["XAUUSD"], [ev(60, currency="XAU", name="Запасы золота")], MONDAY)
    check(len(rows) == 1 and rows[0]["symbols"] == ["XAUUSD"],
          "Новость по золоту привязана только к золоту", str(rows))

    # Идущее сейчас окно
    rows = ts.build_schedule(SYMBOLS, [ev(-10)], MONDAY)
    check(rows and rows[0]["active_now"],
          "Новость 10 минут назад — окно ещё идёт, помечено как активное")

    # Давно прошедшее окно уже не показываем
    rows = ts.build_schedule(SYMBOLS, [ev(-120)], MONDAY)
    check(rows == [], "Окно, которое давно закончилось, из расписания убрано")

    # Сортировка по времени
    rows = ts.build_schedule(SYMBOLS, [ev(300), ev(60), ev(180)], MONDAY)
    check([r["time"] for r in rows] == sorted(r["time"] for r in rows),
          "Записи отсортированы по времени")

    # Горизонт соблюдается
    rows = ts.build_schedule(SYMBOLS, [ev(60 * 30)], MONDAY, hours_ahead=24)
    check(rows == [], "Событие за пределами горизонта не показывается")

    # Выключенный фильтр новостей — расписание пустое, и это честно
    CFG.USE_NEWS_FILTER = False
    check(ts.build_schedule(SYMBOLS, [ev(60)], MONDAY) == [],
          "USE_NEWS_FILTER=False — новости ничего не останавливают")
    CFG.USE_NEWS_FILTER = True


# =====================================================================
# 3. Статус "прямо сейчас"
# =====================================================================
def test_current_status() -> None:
    print("\n[Статус прямо сейчас]")

    CFG.USE_NEWS_FILTER = True
    CFG.USE_TRADING_HOURS = False
    CFG.NEWS_HARD_BLOCK_WINDOW_MIN = 30

    st = ts.current_status(SYMBOLS, [], MONDAY)
    check(st["trading"] is True, "Ничего не мешает — торгует")
    check(st["reason"] == ts.REASON_OK, "Причина: работает")

    st = ts.current_status(SYMBOLS, [ev(-5)], MONDAY)
    check(st["trading"] is False, "Идёт окно важной новости — не входит")
    check(st["reason"] == ts.REASON_NEWS, "Причина: рядом важная новость")
    check("Nonfarm" in st["detail"], "В пояснении названа сама новость", st["detail"])
    check(st["until"] == MONDAY + timedelta(minutes=25),
          "Указано, когда вход откроется", str(st["until"]))

    # Средняя новость статус не меняет — она не блокирует
    st = ts.current_status(SYMBOLS, [ev(-5, impact="medium")], MONDAY)
    check(st["trading"] is True, "Средняя новость торговлю не останавливает")

    # Часы торговли
    CFG.USE_TRADING_HOURS = True
    CFG.TRADING_START_HOUR = 8
    CFG.TRADING_END_HOUR = 20
    st = ts.current_status(SYMBOLS, [], MONDAY.replace(hour=3))
    check(st["trading"] is False and st["reason"] == ts.REASON_HOURS,
          "Вне разрешённых часов — не входит", str(st))
    check("08:00" in st["detail"], "В пояснении показаны разрешённые часы", st["detail"])
    CFG.USE_TRADING_HOURS = False

    # Выходные важнее всего: в субботу рынок закрыт
    saturday = datetime(2026, 8, 8, 12, 0, 0)
    check(saturday.weekday() == 5, "Проверочная дата — суббота")
    st = ts.current_status(SYMBOLS, [], saturday)
    check(st["trading"] is False and st["reason"] == ts.REASON_WEEKEND,
          "Суббота — рынок закрыт", str(st))

    sunday = datetime(2026, 8, 9, 12, 0, 0)
    st = ts.current_status(SYMBOLS, [], sunday)
    check(st["trading"] is False and st["reason"] == ts.REASON_WEEKEND, "Воскресенье — тоже")


def test_next_block() -> None:
    print("\n[Ближайшая пауза]")

    CFG.USE_NEWS_FILTER = True
    CFG.NEWS_HARD_BLOCK_WINDOW_MIN = 30

    nxt = ts.next_block(SYMBOLS, [ev(300), ev(120), ev(600)], MONDAY)
    check(nxt is not None and nxt["time"] == MONDAY + timedelta(minutes=120),
          "Выбрана самая ранняя из будущих пауз", str(nxt and nxt["time"]))

    check(ts.next_block(SYMBOLS, [], MONDAY) is None, "Пауз нет — None")
    check(ts.next_block(SYMBOLS, [ev(60, impact="medium")], MONDAY) is None,
          "Средняя новость паузой не считается")
    # Идущее сейчас окно — это не «ближайшая будущая» пауза
    check(ts.next_block(SYMBOLS, [ev(-5)], MONDAY) is None,
          "Уже идущее окно не выдаётся за будущее")


def test_quiet_windows() -> None:
    print("\n[Спокойные окна]")

    CFG.USE_NEWS_FILTER = True
    CFG.USE_TRADING_HOURS = False
    CFG.NEWS_HARD_BLOCK_WINDOW_MIN = 30

    free = ts.quiet_windows(SYMBOLS, [], MONDAY, hours_ahead=6)
    check(len(free) == 1 and free[0][0] == MONDAY,
          "Без новостей всё время свободно", str(free))

    # Одна новость через 2 часа разрезает интервал на два
    free = ts.quiet_windows(SYMBOLS, [ev(120)], MONDAY, hours_ahead=6)
    check(len(free) == 2, "Новость разрезает свободное время на два куска", str(free))
    if len(free) == 2:
        check(free[0][1] == MONDAY + timedelta(minutes=90),
              "Первое окно кончается за 30 мин до новости")
        check(free[1][0] == MONDAY + timedelta(minutes=150),
              "Второе начинается через 30 мин после")

    # Перекрывающиеся окна должны склеиться, а не удвоиться
    free = ts.quiet_windows(SYMBOLS, [ev(120), ev(140)], MONDAY, hours_ahead=6)
    check(len(free) == 2, "Соседние новости дают ОДНУ общую паузу", str(free))
    if len(free) == 2:
        check(free[1][0] == MONDAY + timedelta(minutes=170),
              "Пауза кончается после ПОСЛЕДНЕЙ из соседних новостей", str(free[1][0]))

    # Свободные окна не пересекаются и идут по возрастанию
    rows = [ev(60), ev(200), ev(220), ev(400)]
    free = ts.quiet_windows(SYMBOLS, rows, MONDAY, hours_ahead=10)
    ok = all(free[i][1] <= free[i + 1][0] for i in range(len(free) - 1))
    check(ok, "Свободные окна не пересекаются", str(free))
    check(all(b > a for a, b in free), "Каждое окно имеет положительную длину", str(free))

    # Порядок событий на входе не должен влиять на результат. Внутри идёт
    # проход слева направо, и без сортировки далёкое окно сдвинуло бы курсор
    # вперёд — раннее занятое время попало бы в "свободные".
    shuffled = ts.quiet_windows(SYMBOLS, [ev(400), ev(60), ev(220), ev(200)],
                                MONDAY, hours_ahead=10)
    check(shuffled == free, "Результат не зависит от порядка событий на входе",
          f"{shuffled} vs {free}")

    # Ни одно свободное окно не должно накрывать время блокировки
    blocked = [(r["start"], r["end"]) for r in
               ts.build_schedule(SYMBOLS, rows, MONDAY, hours_ahead=10)
               if r["action"] == ts.ACTION_BLOCK]
    overlap = [(f, b) for f in free for b in blocked if f[0] < b[1] and b[0] < f[1]]
    check(not overlap, "Свободное окно не накрывает время блокировки", str(overlap))

    # Часы торговли тоже вырезают время
    CFG.USE_TRADING_HOURS = True
    CFG.TRADING_START_HOUR = 8
    CFG.TRADING_END_HOUR = 14
    free = ts.quiet_windows(SYMBOLS, [], MONDAY.replace(hour=12), hours_ahead=6)
    check(free and free[0][1] <= MONDAY.replace(hour=14),
          "Свободное время кончается на границе разрешённых часов", str(free))

    # САМЫЙ КОВАРНЫЙ СЛУЧАЙ: новости И ограничение часов сразу. Внутри эти
    # два источника занятого времени складываются в один список — новостные
    # окна сначала, часовые следом, — и такой список УЖЕ НЕ упорядочен по
    # времени. Без сортировки позднее новостное окно сдвинуло бы курсор
    # вперёд, и запрещённые ранние часы оказались бы помечены как свободные.
    start = MONDAY.replace(hour=9)
    free = ts.quiet_windows(SYMBOLS, [ev(360, base=start)], start, hours_ahead=8)
    forbidden = [(a, b) for a, b in free
                 if any(not ts.within_trading_hours(a + timedelta(minutes=m))
                        for m in range(0, int((b - a).total_seconds() // 60), 15))]
    check(not forbidden,
          "Новости и часы вместе: запрещённые часы не попадают в свободные окна",
          str(forbidden))

    blocked = [(r["start"], r["end"]) for r in
               ts.build_schedule(SYMBOLS, [ev(360, base=start)], start, hours_ahead=8)
               if r["action"] == ts.ACTION_BLOCK]
    overlap = [(f, b) for f in free for b in blocked if f[0] < b[1] and b[0] < f[1]]
    check(not overlap, "Новости и часы вместе: блокировка не попала в свободное окно",
          str(overlap))
    CFG.USE_TRADING_HOURS = False


# =====================================================================
# 4. Вкладка в программе
# =====================================================================
def test_gui_wiring() -> None:
    print("\n[Вкладка «Календарь» в программе]")

    src = (APP / "desktop_app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    check("_build_tab_schedule" in funcs, "Вкладка собирается")
    check("_apply_schedule" in funcs, "Есть заполнение расписания")
    check('"Календарь": tab_schedule' in src, "Вкладка зарегистрирована в списке")
    check("import trading_schedule as tsched" in src, "Модуль расписания подключён")

    # Расписание должно получать события ВКЛЮЧАЯ только что прошедшие, иначе
    # идущее прямо сейчас окно блокировки никогда не покажется.
    worker = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_refresh_news_worker":
            worker = ast.get_source_segment(src, node)
    check(worker is not None, "Загрузчик новостей найден")
    if worker:
        check("for_schedule" in worker,
              "Для расписания готовится отдельный список событий")
        check("recent" in worker,
              "В него попадают и недавно прошедшие новости — их окно ещё идёт")

    # График обязан фильтровать события по вашим парам так же, как таблица.
    # Иначе он рисовал бы зону блокировки на японской статистике, а бот по
    # EURUSD и золоту в этот момент спокойно торговал бы.
    chart = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_draw_news_chart":
            chart = ast.get_source_segment(src, node)
    check(chart is not None, "Функция графика найдена")
    if chart:
        check("affected_symbols" in chart,
              "График показывает только события, затрагивающие ваши пары")
        check("_watched_symbols" in chart,
              "Список пар берётся тот же, что и для таблицы расписания")

    # Расписание не должно заводить собственные числа вместо настроек
    sched_src = (APP / "trading_schedule.py").read_text(encoding="utf-8")
    check("NEWS_HARD_BLOCK_WINDOW_MIN" in sched_src,
          "Окно блокировки читается из настроек")
    check("news_calendar._symbol_codes" in sched_src,
          "Разбор символа переиспользуется, а не написан заново")
    for forbidden in ("order_send", "close_position", "modify_position"):
        check(forbidden not in sched_src,
              f"Расписание ничего не исполняет: нет {forbidden}")


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ РАСПИСАНИЯ РАБОТЫ БОТА")
    print("=" * 62)

    test_matches_real_filters()
    test_block_window_matches_news_filter()
    test_build_schedule()
    test_current_status()
    test_next_block()
    test_quiet_windows()
    test_gui_wiring()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
