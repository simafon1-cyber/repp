#!/usr/bin/env python3
"""Тесты PHASE 2: выгрузка истории, качество данных, прогон, заглядывание вперёд.

САМАЯ ВАЖНАЯ ПРОВЕРКА ЗДЕСЬ — «ОТРАВЛЕННОЕ БУДУЩЕЕ».

Заглядывание вперёд (look-ahead) — главная причина, по которой проверки на
истории врут. Поймать его чтением кода трудно: оно прячется в одном сдвиге
индекса. Поэтому здесь оно ловится опытом, а не чтением.

Способ простой и не оставляющий лазеек: берутся данные, все свечи ПОСЛЕ
некоторой точки заменяются полной чепухой (цены в сто раз выше), и решения
пересчитываются заново. Если хоть одно решение ДО этой точки изменилось —
значит оно как-то зависело от будущего. Ни одно честное решение измениться
не может: будущего оно не видит.

Синтетические свечи здесь допустимы и нужны: ими проверяется САМ ДВИЖОК, а
не стратегия. Для baseline синтетика запрещена, и движок берёт только
настоящую историю брокера.

Запуск:  python3 tests/test_baseline.py
"""

from __future__ import annotations

import math
import os
import random
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
sys.modules["config"] = cfg
CFG = cfg

fake_mt5 = types.ModuleType("MetaTrader5")
for _имя, _знач in (("ORDER_TYPE_BUY", 0), ("ORDER_TYPE_SELL", 1),
                    ("TIMEFRAME_M1", 1), ("TIMEFRAME_M5", 5), ("TIMEFRAME_M15", 15),
                    ("TIMEFRAME_M30", 30), ("TIMEFRAME_H1", 60), ("TIMEFRAME_H4", 240),
                    ("TIMEFRAME_D1", 1440), ("ORDER_FILLING_IOC", 1),
                    ("ORDER_FILLING_FOK", 2), ("TRADE_RETCODE_REQUOTE", 10004),
                    ("TRADE_RETCODE_PRICE_CHANGED", 10020),
                    ("TRADE_RETCODE_PRICE_OFF", 10021), ("TRADE_RETCODE_DONE", 10009),
                    ("POSITION_TYPE_BUY", 0), ("POSITION_TYPE_SELL", 1)):
    setattr(fake_mt5, _имя, _знач)
fake_mt5.symbol_info = lambda s: None
fake_mt5.symbol_info_tick = lambda s: None
fake_mt5.order_calc_profit = lambda *a: None
fake_mt5.order_calc_margin = lambda *a: None
sys.modules["MetaTrader5"] = fake_mt5

import baseline_engine          # noqa: E402
import history_data             # noqa: E402
import trade_stats              # noqa: E402


# =====================================================================
# ДАННЫЕ ДЛЯ ПРОВЕРКИ ДВИЖКА
# =====================================================================
META = {
    "symbol": "EURUSD", "timeframe": "M5", "point": 0.00001, "digits": 5,
    "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
    "trade_tick_value": 1.0, "trade_tick_size": 0.00001,
    "money_per_point_per_lot": 1.0, "stops_level": 0,
    "server_utc_offset_hours": 3.0, "last_bar_closed": True,
    "broker": "Тест", "server": "Тест-Demo",
}


def свечи(n: int, seed: int = 7, start: int = 1700000000) -> list:
    """Правдоподобные свечи с трендами и откатами. Только для проверки
    ДВИЖКА — в baseline такие данные не попадают никогда."""
    rnd = random.Random(seed)
    цена = 1.10000
    ряд = []
    направление = 1
    for i in range(n):
        if i % 60 == 0:
            направление = rnd.choice([1, -1, 1, -1, 0])
        шаг = направление * rnd.uniform(0.00002, 0.00012) + rnd.gauss(0, 0.00006)
        откр = цена
        закр = цена + шаг
        верх = max(откр, закр) + abs(rnd.gauss(0, 0.00003))
        низ = min(откр, закр) - abs(rnd.gauss(0, 0.00003))
        ряд.append({
            "time": start + i * 300,
            "open": round(откр, 5), "high": round(верх, 5),
            "low": round(низ, 5), "close": round(закр, 5),
            "tick_volume": rnd.randint(50, 500),
            "spread": rnd.randint(5, 12),
        })
        цена = закр
    return ряд


