#!/usr/bin/env python3
"""Тесты управления открытой сделкой: журнал выходов, кто двигает стоп, M1.

ПОЧЕМУ ЭТИ ТЕСТЫ ПОЯВИЛИСЬ.

Аудит выходов (docs/EXIT_AUDIT.md) упёрся в то, что по закрытой сделке
НЕВОЗМОЖНО было сказать, какой механизм её закрыл. Стоп двигают четыре
механизма, и все они пишут в одну переменную: по итоговому числу автора не
опознать. Пришлось отдельно дописывать подписи в исследовательский стенд.

Теперь то же самое есть в живой торговле — журнал выходов. Здесь он и
закрепляется: столбцы, названия причин, момент записи, и главное — что
журнал НИ НА ОДНО торговое решение не влияет.

Отдельно закрепляется правило, ради которого всё затевалось: стоп-лосс
может двигаться ТОЛЬКО в сторону уменьшения риска. Никогда назад.

Запуск:  python3 tests/test_exit_management.py
"""

from __future__ import annotations

import re
import sys
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
for _и, _з in (("ORDER_TYPE_BUY", 0), ("ORDER_TYPE_SELL", 1),
               ("TIMEFRAME_M1", 1), ("TIMEFRAME_M5", 5), ("TIMEFRAME_M15", 15),
               ("TIMEFRAME_M30", 30), ("TIMEFRAME_H1", 60), ("TIMEFRAME_H4", 240),
               ("TIMEFRAME_D1", 1440), ("ORDER_FILLING_IOC", 1),
               ("ORDER_FILLING_FOK", 2), ("TRADE_RETCODE_DONE", 10009),
               ("TRADE_RETCODE_REQUOTE", 10004),
               ("TRADE_RETCODE_PRICE_CHANGED", 10020),
               ("TRADE_RETCODE_PRICE_OFF", 10021),
               ("POSITION_TYPE_BUY", 0), ("POSITION_TYPE_SELL", 1),
               ("DEAL_TYPE_BUY", 0), ("DEAL_TYPE_SELL", 1),
               ("DEAL_REASON_CLIENT", 0), ("DEAL_REASON_MOBILE", 1),
               ("DEAL_REASON_WEB", 2), ("DEAL_REASON_EXPERT", 3),
               ("DEAL_REASON_SL", 4), ("DEAL_REASON_TP", 5),
               ("DEAL_REASON_SO", 6)):
    setattr(fake_mt5, _и, _з)
for _и in ("symbol_info", "symbol_info_tick", "order_calc_profit",
           "order_calc_margin", "copy_rates_from_pos", "positions_get",
           "account_info", "last_error", "terminal_info", "history_deals_get"):
    setattr(fake_mt5, _и, lambda *a, **k: None)
sys.modules["MetaTrader5"] = fake_mt5

import trade_manager as tm     # noqa: E402
import trade_stats             # noqa: E402

ИСХОДНИК_MAIN = (APP / "main.py").read_text(encoding="utf-8")
ИСХОДНИК_TM = (APP / "trade_manager.py").read_text(encoding="utf-8")
ИСХОДНИК_CFG = (APP / "config.py.example").read_text(encoding="utf-8")


def позиция(шаблон: str, текст: str) -> int:
    m = re.search(шаблон, текст)
    return m.start() if m else -1


def сброс_состояния() -> None:
    """Стенд должен начинать с чистого листа: словари в trade_manager живут
    на уровне модуля, и остатки от прошлого теста дали бы ложный результат."""
    for словарь in (tm._position_peak_points, tm._position_trough_points,
                    tm._position_risk_points, tm._position_first_seen,
                    tm._position_sl_source, tm._position_tp_source,
                    tm._position_peak_age, tm._position_trough_age,
                    tm._position_initial_sl, tm._closed_journal,
                    tm._closed_peaks):
        словарь.clear()


