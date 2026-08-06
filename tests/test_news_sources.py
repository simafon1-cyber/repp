#!/usr/bin/env python3
"""Тесты источников экономического календаря: встроенный календарь MT5,
цепочка с запасным источником, график событий.

Главное, что проверяется:
  1. Время события переводится из пояса ТОРГОВОГО СЕРВЕРА в местное — иначе
     фильтр новостей блокировал бы торговлю не в те часы.
  2. Протухший файл календаря НЕ выдаётся за свежий.
  3. Пустой список событий (выходные) не считается отказом источника.

Запуск:  python3 tests/test_news_sources.py
"""

from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
APP = BASE.parent / "ai_scalper_standalone"
MQL5 = BASE.parent / "mql5"
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
    mt5.terminal_info = lambda: None      # тесты подменяют, где нужно
    sys.modules["MetaTrader5"] = mt5
    return cfg


CFG = install_stubs()

import news_providers as npv     # noqa: E402
import news_calendar as nc       # noqa: E402


def write_calendar(dirpath: str, events: list, offset_seconds: int = 7200) -> str:
    """Пишет файл в том же виде, в каком его создаёт сервис CalendarExport."""
    files = os.path.join(dirpath, "MQL5", "Files")
    os.makedirs(files, exist_ok=True)
    path = os.path.join(files, npv.MT5_CALENDAR_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "source": "MetaTrader 5 built-in calendar",
            "generated": "2026.08.02 12:00:00",
            "server_utc_offset_seconds": offset_seconds,
            "events": events,
        }, f, ensure_ascii=False)
    return path


def point_mt5_at(dirpath: str) -> None:
    import MetaTrader5 as mt5
    mt5.terminal_info = lambda: types.SimpleNamespace(data_path=dirpath)


# =====================================================================
# 1. Чтение файла календаря MT5
# =====================================================================
def test_mt5_reader() -> None:
    print("\n[Календарь MT5: чтение файла]")

    tmp = tempfile.mkdtemp()
    point_mt5_at(tmp)

    now = datetime.now()
    # Событие "через 2 часа" по местному времени -> записываем его во времени
    # сервера (UTC+2), как это делает терминал.
    target_local = (now + timedelta(hours=2)).replace(second=0, microsecond=0)
    offset = 7200
    server_time = target_local.astimezone().astimezone(timezone.utc).replace(tzinfo=None) \
        + timedelta(seconds=offset)

    write_calendar(tmp, [{
        "time": server_time.strftime("%Y.%m.%d %H:%M:%S"),
        "currency": "usd", "event": "Nonfarm Payrolls", "impact": "high",
        "actual": "", "estimate": "150.0", "prev": "142.0",
    }], offset_seconds=offset)

    today = now.strftime("%Y-%m-%d")
    ahead = (now + timedelta(days=3)).strftime("%Y-%m-%d")
    events = npv.fetch_mt5("", today, ahead)

    check(len(events) == 1, "Событие прочитано", str(len(events)))
    if events:
        e = events[0]
        drift = abs((e["time"] - target_local).total_seconds())
        check(drift < 90,
              "Время переведено из пояса сервера в местное (расхождение < 1.5 мин)",
              f"{drift:.0f} сек, получено {e['time']}, ждали {target_local}")
        check(e["currency"] == "USD", "Валюта приведена к верхнему регистру", e["currency"])
        check(e["impact"] == "high", "Важность прочитана", e["impact"])
        check(e["actual"] == "", "Отсутствующий факт остаётся пустым, а не нулём", repr(e["actual"]))
        check(e["estimate"] == "150.0", "Прогноз прочитан", e["estimate"])

    # Ключ этому источнику не нужен
    check("mt5" in npv.KEYLESS_PROVIDERS, "Источник mt5 помечен как не требующий ключа")
    check("mt5" in npv.PROVIDERS, "Источник mt5 зарегистрирован")


def test_mt5_timezone_math() -> None:
    print("\n[Пересчёт часового пояса]")

    tmp = tempfile.mkdtemp()
    point_mt5_at(tmp)
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    ahead = (now + timedelta(days=5)).strftime("%Y-%m-%d")

    # Одно и то же UTC-время, записанное серверами с разным смещением,
    # должно дать ОДНО И ТО ЖЕ местное время.
    utc_moment = datetime.utcnow().replace(microsecond=0) + timedelta(hours=3)
    got = []
    for offset in (0, 7200, 10800, -18000):
        server_time = utc_moment + timedelta(seconds=offset)
        write_calendar(tmp, [{
            "time": server_time.strftime("%Y.%m.%d %H:%M:%S"),
            "currency": "USD", "event": "X", "impact": "high",
            "actual": "", "estimate": "", "prev": "",
        }], offset_seconds=offset)
        ev = npv.fetch_mt5("", today, ahead)
        got.append(ev[0]["time"] if ev else None)

    check(all(g is not None for g in got), "Все варианты смещения прочитаны", str(got))
    if all(got):
        spread = max(got) - min(got)
        check(spread.total_seconds() < 2,
              "Серверы с разным смещением дают одно местное время", str(spread))