# =====================================================================
# 1. ЗАГЛЯДЫВАНИЕ ВПЕРЁД
# =====================================================================
def test_future_cannot_change_the_past() -> None:
    """ОТРАВЛЕННОЕ БУДУЩЕЕ. Заменяем все свечи после точки K чепухой. Ни одно
    решение, принятое ДО K, измениться не имеет права."""
    print("\n[Будущее не может изменить прошлое]")
    import pandas as pd
    import signal_engine as se
    import mt5_connector as mt5c
    import market_regime as mr
    from indicators import add_all_indicators
    from state import SymbolState

    ряд = свечи(900)
    K = 600
    отравленный = [dict(b) for b in ряд]
    for i in range(K, len(отравленный)):
        # Чепуха: цены в сто раз выше, объём и спред абсурдные.
        for поле in ("open", "high", "low", "close"):
            отравленный[i][поле] = отравленный[i][поле] * 100.0
        отравленный[i]["tick_volume"] = 999999
        отравленный[i]["spread"] = 9999

    # Те же подмены, что делает движок: без них счёт сигнала полез бы в сеть
    # за новостями на КАЖДОЙ свече — медленно, шумно и к делу не относится.
    import news_calendar
    import telegram_signals
    import market_context
    сохранено = [
        (mt5c, "get_spread_points", mt5c.get_spread_points),
        (news_calendar, "soft_news_penalty", news_calendar.soft_news_penalty),
        (telegram_signals, "score_bonus", telegram_signals.score_bonus),
        (se, "market_context_score_adjustment", se.market_context_score_adjustment),
    ]
    mt5c.get_spread_points = lambda s: 8
    news_calendar.soft_news_penalty = lambda *a, **k: 0.0
    telegram_signals.score_bonus = lambda *a, **k: 0.0
    se.market_context_score_adjustment = lambda *a, **k: 0.0
    try:
        одинаковых = 0
        расхождения = []
        for i in range(baseline_engine.ОКНО_БАРОВ, K):
            окно_ч = ряд[i - baseline_engine.ОКНО_БАРОВ + 1:i + 1]
            окно_о = отравленный[i - baseline_engine.ОКНО_БАРОВ + 1:i + 1]

            st_ч, st_о = SymbolState(symbol="EURUSD"), SymbolState(symbol="EURUSD")
            df_ч = add_all_indicators(pd.DataFrame(окно_ч), CFG)
            df_о = add_all_indicators(pd.DataFrame(окно_о), CFG)
            mr.update_market_regime(st_ч, df_ч)
            mr.update_market_regime(st_о, df_о)
            тр_ч = pd.DataFrame(baseline_engine.resample(окно_ч, 3))
            тр_о = pd.DataFrame(baseline_engine.resample(окно_о, 3))

            for направление in (1, -1):
                a = se.calc_signal_score("EURUSD", направление, df_ч, тр_ч, 0.00001, st_ч)
                b = se.calc_signal_score("EURUSD", направление, df_о, тр_о, 0.00001, st_о)
                if abs(a - b) < 1e-9:
                    одинаковых += 1
                else:
                    расхождения.append((i, направление, a, b))

        check(одинаковых > 0, "Решения до точки K вообще считались",
              str(одинаковых))
        check(not расхождения,
              f"Ни одно из {одинаковых} решений до K не изменилось от порчи будущего",
              str(расхождения[:3]))
    finally:
        for модуль, имя, старое in сохранено:
            setattr(модуль, имя, старое)