# =====================================================================
def test_stop_can_only_move_towards_less_risk() -> None:
    """ГЛАВНОЕ ПРАВИЛО. Стоп двигается только в сторону уменьшения риска.

    Это требование задания (пункт 8) и единственная защита от того, чтобы
    один механизм отменял работу другого. Проверяется на самой функции, а
    не на настройке: настройку можно поменять, функцию — нет."""
    print("\n[Стоп-лосс никогда не двигается назад]")

    # BUY: лучше тот стоп, который ВЫШЕ (ближе к цене снизу).
    check(tm._better_sl(True, 1.1000, 1.1020) == 1.1020,
          "BUY: более высокий стоп побеждает")
    check(tm._better_sl(True, 1.1020, 1.1000) == 1.1020,
          "BUY: более низкий стоп отвергается")
    # SELL: лучше тот, который НИЖЕ.
    check(tm._better_sl(False, 1.1020, 1.1000) == 1.1000,
          "SELL: более низкий стоп побеждает")
    check(tm._better_sl(False, 1.1000, 1.1020) == 1.1000,
          "SELL: более высокий стоп отвергается")
    # Ноль означает «стопа не было», а не «стоп на нулевой цене».
    check(tm._better_sl(True, 0.0, 1.1000) == 1.1000,
          "Отсутствующий стоп заменяется любым")
    check(tm._better_sl(False, 1.1000, 0.0) == 1.1000,
          "Нулём существующий стоп не затирается")

    # И то же самое закреплено в самом коде ведения позиции: перенос
    # применяется только если improved.
    check("improved = (best_sl > current_sl) if is_buy else" in ИСХОДНИК_TM,
          "В ведении позиции стоит проверка improved")


def test_every_stop_mover_signs_its_work() -> None:
    """Каждый из четырёх механизмов подписывает свой перенос стопа.

    Без подписи причина выхода в журнале была бы догадкой: по числу
    невозможно понять, чей это уровень."""
    print("\n[Каждый механизм подписывает перенос стопа]")

    for имя, метка in (("безубыток", "ПРИЧИНА_БЕЗУБЫТОК"),
                       ("ATR-трейлинг", "ПРИЧИНА_ТРЕЙЛИНГ"),
                       ("лестница по R", "ПРИЧИНА_ЛЕСТНИЦА"),
                       ("Profit Lock", "ПРИЧИНА_ЛОК")):
        check(f"предложить(" in ИСХОДНИК_TM and метка in ИСХОДНИК_TM,
              f"{имя} подписывается как {метка}")

    check(ИСХОДНИК_TM.count("предложить(") >= 5,
          "Все четыре механизма ходят через одну точку входа",
          f"найдено {ИСХОДНИК_TM.count('предложить(')}")

    # Подпись запоминается ТОЛЬКО когда стоп реально переехал.
    поз_запись = позиция(r"_position_sl_source\[p\.ticket\] = sl_source", ИСХОДНИК_TM)
    поз_проверка = позиция(r"sl_changed = best_sl != current_sl", ИСХОДНИК_TM)
    check(поз_проверка != -1 and поз_запись > поз_проверка,
          "Подпись пишется после проверки, что стоп действительно переехал")


def test_exit_reasons_are_machine_readable() -> None:
    """Названия причин — заглавными и латиницей.

    Это машинный столбец: по нему считают, а не читают. Русский текст с
    пробелами превратил бы подсчёт в разбор строк."""
    print("\n[Названия причин пригодны для подсчёта]")

    причины = [tm.ПРИЧИНА_СТОП, tm.ПРИЧИНА_БЕЗУБЫТОК, tm.ПРИЧИНА_ТРЕЙЛИНГ,
               tm.ПРИЧИНА_ЛЕСТНИЦА, tm.ПРИЧИНА_ЛОК, tm.ПРИЧИНА_ЦЕЛЬ,
               tm.ПРИЧИНА_СПАСЕНИЕ, tm.ПРИЧИНА_ЧАСТИЧНО, tm.ПРИЧИНА_РУЧНОЕ,
               tm.ПРИЧИНА_НЕИЗВЕСТНО]
    check(all(re.fullmatch(r"[A-Z_]+", п) for п in причины),
          "Все причины — только заглавная латиница и подчёркивание")
    check(len(set(причины)) == len(причины),
          "Причины не повторяются")
    check(tm.ПРИЧИНА_СТОП == "STOP_LOSS" and tm.ПРИЧИНА_ЦЕЛЬ == "TAKE_PROFIT",
          "Названия совпадают с принятыми в задании")


