#!/usr/bin/env python3
"""И3: СВЕРКА ФАКТИЧЕСКОГО СОСТОЯНИЯ ПОСЛЕ ИНЦИДЕНТА.

ПОЧЕМУ ЭТИ ТЕСТЫ ПОЯВИЛИСЬ

И2 научил программу не забывать о запрете после перезапуска. Но это была
только половина дела: человеку всё равно приходилось идти в терминал и
разбираться руками, а программа не помогала ничем и снять запрет не
позволяла никак, кроме как на глазок.

И3 добавляет две вещи:

  * программа сама собирает ФАКТЫ — позиции, активные заявки, историю
    заявок и сделок — и выносит вердикт;
  * снять инцидент можно ПО ДОКАЗАТЕЛЬСТВУ, а не по желанию.

ГЛАВНОЕ, ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ

Что «не выяснено» сохраняет запрет. Соблазн рассуждать «позиций сейчас не
видно — значит, всё чисто» здесь запрещён по той же причине, что и в
И1-C: отсутствие позиции в момент запроса — наблюдение, а не
доказательство. Заявка может быть ещё активна.

Запуск:  python3 tests/test_reconcile_after_incident.py
"""

from __future__ import annotations

import shutil
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
        print(f"  СБОЙ {name}" + (f" -> {detail}" if detail else ""))


cfg = types.ModuleType("config")
cfg.__file__ = str(APP / "config.py.example")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
cfg.LIVE_TRADING = False
sys.modules["config"] = cfg

# Состояния заявки
РАЗМЕЩЕНА, ОТМЕНЕНА, ИСПОЛНЕНА, ОТКЛОНЕНА = 1, 2, 4, 5


class _Позиция:
    def __init__(self, ticket, volume=0.01, symbol="EURUSD"):
        self.ticket = ticket
        self.symbol = symbol
        self.volume = volume
        self.type = 0
        self.magic = cfg.MAGIC_NUMBER


class _Заявка:
    def __init__(self, ticket, state, symbol="EURUSD"):
        self.ticket = ticket
        self.symbol = symbol
        self.state = state
        self.magic = cfg.MAGIC_NUMBER


class _Сделка:
    def __init__(self, order, position_id, volume=0.01, entry=0):
        self.order = order
        self.position_id = position_id
        self.volume = volume
        self.entry = entry
        self.symbol = "EURUSD"
        self.magic = cfg.MAGIC_NUMBER


class Счёт:
    """Поддельный счёт: позиции, заявки и сделки задаются прямо в тесте."""

    def __init__(self):
        self.позиции = []
        self.активные = {}
        self.завершённые = {}
        self.сделки_по_заявке = {}
        self.сделки_по_позиции = {}
        self.ломается = set()

    def positions_or_none(self, symbol=None, magic=None):
        return None if "позиции" in self.ломается else list(self.позиции)

    def order_by_ticket(self, ticket):
        if "активные" in self.ломается:
            return None
        з = self.активные.get(int(ticket))
        return [з] if з else []

    def history_order_by_ticket(self, ticket):
        if "завершённые" in self.ломается:
            return None
        з = self.завершённые.get(int(ticket))
        return [з] if з else []

    def deals_by_order(self, ticket):
        if "сделки" in self.ломается:
            return None
        return list(self.сделки_по_заявке.get(int(ticket), []))

    def deals_by_position(self, position_id):
        return list(self.сделки_по_позиции.get(int(position_id), []))


счёт = Счёт()

fake = types.ModuleType("MetaTrader5")
for _и, _з in (("TIMEFRAME_M1", 1), ("TIMEFRAME_M5", 5), ("TIMEFRAME_M15", 15),
               ("TIMEFRAME_M30", 30), ("TIMEFRAME_H1", 60),
               ("TIMEFRAME_H4", 240), ("TIMEFRAME_D1", 1440),
               ("ORDER_TYPE_BUY", 0), ("ORDER_TYPE_SELL", 1),
               ("ORDER_FILLING_IOC", 1), ("ORDER_FILLING_FOK", 2),
               ("ORDER_TIME_GTC", 0), ("TRADE_ACTION_DEAL", 1),
               ("TRADE_ACTION_SLTP", 2), ("SYMBOL_TRADE_MODE_FULL", 4),
               ("ORDER_STATE_STARTED", 0), ("ORDER_STATE_PLACED", 1),
               ("ORDER_STATE_CANCELED", 2), ("ORDER_STATE_PARTIAL", 3),
               ("ORDER_STATE_FILLED", 4), ("ORDER_STATE_REJECTED", 5),
               ("ORDER_STATE_EXPIRED", 6), ("DEAL_ENTRY_IN", 0),
               ("TRADE_RETCODE_DONE", 10009), ("TRADE_RETCODE_REQUOTE", 10004),
               ("TRADE_RETCODE_PRICE_CHANGED", 10020),
               ("TRADE_RETCODE_PRICE_OFF", 10021),
               ("POSITION_TYPE_BUY", 0), ("POSITION_TYPE_SELL", 1)):
    setattr(fake, _и, _з)