def test_engine_trades_do_not_depend_on_future() -> None:
    """То же самое, но на уровне целого прогона: сделки, открытые и закрытые
    до точки K, обязаны совпасть до копейки."""
    print("\n[Сделки не зависят от того, что будет потом]")
    ряд = свечи(1500, seed=11)
    K = 1100

    полный = baseline_engine.run("EURUSD", ряд[:K], META, equity_start=1000.0)
    отравленный = [dict(b) for b in ряд]
    for i in range(K, len(отравленный)):
        for поле in ("open", "high", "low", "close"):
            отравленный[i][поле] *= 100.0
    с_будущим = baseline_engine.run("EURUSD", отравленный, META, equity_start=1000.0)

    граница = ряд[K - 1]["time"]
    было = [t for t in полный["trades"] if t["exit_time"] < граница]
    стало = [t for t in с_будущим["trades"] if t["exit_time"] < граница]

    check(len(было) == len(стало),
          f"Число сделок до K одинаково: {len(было)} и {len(стало)}")
    расхождения = [(a["entry_time"], a["money"], b["money"])
                   for a, b in zip(было, стало)
                   if abs(a["money"] - b["money"]) > 1e-9
                   or a["direction"] != b["direction"]]
    check(not расхождения, "И каждая сделка совпала до копейки",
          str(расхождения[:3]))
    if not было:
        print("       (сделок до K не случилось — проверка выше пуста, "
              "смысл несёт предыдущий тест)")


def test_engine_never_reads_beyond_current_bar() -> None:
    """Движок обязан показывать стратегии окно, кончающееся ТЕКУЩЕЙ свечой."""
    print("\n[Окно кончается текущей свечой]")
    src = (APP / "baseline_engine.py").read_text(encoding="utf-8")
    кусок = src.split("окно = ряд[", 1)[1].split("\n", 1)[0]
    check("i + 1" in кусок, "Окно заканчивается на текущем баре", кусок.strip())
    check("i - ОКНО_БАРОВ + 1" in кусок, "И начинается ровно на 300 баров раньше")

    ряд = свечи(400)
    окно = ряд[100 - baseline_engine.ОКНО_БАРОВ + 1:100 + 1] if False else None
    # Проверяем сам resample: он не имеет права собирать НЕПОЛНУЮ старшую свечу.
    собранное = baseline_engine.resample(свечи(10), 3)
    check(len(собранное) == 3, "Из 10 свечей M5 собирается 3 полных M15, а не 4",
          str(len(собранное)))
    последняя = собранное[-1]
    check(последняя["time"] == свечи(10)[6]["time"],
          "И последняя из них начинается на седьмой свече")


def test_auto_learning_sees_only_closed_trades() -> None:
    """Автообучение обязано получать результат сделки ПОСЛЕ её закрытия."""
    print("\n[Автообучение не получает данные из будущего]")
    src = (APP / "baseline_engine.py").read_text(encoding="utf-8")
    тело = src.split("if открытая is None:", 1)[1].split("состояние.bar_counter", 1)[0]
    check("record_trade_result" in тело and "record_trade_peak" in тело,
          "Обучение кормится только в ветке «сделка закрылась»")
    check("сделки[-1]" in тело, "И именно уже закрытой сделкой")
    # Обратное: вызовов обучения вне этой ветки быть не должно.
    всего_result = src.count("record_trade_result")
    check(всего_result == 1, "Вызов ровно один, других путей нет",
          str(всего_result))


