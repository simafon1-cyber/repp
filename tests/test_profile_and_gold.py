#!/usr/bin/env python3
"""Тесты двух решений владельца при переходе на депозит 500-1000:

  1. «сделай этот режим основным при запуске программы» — профиль по
     умолчанию «Агрессивный» вместо «Истерички»;
  2. «отключи торговлю золота».

ПОЧЕМУ ЭТО НЕ ОДНА СТРОЧКА В НАСТРОЙКАХ

Список торгуемых пар приходит из ЧЕТЫРЁХ мест сразу: SYMBOLS в config.py,
поле symbols у каждого счёта в accounts.json, добавление руками на вкладке
«Символы» и переключатель на дашборде. Вычеркнуть золото из одного места —
оставить три двери открытыми. Плюс у разных брокеров золото называется
по-разному: XAUUSD, XAUUSDs, XAUUSD.m.

А смена профиля важна не процентом риска, а тем, что у «Истерички» стоял
обход мягких фильтров, разом отключавший три защиты.

Запуск:  python3 tests/test_profile_and_gold.py
"""

from __future__ import annotations

import ast
import os
import sys
import tempfile
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


_mt5 = _FakeMT5("MetaTrader5")
_mt5.initialize = lambda *a, **k: False
_mt5.symbol_info = lambda *a, **k: None
sys.modules["MetaTrader5"] = _mt5

import config_migrate as cm      # noqa: E402
import risk_manager as rm        # noqa: E402


# =====================================================================
# ПРОФИЛЬ ПО УМОЛЧАНИЮ
# =====================================================================
def test_default_profile_is_aggressive() -> None:
    print("\n[При запуске включается «Агрессивный»]")
    check(CFG.RISK_PROFILE == CFG.RiskProfile.AGGRESSIVE,
          "Профиль по умолчанию — «Агрессивный»", str(CFG.RISK_PROFILE))

    profile = CFG.RISK_PROFILES[CFG.RISK_PROFILE]
    hysteric = CFG.RISK_PROFILES[CFG.RiskProfile.HYSTERIC]

    # ГЛАВНОЕ, ради чего менялся профиль. У «Истерички» обход мягких фильтров
    # разом отключал паузу вокруг полуночи брокера, защиту от скачка
    # волатильности и проверку «спред не съедает цель».
    check(hysteric["ignore_soft_filters"] is True,
          "У «Истерички» обход фильтров действительно был включён "
          "(иначе проверка ниже ничего не значит)")
    check(profile["ignore_soft_filters"] is False,
          "А на «Агрессивном» мягкие фильтры РАБОТАЮТ")

    check(profile["min_score_to_trade"] > hysteric["min_score_to_trade"],
          "Отбор сигналов строже",
          f"{hysteric['min_score_to_trade']} -> {profile['min_score_to_trade']}")
    check(profile["max_open_positions"] < hysteric["max_open_positions"],
          "Одновременных сделок на пару меньше",
          f"{hysteric['max_open_positions']} -> {profile['max_open_positions']}")
    check(profile["atr_sl_multiplier"] > hysteric["atr_sl_multiplier"],
          "Стоп шире — меньше выбивает шумом",
          f"{hysteric['atr_sl_multiplier']} -> {profile['atr_sl_multiplier']}")


def test_profile_migration_respects_manual_choice() -> None:
    print("\n[Профиль переключается один раз и не спорит с человеком]")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "config.py")

        def write(body: str) -> None:
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)

        def profile_of() -> str:
            text = open(path, encoding="utf-8").read()
            for node in ast.parse(text).body:
                if (isinstance(node, ast.Assign) and node.targets
                        and getattr(node.targets[0], "id", "") == "RISK_PROFILE"):
                    return getattr(node.value, "attr", "?")
            return ""

        # Заводская «Истеричка» -> переключаем
        write("RISK_PROFILE = RiskProfile.HYSTERIC\nSYMBOLS = ['EURUSD']\n")
        notes = cm.apply_one_time(path)
        check(profile_of() == "AGGRESSIVE", "Заводская «Истеричка» заменена",
              profile_of())
        check(any("Агрессивн" in n for n in notes),
              "Человеку объяснили, что изменилось")

        # Человек выбрал «Консервативный» сам -> не трогаем
        write("RISK_PROFILE = RiskProfile.CONSERVATIVE\nSYMBOLS = ['EURUSD']\n")
        cm.apply_one_time(path)
        check(profile_of() == "CONSERVATIVE",
              "Выбранный человеком профиль остаётся нетронутым", profile_of())

        # Уже переключали -> второй раз не лезем
        write("RISK_PROFILE = RiskProfile.HYSTERIC\n"
              "MIGRATED_AGGRESSIVE_PROFILE = True\nSYMBOLS = ['EURUSD']\n")
        cm.apply_one_time(path)
        check(profile_of() == "HYSTERIC",
              "Вернул «Истеричку» вручную — повторно не переключаем",
              profile_of())