def test_mt5_staleness() -> None:
    print("\n[Протухший календарь не выдаётся за свежий]")

    tmp = tempfile.mkdtemp()
    point_mt5_at(tmp)
    path = write_calendar(tmp, [])

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    ahead = (now + timedelta(days=3)).strftime("%Y-%m-%d")

    npv.fetch_mt5("", today, ahead)      # свежий — ошибки нет
    check(True, "Свежий файл читается без ошибки")

    old = time.time() - npv.MT5_CALENDAR_MAX_AGE_SECONDS - 600
    os.utime(path, (old, old))
    try:
        npv.fetch_mt5("", today, ahead)
        check(False, "Старый файл должен вызывать ошибку")
    except RuntimeError as e:
        check("не обновлялся" in str(e), "Старый файл отвергается с понятным текстом", str(e))
    except Exception as e:
        check(False, "Ожидалась RuntimeError", f"{type(e).__name__}: {e}")

    # Файла нет вовсе
    os.remove(path)
    try:
        npv.fetch_mt5("", today, ahead)
        check(False, "Отсутствующий файл должен вызывать ошибку")
    except FileNotFoundError as e:
        check("CalendarExport" in str(e),
              "Текст ошибки подсказывает, что запустить", str(e)[:80])

    # Нет связи с терминалом
    import MetaTrader5 as mt5
    mt5.terminal_info = lambda: None
    try:
        npv.mt5_calendar_path()
        check(False, "Без терминала путь получить нельзя")
    except RuntimeError:
        check(True, "Без связи с терминалом — понятная ошибка, не падение")


# =====================================================================
# 2. Цепочка источников
# =====================================================================
def test_chain() -> None:
    print("\n[Цепочка источников]")

    calls = []

    def good(api_key, f, t):
        calls.append("good")
        return [{"time": datetime.now() + timedelta(hours=1), "currency": "USD",
                 "event": "OK", "impact": "high", "actual": "", "estimate": "", "prev": ""}]

    def empty(api_key, f, t):
        calls.append("empty")
        return []

    def broken(api_key, f, t):
        calls.append("broken")
        raise RuntimeError("источник недоступен")

    saved_providers = dict(npv.PROVIDERS)
    saved_keyless = set(npv.KEYLESS_PROVIDERS)
    npv.PROVIDERS.update({"good": good, "empty": empty, "broken": broken})
    npv.KEYLESS_PROVIDERS.update({"good", "empty", "broken"})

    def reset():
        calls.clear()
        npv._CACHE.clear()

    reset()
    events, used, err = npv.fetch_with_fallback(["good", "broken"], {})
    check(used == "good" and err is None and len(events) == 1,
          "Первый рабочий источник и используется", f"{used} {err}")
    check(calls == ["good"], "Запасной источник не дёргается зря", str(calls))

    reset()
    events, used, err = npv.fetch_with_fallback(["broken", "good"], {})
    check(used == "good" and err is None,
          "При отказе основного срабатывает запасной", f"{used} {err}")
    check(calls == ["broken", "good"], "Оба источника опрошены по порядку", str(calls))

    # КЛЮЧЕВОЕ: пустой список — это не отказ. На выходных новостей нет,
    # переключаться на другой источник из-за этого неправильно.
    reset()
    events, used, err = npv.fetch_with_fallback(["empty", "good"], {})
    check(used == "empty" and err is None and events == [],
          "Пустой календарь (выходные) — не отказ, дальше не идём", f"{used} {err} {events}")
    check(calls == ["empty"], "Запасной источник при пустом календаре не трогается", str(calls))

    reset()
    events, used, err = npv.fetch_with_fallback(["broken"], {})
    check(err is not None and used == "" and events == [],
          "Все источники упали — честная ошибка, а не тишина", f"{used} {err}")
    check("источник недоступен" in (err or ""), "В тексте ошибки видна причина", str(err))

    reset()
    events, used, err = npv.fetch_with_fallback([], {})
    check(err is not None and "пуст" in err, "Пустая цепочка — понятная ошибка", str(err))

    npv.PROVIDERS.clear()
    npv.PROVIDERS.update(saved_providers)
    npv.KEYLESS_PROVIDERS.clear()
    npv.KEYLESS_PROVIDERS.update(saved_keyless)
    npv._CACHE.clear()