def test_journal_has_exactly_the_requested_columns() -> None:
    """Столбцы журнала — те, что перечислены в задании, пункты 14 и 15."""
    print("\n[Журнал выходов: столбцы]")

    нужны = ["symbol", "ticket", "direction", "entry", "exit", "initial_sl",
             "initial_r_points", "max_profit_r", "max_loss_r",
             "holding_time_sec", "exit_reason", "profit", "profit_r",
             "time_to_mfe_sec", "time_to_mae_sec"]
    for имя in нужны:
        check(имя in tm.ЖУРНАЛ_ВЫХОДОВ_СТОЛБЦЫ, f"Есть столбец {имя}")
    check(len(set(tm.ЖУРНАЛ_ВЫХОДОВ_СТОЛБЦЫ)) == len(tm.ЖУРНАЛ_ВЫХОДОВ_СТОЛБЦЫ),
          "Столбцы не повторяются")


def test_journal_records_what_was_measured_and_nothing_else() -> None:
    """Карточка сделки собирается из наших замеров и складывается в архив
    ровно в тот момент, когда позиция исчезла из списка открытых."""
    print("\n[Журнал выходов: карточка сделки]")
    сброс_состояния()

    тикет = 777
    tm.update_position_risk(тикет, 1.1000, 1.0900, 0.0001)   # риск 100 пунктов
    tm.update_peak_profit(тикет, 50.0)                        # пик +0.5R
    tm.update_position_trough(тикет, -30.0)                   # дно -0.3R

    check(abs(tm._position_risk_points[тикет] - 100.0) < 1e-6,
          "Риск запомнен один раз", str(tm._position_risk_points[тикет]))
    check(abs(tm._position_initial_sl[тикет] - 1.0900) < 1e-9,
          "Первоначальный стоп запомнен как цена")

    # Риск не пересчитывается, даже если стоп уже подтянули.
    tm.update_position_risk(тикет, 1.1000, 1.0990, 0.0001)
    check(abs(tm._position_risk_points[тикет] - 100.0) < 1e-6,
          "Подтянутый стоп НЕ переписывает первоначальный риск")

    tm.cleanup_peak_profit(set())          # позиции больше нет
    карточка = tm.pop_closed_journal(тикет)
    check(карточка is not None, "Карточка попала в архив при закрытии")
    check(карточка and abs(карточка["max_profit_r"] - 0.5) < 1e-3,
          "MFE записан в долях риска", str(карточка))
    check(карточка and abs(карточка["max_loss_r"] + 0.3) < 1e-3,
          "MAE записан в долях риска", str(карточка))
    check(tm.pop_closed_journal(тикет) is None,
          "Карточка забирается ОДИН раз — в журнал сделка не попадёт дважды")


def test_journal_is_silent_about_positions_it_never_watched() -> None:
    """Позиция, открытая до запуска программы, не измерялась — и выдумывать
    за неё числа нельзя."""
    print("\n[Чего не измеряли, того не пишем]")
    сброс_состояния()

    tm.cleanup_peak_profit(set())
    check(tm.pop_closed_journal(12345) is None,
          "По неизвестному тикету карточки нет")

    # А если риск известен, но пика не было — карточка есть, поле пустое.
    сброс_состояния()
    tm.update_position_risk(999, 1.1, 1.09, 0.0001)
    tm.cleanup_peak_profit(set())
    к = tm.pop_closed_journal(999)
    check(к is not None and к["max_profit_r"] == "",
          "Неизмеренный пик остаётся пустым, а не нулём", str(к))


def test_manual_close_is_not_blamed_on_the_stop() -> None:
    """Закрытие кнопкой не должно выглядеть в журнале как срабатывание стопа."""
    print("\n[Ручное закрытие отличимо от стопа]")
    сброс_состояния()

    tm.note_manual_close(555)
    check(tm._position_sl_source[555] == tm.ПРИЧИНА_РУЧНОЕ,
          "Кнопка помечает сделку как MANUAL")

    # И помечает ДО отправки приказа: после закрытия помечать будет нечего.
    поз_метка = позиция(r"tm\.note_manual_close\(pos\.ticket\)", ИСХОДНИК_MAIN)
    поз_приказ = позиция(r"def _close_one_position", ИСХОДНИК_MAIN)
    поз_отправка = позиция(r"mt5c\.close_position_partial\(pos, pos\.volume\)",
                           ИСХОДНИК_MAIN)
    check(поз_метка != -1 and поз_приказ < поз_метка < поз_отправка,
          "Отметка стоит до отправки приказа на закрытие")