# =====================================================================
# 2. КАЧЕСТВО ДАННЫХ
# =====================================================================
def test_data_quality_catches_everything() -> None:
    print("\n[Проверка качества данных]")
    хорошие = свечи(200)
    q = history_data.check_quality(хорошие, "M5", META)
    check(q["usable"] is True, "Ровные данные признаны годными",
          str(q["problems"]))
    check(q["duplicates"] == 0 and q["gaps"] == 0, "И претензий к ним нет")
    check(q["timezone_known"] is True, "Часовой пояс известен из паспорта")

    # Повтор
    с_повтором = хорошие + [dict(хорошие[-1])]
    q = history_data.check_quality(с_повтором, "M5", META)
    check(q["duplicates"] == 1, "Повторяющаяся свеча найдена")
    check(q["usable"] is False, "И данные с повтором негодны")

    # Не по порядку
    перемешанные = list(хорошие)
    перемешанные[50], перемешанные[51] = перемешанные[51], перемешанные[50]
    q = history_data.check_quality(перемешанные, "M5", META)
    check(q["out_of_order"] >= 1, "Нарушенный порядок найден")
    check(any("не по порядку" in p for p in q["problems"]),
          "И объяснён словами", "; ".join(q["problems"]))

    # Пропуск
    с_дырой = хорошие[:100] + хорошие[110:]
    q = history_data.check_quality(с_дырой, "M5", META)
    check(q["gaps"] == 1 and q["missing_bars"] == 10,
          "Пропуск найден и посчитан точно",
          f"{q['gaps']} разрывов, {q['missing_bars']} свечей")

    # Законный перерыв (выходные) пропуском не считается
    выходные = hоли = хорошие[:100] + [
        dict(b, time=b["time"] + 300 * 500) for b in хорошие[100:120]]
    q = history_data.check_quality(выходные, "M5", META)
    check(q["legal_breaks"] >= 1, "Длинный перерыв признан законным",
          str(q["legal_breaks"]))

    # Битые цены
    битые = list(хорошие)
    битые[10] = dict(битые[10], high=0.5, low=2.0)
    q = history_data.check_quality(битые, "M5", META)
    check(q["bad_prices"] == 1, "Свеча с максимумом ниже минимума найдена")

    # Нет часового пояса
    q = history_data.check_quality(хорошие, "M5", {})
    check(q["timezone_known"] is False, "Отсутствие часового пояса замечено")
    check(any("смещение времени" in p for p in q["problems"]),
          "И названо проблемой", "; ".join(q["problems"]))

    # Пустые данные
    q = history_data.check_quality([], "M5", META)
    check(q["usable"] is False and q["bars"] == 0, "Пустые данные негодны")


def test_last_bar_must_be_closed() -> None:
    """Незакрытая свеча в данных — это кусок будущего."""
    print("\n[Последняя свеча обязана быть закрытой]")
    ряд = свечи(50)
    q = history_data.check_quality(ряд, "M5", {"last_bar_closed": True})
    check(q["last_bar_open"] is False, "Паспорт говорит «закрыта» — верим ему")

    q = history_data.check_quality(ряд, "M5", {})
    check(q["last_bar_open"] is True, "Без паспорта считаем незакрытой")
    check(q["usable"] is False, "И такие данные негодны")

    # Прямая проверка по времени сервера
    последняя = ряд[-1]["time"]
    q = history_data.check_quality(ряд, "M5", META, now_server=последняя + 100)
    check(q["last_bar_open"] is True, "Свеча моложе своего периода — не закрыта")
    q = history_data.check_quality(ряд, "M5", META, now_server=последняя + 400)
    check(q["last_bar_open"] is False, "Прошёл целый период — закрыта")

    # Выгрузка обязана брать с позиции 1, а не 0.
    src = (APP / "history_export.py").read_text(encoding="utf-8")
    строка = [l for l in src.splitlines()
              if "copy_rates_from_pos" in l and not l.strip().startswith("#")]
    check(строка and ", 1, " in строка[0],
          "Выгрузка начинается с позиции 1 (0 — незакрытая свеча)",
          строка[0].strip() if строка else "строки нет")