def test_chain_config() -> None:
    print("\n[Настройка цепочки]")

    CFG.NEWS_PROVIDER_CHAIN = ["mt5", "finnhub"]
    check(nc.news_source_chain() == ["mt5", "finnhub"], "Цепочка читается из настроек")

    # Несуществующий источник в списке молча отбрасывается, а не роняет всё
    CFG.NEWS_PROVIDER_CHAIN = ["mt5", "выдуманный", "finnhub"]
    check(nc.news_source_chain() == ["mt5", "finnhub"],
          "Неизвестный источник отбрасывается", str(nc.news_source_chain()))

    # Старый конфиг без цепочки — работает по-прежнему, с одним провайдером
    CFG.NEWS_PROVIDER_CHAIN = None
    CFG.NEWS_API_PROVIDER = "finnhub"
    check(nc.news_source_chain() == ["finnhub"],
          "Конфиг прошлой версии продолжает работать", str(nc.news_source_chain()))

    CFG.NEWS_PROVIDER_CHAIN = ["mt5", "finnhub"]

    # Ключ обязателен только тем, кому он нужен
    events, err = npv.fetch_upcoming_events("finnhub", "")
    check(err is not None and "ключ" in err.lower(), "Finnhub без ключа — понятная ошибка", str(err))
    npv._CACHE.clear()


# =====================================================================
# 3. MQL5-сервис выгрузки
# =====================================================================
def test_exporter_source() -> None:
    print("\n[Сервис CalendarExport]")

    path = MQL5 / "CalendarExport.mq5"
    check(path.exists(), "Файл сервиса на месте")
    src = path.read_text(encoding="utf-8")

    check("#property service" in src, "Это сервис, а не советник — график не занимает")
    check("CalendarValueHistory" in src, "Читает встроенный календарь терминала")
    check("CalendarCountryById" in src, "Берёт валюту события через страну")
    check("server_utc_offset_seconds" in src,
          "Пишет смещение времени сервера — без него пересчёт часового пояса невозможен")
    check("JsonEscape" in src, "Экранирует названия событий для JSON")
    check("LONG_MIN" in src, "Отличает 'значения нет' от настоящего нуля")
    check("FileMove" in src, "Пишет через временный файл — программа не прочитает половину")
    check(npv.MT5_CALENDAR_FILENAME in src,
          "Имя файла в сервисе и в программе совпадает", npv.MT5_CALENDAR_FILENAME)

    # Сервис ничего не торгует — это не советник и торговых вызовов быть не должно
    for forbidden in ("OrderSend", "PositionClose", "CTrade", "OrderModify"):
        check(forbidden not in src, f"Сервис не торгует: нет {forbidden}")