def test_broker_decides_what_fired_we_decide_who_set_it() -> None:
    """Причина выхода = ответ брокера, уточнённый нашим знанием.

    Подменять ответ брокера своей догадкой нельзя: только он знает, стоп
    сработал или цель."""
    print("\n[Причина выхода: брокер плюс наше знание]")

    for кусок, зачем in (
            (r"DEAL_REASON_SL", "стоп берётся из ответа брокера"),
            (r"DEAL_REASON_TP", "цель берётся из ответа брокера"),
            (r"DEAL_REASON_CLIENT", "закрытие человеком распознаётся"),
            (r"DEAL_REASON_SO", "принудительное закрытие брокером распознаётся"),
            (r"ПРИЧИНА_НЕИЗВЕСТНО", "незнакомый ответ пишется как UNKNOWN")):
        check(позиция(кусок, ИСХОДНИК_MAIN) != -1, f"В разборе есть: {зачем}")

    # Стоп и цель разведены: их ставили разные механизмы.
    check("_position_tp_source" in ИСХОДНИК_TM,
          "Автор цели запоминается отдельно от автора стопа")


def test_journal_never_stops_trading() -> None:
    """Журнал — наблюдение, а не решение. Его сбой не имеет права
    прервать разбор закрытых сделок."""
    print("\n[Журнал не может остановить торговлю]")

    хвост = ИСХОДНИК_TM[позиция(r"def log_exit_journal", ИСХОДНИК_TM):]
    хвост = хвост[:хвост.find("\ndef ", 10)]
    check("except Exception" in хвост, "Запись журнала обёрнута в перехват ошибок")
    check("raise" not in хвост, "И ошибка наружу не пробрасывается")

    кусок = ИСХОДНИК_MAIN[позиция(r"ЖУРНАЛ ВЫХОДОВ", ИСХОДНИК_MAIN):]
    кусок = кусок[:3000]
    check("except Exception" in кусок,
          "И вызов из разбора закрытых сделок тоже защищён")

    # Журнал пишется ПОСЛЕ учёта результата сделки: сначала деньги и
    # обучение, потом наблюдение.
    поз_учёт = позиция(r"al\.record_trade_result\(sym_state, profit\)", ИСХОДНИК_MAIN)
    поз_журнал = позиция(r"tm\.log_exit_journal", ИСХОДНИК_MAIN)
    check(поз_учёт != -1 and поз_учёт < поз_журнал,
          "Учёт результата идёт раньше записи журнала")


def test_m1_management_is_off_until_proven() -> None:
    """Ведение по M1 выключено по умолчанию.

    Не из осторожности, а потому что проверить его нечем: минутных свечей в
    репозитории нет. Включённая непроверенная настройка — это торговля
    вслепую."""
    print("\n[Ведение по M1: выключено, пока не проверено]")

    check(getattr(CFG, "USE_M1_POSITION_MANAGEMENT", None) is False,
          "USE_M1_POSITION_MANAGEMENT = False по умолчанию")
    check(int(getattr(CFG, "M1_ATR_PERIOD", 0)) > 0, "Период ATR задан")
    check("минутных свечей нет" in ИСХОДНИК_CFG or "минутки" in ИСХОДНИК_CFG,
          "В настройках сказано, почему выключено")


def test_m1_falls_back_to_m5_and_never_touches_entry() -> None:
    """M1 меняет ТОЛЬКО расстояния ведения. Ни вход, ни первоначальный стоп."""
    print("\n[M1 не трогает вход и первоначальный стоп]")

    хвост = ИСХОДНИК_MAIN[позиция(r"def management_atr", ИСХОДНИК_MAIN):]
    хвост = хвост[:хвост.find("\ndef ", 10)]
    check("return m5_atr" in хвост,
          "При любой неудаче возвращается M5-ATR")
    check(хвост.count("return m5_atr") >= 3,
          "Откат на M5 предусмотрен во всех ветках отказа",
          f"веток отката {хвост.count('return m5_atr')}")
    check("except Exception" in хвост,
          "Сбой связи не роняет ведение позиции")

    # Вход считается на M5 и через management_atr НЕ проходит.
    поз_вход = позиция(r"atr_value = float\(df_ind\[.atr.\]\.iloc\[-1\]\)",
                       ИСХОДНИК_MAIN)
    check(поз_вход != -1, "Вход по-прежнему берёт ATR с рабочего таймфрейма")
    сигнальный = ИСХОДНИК_MAIN[поз_вход:поз_вход + 400]
    check("management_atr" not in сигнальный.split("manage_open_positions")[0],
          "До ведения позиции management_atr в сигнальную часть не вмешивается")

    # Оба места ведения позиции переведены на него.
    check(ИСХОДНИК_MAIN.count("management_atr(") >= 3,
          "И полный проход, и быстрый монитор используют один и тот же ATR",
          f"вызовов {ИСХОДНИК_MAIN.count('management_atr(')}")


