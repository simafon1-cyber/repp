"""run_baseline.py — ОДНА КОМАНДА: посчитать baseline по выгруженной истории.

Запуск:

    python run_baseline.py                 — EURUSD и XAUUSD, вся история
    python run_baseline.py EURUSD          — только один инструмент
    python run_baseline.py --bars 50000    — быстрее, по последним 50 000 свечам

Ничего не настраивает и ничего не оптимизирует. Берёт стратегию как есть,
прогоняет по вашей истории и печатает числа. Если данных мало — так и
пишет, а не выдумывает результат.
"""

import logging
import os
import sys

ЗДЕСЬ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ЗДЕСЬ)


def _настройки() -> str:
    """Какие настройки взять. Возвращает, что именно использовано.

    Личный config.py есть не всегда: в нём ключи и пароли, и в репозиторий он
    не кладётся. Тогда берётся образец config.py.example — тот самый, что
    ставится вместе с программой. Молчать об этом нельзя: baseline, посчитанный
    по другим настройкам, — это baseline другой системы."""
    import types
    if os.path.exists(os.path.join(ЗДЕСЬ, "config.py")):
        return "config.py (личные настройки)"
    образец = os.path.join(ЗДЕСЬ, "config.py.example")
    cfg = types.ModuleType("config")
    cfg.__file__ = образец
    with open(образец, encoding="utf-8") as f:
        exec(f.read(), cfg.__dict__)
    sys.modules["config"] = cfg
    return "config.py.example (настройки по умолчанию — личных нет)"


def _терминал() -> str:
    """Заглушка MetaTrader, если настоящего пакета нет.

    Проверка на истории обязана работать БЕЗ терминала: свечи уже выгружены в
    файлы, а всё, что осталось от терминала, — несколько числовых постоянных.
    Пакет MetaTrader5 существует только под Windows, и требовать его для
    расчёта по готовым файлам было бы бессмысленно.

    Ни одна функция здесь не подделывается: заглушка отдаёт только константы,
    а данные о рынке движок берёт из выгруженной истории и из паспорта."""
    try:
        import MetaTrader5  # noqa: F401
        return "MetaTrader5 (настоящий пакет)"
    except ImportError:
        pass
    import types
    m = types.ModuleType("MetaTrader5")
    for имя, знач in (("TIMEFRAME_M1", 1), ("TIMEFRAME_M5", 5), ("TIMEFRAME_M15", 15),
                      ("TIMEFRAME_M30", 30), ("TIMEFRAME_H1", 16385),
                      ("TIMEFRAME_H4", 16388), ("TIMEFRAME_D1", 16408),
                      ("ORDER_TYPE_BUY", 0), ("ORDER_TYPE_SELL", 1),
                      ("ORDER_FILLING_IOC", 1), ("ORDER_FILLING_FOK", 2),
                      ("TRADE_RETCODE_REQUOTE", 10004),
                      ("TRADE_RETCODE_PRICE_CHANGED", 10020),
                      ("TRADE_RETCODE_PRICE_OFF", 10021),
                      ("TRADE_RETCODE_DONE", 10009),
                      ("POSITION_TYPE_BUY", 0), ("POSITION_TYPE_SELL", 1)):
        setattr(m, имя, знач)
    # Ничего не возвращают: движок к терминалу не обращается, а если бы
    # обратился — лучше получить пустоту, чем выдуманное число.
    for имя in ("symbol_info", "symbol_info_tick", "order_calc_profit",
                "order_calc_margin", "copy_rates_from_pos", "positions_get",
                "symbols_get", "account_info", "last_error", "terminal_info"):
        setattr(m, имя, lambda *a, **k: None)
    sys.modules["MetaTrader5"] = m
    return "заглушка (настоящий MetaTrader5 не нужен: свечи уже в файлах)"


ОТКУДА_НАСТРОЙКИ = _настройки()
ОТКУДА_ТЕРМИНАЛ = _терминал()

import baseline_engine          # noqa: E402
import history_data             # noqa: E402
import trade_stats              # noqa: E402
import validation               # noqa: E402

logging.basicConfig(level=logging.WARNING,
                    format="%(levelname)s %(name)s: %(message)s")

СИМВОЛЫ_ПО_УМОЛЧАНИЮ = ["EURUSD", "XAUUSD"]

# Сколько сделок нужно на одно окно walk-forward, чтобы окно вообще что-то
# значило. Меньше — окно превращается в подбрасывание монеты.
СДЕЛОК_НА_ОКНО = 30