fake.positions_get = lambda **k: []
fake.history_deals_get = lambda *a, **k: []
fake.terminal_info = lambda: None
sys.modules["MetaTrader5"] = fake

import mt5_connector as mt5c   # noqa: E402
import reconcile               # noqa: E402
import incident                # noqa: E402
from control import Control    # noqa: E402

# Подменяем ровно те запросы, которыми пользуется сверка.
mt5c.positions_or_none = счёт.positions_or_none
mt5c.order_by_ticket = счёт.order_by_ticket
mt5c.history_order_by_ticket = счёт.history_order_by_ticket
mt5c.deals_by_order = счёт.deals_by_order
mt5c.deals_by_position = счёт.deals_by_position


class Папка:
    def __enter__(self):
        self.path = tempfile.mkdtemp(prefix="reconcile_")
        return self.path

    def __exit__(self, *a):
        shutil.rmtree(self.path, ignore_errors=True)


def сброс():
    счёт.позиции.clear()
    счёт.активные.clear()
    счёт.завершённые.clear()
    счёт.сделки_по_заявке.clear()
    счёт.сделки_по_позиции.clear()
    счёт.ломается.clear()


def сведения(заявка=777, **ещё):
    итог = {"символ": "EURUSD", "вид": "исполнение",
            "причина": "не выяснено, открылась сделка или нет",
            "заявка_без_ответа": str(заявка) if заявка else ""}
    итог.update(ещё)
    return итог


# =====================================================================
# ТРИ ДОКАЗАННЫХ ИСХОДА
# =====================================================================
def test_proven_that_nothing_was_opened() -> None:
    """Заявка завершилась ничем — доказано, что сделки не было."""
    print("\n[Доказано: сделки не было]")
    сброс()
    счёт.завершённые[777] = _Заявка(777, ОТМЕНЕНА)
    в = reconcile.выяснить(сведения())
    check(в.состояние == reconcile.ДОКАЗАНО_НЕ_ОТКРЫЛОСЬ,
          "Вердикт: сделки не было", в.состояние)
    check(в.доказан, "И он считается доказанным")
    check(any("отменена" in ф for ф in в.факты),
          "В фактах названо состояние заявки", str(в.факты))


def test_proven_that_a_position_is_open() -> None:
    """Цепочка прослежена, позиция на счету."""
    print("\n[Доказано: позиция на счету]")
    сброс()
    счёт.сделки_по_заявке[777] = [_Сделка(777, 999, 0.05)]
    счёт.позиции.append(_Позиция(999, 0.05))
    в = reconcile.выяснить(сведения())
    check(в.состояние == reconcile.ДОКАЗАНО_ПОЗИЦИЯ_ЕСТЬ,
          "Вердикт: позиция открыта", в.состояние)
    check(в.доказан, "Доказан")
    check(any("999" in ф and "ОТКРЫТА" in ф for ф in в.факты),
          "Назван номер позиции", str(в.факты))


def test_proven_that_the_position_was_closed() -> None:
    """Сделка была и уже закрыта — тоже доказанный исход."""
    print("\n[Доказано: позиция открывалась и закрыта]")
    сброс()
    счёт.сделки_по_заявке[777] = [_Сделка(777, 999, 0.05)]
    счёт.сделки_по_позиции[999] = [_Сделка(777, 999, 0.05),
                                   _Сделка(888, 999, 0.05, entry=1)]
    в = reconcile.выяснить(сведения())
    check(в.состояние == reconcile.ДОКАЗАНО_ПОЗИЦИЯ_ЗАКРЫТА,
          "Вердикт: позиция закрыта", в.состояние)
    check(в.доказан, "Доказан")


