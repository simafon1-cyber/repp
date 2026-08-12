#!/usr/bin/env python3
"""Тесты хранения замеров пар в файле.

ОТКУДА ЗАДАЧА. Владелец: «пусть просто один раз загружает все пары и хранит у
себя в файлах, чтобы не было такой долгой загрузки».

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ:
  1. Замеренная пара при следующем запуске НЕ замеряется заново — иначе весь
     смысл теряется.
  2. Незамеренные пары дозамеряются, и очередь до них ДОХОДИТ: пара, которой
     нет в файле, не торгуется вообще, и «забыть» её — тихая потеря.
  3. Файл от другого брокера не подмешивается: у другого брокера другие
     спреды и другие имена.
  4. Испорченный файл не роняет программу и не подсовывает мусор.

Запуск:  python3 tests/test_symbol_cache.py
"""

from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import time
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
        print(f"  СБОЙ {name}" + (f" -> {detail}" if detail else ""))


cfg = types.ModuleType("config")
cfg.__file__ = str(APP / "config.py.example")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg
CFG = cfg
sys.modules["MetaTrader5"] = types.ModuleType("MetaTrader5")

import symbol_cache as sc      # noqa: E402


def row(symbol, at=None):
    return {"symbol": symbol, "spread_points": 10, "atr_points": 200,
            "min_lot": 0.01, "money_per_point": 1.0, "stop_points": 300,
            "trade_mode": 4, "measured_at": at if at is not None else time.time()}


def test_saved_measurements_come_back() -> None:
    print("\n[Замеры переживают перезапуск]")
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "s.json")
        data = {"EURUSD": row("EURUSD"), "GBPUSD": row("GBPUSD")}
        check(sc.save(data, server="Demo", path=path), "Файл записан")
        back = sc.load(path=path, server="Demo")
        check(set(back) == {"EURUSD", "GBPUSD"}, "И прочитан целиком", str(list(back)))
        check(back["EURUSD"]["atr_points"] == 200, "Значения не потеряны")

        # Тот же файл, тот же брокер — замерять больше нечего
        check(sc.to_survey(["EURUSD", "GBPUSD"], back, limit=60) == [],
              "Замеренные пары повторно НЕ замеряются — ради этого всё и делалось")


def test_unmeasured_pairs_get_their_turn() -> None:
    """Пара, которой нет в файле, не торгуется вообще. Если очередь до неё не
    дойдёт, это тихая потеря: ошибок нет, сделок тоже."""
    print("\n[До незамеренных пар очередь доходит]")
    cached = {f"OLD{i}": row(f"OLD{i}") for i in range(50)}
    names = list(cached) + [f"NEW{i}" for i in range(30)]

    queue = sc.to_survey(names, cached, limit=10)
    check(len(queue) == 10, "За раз замеряется не больше заданного", str(len(queue)))
    check(all(n.startswith("NEW") for n in queue),
          "И первыми идут те, которых в файле НЕТ — они пока не торгуются",
          str(queue[:3]))

    # За несколько запусков покрываются ВСЕ
    seen = dict(cached)
    for _ in range(5):
        batch = sc.to_survey(names, seen, limit=10)
        if not batch:
            break
        seen = sc.merge(seen, [row(n) for n in batch])
    check(set(names) <= set(seen),
          "За несколько запусков замерены все пары брокера",
          str(sorted(set(names) - set(seen))[:5]))

    check(sc.to_survey([], cached, 10) == [], "Пустой список не роняет")
    check(sc.to_survey(None, None, 10) == [], "None тоже")
    check(len(sc.to_survey(names, {}, limit=0)) == len(names),
          "Нулевой предел означает «все», а не «ни одной»")


