"""
signal_engine.py — единый score 0..100, сводит тренд/паттерн/price action/
волатильность/новости/режим рынка/контекст в одно число. 1:1 портировано из
SignalEngine.mqh (веса: Тренд20 Откат20 PriceAction15 ATR10 ADX10 Спред5
Объём10 Время5 RSI5 = 100 — совпадает с MQL5-версией).
"""

import pandas as pd

import config as cfg
import mt5_connector as mt5c
import news_calendar
import telegram_signals
from indicators import (
    pullback_breakout_ok, ema_stack_ok, is_bullish_confirmation, is_bearish_confirmation,
)
from market_regime import regime_score_adjustment
from market_context import market_context_score_adjustment
from state import SymbolState


def _adaptive_multiplier(sym_state: SymbolState, kind: str) -> float:
    """"Умнее" score (см. USE_ADAPTIVE_SCORE_WEIGHTS в config.py): вместо
    фиксированных весов компонентов — подстраивает их под уже посчитанный
    режим рынка (sym_state.current_regime, см. market_regime.py). В
    подтверждённом ТРЕНДЕ структурные сигналы (тренд со старшего ТФ + сам
    паттерн pullback+breakout) надёжнее — усиливаем их. Во ФЛЭТЕ, наоборот,
    структурные пробои чаще ложные, а мин-реверсия (запас хода по RSI)
    работает лучше — усиливаем её. Выключается (возвращает 1.0 = без
    изменений) флагом USE_ADAPTIVE_SCORE_WEIGHTS=False — тогда веса как в
    MQL5-версии (фиксированные Тренд20/Откат20/.../RSI5)."""
    if not getattr(cfg, "USE_ADAPTIVE_SCORE_WEIGHTS", False):
        return 1.0
    regime = getattr(sym_state, "current_regime", "unknown")
    if kind == "trend_structure":
        if regime == "trend":
            return 1.15
        if regime == "range":
            return 0.75
        return 1.0
    if kind == "mean_reversion":
        if regime == "range":
            return 1.6
        if regime == "trend":
            return 0.7
        return 1.0
    return 1.0


def trend_direction_mtf(trend_df) -> int:
    if trend_df is None or len(trend_df) == 0:
        return 0
    ema_trend = trend_df["close"].ewm(span=cfg.EMA_TREND_PERIOD, adjust=False).mean().iloc[-1]
    c = trend_df["close"].iloc[-1]
    if c > ema_trend:
        return 1
    if c < ema_trend:
        return -1
    return 0


def _candle_overextended(df: pd.DataFrame, cfg) -> bool:
    """Анти-'зеркало' фильтр #3 (см. USE_EXHAUSTION_FILTER): если диапазон
    сигнальной свечи намного больше среднего ATR — движение, скорее всего, уже
    во многом прошло/истощилось, и именно такие "растянутые" свечи чаще всего
    разворачиваются сразу после входа (это и жаловался пользователь как "бот
    открывает в зеркало"). Не блокирует умеренно широкие свечи — только явный
    выброс диапазона."""
    if not getattr(cfg, "USE_EXHAUSTION_FILTER", False):
        return False
    n = getattr(cfg, "ATR_AVG_PERIOD", 20)
    window = df["atr"].iloc[-(n + 1):]
    if len(window) < 2:
        return False
    avg_atr = window.iloc[:-1].mean()
    if avg_atr <= 0:
        return False
    sig = df.iloc[-1]
    candle_range = sig["high"] - sig["low"]
    ratio_limit = getattr(cfg, "EXHAUSTION_RANGE_ATR_RATIO", 2.0)
    return (candle_range / avg_atr) > ratio_limit