def test_clean_does_not_invent_data() -> None:
    """Очистка выбрасывает мусор, но НИЧЕГО не дорисовывает."""
    print("\n[Очистка ничего не выдумывает]")
    ряд = свечи(100)
    с_дырой = ряд[:50] + ряд[60:]
    чистые = history_data.clean(с_дырой, "M5")
    check(len(чистые) == 90, "Пропуск остался пропуском, свечи не дорисованы",
          str(len(чистые)))

    с_повтором = ряд + [dict(ряд[-1])]
    check(len(history_data.clean(с_повтором, "M5")) == 100, "Повтор убран")

    перемешанные = list(reversed(ряд))
    восстановленные = history_data.clean(перемешанные, "M5")
    времена = [b["time"] for b in восстановленные]
    check(времена == sorted(времена), "Порядок восстановлен по времени")

    # Сырое и обработанное хранятся ОТДЕЛЬНО: всегда можно вернуться к
    # исходнику и посмотреть, что именно было выброшено.
    with tempfile.TemporaryDirectory() as папка:
        путь = history_data.save_clean(чистые, "EURUSD", "M5", folder=папка)
        check(os.path.exists(путь), "Обработанные данные сохраняются отдельно")
        назад = history_data.load_csv(путь)
        check(len(назад) == len(чистые), "И читаются обратно без потерь",
              f"{len(назад)} из {len(чистые)}")
        check(history_data.CLEAN_FOLDER != history_data.RAW_FOLDER,
              "Папка обработанных не совпадает с папкой сырых")
    src = (APP / "run_baseline.py").read_text(encoding="utf-8")
    check("save_clean" in src, "И расчёт baseline действительно их сохраняет")


# =====================================================================
# 3. СТАТИСТИКА
# =====================================================================
def test_stats_are_correct() -> None:
    print("\n[Статистика считается верно]")
    сделки = [
        {"direction": 1, "money": 10.0, "points": 100, "r": 1.0,
         "mae_points": -20, "mfe_points": 120, "held_seconds": 600},
        {"direction": 1, "money": -5.0, "points": -50, "r": -0.5,
         "mae_points": -60, "mfe_points": 10, "held_seconds": 300},
        {"direction": -1, "money": -5.0, "points": -50, "r": -0.5,
         "mae_points": -55, "mfe_points": 5, "held_seconds": 900},
        {"direction": -1, "money": 20.0, "points": 200, "r": 2.0,
         "mae_points": -10, "mfe_points": 220, "held_seconds": 1200},
    ]
    св = trade_stats.summarize(сделки, "тест")
    check(св["сделок"] == 4, "Число сделок")
    check(св["чистая_прибыль"] == 20.0, "Чистая прибыль", str(св["чистая_прибыль"]))
    check(св["профит_фактор"] == 3.0, "Профит-фактор 30/10", str(св["профит_фактор"]))
    check(св["винрейт"] == 50.0, "Винрейт")
    check(св["средний_выигрыш"] == 15.0, "Средний выигрыш")
    check(св["средний_проигрыш"] == 5.0, "Средний проигрыш")
    check(св["ожидание"] == 5.0, "Ожидание на сделку")
    check(св["средний_R"] == 0.5, "Средний R", str(св["средний_R"]))
    check(св["средний_MAE"] == 36.2, "Средний MAE", str(св["средний_MAE"]))
    check(св["среднее_время_в_сделке_мин"] == 12.5, "Среднее время в сделке")
    check(св["макс_серия_убытков"] == 2, "Максимальная серия убытков")
    check(св["макс_серия_побед"] == 1, "Максимальная серия побед")
    check(св["достаточно_данных"] is False,
          "Четыре сделки — это НЕ достаточно данных")

    по_сторонам = trade_stats.by_direction(сделки)
    check(по_сторонам["BUY"]["сделок"] == 2, "Покупки отделены")
    check(по_сторонам["SELL"]["сделок"] == 2, "Продажи отделены")
    check(по_сторонам["BUY"]["чистая_прибыль"] == 5.0, "И считаются раздельно")
    check(по_сторонам["SELL"]["чистая_прибыль"] == 15.0,
          "Продажи могут быть лучше покупок — это и надо видеть")

    # Просадка считается от пика, а не от нуля.
    check(св["макс_просадка"] == 10.0, "Просадка от пика", str(св["макс_просадка"]))

    пусто = trade_stats.summarize([], "пусто")
    check(пусто["сделок"] == 0 and пусто["профит_фактор"] == 0.0,
          "Пустой список не роняет счёт")
    check("сделок не было" in trade_stats.describe(пусто), "И честно об этом говорит")