# =====================================================================
# 4. График календаря в интерфейсе
# =====================================================================
def test_chart_code() -> None:
    print("\n[График календаря]")

    src = (APP / "desktop_app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_draw_news_chart":
            fn = node
    check(fn is not None, "Функция отрисовки графика существует")
    if fn is None:
        return

    body = ast.get_source_segment(src, fn)
    check("NEWS_HARD_BLOCK_WINDOW_MIN" in body,
          "Зона блокировки строится по НАСТОЯЩЕЙ настройке фильтра, а не по числу из головы")
    check("winfo_width" in body, "Ширина берётся фактическая — график не обрезается")
    check("canvas.delete" in body, "Перед перерисовкой холст очищается (иначе наложение)")

    # datetime обязан быть импортирован — иначе график упадёт при первом показе
    check("from datetime import datetime, timedelta" in src,
          "datetime импортирован в desktop_app.py")

    # Константы графика заданы
    for const in ("NEWS_CHART_HOURS", "NEWS_CHART_HEIGHT", "NEWS_LABEL_MIN_GAP_PX"):
        check(const in src, f"Константа {const} задана")

    # Подпись не должна рисоваться за верхним краем холста
    check("label_y >= 6" in body,
          "Подпись не рисуется, если для неё нет места — обрезанная хуже отсутствующей")

    # Настройки новостей сохраняют именно цепочку
    check("_write_config_value(\"NEWS_PROVIDER_CHAIN\"" in src,
          "Кнопка «Сохранить» пишет цепочку источников")
    check("news_chain_vars" in src, "В интерфейсе есть выбор источников")


# =====================================================================
# 5. Установщик сервиса
# =====================================================================
def test_installer() -> None:
    print("\n[Установщик сервиса]")

    inst = BASE.parent / "install"
    ps1 = inst / "Install-CalendarExport.ps1"
    bat = inst / "install-calendar.bat"

    check(ps1.exists(), "Скрипт установки на месте")
    check(bat.exists(), "Ярлык .bat на месте")
    if not (ps1.exists() and bat.exists()):
        return

    raw = ps1.read_bytes()
    # PowerShell 5.1 (штатный в Windows 10/11) читает кириллицу в .ps1 ТОЛЬКО
    # при наличии BOM — без него русский текст превратится в кракозябры.
    check(raw.startswith(b"\xef\xbb\xbf"), "У .ps1 есть BOM — иначе русский текст испортится")

    src = raw.decode("utf-8")
    check(src.count("{") == src.count("}"), "Фигурные скобки сбалансированы",
          f"{src.count('{')} против {src.count('}')}")
    check(src.count("'") % 2 == 0, "Одинарные кавычки сбалансированы")
    check("\\\\" not in src,
          "Нет удвоенных обратных слэшей — пути показались бы пользователю неверно")

    # Сервисы лежат в Services, а НЕ в Experts: перепутать легко, и тогда
    # терминал просто не увидит сервис в Навигаторе.
    check("'Services'" in src, "Целевая папка — Services")
    # Строка 'Experts' в кавычках означала бы, что скрипт реально кладёт файл
    # в папку советников — там терминал сервис не увидит. В комментариях это
    # слово упоминаться может, поэтому ищем именно кавычки PowerShell.
    check("'Experts'" not in src, "Файл НЕ кладётся в папку советников Experts")
    check("CalendarExport.mq5" in src, "Копирует именно файл сервиса")

    # .bat должен быть чисто ASCII: русский в .bat ломается даже с chcp
    bat_raw = bat.read_bytes()
    try:
        bat_raw.decode("ascii")
        check(True, "Ярлык .bat в чистом ASCII — русский в .bat отображается неверно")
    except UnicodeDecodeError:
        check(False, "Ярлык .bat содержит не-ASCII символы")
    check(b"Install-CalendarExport.ps1" in bat_raw, "Ярлык запускает нужный скрипт")


def test_forexfactory_source() -> None:
    """Новый бесплатный источник без ключа.

    Владелец: «найди другие источники для получения новостной торговли».
    Календарь MT5 требует запущенного сервиса в терминале, Finnhub — ключ.
    Получалось, что «из коробки» новостей нет вовсе и новостной режим молчит.

    Живьём ответ сервера здесь не проверить (сеть окружения к этому хосту не
    пускает), поэтому разбор проверяется на ЗАПИСАННОМ образце ровно того
    формата, который отдаёт Forex Factory."""
    print("\n[Forex Factory: бесплатный календарь без ключа]")

    check("forexfactory" in npv.PROVIDERS, "Источник добавлен")
    check("forexfactory" in npv.KEYLESS_PROVIDERS, "Ключ ему не нужен")
    check("forexfactory" in npv.PROVIDER_TITLES, "И у него есть понятное название")
    # Цепочку из настроек берём из ЭТАЛОНА: предыдущие тесты подменяли её
    # в CFG, чтобы проверить разбор списка источников.
    fresh = types.ModuleType("config_fresh")
    exec((APP / "config.py.example").read_text(encoding="utf-8"), fresh.__dict__)
    check("forexfactory" in (fresh.NEWS_PROVIDER_CHAIN or []),
          "Он стоит в цепочке источников по умолчанию",
          str(fresh.NEWS_PROVIDER_CHAIN))
    check(fresh.NEWS_PROVIDER_CHAIN.index("mt5") <
          fresh.NEWS_PROVIDER_CHAIN.index("forexfactory"),
          "Встроенный календарь терминала остаётся первым — он подробнее")

    sample = [
        {"title": "Non-Farm Employment Change", "country": "USD",
         "date": "2026-08-06T12:30:00+00:00", "impact": "High",
         "forecast": "185K", "previous": "206K"},
        {"title": "ECB President Speaks", "country": "EUR",
         "date": "2026-08-06T14:00:00+00:00", "impact": "Medium",
         "forecast": "", "previous": ""},
        {"title": "Bank Holiday", "country": "GBP",
         "date": "2026-08-06T00:00:00+00:00", "impact": "Holiday",
         "forecast": "", "previous": ""},
        {"title": "Слишком старое", "country": "USD",
         "date": "2020-01-01T10:00:00+00:00", "impact": "High",
         "forecast": "", "previous": ""},
        {"нет": "нужных полей"},
        "вообще не словарь",
    ]
    events = npv.parse_forexfactory(sample, "2026-08-05", "2026-08-07")
    check(len(events) == 3, "Разобрано ровно то, что попадает в окно дат",
          str(len(events)))

    by_title = {e["event"]: e for e in events}
    nfp = by_title.get("Non-Farm Employment Change")
    check(nfp is not None, "Главная новость месяца на месте")
    if nfp:
        check(nfp["currency"] == "USD", "Валюта берётся из поля country")
        check(nfp["impact"] == "high", "Важность приведена к нашему виду")
        check(nfp["estimate"] == "185K", "Прогноз прочитан")
        check(nfp["prev"] == "206K", "Предыдущее значение прочитано")

    ecb = by_title.get("ECB President Speaks")
    check(ecb is not None and ecb["impact"] == "medium", "Средняя важность — medium")

    holiday = by_title.get("Bank Holiday")
    check(holiday is not None and holiday["impact"] == "low",
          "Выходной день рынка — не повод для сделки")

    # Часовой пояс: время должно пересчитываться в местное, иначе фильтр
    # промахнётся на часы (та же ловушка, что и с календарём MT5)
    utc = npv._parse_ff_time("2026-08-06T12:30:00+00:00")
    check(utc.tzinfo is None, "Время «наивное», как во всей программе")
    with_z = npv._parse_ff_time("2026-08-06T12:30:00Z")
    check(with_z == utc, "Запись через Z понимается так же")

    # Одно и то же мгновение, записанное с разным смещением, обязано дать
    # ОДНО время. Так проверка не зависит от часового пояса машины: если
    # смещение игнорировать, разница будет ровно в 4 часа.
    same_moment = npv._parse_ff_time("2026-08-06T08:30:00-04:00")
    check(same_moment == utc,
          "Смещение часового пояса действительно учитывается",
          f"{same_moment} против {utc}")
    other = npv._parse_ff_time("2026-08-06T14:30:00+02:00")
    check(other == utc, "И с положительным смещением тоже",
          f"{other} против {utc}")

    broken = False
    try:
        npv._parse_ff_time("")
    except (ValueError, TypeError):
        broken = True
    check(broken, "Пустое время отвергается, а не превращается в мусор")

    check(npv.parse_forexfactory([], "2026-08-05", "2026-08-07") == [],
          "Пустой ответ — пустой список, без падения")
    check(npv.parse_forexfactory(None, "2026-08-05", "2026-08-07") == [],
          "И на None тоже не падаем")


def test_news_entry_threshold() -> None:
    """Владелец: «календарь отрабатывает все новости? я не заметил за ним
    этого». Не все — поводом для ВХОДА считались только самые важные, и
    нигде об этом не было сказано."""
    print("\n[Какие новости считаются поводом для входа]")

    check(hasattr(CFG, "NEWS_TRADE_MIN_IMPACT"), "Порог важности настраивается")
    check(CFG.NEWS_TRADE_MIN_IMPACT == "high",
          "По умолчанию — только самые важные новости",
          str(CFG.NEWS_TRADE_MIN_IMPACT))

    src = (APP / "news_calendar.py").read_text(encoding="utf-8")
    check('e["impact"] != "high"' not in src,
          "Важность больше не зашита в код намертво")
    check("NEWS_TRADE_MIN_IMPACT" in src, "Берётся из настроек")

    # На вкладке «Новости» видны ВСЕ события, независимо от порога входа
    upcoming = src.split("def upcoming_events", 1)[1][:600]
    check("NEWS_TRADE_MIN_IMPACT" not in upcoming,
          "Список новостей на вкладке порогом входа не режется")


def main() -> int:
    print("=" * 62)
    print("ТЕСТЫ ИСТОЧНИКОВ НОВОСТЕЙ")
    print("=" * 62)

    test_mt5_reader()
    test_mt5_timezone_math()
    test_mt5_staleness()
    test_chain()
    test_chain_config()
    test_forexfactory_source()
    test_news_entry_threshold()
    test_exporter_source()
    test_chart_code()
    test_installer()

    print("\n" + "=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