# =====================================================================
# ЗОЛОТО ВЫКЛЮЧЕНО
# =====================================================================
def test_gold_is_blocked() -> None:
    print("\n[Золото не торгуется]")
    check("XAUUSD" in CFG.BLOCKED_SYMBOLS, "Золото в списке выключенных",
          str(CFG.BLOCKED_SYMBOLS))
    check(rm.blocked_symbol_reason("XAUUSD") != "", "XAUUSD выключен")
    check(rm.blocked_symbol_reason("EURUSD") == "", "EURUSD торгуется как обычно")


def test_broker_suffixes_are_covered() -> None:
    """У разных брокеров золото называется по-разному. Список, работающий
    только на точное совпадение, у половины брокеров молча не блокировал бы
    ничего."""
    print("\n[Любое написание золота у брокера]")
    for name in ("XAUUSD", "XAUUSDs", "XAUUSD.m", "XAUUSD_i", "xauusd",
                 "XAUUSDm", "xau/usd"):
        check(rm.blocked_symbol_reason(name) != "", f"{name} выключен")

    # И ничего лишнего: похожие имена других инструментов не должны попасть
    for name in ("EURUSD", "XAGUSD", "USDJPY", "GBPUSD", "AUDUSD"):
        check(rm.blocked_symbol_reason(name) == "", f"{name} НЕ задет")


def test_blocked_list_edge_cases() -> None:
    print("\n[Пустой список и мусор]")
    saved = CFG.BLOCKED_SYMBOLS
    try:
        CFG.BLOCKED_SYMBOLS = []
        check(rm.blocked_symbol_reason("XAUUSD") == "",
              "Пустой список — не блокируем ничего")
        CFG.BLOCKED_SYMBOLS = ["", None, "  "]
        check(rm.blocked_symbol_reason("XAUUSD") == "",
              "Мусор в списке не превращается в запрет всего")
        CFG.BLOCKED_SYMBOLS = ["XAUUSD"]
        check(rm.blocked_symbol_reason("") == "",
              "Пустое имя пары не падает и не блокируется")
        check(rm.blocked_symbol_reason(None) == "", "None тоже")
    finally:
        CFG.BLOCKED_SYMBOLS = saved


def _process_symbol_src() -> str:
    src = (APP / "main.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "process_symbol")
    return ast.get_source_segment(src, fn) or ""


def test_open_gold_position_is_still_managed() -> None:
    """САМОЕ ВАЖНОЕ В ЭТОЙ ПРАВКЕ. Запрет касается только НОВЫХ входов. Если
    по золоту уже висит открытая сделка, её обязаны довести до конца:
    трейлинг, безубыток, частичное закрытие. Поставь проверку раньше ведения
    позиций — и такая сделка осталась бы вообще без присмотра, со стопом на
    исходном месте."""
    print("\n[Уже открытая сделка по золоту продолжает вестись]")
    src = _process_symbol_src()
    check("blocked_symbol_reason" in src, "Проверка есть в торговом цикле")
    check(src.index("manage_open_positions") < src.index("blocked_symbol_reason"),
          "Ведение открытых сделок стоит РАНЬШЕ запрета — иначе открытая "
          "сделка по золоту осталась бы без трейлинга")


def test_block_actually_blocks() -> None:
    """Причина должна ЗАПРЕЩАТЬ вход, а не просто вычисляться."""
    print("\n[Запрет действительно закрывает вход]")
    tree = ast.parse(_process_symbol_src())
    works = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Name):
            continue
        if node.test.id != "symbol_blocked":
            continue
        body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
        if "Return" in body and "last_reject_reason" in body:
            works = True
    check(works,
          "Найденный запрет закрывает вход: выход из функции и объяснение "
          "человеку")


def test_gold_migration() -> None:
    print("\n[Золото убирается и из личного списка пар]")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "config.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write("RISK_PROFILE = RiskProfile.HYSTERIC\n"
                    "SYMBOLS = ['EURUSD', 'XAUUSD', 'GBPUSD', 'XAUUSDs']\n")
        notes = cm.apply_one_time(path)

        applied = types.ModuleType("t")
        exec(open(path, encoding="utf-8").read(),
             {"RiskProfile": CFG.RiskProfile, **applied.__dict__}, applied.__dict__)

        check("XAUUSD" in applied.BLOCKED_SYMBOLS, "Список выключенных записан",
              str(getattr(applied, "BLOCKED_SYMBOLS", None)))
        check("XAUUSD" not in applied.SYMBOLS and "XAUUSDs" not in applied.SYMBOLS,
              "Золото убрано из списка пар — иначе висело бы на вкладке "
              "«Символы» как рабочее", str(applied.SYMBOLS))
        check(applied.SYMBOLS == ["EURUSD", "GBPUSD"],
              "А остальные пары сохранены как были", str(applied.SYMBOLS))
        check(any("золото" in n.lower() for n in notes),
              "Человеку объяснили", str(notes))

        # Повторный запуск ничего не ломает и не возвращает
        cm.apply_one_time(path)
        again = types.ModuleType("t2")
        exec(open(path, encoding="utf-8").read(),
             {"RiskProfile": CFG.RiskProfile, **again.__dict__}, again.__dict__)
        check(again.SYMBOLS == ["EURUSD", "GBPUSD"],
              "Повторная миграция ничего не меняет", str(again.SYMBOLS))