def test_small_sample_is_flagged_everywhere() -> None:
    """Меньше сотни сделок — вывод не делается нигде."""
    print("\n[Мало сделок — STATUS UNKNOWN]")
    мало = [{"direction": 1, "money": 1.0} for _ in range(50)]
    св = trade_stats.summarize(мало, "мало")
    check(св["достаточно_данных"] is False, "Пятьдесят сделок — недостаточно")
    check("UNKNOWN" in trade_stats.describe(св), "И это написано словами")

    много = [{"direction": 1, "money": 1.0} for _ in range(trade_stats.МАЛО_СДЕЛОК)]
    check(trade_stats.summarize(много, "много")["достаточно_данных"] is True,
          "Ровно на пороге — достаточно")

    текст = trade_stats.compare(св, св, "A", "B")
    check("ВНИМАНИЕ" in текст, "Сравнение малых выборок сопровождается оговоркой")


# =====================================================================
# 4. ДВИЖОК
# =====================================================================
def test_engine_uses_live_functions() -> None:
    """Отдельной упрощённой стратегии быть не должно."""
    print("\n[Движок использует ЖИВЫЕ функции, а не свою копию]")
    src = (APP / "baseline_engine.py").read_text(encoding="utf-8")
    for вызов, зачем in (("se.calc_signal_score", "счёт сигнала"),
                         ("add_all_indicators", "индикаторы"),
                         ("mr.update_market_regime", "режим рынка"),
                         ("al.adaptive_score_threshold", "порог входа"),
                         ("rm.calc_lot", "объём"),
                         ("rm.apply_min_stop_floor", "пол стопа"),
                         ("rm.spread_ok", "фильтр спреда"),
                         ("rm.volatility_ok", "фильтр волатильности"),
                         ("rm.reversal_cooldown_ok", "анти-дребезг"),
                         ("tm.r_ladder_lock_points", "лестница по R"),
                         ("tm._tiered_lock_percent", "Profit Lock")):
        check(вызов in src, f"Используется живая функция: {зачем}")

    # Своих формул счёта быть не должно.
    for запрещено in ("score += 20", "score += 15", "BODY_PERCENT_MIN"):
        check(запрещено not in src,
              f"Своей копии логики нет: {запрещено}")


def test_engine_restores_trading_modules() -> None:
    """Прогон подменяет источники данных, но обязан всё вернуть на место."""
    print("\n[После прогона живая программа не испорчена]")
    import mt5_connector as mt5c
    import news_calendar
    import risk_manager as rm
    import telegram_signals

    до = (mt5c.get_spread_points, news_calendar.soft_news_penalty,
          telegram_signals.score_bonus, rm.money_risk_per_lot,
          rm.margin_block_reason)
    baseline_engine.run("EURUSD", свечи(400), META, equity_start=1000.0)
    после = (mt5c.get_spread_points, news_calendar.soft_news_penalty,
             telegram_signals.score_bonus, rm.money_risk_per_lot,
             rm.margin_block_reason)
    check(до == после, "Все подменённые функции вернулись на место")