def test_m1_cache_does_not_hammer_the_terminal() -> None:
    """Быстрый монитор ходит раз в секунду, а минутка меняется раз в минуту.
    Запрашивать её каждую секунду — зря дёргать терминал."""
    print("\n[M1 запрашивается не чаще, чем меняется]")

    хвост = ИСХОДНИК_MAIN[позиция(r"def management_atr", ИСХОДНИК_MAIN):]
    хвост = хвост[:хвост.find("\ndef ", 10)]
    check("_m1_atr_cache" in хвост, "Есть кэш минутного ATR")
    check("было[0] == последняя" in хвост,
          "Кэш обновляется по времени последней минутной свечи, а не по часам")


def test_m1_in_the_backtest_never_looks_ahead() -> None:
    """Минутный ATR в проверке на истории берётся ТОЛЬКО из прошлого.

    Это главная опасность минуток: соблазн взять минутку из середины
    пятиминутного бара. Она уже знает то, чего решение по цене закрытия
    знать не может, и весь отчёт превращается в самообман."""
    print("\n[Минутки в стенде не заглядывают вперёд]")
    import baseline_engine as be

    индекс = {100: 1.0, 200: 2.0, 300: 3.0}
    времена = sorted(индекс)

    check(be._m1_atr_на_момент(индекс, времена, 250, 9.9) == 2.0,
          "Берётся последняя минутка НЕ ПОЗЖЕ бара, а не ближайшая")
    check(be._m1_atr_на_момент(индекс, времена, 200, 9.9) == 2.0,
          "Минутка ровно на границе бара считается прошлым")
    check(be._m1_atr_на_момент(индекс, времена, 299, 9.9) == 2.0,
          "Будущая минутка не берётся, даже если она рядом")
    check(be._m1_atr_на_момент(индекс, времена, 50, 9.9) == 9.9,
          "До первой минутки честно возвращается запасной ATR")


def test_m1_index_refuses_to_invent_numbers() -> None:
    """Минутного индекса не должно появляться там, где считать не из чего."""
    print("\n[Минутный индекс не выдумывает значений]")
    import baseline_engine as be

    check(be._m1_atr_index(None) is None, "Без свечей индекса нет")
    check(be._m1_atr_index([]) is None, "Из пустого списка индекса нет")
    коротко = [{"time": t, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0}
               for t in range(5)]
    check(be._m1_atr_index(коротко) is None,
          "Свечей меньше периода ATR — индекса нет, а не ноль")

    длинно = [{"time": t * 60, "open": 1.0, "high": 1.0 + t * 0.001,
               "low": 1.0 - t * 0.001, "close": 1.0} for t in range(60)]
    индекс = be._m1_atr_index(длинно)
    check(индекс is not None and len(индекс) > 0, "На нормальных свечах индекс есть")
    check(индекс is None or all(з > 0 for з in индекс.values()),
          "В индексе нет нулей и NaN")


def test_version_b_is_skipped_when_there_is_nothing_to_check_it_with() -> None:
    """Версия B не попадает в отчёт, если минуток нет.

    Показать её без данных значило бы напечатать пустой столбец, который
    легко принять за результат."""
    print("\n[Версия B без минуток пропускается]")
    исходник = (APP / "run_exit_versions.py").read_text(encoding="utf-8")

    check("по_минуткам" in исходник, "Версия B помечена отдельным признаком")
    check("версии_для" in исходник, "Есть отбор версий по наличию минуток")
    check("ПРОПУЩЕНА" in исходник,
          "Про пропуск версии B говорится вслух, а не молча")

    import run_exit_versions as rv
    без = rv.версии_для(None)
    check(all(not в.get("по_минуткам") for в in без),
          "Без минуток версии по M1 в списке нет")
    с_минутками = rv.версии_для([{"time": 0}])
    check(any(в.get("по_минуткам") for в in с_минутками),
          "С минутками версия B появляется сама")
    check(len(с_минутками) == len(без) + 1,
          "И появляется ровно одна, остальные не трогаются")