def test_fresh_example_stays_valid() -> None:
    """Миграция поверх свежего эталона обязана оставить рабочий файл."""
    print("\n[Миграция поверх эталона не ломает config.py]")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "config.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write((APP / "config.py.example").read_text(encoding="utf-8"))
        cm.apply_one_time(path)
        try:
            mod = types.ModuleType("z")
            exec(open(path, encoding="utf-8").read(), mod.__dict__)
            check(True, "Файл после миграции разбирается")
            check(mod.RISK_PROFILE == mod.RiskProfile.AGGRESSIVE,
                  "Профиль остался «Агрессивным»")
            check("XAUUSD" in mod.BLOCKED_SYMBOLS, "Золото осталось выключенным")
        except SyntaxError as e:
            check(False, "Файл после миграции разбирается", str(e))


def test_перенос_настроек_не_подвешивает_запуск() -> None:
    """Перенос настроек обязан быть БЫСТРЫМ, а не «когда-нибудь».

    ОТКУДА ЭТО. Разбор config.py брал кусок исходника через
    ast.get_source_segment, а тот режет весь файл заново для КАЖДОЙ
    строки. На эталонном config.py и тринадцати переносах выходило
    88 СЕКУНД. Перенос выполняется при запуске программы — то есть после
    установки новой версии окно полторы минуты стояло молча. Со стороны
    это неотличимо от «зависла», и владелец такое уже описывал словами
    «программа не открывается».

    Порог 15 секунд взят с большим запасом: после правки выходит около
    0,1 секунды. Со старым кодом проверка падает — 88 секунд в порог не
    лезут никаким запасом."""
    print("\n[Перенос настроек не подвешивает запуск]")
    import time
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "config.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write((APP / "config.py.example").read_text(encoding="utf-8"))
        начало = time.monotonic()
        notes = cm.apply_one_time(path)
        ушло = time.monotonic() - начало
    check(ушло < 15.0,
          f"Перенос уложился в 15 секунд (ушло {ушло:.2f} с)",
          f"{ушло:.1f} с")
    check(bool(notes), "И перенос действительно что-то сделал",
          str(len(notes)))


def test_разбор_настроек_даёт_тот_же_текст() -> None:
    """Ускорение не имеет права менять РЕЗУЛЬТАТ.

    Здесь быстрый разбор сверяется с медленным, но заведомо правильным
    ast.get_source_segment — строка в строку, по всем настройкам
    эталонного файла. Если срез хоть где-то съедет на символ, видно
    будет сразу."""
    print("\n[Быстрый разбор совпадает с медленным, но верным]")
    текст = (APP / "config.py.example").read_text(encoding="utf-8")
    быстро = cm._top_level_assignments(текст)

    медленно = {}
    for node in ast.parse(текст).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        цель = node.targets[0]
        if not isinstance(цель, ast.Name):
            continue
        кусок = ast.get_source_segment(текст, node)
        if кусок is not None:
            медленно[цель.id] = кусок

    check(set(быстро) == set(медленно),
          f"Найдены те же настройки ({len(медленно)} шт.)",
          str(sorted(set(быстро) ^ set(медленно))[:5]))
    расхождения = [имя for имя in медленно
                   if быстро.get(имя, {}).get("source") != медленно[имя]]
    check(not расхождения, "И текст каждой совпадает дословно",
          str(расхождения[:5]))


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ: ПРОФИЛЬ ПО УМОЛЧАНИЮ И ВЫКЛЮЧЕНИЕ ЗОЛОТА")
    print("=" * 62)

    test_default_profile_is_aggressive()
    test_profile_migration_respects_manual_choice()

    test_gold_is_blocked()
    test_broker_suffixes_are_covered()
    test_blocked_list_edge_cases()
    test_open_gold_position_is_still_managed()
    test_block_actually_blocks()
    test_gold_migration()
    test_fresh_example_stays_valid()
    test_перенос_настроек_не_подвешивает_запуск()
    test_разбор_настроек_даёт_тот_же_текст()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
