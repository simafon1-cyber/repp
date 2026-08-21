#!/usr/bin/env python3
"""ДАННЫЕ-P1: СНИМКИ ВЫГРУЗКИ И НАСТОЯЩАЯ КОМАНДА ЗАПУСКА.

ПОЧЕМУ ЭТИ ТЕСТЫ ПОЯВИЛИСЬ

Приёмочный аудит ревизии данных нашёл два дефекта, и оба мои.

ПЕРВЫЙ (P1-H1). В памятке владельцу было написано «запустите
python3 history_export.py». Команда не работала: в файле не было ни точки
входа, ни подключения к терминалу, ни вызова выгрузки. На Linux она
падала на импорте, а на Windows с установленной библиотекой просто
ЗАВЕРШАЛАСЬ БЕЗ СЛОВА И БЕЗ ОШИБКИ — то есть создавала уверенность, что
данные выгружены, когда не выгружено ничего.

Молчаливый успех там, где ничего не произошло, опаснее падения: падение
видно сразу, а это — нет.

ВТОРОЙ (P1-H2). Правило «старые выгрузки нельзя затирать новыми» было
записано в документе, но НЕ РЕАЛИЗОВАНО. Выгрузка писала в постоянное имя
в режиме "w". А поскольку терминал отдаёт скользящее окно фиксированного
размера, затиралось невосстановимое: при повторной выгрузке EURUSD_M5
потерял шесть дней начала, EURUSD_M15 — четыре.

ЧТО ЗДЕСЬ ПРОВЕРЯЕТСЯ ПО ФАКТУ

Не «функция вернула правильное значение», а состояние диска: сколько
каталогов появилось, изменились ли байты прежнего, какие файлы созданы, и
какой код возврата получил тот, кто запускал.

ЧЕГО ЭТИ ТЕСТЫ НЕ ПРОВЕРЯЮТ

Настоящий терминал. Здесь подделка, и она отвечает так, как её научили.
Сколько лет истории отдаст MetaQuotes-Demo на самом деле — покажет только
живая выгрузка.

Запуск:  python3 tests/test_history_snapshots.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
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


# ---------------------------------------------------------------------
# ПОДДЕЛЬНОЕ ОКРУЖЕНИЕ
# ---------------------------------------------------------------------
cfg = types.ModuleType("config")
cfg.__file__ = str(APP / "config.py.example")
exec((APP / "config.py.example").read_text(encoding="utf-8"), cfg.__dict__)
sys.modules["config"] = cfg

fake_mt5 = types.ModuleType("MetaTrader5")
for _имя, _знач in (("TIMEFRAME_M1", 1), ("TIMEFRAME_M5", 5),
                    ("TIMEFRAME_M15", 15), ("TIMEFRAME_M30", 30),
                    ("TIMEFRAME_H1", 60), ("TIMEFRAME_H4", 240),
                    ("TIMEFRAME_D1", 1440), ("ORDER_TYPE_BUY", 0),
                    ("ORDER_TYPE_SELL", 1), ("ORDER_FILLING_IOC", 1),
                    ("ORDER_FILLING_FOK", 2), ("TRADE_RETCODE_DONE", 10009),
                    ("TRADE_RETCODE_REQUOTE", 10004),
                    ("TRADE_RETCODE_PRICE_CHANGED", 10020),
                    ("TRADE_RETCODE_PRICE_OFF", 10021),
                    ("POSITION_TYPE_BUY", 0), ("POSITION_TYPE_SELL", 1)):
    setattr(fake_mt5, _имя, _знач)

СПРОШЕНО = []          # что именно спрашивали у терминала


def _свечи(symbol, tf, start, count):
    СПРОШЕНО.append({"symbol": symbol, "tf": tf, "start": start, "count": count})
    шаг = {1: 60, 5: 300, 15: 900, 60: 3600}.get(tf, 300)
    return [{"time": 1700000000 + i * шаг, "open": 1.0, "high": 1.1,
             "low": 0.9, "close": 1.05, "tick_volume": 10, "spread": 8,
             "real_volume": 0} for i in range(20)]


fake_mt5.copy_rates_from_pos = _свечи
fake_mt5.symbol_info = lambda s: types.SimpleNamespace(
    point=0.00001, digits=5, volume_min=0.01, volume_max=500.0,
    volume_step=0.01, trade_tick_value=1.0, trade_tick_size=0.00001,
    trade_contract_size=100000.0, trade_stops_level=0, path="Forex\\\\" + s,
    visible=True, name=s)
fake_mt5.symbol_info_tick = lambda s: types.SimpleNamespace(bid=1.1, ask=1.1001)
fake_mt5.symbol_select = lambda *a, **k: True
fake_mt5.symbols_get = lambda: []
fake_mt5.order_calc_profit = lambda *a: 1.0
fake_mt5.order_calc_margin = lambda *a: 1.0
fake_mt5.last_error = lambda: (0, "ok")
sys.modules["MetaTrader5"] = fake_mt5

import history_export as he          # noqa: E402


# =====================================================================
# 1. СНИМОК НЕ ЗАТИРАЕТ ПРЕДЫДУЩИЙ
# =====================================================================

def test_повторная_выгрузка_создаёт_второй_каталог():
    """ГЛАВНАЯ проверка P1-H2. Смотрим НА ДИСК, а не на возвращённое значение.

    Байты первого снимка обязаны остаться прежними до последнего.

    Наоборот: верните запись в постоянное имя (RAW_FOLDER) — проверка
    падает, потому что каталог окажется один, а его содержимое сменится."""
    корень = tempfile.mkdtemp(prefix="снимки_")
    try:
        когда = datetime(2026, 8, 21, 20, 30, tzinfo=timezone.utc)
        первый = he.новый_снимок(корень, когда)
        he.export_symbol("EURUSD", "H1", 20, первый)
        he.записать_манифест(первый)

        было = {}
        for f in sorted(Path(первый).iterdir()):
            было[f.name] = f.read_bytes()
        check(len(было) >= 3, "В первом снимке есть свечи, паспорт и манифест",
              str(sorted(было)))

        # Вторая выгрузка минутой позже.
        второй = he.новый_снимок(корень, когда + timedelta(minutes=1))
        he.export_symbol("EURUSD", "H1", 20, второй)
        he.записать_манифест(второй)

        каталоги = sorted(p.name for p in Path(корень).iterdir() if p.is_dir())
        check(len(каталоги) == 2, "На диске ДВА каталога, а не один",
              str(каталоги))
        check(первый != второй, "И это разные каталоги")

        стало = {f.name: f.read_bytes() for f in Path(первый).iterdir()}
        изменились = [и for и in было if было[и] != стало.get(и)]
        check(not изменились,
              "Байты первого снимка не изменились НИ В ОДНОМ файле",
              str(изменились))
    finally:
        shutil.rmtree(корень, ignore_errors=True)


def test_снимок_с_тем_же_именем_отвергается():
    """Одинаковое имя — отказ, а не «допишем туда же».

    Дописать в чужой снимок значит смешать две выгрузки и потерять
    возможность сказать, какая когда сделана."""
    корень = tempfile.mkdtemp(prefix="снимки_")
    try:
        когда = datetime(2026, 8, 21, 20, 30, tzinfo=timezone.utc)
        he.новый_снимок(корень, когда)
        try:
            he.новый_снимок(корень, когда)
            check(False, "Повтор имени отвергнут", "создался второй раз")
        except FileExistsError as e:
            check(True, "Повтор имени отвергнут")
            check("не перезаписывает" in str(e),
                  "И сказано почему", str(e)[:80])
    finally:
        shutil.rmtree(корень, ignore_errors=True)


def test_существующий_файл_не_перезаписывается():
    """Даже если каталог тот же — файл не затирается молча.

    Это второй рубеж: каталоги защищают от повторного запуска, а этот
    запрет — от любого пути в коде, который попробует писать поверх."""
    папка = tempfile.mkdtemp(prefix="снимок_")
    try:
        первый = he.export_symbol("EURUSD", "H1", 20, папка)
        check(not первый.get("error"), "Первая запись прошла",
              str(первый.get("error")))
        путь = Path(первый["csv"])
        было = путь.read_bytes()

        второй = he.export_symbol("EURUSD", "H1", 20, папка)
        check(bool(второй.get("error")), "Вторая запись ОТКАЗАНА",
              "перезаписала молча")
        check("не перезаписывается" in второй.get("error", ""),
              "И названа причина", второй.get("error", "")[:80])
        check(путь.read_bytes() == было, "Файл на диске не изменился")

        # Явное разрешение всё-таки работает — иначе чинить будет нечем.
        третий = he.export_symbol("EURUSD", "H1", 20, папка,
                                  перезаписывать=True)
        check(not третий.get("error"),
              "С явным разрешением перезапись возможна",
              str(третий.get("error")))
    finally:
        shutil.rmtree(папка, ignore_errors=True)


def test_манифест_снимка_содержит_отпечатки():
    """«Неизменяемый каталог» без отпечатков — только слово.

    Манифест позволяет потом доказать, что снимок с тех пор не менялся."""
    папка = tempfile.mkdtemp(prefix="снимок_")
    try:
        he.export_symbol("EURUSD", "H1", 20, папка)
        путь = he.записать_манифест(папка)
        м = json.loads(Path(путь).read_text(encoding="utf-8"))
        check(м["файлов"] == 2, "В манифесте оба файла: свечи и паспорт",
              str(м["файлов"]))
        for имя, з in м["файлы"].items():
            check(len(з["sha256"]) == 64, f"У {имя} есть отпечаток")
            check(з["байт"] > 0, f"И размер {имя} записан")
        check(he.МАНИФЕСТ not in м["файлы"],
              "Сам манифест в себя не включён — иначе отпечаток был бы "
              "невычислим")
    finally:
        shutil.rmtree(папка, ignore_errors=True)


# =====================================================================
# 2. КОМАНДА ЗАПУСКА СУЩЕСТВУЕТ И ЧЕСТНО ОТЧИТЫВАЕТСЯ
# =====================================================================

class ПоддельнаяСвязка(types.ModuleType):
    """mt5_connector, который запоминает порядок вызовов."""

    def __init__(self, падать_на_connect=False):
        super().__init__("mt5_connector")
        self.порядок = []
        self._падать = падать_на_connect
        self.TF_MAP = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}

    def connect(self):
        self.порядок.append("connect")
        if self._падать:
            raise RuntimeError("терминал не запущен")
        return types.SimpleNamespace(login=5054028014, server="MetaQuotes-Demo",
                                     company="MetaQuotes Ltd.", currency="USD")

    def disconnect(self):
        self.порядок.append("disconnect")

    def get_account_info(self):
        return None


def _запустить(аргументы, связка=None, экспорт=None):
    """Вызвать main() с подделками. Возвращает (код, связка, что_просили)."""
    связка = связка or ПоддельнаяСвязка()
    sys.modules["mt5_connector"] = связка
    просили = {}
    было = he.export_all
    if экспорт is not None:
        def перехват(**k):
            просили.update(k)
            связка.порядок.append("export_all")
            return экспорт(**k)
        he.export_all = перехват
    try:
        код = he.main(аргументы)
    finally:
        he.export_all = было
        sys.modules.pop("mt5_connector", None)
    return код, связка, просили


def test_команда_запуска_существует_и_работает():
    """ГЛАВНАЯ проверка P1-H1.

    Наоборот: уберите main() и блок __main__ — проверка падает, а вместе
    с ней возвращается «команда завершилась без единого слова»."""
    исходник = (APP / "history_export.py").read_text(encoding="utf-8")
    check('if __name__ == "__main__":' in исходник,
          "У файла есть точка входа для командной строки")
    check(callable(getattr(he, "main", None)),
          "И функция main(), возвращающая код")

    папка = tempfile.mkdtemp(prefix="снимки_")
    try:
        код, связка, просили = _запустить(
            ["--timeframe", "H1", "--symbol", "EURUSD", "--folder", папка],
            экспорт=lambda **k: [{"symbol": "EURUSD", "timeframe": "H1",
                                  "bars": 20, "csv": "EURUSD_H1.csv"}])
        check(код == 0, "Успешная выгрузка даёт код 0", str(код))
        check(связка.порядок == ["connect", "export_all", "disconnect"],
              "Порядок: подключились, выгрузили, отключились",
              str(связка.порядок))
        каталоги = [p.name for p in Path(папка).iterdir() if p.is_dir()]
        check(len(каталоги) == 1, "Создан ровно один каталог снимка",
              str(каталоги))
    finally:
        shutil.rmtree(папка, ignore_errors=True)


def test_команда_берёт_только_названный_таймфрейм():
    """Вторая проверка, которую потребовал аудит.

    Команда для H1 не должна трогать M1, M5 и M15: их окна уже сохранены,
    и лишняя выгрузка отняла бы минуты и место без пользы."""
    папка = tempfile.mkdtemp(prefix="снимки_")
    try:
        код, _, просили = _запустить(
            ["--timeframe", "H1", "--folder", папка],
            экспорт=lambda **k: [{"symbol": "EURUSD", "timeframe": "H1",
                                  "bars": 20, "csv": "x.csv"}])
        check(код == 0, "Команда отработала", str(код))
        check(просили.get("timeframe") == ["H1"],
              "Просили ТОЛЬКО H1", str(просили.get("timeframe")))
        for лишний in ("M1", "M5", "M15"):
            check(лишний not in (просили.get("timeframe") or []),
                  f"{лишний} не запрашивался")
    finally:
        shutil.rmtree(папка, ignore_errors=True)


def test_таймфрейм_обязателен():
    """Без явного таймфрейма команда не запускается.

    Выгрузка всего подряд занимает минуты и место, а нужен обычно один."""
    поймано = False
    try:
        he.main(["--folder", "/tmp"])
    except SystemExit as e:
        поймано = e.code != 0
    check(поймано, "Без --timeframe команда не выполняется")


def test_ошибки_дают_ненулевой_код():
    """Тот, кто запускал, обязан отличить «не вышло» от «всё хорошо».

    Нулевой код при пустом отчёте — ровно та беда, из-за которой памятка
    и оказалась вредной."""
    папка = tempfile.mkdtemp(prefix="снимки_")
    try:
        код, _, _ = _запустить(["--timeframe", "H1", "--folder", папка],
                               экспорт=lambda **k: [])
        check(код != 0, "Пустой отчёт — ненулевой код", str(код))

        код, _, _ = _запустить(
            ["--timeframe", "H1", "--folder", папка],
            экспорт=lambda **k: [{"symbol": "EURUSD", "timeframe": "H1",
                                  "error": "терминал молчит"}])
        check(код != 0, "Все инструменты с ошибкой — ненулевой код", str(код))

        код, _, _ = _запустить(
            ["--timeframe", "H1", "--folder", папка],
            экспорт=lambda **k: [
                {"symbol": "EURUSD", "timeframe": "H1", "bars": 20, "csv": "a"},
                {"symbol": "GBPUSD", "timeframe": "H1", "error": "молчит"}])
        check(код != 0, "Часть с ошибкой — тоже ненулевой код", str(код))

        код, _, _ = _запустить(["--timeframe", "ЧАС", "--folder", папка])
        check(код != 0, "Неизвестный таймфрейм — ненулевой код", str(код))
    finally:
        shutil.rmtree(папка, ignore_errors=True)


def test_терминал_отпускается_даже_при_обрыве():
    """disconnect обязан случиться, что бы ни произошло внутри.

    Занятое подключение переживает процесс, и следующий запуск объяснит
    это как-нибудь иначе — человек будет искать не там."""
    папка = tempfile.mkdtemp(prefix="снимки_")

    def ломается(**k):
        raise RuntimeError("терминал отвалился на середине")

    try:
        связка = ПоддельнаяСвязка()
        поймано = False
        try:
            _запустить(["--timeframe", "H1", "--folder", папка],
                       связка=связка, экспорт=ломается)
        except RuntimeError:
            поймано = True
        check(поймано, "Ошибка выгрузки не проглочена")
        check("disconnect" in связка.порядок,
              "Но терминал всё равно отпущен", str(связка.порядок))
    finally:
        shutil.rmtree(папка, ignore_errors=True)


def test_без_терминала_не_создаётся_пустой_снимок():
    """Не подключились — каталог не создаём.

    Пустой каталог с датой выглядит как «выгрузка была, но данных нет» —
    а на деле выгрузки не было вовсе."""
    папка = tempfile.mkdtemp(prefix="снимки_")
    try:
        код, связка, _ = _запустить(
            ["--timeframe", "H1", "--folder", папка],
            связка=ПоддельнаяСвязка(падать_на_connect=True))
        check(код != 0, "Код ненулевой", str(код))
        каталоги = list(Path(папка).iterdir())
        check(not каталоги, "И ни одного каталога не создано", str(каталоги))
    finally:
        shutil.rmtree(папка, ignore_errors=True)


def test_кнопка_в_окне_тоже_пишет_в_снимок():
    """Защита бесполезна, если главный путь её обходит.

    Кнопка «Выгрузить историю» — тот самый путь, которым владелец и
    затирал данные. Проверяется по разобранному дереву кода, а не по
    тексту: поиск по тексту нашёл бы слово «снимок» в комментариях.

    Наоборот: верните в окне вызов export_all без folder — проверка
    падает."""
    import ast
    исходник = (APP / "desktop_app.py").read_text(encoding="utf-8")
    дерево = ast.parse(исходник)

    вызовы = []
    for узел in ast.walk(дерево):
        if (isinstance(узел, ast.Call)
                and isinstance(узел.func, ast.Attribute)
                and узел.func.attr == "export_all"):
            вызовы.append(узел)

    check(вызовы, "Окно действительно зовёт выгрузку")
    без_папки = [в.lineno for в in вызовы
                 if not any(к.arg == "folder" for к in в.keywords)]
    check(not без_папки,
          "И каждый вызов из окна получает КАТАЛОГ СНИМКА",
          f"строки без folder: {без_папки}")

    снимки = [у.lineno for у in ast.walk(дерево)
              if isinstance(у, ast.Call)
              and isinstance(у.func, ast.Attribute)
              and у.func.attr == "новый_снимок"]
    check(len(снимки) >= len(вызовы),
          "Перед каждой выгрузкой создаётся новый снимок",
          f"снимков {len(снимки)}, выгрузок {len(вызовы)}")


def main() -> int:
    print("=" * 70)
    print("ДАННЫЕ-P1: СНИМКИ ВЫГРУЗКИ И КОМАНДА ЗАПУСКА")
    print("=" * 70)
    for имя, ф in sorted(globals().items()):
        if имя.startswith("test_") and callable(ф):
            print(f"\n--- {имя}")
            try:
                ф()
            except Exception as e:  # noqa: BLE001
                check(False, f"{имя} доработала до конца",
                      f"{type(e).__name__}: {str(e).splitlines()[0]}")
    print("\n" + "=" * 70)
    print(f"Пройдено: {passed}   Сбоев: {failed}")
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