def test_new_metrics_exist_and_are_honest() -> None:
    """Метрики из пункта 17. Особенно — что они не врут на пустых данных."""
    print("\n[Метрики отчёта о версиях]")

    сделки = [
        {"money": 2.0, "r": 0.5, "held_seconds": 600, "mfe_r": 0.8, "mae_r": -0.2},
        {"money": -1.0, "r": -1.0, "held_seconds": 1200, "mfe_r": 0.1, "mae_r": -1.0},
        {"money": 3.0, "r": 0.7, "held_seconds": 300, "mfe_r": 0.9, "mae_r": -0.1},
    ]
    св = trade_stats.summarize(сделки, "тест")
    for имя in ("медианный_R", "медианное_время_мин", "крупнейший_выигрыш",
                "крупнейший_убыток", "sharpe_R", "sortino_R",
                "средний_MFE_R", "средний_MAE_R"):
        check(имя in св, f"Есть метрика {имя}")

    check(св["крупнейший_выигрыш"] == 3.0, "Крупнейший выигрыш посчитан")
    check(св["крупнейший_убыток"] == -1.0, "Крупнейший убыток посчитан")
    check(св["медианный_R"] == 0.5, "Медианный R посчитан", str(св["медианный_R"]))
    check(св["медианное_время_мин"] == 10.0, "Медианное время посчитано")

    # Пустой список не должен давать ни ошибок, ни выдуманных чисел.
    пусто = trade_stats.summarize([], "пусто")
    check(пусто["sharpe_R"] == 0.0 and пусто["sortino_R"] == 0.0,
          "На пустых данных Sharpe и Sortino равны нулю, а не бесконечности")
    check(пусто["крупнейший_выигрыш"] == 0.0,
          "И крупнейший выигрыш тоже ноль, а не ошибка")

    # Sortino без единого убытка — это НЕ бесконечность.
    только_плюс = trade_stats.summarize(
        [{"money": 1.0, "r": 0.5}, {"money": 2.0, "r": 0.6}], "плюс")
    check(только_плюс["sortino_R"] == 0.0,
          "Без убытков Sortino = 0, а не бесконечность: это мало данных")


def test_only_the_proven_change_was_adopted() -> None:
    """Принято ровно то, что подтвердили ворота проверки, и ничего сверх.

    Проверка версий дала три разных ответа, и все три закреплены здесь:

      * новая лестница по R    — ПРИНЯТЬ (3 способа из 5 на VALIDATION по
                                 обоим инструментам, подтверждено на OOS);
      * выключение механизмов
        времени                — ОТКЛОНИТЬ (0 из 5 во всех четырёх
                                 сочетаниях, хуже по каждой метрике);
      * ведение по M1          — НЕ ПРОВЕРЕНО, значит выключено.

    Тест существует затем, чтобы отклонённое не просочилось в настройки
    позже, когда причина забудется."""
    print("\n[Принято только подтверждённое]")

    лестница = list(getattr(CFG, "R_TRAIL_LADDER", []))
    check(len(лестница) >= 5, "Лестница на месте", str(лестница))
    первая = лестница[0] if лестница else (0, 0)
    check(abs(float(первая[0]) - 0.50) < 1e-9,
          "Защита включается с +0.5R, а не с +0.30R", str(первая))
    check(float(первая[1]) < 0,
          "Первая ступень урезает риск, а НЕ ставит стоп в безубыток",
          str(первая))
    check(all(float(лестница[i][0]) < float(лестница[i + 1][0])
              for i in range(len(лестница) - 1)),
          "Ступени идут по возрастанию пика")
    check(all(float(лестница[i][1]) < float(лестница[i + 1][1])
              for i in range(len(лестница) - 1)),
          "И запирают всё больше — лестница не может идти вниз")
    check(all(float(п) >= float(з) for п, з in лестница),
          "Ни одна ступень не запирает больше, чем сделка стоила")

    # ОТКЛОНЁННОЕ. Механизмы времени остаются включёнными: их выключение
    # измерено и делает счёт хуже в 2.2 и 2.9 раза.
    check(getattr(CFG, "USE_TP_TIGHTEN", None) is True,
          "Поджим цели по времени НЕ выключен: измерение против")
    check(getattr(CFG, "USE_BREAK_EVEN_RESCUE", None) is True,
          "Спасение в безубыток НЕ выключено: измерение против")
    check(getattr(CFG, "USE_M1_POSITION_MANAGEMENT", None) is False,
          "Ведение по M1 выключено: не проверено")

    # И то же самое доезжает до УЖЕ УСТАНОВЛЕННОЙ программы: config.py
    # обновление обычно не трогает, поэтому нужна разовая миграция.
    миграции = (APP / "config_migrate.py").read_text(encoding="utf-8")
    check("MIGRATED_R_LADDER_LATER_START" in миграции,
          "Новая лестница доезжает до уже установленной программы")

    # ГЛАВНОЕ ЗДЕСЬ. Лестницу задаёт НЕ ОДНА разовая правка, а две: одна для
    # тех, кто ещё не мигрировал, вторая для тех, кто уже получил её со
    # старыми числами. Правки применяются ПО ПОРЯДКУ, и та, что идёт позже,
    # переписывает предыдущую. Если бы они несли разные лестницы, результат
    # зависел бы от порядка строк в списке — а это худший вид ошибки: он не
    # виден при чтении и меняется от невинной перестановки.
    #
    # Поэтому требование простое: ВСЕ правки, трогающие лестницу, обязаны
    # ставить ОДНО И ТО ЖЕ значение. Тогда порядок перестаёт иметь значение.
    import config_migrate as cm
    лестницы = [tuple(tuple(ст) for ст in изм["R_TRAIL_LADDER"])
                for _, изм, _ in cm.ONE_TIME if "R_TRAIL_LADDER" in изм]
    check(len(лестницы) >= 1, "Лестница есть хотя бы в одной разовой правке")
    check(len(set(лестницы)) <= 1,
          "Все разовые правки ставят ОДНУ И ТУ ЖЕ лестницу — порядок не решает",
          f"разных значений: {len(set(лестницы))}")
    if лестницы:
        check([list(ст) for ст in лестницы[0]] == [list(ст) for ст in лестница],
              "И она совпадает с эталоном config.py.example",
              f"{лестницы[0]} против {лестница}")