# =====================================================================
# ГЛАВНОЕ: ЧТО СОХРАНЯЕТ ЗАПРЕТ
# =====================================================================
def test_live_order_keeps_the_block() -> None:
    """Заявка ещё жива — запрет остаётся.

    Самый важный случай: на счету пусто, и соблазн сказать «всё чисто»
    максимален. А заявка вот-вот исполнится."""
    print("\n[Живая заявка сохраняет запрет]")
    сброс()
    счёт.активные[777] = _Заявка(777, РАЗМЕЩЕНА)
    в = reconcile.выяснить(сведения())
    check(в.состояние == reconcile.НЕ_ВЫЯСНЕНО, "Вердикт: не выяснено",
          в.состояние)
    check(not в.доказан, "И он НЕ доказан — запрет остаётся")
    check(any("ЕЩЁ ЖИВА" in ф for ф in в.факты),
          "В фактах сказано, что заявка жива", str(в.факты))


def test_empty_account_alone_proves_nothing() -> None:
    """Пусто на счету и заявки нигде нет — это НЕ доказательство."""
    print("\n[Пустой счёт сам по себе ничего не доказывает]")
    сброс()   # ни позиций, ни заявок, ни сделок
    в = reconcile.выяснить(сведения())
    check(в.состояние == reconcile.НЕ_ВЫЯСНЕНО,
          "Вердикт: не выяснено", в.состояние)
    check(any("не найдена" in ф for ф in в.факты),
          "Сказано, что заявка не найдена нигде", str(в.факты))


def test_no_ticket_recorded_keeps_the_block() -> None:
    """Номер заявки не записан — проследить нечего."""
    print("\n[Без номера заявки запрет остаётся]")
    сброс()
    в = reconcile.выяснить(сведения(заявка=0))
    check(в.состояние == reconcile.НЕ_ВЫЯСНЕНО, "Не выяснено", в.состояние)
    check(any("номер заявки" in ф for ф in в.факты),
          "И причина названа", str(в.факты))


def test_filled_order_without_deals_is_a_contradiction() -> None:
    """Заявка помечена исполненной, а сделок нет — противоречие."""
    print("\n[Исполнена, но сделок нет: противоречие сохраняет запрет]")
    сброс()
    счёт.завершённые[777] = _Заявка(777, ИСПОЛНЕНА)
    в = reconcile.выяснить(сведения())
    check(в.состояние == reconcile.НЕ_ВЫЯСНЕНО, "Не выяснено", в.состояние)
    check(any("противоречие" in ф for ф in в.факты), "И это названо",
          str(в.факты))


def test_deal_without_position_and_without_close_keeps_the_block() -> None:
    """Сделка была, позиции нет, закрытия не видно."""
    print("\n[Сделка есть, позиции нет, закрытия не видно]")
    сброс()
    счёт.сделки_по_заявке[777] = [_Сделка(777, 999, 0.05)]
    в = reconcile.выяснить(сведения())
    check(в.состояние == reconcile.НЕ_ВЫЯСНЕНО, "Не выяснено", в.состояние)


def test_deals_pointing_at_several_positions_keep_the_block() -> None:
    print("\n[Сделки одной заявки на разные позиции]")
    сброс()
    счёт.сделки_по_заявке[777] = [_Сделка(777, 111), _Сделка(777, 222)]
    в = reconcile.выяснить(сведения())
    check(в.состояние == reconcile.НЕ_ВЫЯСНЕНО, "Не выяснено", в.состояние)


def test_any_failed_query_keeps_the_block() -> None:
    """Любой недоступный запрос сохраняет запрет."""
    print("\n[Недоступный запрос сохраняет запрет]")
    for что in ("позиции", "сделки", "активные", "завершённые"):
        сброс()
        счёт.завершённые[777] = _Заявка(777, ОТМЕНЕНА)
        счёт.ломается.add(что)
        в = reconcile.выяснить(сведения())
        check(в.состояние == reconcile.НЕ_ВЫЯСНЕНО,
              f"Недоступно «{что}» — не выяснено", в.состояние)


def test_damaged_incident_file_keeps_the_block() -> None:
    """Отметка повреждена — неизвестно даже, что искать."""
    print("\n[Повреждённая отметка сохраняет запрет]")
    сброс()
    в = reconcile.выяснить({"состояние": incident.ПОВРЕЖДЁН,
                            "причина": "хэш-файл отсутствует"})
    check(в.состояние == reconcile.НЕ_ВЫЯСНЕНО, "Не выяснено", в.состояние)