def test_stale_rows_are_refreshed_oldest_first() -> None:
    """Спред зависит от времени суток: замер, сделанный ночью, показывает
    пару хуже, чем она есть. Полностью это не лечится — лечится тем, что
    записи постепенно обновляются, начиная с самых старых."""
    print("\n[Старые замеры обновляются, начиная с самых старых]")
    now = 1_000_000.0
    hour = 3600.0
    cached = {
        "FRESH": row("FRESH", at=now - hour),           # час назад
        "OLD": row("OLD", at=now - 30 * hour),          # больше суток
        "ANCIENT": row("ANCIENT", at=now - 100 * hour),
    }
    check(sc.is_fresh(cached["FRESH"], now), "Часовой замер ещё годится")
    check(not sc.is_fresh(cached["OLD"], now), "Замер старше суток — на обновление")

    queue = sc.to_survey(["FRESH", "OLD", "ANCIENT"], cached, limit=5, now=now)
    check(queue == ["ANCIENT", "OLD"],
          "Обновляем устаревшие, самый старый первым; свежий не трогаем",
          str(queue))

    # Новых пар всё равно вперёд: они пока не торгуются вовсе
    cached2 = dict(cached)
    queue = sc.to_survey(["FRESH", "OLD", "ANCIENT", "NEW"], cached2, limit=5, now=now)
    check(queue[0] == "NEW", "Новая пара важнее обновления старой", str(queue))

    check(not sc.is_fresh({}, now), "Без отметки времени замер не годится")
    check(not sc.is_fresh({"measured_at": "мусор"}, now), "Мусор в отметке — тоже")
    check(sc.is_fresh(cached["ANCIENT"], now, max_age_hours=0),
          "Нулевой срок = обновление выключено, замер годится всегда")


def test_other_broker_is_not_mixed_in() -> None:
    """У другого брокера другие спреды и другие имена пар. Подмешать его
    замеры — значит отбирать пары по чужим числам."""
    print("\n[Замеры другого брокера не подмешиваются]")
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "s.json")
        sc.save({"EURUSD": row("EURUSD")}, server="BrokerA", path=path)
        check(sc.load(path=path, server="BrokerA") != {}, "Свой брокер — читаем")
        check(sc.load(path=path, server="BrokerB") == {},
              "Чужой брокер — начинаем с нуля, а не смешиваем")
        check(sc.load(path=path, server="") != {},
              "Сервер неизвестен — читаем, но это единственная поблажка")


def test_broken_file_does_not_break_the_program() -> None:
    print("\n[Испорченный файл не роняет программу]")
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "s.json")

        check(sc.load(path=os.path.join(folder, "нет-такого.json")) == {},
              "Файла нет — просто пусто")

        for text, why in (("не json вовсе", "не JSON"),
                          ("[1, 2, 3]", "вместо словаря список"),
                          ('{"version": 999, "symbols": {}}', "чужой формат"),
                          ('{"version": 1, "symbols": "строка"}', "мусор внутри")):
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            check(sc.load(path=path) == {}, f"Не падаем и не верим ({why})")

        # Строки-мусор внутри правильного файла отбрасываются поштучно
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": sc.VERSION, "symbols":
                       {"GOOD": row("GOOD"), "BAD": "не словарь"}}, f)
        back = sc.load(path=path)
        check(list(back) == ["GOOD"], "Годная строка сохранена, мусорная выброшена",
              str(list(back)))

        check(sc.save({"A": row("A")}, path=os.path.join(folder, "нет", "пути")) is False,
              "Некуда записать — сообщаем неудачу, а не падаем")


def test_delisted_symbols_are_dropped() -> None:
    print("\n[Пары, которых у брокера больше нет, не предлагаются]")
    cached = {"EURUSD": row("EURUSD"), "СНЯТА": row("СНЯТА")}
    rows = sc.usable_rows(cached, ["EURUSD", "GBPUSD"])
    check([r["symbol"] for r in rows] == ["EURUSD"],
          "Снятый с торгов инструмент отброшен", str([r["symbol"] for r in rows]))
    check(len(sc.usable_rows(cached)) == 2, "Без списка брокера отдаём всё")
    check(sc.usable_rows(None) == [], "Пусто не роняет")


def test_merge_prefers_fresh() -> None:
    print("\n[Свежий замер важнее сохранённого]")
    old = {"EURUSD": row("EURUSD", at=1.0)}
    fresh = dict(row("EURUSD"))
    fresh["atr_points"] = 999
    merged = sc.merge(old, [fresh], now=500.0)
    check(merged["EURUSD"]["atr_points"] == 999, "Значения обновились")
    check(merged["EURUSD"]["measured_at"] == 500.0,
          "И отметка времени проставлена заново — иначе запись «вечно свежая»")
    check(sc.merge({}, None) == {}, "Пусто не роняет")
    check(sc.merge({"A": row("A")}, ["не словарь"]) .get("A") is not None,
          "Мусор в свежих данных не стирает сохранённое")