def calc_signal_score(symbol: str, direction: int, df, trend_df, point: float,
                       sym_state: SymbolState) -> float:
    sig = df.iloc[-1]

    # Анти-"зеркало" фильтр #1 (по факту жалобы пользователя на входы прямо
    # перед разворотом): Price Action подтверждение теперь ЖЁСТКОЕ условие
    # входа, а не просто бонус +15 к score, как было раньше — без сильного
    # тела и маленькой противоположной тени на сигнальной свече сделки в эту
    # сторону просто не будет, независимо от остальных компонентов score.
    pa_ok = is_bullish_confirmation(sig, cfg) if direction == 1 else is_bearish_confirmation(sig, cfg)
    if not pa_ok and getattr(cfg, "USE_PA_HARD_GATE", True):
        return 0.0

    # Анти-"зеркало" фильтр #3: свеча уже слишком растянута относительно ATR —
    # высокий риск, что импульс исчерпан и вот-вот развернётся.
    if _candle_overextended(df, cfg):
        return 0.0

    score = 0.0

    # Trend со старшего TF (20, адаптируется под режим рынка — см. USE_ADAPTIVE_SCORE_WEIGHTS)
    trend_mult = _adaptive_multiplier(sym_state, "trend_structure")
    if ema_stack_ok(sig, direction):
        mtf = trend_direction_mtf(trend_df)
        if (direction == 1 and mtf == 1) or (direction == -1 and mtf == -1):
            score += 20 * trend_mult
        elif mtf == 0:
            score += 10 * trend_mult

    # Pullback + пробой (20, тот же адаптивный множитель — тоже "структурный" сигнал)
    tol_points = cfg.PULLBACK_TOLERANCE_POINTS
    import risk_manager as rm
    atr_value = sig["atr"]
    tol_points = rm.eff_points_threshold(cfg.PULLBACK_TOLERANCE_POINTS, 0.10, atr_value, point)
    if pullback_breakout_ok(df, direction, point, tol_points):
        score += 20 * trend_mult

    # Price Action (15) — если USE_PA_HARD_GATE=True (по умолчанию), сюда
    # попадают только уже подтверждённые свечи (см. гейт выше), поэтому бонус
    # начисляется всегда; если гейт выключен вручную — начисляется как раньше,
    # только когда pa_ok=True.
    if pa_ok:
        score += 15

    # ATR относительно среднего (10)
    atr_window = df["atr"].iloc[-(cfg.ATR_AVG_PERIOD + 1):]
    if len(atr_window) >= 2:
        avg = atr_window.iloc[:-1].mean()
        if avg > 0:
            score += min(10, (atr_window.iloc[-1] / avg) * 10 * 0.7)

    # ADX (10)
    adx_val = sig["adx"]
    score += min(10, (adx_val / max(cfg.ADX_MIN_LEVEL, 1)) * 10 * 0.6)

    # Spread (5)
    spread = mt5c.get_spread_points(symbol)
    score += max(0, min(5, (1.0 - spread / max(cfg.MAX_SPREAD_POINTS, 1)) * 5))

    # Volume (10) — мягкий, как в MQL5: если фильтр выключен, баллы не теряются
    if cfg.USE_VOLUME_FILTER and "tick_volume" in df.columns:
        vol_window = df["tick_volume"].iloc[-(cfg.VOLUME_AVG_PERIOD + 1):]
        if len(vol_window) >= 2:
            avg_vol = vol_window.iloc[:-1].mean()
            if avg_vol > 0:
                score += min(10, (vol_window.iloc[-1] / avg_vol) * 10 * 0.7)
    else:
        score += 10

    # Time (5) — жёсткого тайм-фильтра в standalone-версии нет (торгуем круглосуточно), баллы даются всегда
    score += 5

    # RSI — запас хода (5, адаптируется под режим рынка — усиливается во флэте)
    rsi_val = sig["rsi"]
    room = (cfg.RSI_OVERBOUGHT - rsi_val) if direction == 1 else (rsi_val - cfg.RSI_OVERSOLD)
    score += max(0, min(5, room / 10.0 * 5)) * _adaptive_multiplier(sym_state, "mean_reversion")

    # Мягкий штраф за новость рядом (не блокирует, просто немного снижает score)
    hard_block_window = getattr(cfg, "NEWS_HARD_BLOCK_WINDOW_MIN", cfg.NEWS_BREAKOUT_WINDOW_MIN)
    penalty_points = getattr(cfg, "NEWS_SOFT_PENALTY_POINTS", 8)
    score -= news_calendar.soft_news_penalty(symbol, hard_block_window, penalty_points)

    # Адаптация под режим рынка
    score += regime_score_adjustment(sym_state)

    # Контекст рынка (коррелирующие инструменты)
    score += market_context_score_adjustment(symbol, direction)

    # Сигнал из Telegram — ТОЛЬКО надбавка за совпадение, с жёстким потолком
    # (см. telegram_signals.py). Чужой сигнал не может открыть сделку, поднять
    # лот или отодвинуть стоп; направление здесь уже выбрано программой, и
    # надбавка лишь усиливает то, что она и так собиралась сделать.
    # При TELEGRAM_ROLE = "show" (по умолчанию) надбавка всегда 0.
    score += telegram_signals.score_bonus(symbol, direction)

    return round(max(0, min(100, score)), 1)
