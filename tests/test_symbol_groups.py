#!/usr/bin/env python3
"""ОТБОР ПАР НЕ ДОЛЖЕН ВЫПУСКАТЬ АКЦИИ В ВАЛЮТНУЮ ТОРГОВЛЮ.

ПОЧЕМУ ЭТОТ НАБОР ПОЯВИЛСЯ

У владельца в настройках стояло AUTO_PICK_GROUPS = ["Forex"], а счёт
торговал американскими акциями: AUPH, CDW, BMNR, ARVN, BHVN, CNH, CHYM —
семь акций по 16 долларов при счёте 384. Депозит 500, баланс 384.64,
убыток 115.29.

Причина в коде. Фильтр по разделу стоял только на ПЕРВОМ этапе отбора —
он решал, что замерять. Окончательный выбор берёт файл замеров, а
раздела в замере не было вообще: поле "path" читалось на первом этапе и
до файла не доходило. Инструмент, однажды попавший в файл, выбирался
дальше всегда — независимо от настройки.

ЧТО ПРОВЕРЯЕТСЯ ПО ФАКТУ

Что именно останется в списке после отсева, при разных разделах. И что
замер без раздела НЕ проходит: старый файл не должен становиться дырой.

ЧЕГО ЭТОТ НАБОР НЕ ПРОВЕРЯЕТ

Что брокер вернёт раздел в том же виде: у разных брокеров строка
отличается. Проверяется правило, а не справочник конкретного брокера.

Запуск:  python3 tests/test_symbol_groups.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
APP = ROOT / "ai_scalper_standalone"
sys.path.insert(0, str(BASE))
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


import symbol_picker as sp  # noqa: E402
import symbol_cache as sc  # noqa: E402


def ряд(имя, путь):
    return {"symbol": имя, "path": путь, "spread_points": 10,
            "atr_points": 200, "min_lot": 0.01, "money_per_point": 1.0}


def test_акции_не_проходят():
    print("\n[Акции не попадают в валютный отбор]")
    ряды = [
        ряд("EURUSD", "Forex\\\\Majors\\\\EURUSD"),
        ряд("AUPH", "Stocks\\\\US\\\\AUPH"),
        ряд("CHYM", "Stocks\\\\US\\\\CHYM"),
        ряд("GBPUSD", "Forex\\\\Majors\\\\GBPUSD"),
    ]
    оставили, убрали = sp.only_allowed_groups(ряды, ("Forex",))
    имена = [р["symbol"] for р in оставили]
    check(имена == ["EURUSD", "GBPUSD"],
          "Остались только валютные пары", str(имена))
    check(sorted(убрали) == ["AUPH", "CHYM"],
          "А обе акции названы отброшенными", str(убрали))


def test_замер_без_раздела_не_проходит():
    """Главная проверка. Старый файл замеров — без раздела; если пускать
    такие записи «на всякий случай», дыра остаётся ровно та же."""
    print("\n[Замер без раздела не проходит]")
    ряды = [ряд("EURUSD", "Forex\\\\Majors\\\\EURUSD"), ряд("СТАРЫЙ", "")]
    ряды[1].pop("path")
    оставили, убрали = sp.only_allowed_groups(ряды, ("Forex",))
    имена = [р["symbol"] for р in оставили]
    check(имена == ["EURUSD"], "Запись без раздела отброшена", str(имена))
    check(убрали == ["СТАРЫЙ"], "И названа", str(убрали))


def test_без_настройки_отсева_нет():
    print("\n[Пустая настройка = ограничения нет]")
    ряды = [ряд("AUPH", "Stocks\\\\US\\\\AUPH")]
    оставили, убрали = sp.only_allowed_groups(ряды, ())
    check(len(оставили) == 1 and not убрали,
          "Без AUTO_PICK_GROUPS ничего не отсеивается")


def test_регистр_и_разделители():
    print("\n[Раздел пишется у брокеров по-разному]")
    for путь in ("forex/majors/EURUSD", "FOREX\\\\Minors\\\\EURNZD",
                 "Forex.Exotic\\\\USDTRY"):
        оставили, _ = sp.only_allowed_groups([ряд("X", путь)], ("Forex",))
        check(len(оставили) == 1, f"«{путь}» признан валютным")


def test_старый_файл_замеров_не_читается():
    """Файл версии 1 раздела не содержит. Читать его — значит вернуть дыру."""
    print("\n[Файл замеров прежнего формата выбрасывается]")
    with tempfile.TemporaryDirectory() as папка:
        путь = str(Path(папка) / "symbols_survey.json")
        with open(путь, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "server": "", "saved_at": 0,
                       "symbols": {"AUPH": {"symbol": "AUPH"}}}, f)
        прочитано = sc.load(путь)
        check(прочитано == {}, "Файл версии 1 не читается", str(прочитано))
        check(sc.VERSION == 2, "Версия формата поднята до 2", str(sc.VERSION))

        # А свой, новый — читается, иначе проверка выше проходила бы и на
        # сломанной загрузке.
        sc.save({"EURUSD": {"symbol": "EURUSD", "path": "Forex\\\\Majors"}},
                path=путь)
        снова = sc.load(путь)
        check(list(снова) == ["EURUSD"], "Новый файл читается", str(снова))


def test_замер_сохраняет_раздел():
    """Без этого поля фильтр на окончательном выборе нечем кормить."""
    print("\n[Замер пары сохраняет раздел брокера]")
    import ast
    исходник = (APP / "main.py").read_text(encoding="utf-8")
    дерево = ast.parse(исходник)
    нашли = False
    for узел in ast.walk(дерево):
        if isinstance(узел, ast.FunctionDef) and узел.name == "survey_symbol":
            for под in ast.walk(узел):
                if isinstance(под, ast.Dict):
                    ключи = [k.value for k in под.keys
                             if isinstance(k, ast.Constant)]
                    if "symbol" in ключи and "path" in ключи:
                        нашли = True
    check(нашли, "survey_symbol кладёт «path» в замер")


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ: ОТБОР ПАР — ТОЛЬКО РАЗРЕШЁННЫЕ РАЗДЕЛЫ")
    print("=" * 62)
    test_акции_не_проходят()
    test_замер_без_раздела_не_проходит()
    test_без_настройки_отсева_нет()
    test_регистр_и_разделители()
    test_старый_файл_замеров_не_читается()
    test_замер_сохраняет_раздел()
    print("\n" + "=" * 70)
    print(f"Пройдено: {passed}   Сбоев: {failed}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