def прогнать(symbol: str, max_bars: int = 0, equity: float = 0.0) -> dict:
    данные = history_data.load(symbol)
    качество = данные["quality"]
    print()
    print("=" * 70)
    print(f"ДАННЫЕ: {symbol}")
    print("=" * 70)
    print(history_data.describe(качество, данные["meta"]))

    if not данные["clean"]:
        print(f"\nСТАТУС: INSUFFICIENT DATA — файла нет или он пуст.")
        print(f"Ожидался файл: {данные['path']}")
        return {"symbol": symbol, "status": "INSUFFICIENT DATA", "stats": None}

    if not качество["usable"]:
        print("\nСТАТУС: DATA NOT USABLE — сначала надо починить данные "
              "(см. замечания выше). Считать по ним нельзя.")
        return {"symbol": symbol, "status": "DATA NOT USABLE", "stats": None}

    # Обработанные данные кладём ОТДЕЛЬНО от сырых: history/clean. Сырые
    # (history/raw) не меняются никогда, чтобы всегда можно было вернуться к
    # исходнику и посмотреть, что именно было выброшено и почему.
    чистый_путь = history_data.save_clean(данные["clean"], symbol, данные["timeframe"])
    выброшено = len(данные["bars"]) - len(данные["clean"])
    print(f"\nОбработанные данные: {чистый_путь}")
    print(f"Выброшено при очистке: {выброшено} свечей "
          f"(повторы и битые цены; пропуски НЕ дорисовывались)")

    print()
    print("=" * 70)
    print(f"ПРОГОН СТРАТЕГИИ: {symbol}")
    print("=" * 70)

    итог = baseline_engine.run(symbol, данные["clean"], данные["meta"],
                               equity_start=equity, max_bars=max_bars,
                               progress=lambda t: print(f"  {t}", flush=True))
    if итог.get("error"):
        print(f"СТАТУС: INSUFFICIENT DATA — {итог['error']}")
        return {"symbol": symbol, "status": "INSUFFICIENT DATA", "stats": None}

    сделки = итог["trades"]
    общая = trade_stats.summarize(сделки, f"{symbol} — ВСЕ СДЕЛКИ")
    print()
    print(trade_stats.describe(общая))

    по_сторонам = trade_stats.by_direction(сделки)
    print()
    print(trade_stats.describe(по_сторонам["BUY"]))
    print()
    print(trade_stats.describe(по_сторонам["SELL"]))

    # Почему входов не было — самое полезное, когда сделок мало.
    if итог["rejects"]:
        print()
        print("ПОЧЕМУ ВХОД НЕ СОСТОЯЛСЯ (счёт по барам):")
        for причина, сколько in sorted(итог["rejects"].items(),
                                       key=lambda x: -x[1])[:10]:
            print(f"  {сколько:>8}  {причина}")

    return {"symbol": symbol, "status": "OK", "stats": общая,
            "by_direction": по_сторонам, "trades": сделки, "run": итог}


def разбивки(symbol: str, сделки) -> None:
    """Разбивка статистики по сессиям, волатильности и режиму рынка.

    Только исследование. Ни один параметр по этим числам не меняется."""
    print()
    print("=" * 70)
    print(f"РАЗБИВКИ (только исследование, параметры не меняются): {symbol}")
    print("=" * 70)
    for ключ, имя in (("session", "ПО СЕССИЯМ"),
                      ("atr_bucket", "ПО ВОЛАТИЛЬНОСТИ"),
                      ("regime", "ПО РЕЖИМУ РЫНКА"),
                      ("news", "ПО НОВОСТЯМ")):
        группы = trade_stats.by_bucket(сделки, ключ)
        print(f"\n{имя}:")
        for имя_группы, св in группы.items():
            метка = "" if св["достаточно_данных"] else "  [UNKNOWN: мало сделок]"
            print(f"  {имя_группы:<22} сделок {св['сделок']:>5}  "
                  f"PF {св['профит_фактор']:>6}  ожидание {св['ожидание']:>9}"
                  f"{метка}")


