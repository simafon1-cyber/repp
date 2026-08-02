"""
custom_strategy.py — "Proprietary Edge": СОБСТВЕННАЯ стратегия/сигнал этой
Python-программы, отдельная от портированного из MQL5-советника pullback+
breakout паттерна (signal_engine.py). По просьбе пользователя ("добавить в
программу, не в советник, собственную стратегию, разработанную и
обновляемую") — живёт ТОЛЬКО здесь, в standalone-боте, и не портируется в
AI_Scalper_Pro.mq5.

Смысл: не заменяет существующий score, а даёт ВТОРОЕ, независимое мнение по
4 факторам, которых в signal_engine.py ещё нет — импульс, ускорение импульса,
направленная согласованность последних баров и расширение диапазона свечей.
Мнение подмешивается в общий score с ограниченным весом (как AI-сигнал, см.
CUSTOM_STRATEGY_WEIGHT в config.py) — не жёсткая команда, а мягкая добавка.

ОБНОВЛЯЕМОСТЬ: код и веса намеренно вынесены в понятные именованные константы
и версионируются здесь же (CUSTOM_STRATEGY_VERSION + CHANGELOG) — чтобы менять
логику/веса со временем было легко, не переписывая остальной проект. Просто
правь константы/формулы ниже и добавляй запись в CHANGELOG.

CHANGELOG:
  v1.0 (первая версия) — 4 фактора по 25 баллов: Momentum Thrust, Momentum
  Acceleration, Directional Consistency, Range Expansion.
"""

import pandas as pd

import config as cfg

CUSTOM_STRATEGY_VERSION = "1.0"

# ---- Настраиваемые параметры (меняй здесь при "обновлении" стратегии) -----
MOMENTUM_LOOKBACK = 5        # баров назад для Momentum Thrust
CONSISTENCY_LOOKBACK = 8     # баров для Directional Consistency
RANGE_LOOKBACK = 10          # баров для среднего диапазона (Range Expansion)

# Масштаб перевода "движение / ATR" в баллы (0..25) — подобраны эмпирически,
# как отправная точка; при обновлении стратегии их можно свободно менять.
MOMENTUM_SCALE = 12.0
ACCELERATION_SCALE = 20.0
RANGE_EXPANSION_SCALE = 15.0


def _clamp(value: float, lo: float = 0.0, hi: float = 25.0) -> float:
    return max(lo, min(hi, value))


def _momentum_thrust(df: pd.DataFrame, direction: int, atr_value: float) -> float:
    """0..25: сила недавнего направленного движения, нормированная по ATR —
    чем сильнее цена уже прошла в сторону сделки за MOMENTUM_LOOKBACK баров,
    тем больше баллов (ловим "свежий" импульс, а не гадаем на пустом месте)."""
    n = MOMENTUM_LOOKBACK
    if len(df) < n + 1 or atr_value <= 0:
        return 0.0
    closes = df["close"]
    roc = (closes.iloc[-1] - closes.iloc[-1 - n]) / atr_value
    aligned = roc if direction == 1 else -roc
    return _clamp(aligned * MOMENTUM_SCALE)


def _momentum_acceleration(df: pd.DataFrame, direction: int, atr_value: float) -> float:
    """0..25: РАЗГОНЯЕТСЯ ли движение (вторая производная цены), а не просто
    существует — отличает начало движения (баллы растут) от уже уставшего,
    затухающего хода (баллы падают к нулю), даже если сам импульс ещё виден
    в _momentum_thrust выше."""
    if len(df) < 3 or atr_value <= 0:
        return 0.0
    closes = df["close"]
    roc_now = closes.iloc[-1] - closes.iloc[-2]
    roc_prev = closes.iloc[-2] - closes.iloc[-3]
    accel = (roc_now - roc_prev) / atr_value
    aligned = accel if direction == 1 else -accel
    return _clamp(aligned * ACCELERATION_SCALE)


def _directional_consistency(df: pd.DataFrame, direction: int) -> float:
    """0..25: какая доля последних CONSISTENCY_LOOKBACK баров закрылась в
    сторону сделки — высокая согласованность движения снижает шанс, что это
    случайный/шумовой всплеск на одной свече."""
    n = CONSISTENCY_LOOKBACK
    if len(df) < n + 1:
        return 0.0
    closes = df["close"].iloc[-(n + 1):]
    diffs = closes.diff().dropna()
    if len(diffs) == 0:
        return 0.0
    aligned_bars = (diffs > 0) if direction == 1 else (diffs < 0)
    fraction = aligned_bars.sum() / len(diffs)
    return _clamp(fraction * 25.0)


def _range_expansion(df: pd.DataFrame) -> float:
    """0..25: расширяется ли диапазон последней свечи относительно недавнего
    среднего — признак свежего интереса/продолжения пробоя, а не вязкого
    "растирания" внутри узкого диапазона."""
    n = RANGE_LOOKBACK
    if len(df) < n + 1:
        return 0.0
    ranges = (df["high"] - df["low"]).iloc[-(n + 1):]
    current = ranges.iloc[-1]
    avg = ranges.iloc[:-1].mean()
    if avg <= 0:
        return 0.0
    ratio = current / avg
    return _clamp((ratio - 1.0) * RANGE_EXPANSION_SCALE + 5.0)


def calc_custom_score(direction: int, df: pd.DataFrame, atr_value: float) -> float:
    """Итоговый 0..100 score этой стратегии для направления direction (1=BUY,
    -1=SELL). df — тот же df_ind (с колонками close/high/low/atr), что уже
    считает главный цикл (main.py) — доп. запросов к MT5 не требуется."""
    if not getattr(cfg, "USE_CUSTOM_STRATEGY", False):
        return 50.0  # нейтрально — apply_custom_strategy() ниже всё равно даст 0 влияния
    total = (
        _momentum_thrust(df, direction, atr_value)
        + _momentum_acceleration(df, direction, atr_value)
        + _directional_consistency(df, direction)
        + _range_expansion(df)
    )
    return round(max(0.0, min(100.0, total)), 1)


def apply_custom_strategy(score: float, custom_score: float, weight: float = None) -> float:
    """Подмешивает custom_score (0..100) в основной score с ограниченным
    весом: custom_score=50 (нейтрально) -> без изменений; 100 -> +weight;
    0 -> -weight. Как apply_ai_signal() в ai_signal.py — мягкая добавка,
    а не самостоятельная команда."""
    if not getattr(cfg, "USE_CUSTOM_STRATEGY", False):
        return score
    w = weight if weight is not None else getattr(cfg, "CUSTOM_STRATEGY_WEIGHT", 15)
    delta = (custom_score / 100.0 - 0.5) * 2.0 * w
    return max(0.0, min(100.0, score + delta))