def test_one_unclear_order_out_of_two_keeps_the_block() -> None:
    """Две заявки, по одной ответа нет — запрет остаётся.

    Иначе доказанная половина прикрывала бы недоказанную."""
    print("\n[Одна невыясненная заявка из двух: запрет остаётся]")
    сброс()
    счёт.завершённые[777] = _Заявка(777, ОТМЕНЕНА)
    счёт.активные[888] = _Заявка(888, РАЗМЕЩЕНА)
    в = reconcile.выяснить(сведения(заявка=777, не_закрылись="888"))
    check(в.состояние == reconcile.НЕ_ВЫЯСНЕНО, "Не выяснено", в.состояние)


# =====================================================================
# СНЯТИЕ ПО ДОКАЗАТЕЛЬСТВУ
# =====================================================================
def test_resolution_requires_proof() -> None:
    """Снять по доказательству нельзя, пока доказательства нет."""
    print("\n[Без доказательства инцидент не снимается]")
    with Папка() as f:
        сброс()
        счёт.активные[777] = _Заявка(777, РАЗМЕЩЕНА)
        c = Control()
        c.открыть_инцидент(сведения(), f)

        снят, в = c.разрешить_инцидент("владелец", "вроде всё ок", f)
        check(not снят, "Снятие отклонено")
        check(в.состояние == reconcile.НЕ_ВЫЯСНЕНО, "С вердиктом «не выяснено»")
        check(c.is_paused(), "ТОРГОВЛЯ ПО-ПРЕЖНЕМУ ОСТАНОВЛЕНА")


def test_resolution_works_with_proof() -> None:
    """С доказательством — снимается и помечается в журнале."""
    print("\n[С доказательством инцидент снимается]")
    with Папка() as f:
        сброс()
        счёт.завершённые[777] = _Заявка(777, ОТМЕНЕНА)
        c = Control()
        c.открыть_инцидент(сведения(), f)

        снят, в = c.разрешить_инцидент("владелец", "проверил", f)
        check(снят, "Снят")
        check(в.доказан, "По доказательству")
        check(not c.is_paused(), "Торговля разрешена")
        журнал = Path(incident.путь_журнала(f)).read_text(encoding="utf-8")
        check("СНЯТ ПО ДОКАЗАТЕЛЬСТВУ" in журнал,
              "В журнале помечено, что снято по фактам", журнал)


def test_forced_clearing_is_marked_as_such() -> None:
    """Принудительное снятие возможно, но помечается отдельно.

    Человек вправе взять ответственность на себя. Но через месяц должно
    быть видно, снимали по фактам или на глазок."""
    print("\n[Принудительное снятие помечается в журнале]")
    with Папка() as f:
        сброс()
        c = Control()
        c.открыть_инцидент(сведения(), f)
        check(c.снять_инцидент("владелец", "разобрался сам", f),
              "Снятие удалось")
        журнал = Path(incident.путь_журнала(f)).read_text(encoding="utf-8")
        check("СНЯТ ПРИНУДИТЕЛЬНО" in журнал,
              "И помечено как принудительное", журнал)


def test_reconciliation_never_clears_by_itself() -> None:
    """Сверка ничего не меняет — ни при каком вердикте."""
    print("\n[Сверка сама ничего не снимает]")
    with Папка() as f:
        сброс()
        счёт.завершённые[777] = _Заявка(777, ОТМЕНЕНА)   # самый «чистый» исход
        c = Control()
        c.открыть_инцидент(сведения(), f)
        в = c.сверить_инцидент()
        check(в.доказан, "Вердикт доказанный")
        check(c.is_paused(),
              "НО ТОРГОВЛЯ ВСЁ РАВНО ОСТАНОВЛЕНА — сверка не снимает")

    # И в исходнике сверка не вызывает снятия. Разбор ПО ДЕРЕВУ: слово
    # «снять» встречается в пояснениях и в совете человеку, и текстовая
    # проверка ловила бы сама себя — это уже попадалось дважды.
    import ast
    дерево = ast.parse((APP / "reconcile.py").read_text(encoding="utf-8"))
    снятия = [у.lineno for у in ast.walk(дерево)
              if isinstance(у, ast.Call)
              and (getattr(у.func, "attr", "") or getattr(у.func, "id", ""))
              in ("снять", "снять_инцидент", "разрешить_инцидент")]
    check(not снятия, "В модуле сверки нет ни одного вызова снятия",
          str(снятия))
    # И самого модуля отметки он не импортирует — трогать её нечем.
    импорты = [и.name for у in ast.walk(дерево)
               if isinstance(у, ast.Import) for и in у.names]
    check("incident" not in импорты,
          "Модуль отметки сверкой даже не импортируется", str(импорты))


