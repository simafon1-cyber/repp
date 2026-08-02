"""
market_context.py — сверка сигнала с трендом коррелирующих инструментов
(например, DXY для золота). 1:1 портировано из MarketContext.mqh.
"""

import config as cfg
import mt5_connector as mt5c
from indicators import ema


def get_context_trend(symbol: str) -> int:
    df = mt5c.get_rates_df(symbol, cfg.TREND_TIMEFRAME, count=cfg.CONTEXT_EMA_PERIOD + 5)
    if df is None or len(df) < cfg.CONTEXT_EMA_PERIOD:
        return 0
    e = ema(df["close"], cfg.CONTEXT_EMA_PERIOD).iloc[-1]
    c = df["close"].iloc[-1]
    if c > e:
        return 1
    if c < e:
        return -1
    return 0


def context_slot_adjustment(context_symbol: str, corr: str, direction: int) -> float:
    trend = get_context_trend(context_symbol)
    if trend == 0:
        return 0.0
    expected = direction if corr == "positive" else -direction
    if trend == expected:
        return cfg.CONTEXT_SCORE_WEIGHT
    return -cfg.CONTEXT_SCORE_WEIGHT * 0.5


def market_context_score_adjustment(symbol: str, direction: int) -> float:
    if not cfg.USE_MARKET_CONTEXT:
        return 0.0
    slots = cfg.MARKET_CONTEXT.get(symbol, [])
    adj = 0.0
    for context_symbol, corr in slots:
        adj += context_slot_adjustment(context_symbol, corr, direction)
    return adj


def market_context_text(symbol: str) -> str:
    if not cfg.USE_MARKET_CONTEXT:
        return "выкл"
    slots = cfg.MARKET_CONTEXT.get(symbol, [])
    if not slots:
        return "нет настроенных символов"
    parts = []
    for context_symbol, _ in slots:
        trend = get_context_trend(context_symbol)
        t = "ВВЕРХ" if trend == 1 else ("ВНИЗ" if trend == -1 else "?")
        parts.append(f"{context_symbol}:{t}")
    return "  ".join(parts)