def walk_forward(symbol: str, сделки) -> None:
    """Разделение TRAIN / VALIDATION / OOS. Параметры НЕ подбираются."""
    print()
    print("=" * 70)
    print(f"WALK-FORWARD: {symbol}")
    print("=" * 70)
    n = len(сделки)
    нужно = СДЕЛОК_НА_ОКНО * validation.ОКОН_WALK_FORWARD
    if n < нужно:
        print(f"СТАТУС: INSUFFICIENT DATA — сделок {n}, для {validation.ОКОН_WALK_FORWARD} "
              f"окон нужно хотя бы {нужно} (по {СДЕЛОК_НА_ОКНО} на окно).")
        print("Результат не выдумывается. Нужна более длинная история.")
        return

    окна = validation.windows(сделки, validation.ОКОН_WALK_FORWARD)
    print(f"История разрезана на {len(окна)} окон ПО ПОРЯДКУ ВРЕМЕНИ.")
    for i, окно in enumerate(окна, 1):
        св = trade_stats.summarize(окно, f"окно {i}")
        print(f"  Окно {i}: сделок {св['сделок']:>4}  PF {св['профит_фактор']:>6}  "
              f"ожидание {св['ожидание']:>9}  просадка {св['макс_просадка']:>8}")

    обучение, проверка = validation.split_out_of_sample(сделки)
    св_об = trade_stats.summarize(обучение, "TRAIN+VALIDATION")
    св_пр = trade_stats.summarize(проверка, "OUT OF SAMPLE")
    print()
    print(trade_stats.compare(св_об, св_пр, "TRAIN", "OOS"))
    print()
    print("OOS В ЭТОМ ЭТАПЕ НЕ ИСПОЛЬЗУЕТСЯ ДЛЯ ПОДБОРА. Параметры не менялись "
          "вовсе, поэтому подбирать было нечего — это чистая проверка.")


def main(argv) -> int:
    символы = [a for a in argv if not a.startswith("--")] or СИМВОЛЫ_ПО_УМОЛЧАНИЮ
    max_bars = 0
    if "--bars" in argv:
        try:
            max_bars = int(argv[argv.index("--bars") + 1])
        except (IndexError, ValueError):
            print("После --bars нужно число, например: --bars 50000")
            return 2

    print("=" * 70)
    print("BASELINE — ПРОГОН ТЕКУЩЕЙ СТРАТЕГИИ ПО ВАШЕЙ ИСТОРИИ")
    print("=" * 70)
    print("Ни один параметр не меняется. Это замер, а не настройка.")
    print(f"Настройки взяты из: {ОТКУДА_НАСТРОЙКИ}")
    print(f"Терминал: {ОТКУДА_ТЕРМИНАЛ}")
    import config as _c
    профиль = getattr(getattr(_c, "RISK_PROFILE", None), "value", "?")
    _p = _c.RISK_PROFILES.get(getattr(_c, "RISK_PROFILE", None), {})
    print(f"Профиль риска: {профиль} — порог входа "
          f"{_p.get('min_score_to_trade', '?')}, риск {_p.get('risk_percent', '?')}%, "
          f"стоп {_p.get('atr_sl_multiplier', '?')} ATR, "
          f"макс. сделок по паре {_p.get('max_open_positions', '?')}")
    print(f"Автообучение: {'включено' if getattr(_c, 'USE_AUTO_LEARNING', False) else 'выключено'}, "
          f"AI в прогоне НЕ участвует (см. ниже)")
    print()
    print(baseline_engine.describe_not_reproducible())

    результаты = []
    for symbol in символы:
        r = прогнать(symbol, max_bars=max_bars)
        результаты.append(r)
        if r["status"] == "OK" and r["trades"]:
            разбивки(symbol, r["trades"])
            walk_forward(symbol, r["trades"])

    # Сравнение инструментов — но только если по обоим есть что сравнивать.
    годные = [r for r in результаты if r["status"] == "OK" and r["stats"]]
    if len(годные) >= 2:
        print()
        print("=" * 70)
        print("СРАВНЕНИЕ ИНСТРУМЕНТОВ")
        print("=" * 70)
        print("Инструменты сравниваются, но НЕ смешиваются: у них разная цена "
              "пункта и разное поведение, общая статистика по ним не значит "
              "ничего.")
        print()
        print(trade_stats.compare(годные[0]["stats"], годные[1]["stats"],
                                  годные[0]["symbol"], годные[1]["symbol"]))

    print()
    print("=" * 70)
    print("ИТОГ")
    print("=" * 70)
    for r in результаты:
        св = r.get("stats")
        if not св:
            print(f"{r['symbol']}: {r['status']}")
        elif not св["достаточно_данных"]:
            print(f"{r['symbol']}: STATUS = UNKNOWN — сделок {св['сделок']}, "
                  f"меньше {trade_stats.МАЛО_СДЕЛОК}. Числа посчитаны, но "
                  f"выводы по ним делать нельзя.")
        else:
            print(f"{r['symbol']}: сделок {св['сделок']}, "
                  f"профит-фактор {св['профит_фактор']}, "
                  f"ожидание {св['ожидание']}, просадка {св['макс_просадка']}")
    print()
    print("Ни один параметр стратегии не изменён. Следующий шаг решается "
          "отдельно, по этим числам.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