def test_engine_is_repeatable_and_honest() -> None:
    print("\n[Прогон воспроизводим и не врёт в свою пользу]")
    ряд = свечи(800, seed=3)
    a = baseline_engine.run("EURUSD", ряд, META, equity_start=1000.0)
    b = baseline_engine.run("EURUSD", ряд, META, equity_start=1000.0)
    check(len(a["trades"]) == len(b["trades"]),
          "Один и тот же прогон даёт одно и то же число сделок")
    check([t["money"] for t in a["trades"]] == [t["money"] for t in b["trades"]],
          "И те же результаты")

    check(a["bars_seen"] == len(ряд) - baseline_engine.ОКНО_БАРОВ,
          "Разобрано столько баров, сколько было (минус окно разгона)")

    # Мало данных — честный отказ, а не выдуманный результат.
    мало = baseline_engine.run("EURUSD", свечи(100), META)
    check(мало.get("error", "") != "", "На коротком куске движок отказывается считать")
    check("минимум" in мало.get("error", ""), "И объясняет, чего не хватило",
          мало.get("error", ""))

    # Стоп раньше цели: порядок внутри бара неизвестен, берём худшее.
    src = (APP / "baseline_engine.py").read_text(encoding="utf-8")
    место_стоп = src.index("стоп_задет = ")
    место_цель = src.index("цель_задета = ")
    check(место_стоп < место_цель, "Стоп проверяется РАНЬШЕ цели")
    check("СНАЧАЛА СТОП" in src, "И это объяснено прямо в коде")


def test_not_reproducible_is_declared() -> None:
    """Невоспроизводимое обязано быть названо, а не подменено выдумкой."""
    print("\n[Невоспроизводимое названо честно]")
    for часть in ("AI-сигнал", "Новости", "Сигналы Telegram",
                  "Проскальзывание и отказы брокера"):
        check(часть in baseline_engine.NOT_REPRODUCIBLE,
              f"Помечено как невоспроизводимое: {часть}")
    текст = baseline_engine.describe_not_reproducible()
    check("НЕ заменена выдуманной логикой" in текст,
          "И сказано, что замены выдумкой нет")

    # AI действительно не вызывается в прогоне.
    src = (APP / "baseline_engine.py").read_text(encoding="utf-8")
    check("ai.fetch_ai_signal" not in src and "apply_ai_signal" not in src,
          "AI в прогоне не вызывается вовсе")


def test_symbols_are_never_mixed() -> None:
    """EURUSD и XAUUSD считаются раздельно — это требование владельца."""
    print("\n[Инструменты не смешиваются]")
    src = (APP / "run_baseline.py").read_text(encoding="utf-8")
    check("НЕ смешиваются" in src or "не смешиваются" in src,
          "В отчёте прямо сказано, что инструменты не смешиваются")
    check("СИМВОЛЫ_ПО_УМОЛЧАНИЮ = [\"EURUSD\", \"XAUUSD\"]" in src,
          "Оба инструмента считаются, но по отдельности")
    # У каждого прогона свой символ и свой набор сделок.
    a = baseline_engine.run("EURUSD", свечи(400, seed=1), META, equity_start=1000.0)
    check(all(t["symbol"] == "EURUSD" for t in a["trades"]),
          "Каждая сделка помечена своим инструментом")


if __name__ == "__main__":
    print("=" * 62)
    print("ТЕСТЫ: BASELINE, ДАННЫЕ, ЗАГЛЯДЫВАНИЕ ВПЕРЁД")
    print("=" * 62)
    test_future_cannot_change_the_past()
    test_engine_trades_do_not_depend_on_future()
    test_engine_never_reads_beyond_current_bar()
    test_auto_learning_sees_only_closed_trades()
    test_data_quality_catches_everything()
    test_last_bar_must_be_closed()
    test_clean_does_not_invent_data()
    test_stats_are_correct()
    test_small_sample_is_flagged_everywhere()
    test_engine_uses_live_functions()
    test_engine_restores_trading_modules()
    test_engine_is_repeatable_and_honest()
    test_not_reproducible_is_declared()
    test_symbols_are_never_mixed()
    print()
    print("=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    sys.exit(1 if failed else 0)