def test_the_forbidden_things_are_still_forbidden() -> None:
    """Задание, пункты 12, 13 и 22: чего в коде быть не должно."""
    print("\n[Запрещённое не появилось]")

    весь = ИСХОДНИК_MAIN + ИСХОДНИК_TM
    for слово in ("martingale", "мартингейл", "averaging_down", "усреднени",
                  "recovery_lot", "grid_step"):
        check(слово.lower() not in весь.lower(),
              f"Нет следов «{слово}»")

    # Риск не подкручен ради отчёта.
    check("RISK_PERCENT" not in ИСХОДНИК_TM,
          "Управление сделкой не трогает процент риска")
    # Стоп не расширяется: единственный способ его сдвинуть — _better_sl.
    check(ИСХОДНИК_TM.count("_better_sl") >= 2,
          "Перенос стопа идёт только через _better_sl")


if __name__ == "__main__":
    print("=" * 62)
    print("УПРАВЛЕНИЕ ОТКРЫТОЙ СДЕЛКОЙ: журнал, авторство стопа, M1")
    print("=" * 62)
    test_stop_can_only_move_towards_less_risk()
    test_every_stop_mover_signs_its_work()
    test_exit_reasons_are_machine_readable()
    test_journal_has_exactly_the_requested_columns()
    test_journal_records_what_was_measured_and_nothing_else()
    test_journal_is_silent_about_positions_it_never_watched()
    test_manual_close_is_not_blamed_on_the_stop()
    test_broker_decides_what_fired_we_decide_who_set_it()
    test_journal_never_stops_trading()
    test_m1_management_is_off_until_proven()
    test_m1_falls_back_to_m5_and_never_touches_entry()
    test_m1_cache_does_not_hammer_the_terminal()
    test_m1_in_the_backtest_never_looks_ahead()
    test_m1_index_refuses_to_invent_numbers()
    test_version_b_is_skipped_when_there_is_nothing_to_check_it_with()
    test_new_metrics_exist_and_are_honest()
    test_only_the_proven_change_was_adopted()
    test_the_forbidden_things_are_still_forbidden()
    print()
    print("=" * 62)
    print(f"Пройдено: {passed}   Провалено: {failed}")
    print("=" * 62)
    sys.exit(1 if failed else 0)
