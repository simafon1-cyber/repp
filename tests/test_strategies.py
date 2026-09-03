#!/usr/bin/env python3
"""Тесты готовых стратегий: параметры реальны, риск не затрагивается.

Запуск:  python3 tests/test_strategies.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
APP = BASE.parent / "ai_scalper_standalone"
sys.path.insert(0, str(APP))

import strategies as S  # noqa: E402

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


def known_config_params() -> set[str]:
    """Имена параметров, которые реально существуют в программе."""
    src = (APP / "desktop_app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "ADVANCED_PARAMS":
            return {p[0] for p in ast.literal_eval(node.value)}
    return set()


def config_example_params() -> set[str]:
    """Имена, объявленные в config.py.example."""
    src = (APP / "config.py.example").read_text(encoding="utf-8")
    names = set()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def test_basics() -> None:
    print("\n=== 1. Состав набора стратегий ===")
    check(len(S.STRATEGIES) >= 4, f"стратегий несколько ({len(S.STRATEGIES)})")
    keys = [s.key for s in S.STRATEGIES]
    check(len(keys) == len(set(keys)), "ключи не повторяются", str(keys))
    titles = S.titles()
    check(len(titles) == len(set(titles)), "названия не повторяются", str(titles))

    for s in S.STRATEGIES:
        check(bool(s.idea and s.when and s.caution),
              f"«{s.title}»: заполнены смысл, применимость и предупреждение")
        # РАБОЧАЯ стратегия — это СВЯЗКА настроек: она перестраивает
        # поведение целиком, и одной строчкой такое не задать.
        #
        # ЧЕРНОВИК — другое дело, и требовать от него пяти настроек
        # ВРЕДНО. Черновик проверяет ОДНУ причину, и чем меньше он
        # меняет, тем честнее проверка: изменил два условия сразу — потом
        # не скажешь, какое подействовало. Стратегия «Зеркало» меняет
        # ровно одну настройку (сторону входа) именно поэтому.
        #
        # Требование «хоть что-то менять» остаётся для всех: стратегия,
        # не меняющая ничего, — это пункт в списке, который врёт.
        нужно = 1 if S.черновик(s) else 5
        check(len(s.params) >= нужно,
              f"«{s.title}»: меняет достаточно настроек "
              f"({len(s.params)}, нужно не меньше {нужно})")


def test_lookup() -> None:
    print("\n=== 2. Поиск стратегии ===")
    first = S.STRATEGIES[0]
    check(S.by_key(first.key) is first, "поиск по ключу")
    check(S.by_title(first.title) is first, "поиск по названию")
    check(S.by_key("нет-такой") is None, "несуществующий ключ -> None")
    check(S.by_title("нет такой") is None, "несуществующее название -> None")


def test_params_are_real() -> None:
    print("\n=== 3. Все параметры существуют в программе ===")
    known = known_config_params()
    check(len(known) > 100, f"список параметров программы прочитан ({len(known)})")
    declared = config_example_params()

    for s in S.STRATEGIES:
        unknown = [k for k in s.params if k not in known and k not in declared]
        check(not unknown, f"«{s.title}»: нет выдуманных параметров",
              f"не найдены: {unknown}")


def test_risk_is_protected() -> None:
    print("\n=== 4. Стратегия НЕ управляет риском ===")
    for s in S.STRATEGIES:
        touched = [k for k in s.params if k in S.PROTECTED_PARAMS]
        check(not touched, f"«{s.title}»: не трогает настройки риска",
              f"трогает: {touched}")

    # Даже если параметр риска попадёт в стратегию по ошибке — он отбрасывается
    dirty = S.Strategy(key="x", title="Проверка", idea="i", when="w", caution="c",
                       params={"EMA_FAST_PERIOD": 5,
                               "RISK_PERCENT": 99.0,
                               "DAILY_LOSS_LIMIT_PERCENT": 50.0,
                               "MAX_OPEN_POSITIONS": 100})
    safe = S.safe_params(dirty)
    check("EMA_FAST_PERIOD" in safe, "обычный параметр проходит")
    check("RISK_PERCENT" not in safe, "риск на сделку отброшен")
    check("DAILY_LOSS_LIMIT_PERCENT" not in safe, "дневной лимит убытка отброшен")
    check("MAX_OPEN_POSITIONS" not in safe, "лимит числа сделок отброшен")
    check(len(safe) == 1, "из четырёх параметров остался только безопасный",
          str(sorted(safe)))


def test_values_sane() -> None:
    print("\n=== 5. Значения параметров осмысленны ===")
    for s in S.STRATEGIES:
        p = s.params
        fast, slow = p.get("EMA_FAST_PERIOD"), p.get("EMA_SLOW_PERIOD")
        if fast and slow:
            check(fast < slow, f"«{s.title}»: быстрая EMA быстрее медленной",
                  f"{fast} vs {slow}")
        trend = p.get("EMA_TREND_PERIOD")
        if slow and trend:
            check(slow < trend, f"«{s.title}»: EMA тренда длиннее медленной",
                  f"{slow} vs {trend}")

        ob, os_ = p.get("RSI_OVERBOUGHT"), p.get("RSI_OVERSOLD")
        if ob and os_:
            check(os_ < ob and 0 < os_ < 50 < ob < 100,
                  f"«{s.title}»: уровни RSI в разумных границах", f"{os_}..{ob}")

        rr, min_rr = p.get("RISK_REWARD_RATIO"), p.get("MIN_RISK_REWARD_RATIO")
        if rr and min_rr:
            check(min_rr <= rr, f"«{s.title}»: минимальное R:R не выше целевого",
                  f"{min_rr} vs {rr}")
            check(min_rr >= 1.0, f"«{s.title}»: не берём сделки с R:R хуже 1:1",
                  str(min_rr))


def test_strategies_differ() -> None:
    print("\n=== 6. Стратегии действительно разные ===")
    trend = S.by_key("trend_follow")
    mean = S.by_key("mean_reversion")
    check(trend is not None and mean is not None, "обе стратегии на месте")
    if trend and mean:
        check(trend.params["ADX_MIN_LEVEL"] > mean.params["ADX_MIN_LEVEL"],
              "тренду нужен сильный ADX, возврату к среднему — слабый",
              f"{trend.params['ADX_MIN_LEVEL']} vs {mean.params['ADX_MIN_LEVEL']}")
        check(trend.params["RISK_REWARD_RATIO"] > mean.params["RISK_REWARD_RATIO"],
              "тренд тянет прибыль дольше, возврат к среднему забирает быстро",
              f"{trend.params['RISK_REWARD_RATIO']} vs {mean.params['RISK_REWARD_RATIO']}")
        check(trend.params.get("USE_MAX_PROFIT_RIDE") is True
              and mean.params.get("USE_MAX_PROFIT_RIDE") is False,
              "тренд ведёт трейлингом, возврат к среднему — с фиксированной целью")

    breakout = S.by_key("breakout")
    if breakout:
        check(breakout.params.get("USE_MARKET_REGIME_FILTER") is False,
              "пробою не мешает фильтр флэта: пробой рождается именно из флэта")

    descriptions = {S.describe(s) for s in S.STRATEGIES}
    check(len(descriptions) == len(S.STRATEGIES), "описания у всех разные")


def main_run() -> int:
    test_basics()
    test_lookup()
    test_params_are_real()
    test_risk_is_protected()
    test_values_sane()
    test_strategies_differ()
    test_signal_functions()

    print("\n===========================================")
    print(f"Пройдено: {passed}, провалено: {failed}")
    print("===========================================")
    return 1 if failed else 0



# ===========================================================================
# Тесты сигнальных функций: у каждой стратегии своя логика оценки
# ===========================================================================

def _frame(rows):
    """Мини-таблица свечей с индикаторами (как df_ind в торговом цикле)."""
    import pandas as pd
    return pd.DataFrame(rows)


def _trending_up(n=40):
    """Растущий рынок: EMA выстроены вверх, ADX высокий."""
    rows = []
    for i in range(n):
        price = 100 + i * 0.5
        rows.append({"open": price - 0.2, "high": price + 0.3, "low": price - 0.3,
                     "close": price, "ema_fast": price - 0.5, "ema_slow": price - 2.0,
                     "adx": 35.0, "rsi": 62.0, "macd_hist": 0.4,
                     "bb_mid": price - 1.0, "bb_upper": price + 1.0,
                     "bb_lower": price - 3.0, "stoch_k": 70.0, "stoch_d": 65.0})
    return _frame(rows)


def _oversold_range(n=40):
    """Флэт, цена у нижней полосы: родная среда возврата к среднему."""
    rows = []
    for i in range(n):
        price = 100 + (i % 3) * 0.1
        rows.append({"open": price, "high": price + 0.2, "low": price - 0.2,
                     "close": price, "ema_fast": price, "ema_slow": price,
                     "adx": 14.0, "rsi": 22.0, "macd_hist": -0.01,
                     "bb_mid": price + 2.0, "bb_upper": price + 4.0,
                     "bb_lower": price - 0.2, "stoch_k": 12.0, "stoch_d": 15.0})
    return _frame(rows)


def _breakout_up(n=40):
    """Долгий узкий диапазон и резкий выход вверх сильной свечой."""
    rows = []
    for i in range(n - 1):
        rows.append({"open": 100, "high": 100.4, "low": 99.6, "close": 100,
                     "ema_fast": 100, "ema_slow": 100, "adx": 15.0, "rsi": 50.0,
                     "macd_hist": 0.0, "bb_mid": 100, "bb_upper": 100.5,
                     "bb_lower": 99.5, "stoch_k": 50.0, "stoch_d": 50.0})
    rows.append({"open": 100.1, "high": 102.6, "low": 100.0, "close": 102.5,
                 "ema_fast": 100.8, "ema_slow": 100.2, "adx": 22.0, "rsi": 68.0,
                 "macd_hist": 0.5, "bb_mid": 100.2, "bb_upper": 100.8,
                 "bb_lower": 99.6, "stoch_k": 85.0, "stoch_d": 70.0})
    return _frame(rows)


def test_signal_functions():
    print("\n=== 7. Сигнальные функции реагируют на «свой» рынок ===")
    up, flat, brk = _trending_up(), _oversold_range(), _breakout_up()

    # По тренду
    t_buy = S.calc_strategy_score("trend_follow", 1, up, 1.0)
    t_sell = S.calc_strategy_score("trend_follow", -1, up, 1.0)
    check(t_buy > 0, "по тренду: растущий рынок даёт баллы на покупку", str(t_buy))
    check(t_sell == 0, "по тренду: против тренда баллов НЕТ", str(t_sell))
    check(t_buy <= S.SCORE_MAX, "оценка не выходит за предел", str(t_buy))

    # Возврат к среднему
    m_buy = S.calc_strategy_score("mean_reversion", 1, flat, 1.0)
    m_sell = S.calc_strategy_score("mean_reversion", -1, flat, 1.0)
    check(m_buy > 0, "возврат к среднему: перепроданность даёт покупку", str(m_buy))
    check(m_sell == 0, "возврат к среднему: продавать на дне не предлагает", str(m_sell))

    m_in_trend = S.calc_strategy_score("mean_reversion", 1, up, 1.0)
    check(m_in_trend < m_buy,
          "возврат к среднему: в сильном тренде оценка ГАСИТСЯ (главная защита)",
          f"{m_in_trend} против {m_buy}")

    # Пробой
    b_buy = S.calc_strategy_score("breakout", 1, brk, 1.0)
    b_flat = S.calc_strategy_score("breakout", 1, flat, 1.0)
    check(b_buy > 0, "пробой: выход из диапазона даёт баллы", str(b_buy))
    check(b_flat == 0, "пробой: внутри диапазона баллов нет", str(b_flat))

    # Осторожный скальп — требует ВСЕ условия
    c_ok = S.calc_strategy_score("careful_scalp", 1, up, 1.0)
    check(c_ok > 0, "осторожный: все условия совпали — есть баллы", str(c_ok))
    broken = up.copy()
    broken.loc[broken.index[-1], "macd_hist"] = -1.0   # ломаем одно условие
    c_broken = S.calc_strategy_score("careful_scalp", 1, broken, 1.0)
    check(c_broken == 0, "осторожный: одно условие не выполнено — ноль", str(c_broken))

    # Универсальная не вмешивается
    check(S.calc_strategy_score("balanced_hybrid", 1, up, 1.0) == 0,
          "универсальная: своей оценки не добавляет")

    print("\n=== 8. Устойчивость и границы ===")
    check(S.calc_strategy_score("нет-такой", 1, up, 1.0) == 0,
          "неизвестная стратегия -> 0, без ошибки")
    check(S.calc_strategy_score("trend_follow", 1, _frame([]), 1.0) == 0,
          "пустые данные -> 0, без ошибки")
    check(S.calc_strategy_score("trend_follow", 1, up, 0) == 0,
          "нулевой ATR -> 0, без деления на ноль")

    for key in S.SIGNAL_FUNCTIONS:
        for df in (up, flat, brk):
            for d in (1, -1):
                v = S.calc_strategy_score(key, d, df, 1.0)
                check(0 <= v <= S.SCORE_MAX, f"{key}: оценка в границах 0..25", str(v))

    print("\n=== 9. Стратегия только ДОБАВЛЯЕТ баллы ===")
    check(S.apply_strategy_score(50.0, 0, 12) == 50.0, "нулевой вклад ничего не меняет")
    check(S.apply_strategy_score(50.0, 25, 12) == 62.0, "полный вклад добавляет вес")
    check(S.apply_strategy_score(50.0, 25, 0) == 50.0, "нулевой вес ничего не меняет")
    check(S.apply_strategy_score(95.0, 25, 30) <= 100.0, "score не превышает 100")
    check(S.apply_strategy_score(10.0, 25, 12) > 10.0, "оценка никогда не уменьшается")


if __name__ == "__main__":
    sys.exit(main_run())
