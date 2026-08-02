"""
market_regime.py — определение режима рынка: ТРЕНД или ФЛЭТ.

1:1 портировано из MarketRegime.mqh. Три независимых голоса (ADX, Kaufman
Efficiency Ratio, отношение ATR к среднему) — режим определяется большинством,
смена подтверждается несколько баров подряд (защита от дребезга).
"""

import config as cfg
from indicators import efficiency_ratio
from state import SymbolState


def detect_raw_regime(df) -> str:
    trend_votes = 0
    range_votes = 0

    adx_val = df["adx"].iloc[-1]
    if adx_val >= cfg.REGIME_ADX_TREND_LEVEL:
        trend_votes += 1
    elif adx_val < cfg.REGIME_ADX_RANGE_LEVEL:
        range_votes += 1

    er = efficiency_ratio(df["close"], cfg.REGIME_ER_PERIOD)
    if er >= cfg.REGIME_ER_TREND_LEVEL:
        trend_votes += 1
    elif er < cfg.REGIME_ER_RANGE_LEVEL:
        range_votes += 1

    atr_window = df["atr"].iloc[-(cfg.ATR_AVG_PERIOD + 1):]
    if len(atr_window) >= 2:
        avg = atr_window.iloc[:-1].mean()
        if avg > 0:
            ratio = atr_window.iloc[-1] / avg
            if ratio >= 1.2:
                trend_votes += 1
            elif ratio < 0.85:
                range_votes += 1

    if trend_votes > range_votes:
        return "trend"
    if range_votes > trend_votes:
        return "range"
    return "unknown"


def update_market_regime(sym_state: SymbolState, df):
    if not cfg.USE_MARKET_REGIME_FILTER:
        sym_state.current_regime = "unknown"
        return

    raw = detect_raw_regime(df)

    if raw == sym_state.regime_candidate:
        sym_state.regime_candidate_streak += 1
    else:
        sym_state.regime_candidate = raw
        sym_state.regime_candidate_streak = 1

    if raw != "unknown" and sym_state.regime_candidate_streak >= cfg.REGIME_CONFIRM_BARS:
        sym_state.current_regime = raw


def regime_score_adjustment(sym_state: SymbolState) -> float:
    if not cfg.USE_MARKET_REGIME_FILTER:
        return 0.0
    if sym_state.current_regime == "range":
        return -cfg.REGIME_RANGE_PENALTY
    if sym_state.current_regime == "trend":
        return cfg.REGIME_TREND_BONUS
    return 0.0


def regime_text(sym_state: SymbolState) -> str:
    if not cfg.USE_MARKET_REGIME_FILTER:
        return "выкл"
    if sym_state.current_regime == "trend":
        return f"ТРЕНД (+{cfg.REGIME_TREND_BONUS:.0f} score)"
    if sym_state.current_regime == "range":
        return f"ФЛЭТ (-{cfg.REGIME_RANGE_PENALTY:.0f} score)"
    return "определяется..."