def test_wired_into_startup() -> None:
    print("\n[Хранилище подключено к запуску]")
    src = (APP / "main.py").read_text(encoding="utf-8")
    check("import symbol_cache" in src, "Модуль подключён")

    func = next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef) and n.name == "auto_pick_symbols")
    calls = [ast.unparse(n.func) for n in ast.walk(func)
             if isinstance(n, ast.Call)]
    for needed in ("symbol_cache.load", "symbol_cache.to_survey",
                   "symbol_cache.merge", "symbol_cache.save",
                   "symbol_cache.usable_rows"):
        check(needed in calls, f"Вызывается {needed}", str(calls[:6]))

    body = ast.unparse(func)
    # Замерять надо ТОЛЬКО очередь из хранилища, иначе файл ничего не экономит
    check("for name in shortlist" in body, "Замеряется только очередь из файла")
    check(body.index("symbol_cache.to_survey") < body.index("survey_symbol(name)"),
          "Очередь берётся ДО замеров")
    check(body.index("symbol_cache.save") > body.index("survey_symbol(name)"),
          "А сохранение — ПОСЛЕ них")

    # Отбор идёт по ВСЕМ сохранённым замерам, а не только по свежезамеренным:
    # иначе при полном файле выбирать было бы не из чего
    pick_call = [n for n in ast.walk(func) if isinstance(n, ast.Call)
                 and ast.unparse(n.func) == "symbol_picker.pick"]
    check(len(pick_call) == 1, "Отбор вызывается один раз")
    if pick_call:
        first = ast.unparse(pick_call[0].args[0])
        check("usable_rows" in first,
              "И отбирает из сохранённых замеров, а не только из свежих", first)


def test_file_is_not_committed() -> None:
    """В файле — инструменты конкретного брокера. У другого человека они
    другие, и попадание файла в репозиторий сломало бы ему отбор."""
    print("\n[Файл замеров не попадает в репозиторий]")
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    check(sc.CACHE_FILE in ignore, f"{sc.CACHE_FILE} в .gitignore")
    check(not (APP / sc.CACHE_FILE).exists() or sc.CACHE_FILE in ignore,
          "И если он есть локально, то игнорируется")


def test_progress_line_does_not_lie() -> None:
    """Строка в журнале — то, по чему человек судит о происходящем. Если она
    показывает «замерено 200 из 50», доверять ей нельзя вообще."""
    print("\n[Строка о ходе замеров не врёт]")
    cached = {f"S{i}": row(f"S{i}") for i in range(200)}

    # Первый этап оборвался по времени: просмотрено меньше, чем уже в файле
    line = sc.describe(cached, total=50, measured_now=0)
    check("200 из 50" not in line, "Не пишем «200 из 50»", line)
    check("200 из 200" in line, "Считаем по большему числу", line)
    check("замерю при следующих" not in line,
          "И не обещаем догнать то, что уже сделано", line)

    line = sc.describe({"A": row("A")}, total=10, measured_now=1)
    check("1 из 10" in line, "Обычный случай считается как есть", line)
    check("добавлено 1" in line, "Сказано, сколько добавлено в этот раз", line)
    check("9" in line, "И сколько осталось", line)

    check(sc.describe({}, total=0, measured_now=0) != "", "Пусто не роняет")


if __name__ == "__main__":
    print("=" * 62)
    print("ТЕСТЫ: ЗАМЕРЫ ПАР ХРАНЯТСЯ В ФАЙЛЕ")
    print("=" * 62)
    test_saved_measurements_come_back()
    test_unmeasured_pairs_get_their_turn()
    test_stale_rows_are_refreshed_oldest_first()
    test_other_broker_is_not_mixed_in()
    test_broken_file_does_not_break_the_program()
    test_delisted_symbols_are_dropped()
    test_merge_prefers_fresh()
    test_wired_into_startup()
    test_file_is_not_committed()
    test_progress_line_does_not_lie()
    print()
    print("=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    sys.exit(1 if failed else 0)
