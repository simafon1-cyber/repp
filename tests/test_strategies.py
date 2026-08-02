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
        check(len(s.params) >= 5, f"«{s.title}»: меняет достаточно параметров "
                                  f"({len(s.params)})")


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

    print("\n===========================================")
    print(f"Пройдено: {passed}, провалено: {failed}")
    print("===========================================")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main_run())