# =====================================================================
# ЗАКРЫТИЕ НОГИ: ПОДТВЕРЖДЕНИЕ И ЗАПРЕТ ПРИМЕТ
# =====================================================================
def test_close_leg_refuses_to_guess_without_a_ticket() -> None:
    """Без номера ноги закрывать по приметам запрещено.

    Под приметы одинаково подходит сделка второго экземпляра программы и
    сделка, открытая человеком руками. Закрыть её — распорядиться чужими
    деньгами по совпадению признаков."""
    print("\n[Без номера ноги закрытие по приметам запрещено]")
    import execution as ex
    import trade_manager as tm
    было = cfg.LIVE_TRADING
    cfg.LIVE_TRADING = True
    счёт.позиции.clear()
    счёт.позиции.append(_Позиция(4321, 0.01))
    приказы = []
    настоящий = mt5c.close_position_partial
    mt5c.close_position_partial = lambda *a, **k: приказы.append(a)
    try:
        итог = tm.close_leg("EURUSD", tm.TICKET_НЕИЗВЕСТЕН,
                            direction=1, volume=0.01)
        check(итог.статус == ex.НЕИЗВЕСТНО,
              "Итог — «неизвестно», а не успех", итог.статус)
        check(приказы == [], "И приказа на закрытие не отправлено",
              str(приказы))
        check(любая_позиция_на_месте(), "Чужая позиция не тронута")
    finally:
        mt5c.close_position_partial = настоящий
        cfg.LIVE_TRADING = было
        счёт.позиции.clear()


def любая_позиция_на_месте() -> bool:
    return len(счёт.позиции) == 1


def test_source_no_longer_matches_a_leg_by_appearance() -> None:
    """Структурная проверка: примет в поиске ноги больше нет."""
    print("\n[В поиске ноги примет не осталось]")
    исходник = (APP / "trade_manager.py").read_text(encoding="utf-8")
    начало = исходник.index("def _найти_ногу(")
    конец = исходник.index("\n\n\n", начало)
    тело = исходник[начало:конец]
    без_комментариев = "\n".join(
        с.split("#", 1)[0] for с in тело.splitlines())
    check("_тип_позиции" not in без_комментариев,
          "Направление позиции не используется")
    check("max(" not in без_комментариев,
          "И «самая свежая из похожих» тоже")
    check("p.ticket) == int(ticket)" in без_комментариев,
          "Поиск идёт строго по номеру")


def test_close_leg_confirms_the_position_is_gone() -> None:
    """Ответ «закрыто» подтверждается отдельным запросом позиций."""
    print("\n[Ответ «закрыто» подтверждается запросом позиций]")
    исходник = (APP / "trade_manager.py").read_text(encoding="utf-8")
    начало = исходник.index("def close_leg(")
    конец = исходник.index("def _найти_ногу(")
    тело = исходник[начало:конец]
    без_комментариев = "\n".join(
        с.split("#", 1)[0] for с in тело.splitlines())
    место_done = без_комментариев.index("если_полное" if "если_полное"
                                        in без_комментариев
                                        else "итог.статус == ex.ПОЛНОЕ")
    место_проверки = без_комментариев.index("подтверждение = mt5c.positions_or_none")
    check(место_проверки > место_done,
          "Проверка идёт ПОСЛЕ ответа «закрыто»")
    check("всё_ещё is None" in без_комментариев,
          "И успех объявляется, только если позиции действительно нет")


def main() -> int:
    print("=" * 70)
    print("И3: СВЕРКА ФАКТИЧЕСКОГО СОСТОЯНИЯ ПОСЛЕ ИНЦИДЕНТА")
    print("=" * 70)
    for имя, ф in sorted(globals().items()):
        if имя.startswith("test_") and callable(ф):
            ф()
    print("\n" + "=" * 70)
    print(f"Пройдено: {passed}   Сбоев: {failed}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
