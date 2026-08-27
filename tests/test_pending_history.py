#!/usr/bin/env python3
"""ЗАЯВКА, УСПЕВШАЯ ЗАКРЫТЬСЯ, — НЕ ПРОПАВШАЯ.

ПОЧЕМУ ЭТОТ НАБОР ПОЯВИЛСЯ

Владелец: «он сам себя задушил бот, ни одной сделки за три дня».

Сверка ожидаемых заявок смотрела ТОЛЬКО на открытые позиции. Заявка,
которая открылась и закрылась по стопу до следующего круга, среди
открытых не находилась — и объявлялась пропавшей. Открывался инцидент,
торговля вставала намертво. При этом заявка отработала штатно, и
доказательство лежало в истории сделок, куда никто не смотрел.

Функция mt5_connector.deals_or_none была написана ровно для этого, и в
её собственном описании это сказано: «позиция могла открыться и тут же
закрыться по стопу, и тогда среди открытых её нет, а в истории есть». Её
просто не подключили.

ЧТО ПРОВЕРЯЕТСЯ ПО ФАКТУ

Настоящий файл журнала, настоящие записи, и решение сверки: пропала
заявка или нашлась, и ГДЕ нашлась.

ЧЕГО ЭТОТ НАБОР НЕ ПРОВЕРЯЕТ

Что терминал отдаст историю в этом виде: MetaTrader здесь заглушка.
Что сделка в истории — действительно наша: сверяются magic, инструмент,
направление и время, но стопроцентной уверенности это не даёт.

Запуск:  python3 tests/test_pending_history.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
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


import pending_orders as po  # noqa: E402

MAGIC = 777


class Сделка:
    """Строка истории. Поля — как у терминала."""

    def __init__(self, symbol, тип, entry, magic, когда, order=555, volume=0.01):
        self.symbol = symbol
        self.type = тип          # 0 = buy, 1 = sell
        self.entry = entry       # 0 = вход в рынок
        self.magic = magic
        self.time = когда.timestamp()
        self.order = order
        self.volume = volume


class Позиция:
    def __init__(self, symbol, тип, magic, ticket=111):
        self.symbol = symbol
        self.type = тип
        self.magic = magic
        self.ticket = ticket
        self.volume = 0.01


def журнал(папка, symbol="CADMXN", направление=1):
    po.записать(symbol, направление, 0.01, 1.0, folder=папка)
    return po.открытые(папка)[0]


def test_без_истории_заявка_пропала():
    """Так было раньше — и так остаётся, если историю спросить не вышло."""
    print("\n[Истории нет — заявка считается пропавшей]")
    with tempfile.TemporaryDirectory() as п:
        журнал(п)
        итог = po.сверить([], folder=п, magic=MAGIC, сделки=None)
        check(len(итог["пропали"]) == 1,
              "Без истории заявка пропавшая — выдумывать нечего")


def test_закрытая_сделка_подтверждает_заявку():
    """Главная проверка."""
    print("\n[Заявка открылась и закрылась — она НЕ пропала]")
    with tempfile.TemporaryDirectory() as п:
        з = журнал(п)
        отправлена = datetime.fromisoformat(з["когда_utc"])
        сделка = Сделка("CADMXN", 0, 0, MAGIC, отправлена + timedelta(minutes=1))
        итог = po.сверить([], folder=п, magic=MAGIC, сделки=[сделка])
        check(not итог["пропали"], "Заявка не объявлена пропавшей",
              str(итог["пропали"]))
        check(len(итог["нашлись"]) == 1, "Она нашлась")
        if итог["нашлись"]:
            найдено = итог["нашлись"][0]
            check(найдено["где"] == "история сделок",
                  "И сказано, ГДЕ нашлась", str(найдено))
            check(найдено["тикет"] == 555,
                  "С номером заявки из истории", str(найдено))


def test_чужая_сделка_не_подтверждает():
    print("\n[Чужая сделка не годится]")
    with tempfile.TemporaryDirectory() as п:
        з = журнал(п)
        отправлена = datetime.fromisoformat(з["когда_utc"])
        чужие = [
            Сделка("CADMXN", 0, 0, MAGIC + 1, отправлена),      # чужой magic
            Сделка("EURUSD", 0, 0, MAGIC, отправлена),          # другая пара
            Сделка("CADMXN", 1, 0, MAGIC, отправлена),          # другая сторона
            Сделка("CADMXN", 0, 1, MAGIC, отправлена),          # это ВЫХОД
        ]
        итог = po.сверить([], folder=п, magic=MAGIC, сделки=чужие)
        check(len(итог["пропали"]) == 1,
              "Ни одна из четырёх чужих сделок заявку не подтвердила",
              str(итог["нашлись"]))


def test_сделка_раньше_отправки_не_годится():
    """Сделка, случившаяся ДО отправки заявки, не может быть её исполнением."""
    print("\n[Сделка раньше отправки]")
    with tempfile.TemporaryDirectory() as п:
        з = журнал(п)
        отправлена = datetime.fromisoformat(з["когда_utc"])
        рано = Сделка("CADMXN", 0, 0, MAGIC, отправлена - timedelta(hours=2))
        итог = po.сверить([], folder=п, magic=MAGIC, сделки=[рано])
        check(len(итог["пропали"]) == 1, "Не засчитана", str(итог["нашлись"]))


def test_позиция_важнее_истории():
    print("\n[Открытая позиция берётся раньше истории]")
    with tempfile.TemporaryDirectory() as п:
        з = журнал(п)
        отправлена = datetime.fromisoformat(з["когда_utc"])
        поз = Позиция("CADMXN", 0, MAGIC)
        сделка = Сделка("CADMXN", 0, 0, MAGIC, отправлена)
        итог = po.сверить([поз], folder=п, magic=MAGIC, сделки=[сделка])
        check(итог["нашлись"] and итог["нашлись"][0]["где"] == "позиция",
              "Нашлась как открытая позиция", str(итог["нашлись"]))


def test_одна_сделка_на_одну_заявку():
    """Две заявки не закрываются одной сделкой."""
    print("\n[Одна сделка подтверждает только одну заявку]")
    with tempfile.TemporaryDirectory() as п:
        журнал(п)
        журнал(п)
        з = po.открытые(п)[0]
        отправлена = datetime.fromisoformat(з["когда_utc"])
        сделка = Сделка("CADMXN", 0, 0, MAGIC, отправлена + timedelta(minutes=1))
        итог = po.сверить([], folder=п, magic=MAGIC, сделки=[сделка])
        check(len(итог["нашлись"]) == 1 and len(итог["пропали"]) == 1,
              "Подтверждена одна, вторая осталась пропавшей",
              f"нашлись={len(итог['нашлись'])} пропали={len(итог['пропали'])}")


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ: СВЕРКА ЗАЯВОК С ИСТОРИЕЙ СДЕЛОК")
    print("=" * 62)
    test_без_истории_заявка_пропала()
    test_закрытая_сделка_подтверждает_заявку()
    test_чужая_сделка_не_подтверждает()
    test_сделка_раньше_отправки_не_годится()
    test_позиция_важнее_истории()
    test_одна_сделка_на_одну_заявку()
    print("\n" + "=" * 70)
    print(f"Пройдено: {passed}   Сбоев: {failed}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
