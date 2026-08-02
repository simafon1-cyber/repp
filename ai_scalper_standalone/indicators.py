"""
indicators.py — EMA/ATR/ADX/RSI и price-action на pandas.

Важное отличие от MQL5-версии: там индикаторы считает сам терминал (iMA/iATR/
iADX/iRSI — стандартные буферы MT5). Python-библиотека MetaTrader5 отдаёт
только СЫРЫЕ свечи (copy_rates_from_pos) — буферов индикаторов у неё нет,
поэтому здесь EMA/ATR/RSI/ADX считаются вручную (Wilder-сглаживание для
ATR/RSI/ADX, как в MT5). Значения будут ОЧЕНЬ близки к тому, что показывает
MT5, но не гарантированно бит-в-бит идентичны — для торговой логики (score,
фильтры, авто-адаптация) это не критично.
"""

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    return wilder_smooth(true_range(df), period)


def rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = wilder_smooth(gain, period)
    avg_loss = wilder_smooth(loss, period)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    return result.fillna(50.0)  # нет данных / нет движения -> нейтральные 50, как MT5 в начале истории


def adx(df: pd.DataFrame, period: int) -> pd.Series:
    high, low = df["high"], df["low"]
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = true_range(df)
    atr_s = wilder_smooth(tr, period).replace(0, np.nan)

    plus_di = 100 * wilder_smooth(pd.Series(plus_dm, index=df.index), period) / atr_s
    minus_di = 100 * wilder_smooth(pd.Series(minus_dm, index=df.index), period) / atr_s

    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    return wilder_smooth(dx.fillna(0.0), period)


def macd_lines(series: pd.Series, fast: int, slow: int, signal: int):
    """MACD: линия (EMA быстрая - EMA медленная), сигнальная линия (EMA линии),
    гистограмма (линия - сигнальная). Стандартные 12/26/9, как в MT5/большинстве
    платформ."""
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger_bands(series: pd.Series, period: int, std_mult: float):
    """Полосы Боллинджера: средняя (SMA) ± std_mult стандартных отклонений."""
    mid = series.rolling(period).mean()
    std = series.rolling(period).std(ddof=0)
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return mid, upper, lower


def stochastic(df: pd.DataFrame, k_period: int, d_period: int):
    """Стохастик: %K = позиция close в диапазоне (high_max-low_min) за
    k_period баров, %D = скользящее среднее %K за d_period баров."""
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    rng = (high_max - low_min).replace(0, np.nan)
    k = 100 * (df["close"] - low_min) / rng
    k = k.fillna(50.0)
    d = k.rolling(d_period).mean().fillna(50.0)
    return k, d


def efficiency_ratio(close: pd.Series, period: int) -> float:
    """Kaufman Efficiency Ratio на последних `period` барах (без текущего формирующегося)."""
    if len(close) < period + 1:
        return 0.0
    window = close.iloc[-(period + 1):]
    net_change = abs(window.iloc[-1] - window.iloc[0])
    sum_changes = window.diff().abs().sum()
    if sum_changes <= 0:
        return 0.0
    return float(net_change / sum_changes)


def add_all_indicators(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """Добавляет колонки ema_fast/ema_slow/atr/adx/rsi (+ MACD/Bollinger/
    Stochastic для multi_indicator.py) в df (не мутирует исходный)."""
    out = df.copy()
    out["ema_fast"] = ema(out["close"], cfg.EMA_FAST_PERIOD)
    out["ema_slow"] = ema(out["close"], cfg.EMA_SLOW_PERIOD)
    out["atr"] = atr(out, cfg.ATR_PERIOD)
    out["adx"] = adx(out, cfg.ADX_PERIOD)
    out["rsi"] = rsi(out["close"], cfg.RSI_PERIOD)

    # По просьбе пользователя "используй как можно больше индикаторов и
    # стратегий" (см. multi_indicator.py) — считаются всегда (дёшево на pandas),
    # используются только если USE_MULTI_INDICATOR=True.
    out["macd_line"], out["macd_signal"], out["macd_hist"] = macd_lines(
        out["close"], cfg.MACD_FAST_PERIOD, cfg.MACD_SLOW_PERIOD, cfg.MACD_SIGNAL_PERIOD
    )
    out["bb_mid"], out["bb_upper"], out["bb_lower"] = bollinger_bands(
        out["close"], cfg.BB_PERIOD, cfg.BB_STD_MULT
    )
    out["stoch_k"], out["stoch_d"] = stochastic(out, cfg.STOCH_K_PERIOD, cfg.STOCH_D_PERIOD)
    return out


def is_bullish_confirmation(bar, cfg) -> bool:
    o, c, h, l = bar["open"], bar["close"], bar["high"], bar["low"]
    rng = h - l
    if rng <= 0 or c <= o:
        return False
    body = c - o
    upper_wick = h - c
    if body / rng * 100.0 < cfg.BODY_PERCENT_MIN:
        return False
    if upper_wick / rng * 100.0 > cfg.MAX_WICK_PERCENT:
        return False
    return True


def is_bearish_confirmation(bar, cfg) -> bool:
    o, c, h, l = bar["open"], bar["close"], bar["high"], bar["low"]
    rng = h - l
    if rng <= 0 or c >= o:
        return False
    body = o - c
    lower_wick = c - l
    if body / rng * 100.0 < cfg.BODY_PERCENT_MIN:
        return False
    if lower_wick / rng * 100.0 > cfg.MAX_WICK_PERCENT:
        return False
    return True


def ema_stack_ok(sig_bar, direction: int) -> bool:
    f, s = sig_bar["ema_fast"], sig_bar["ema_slow"]
    return (f > s) if direction == 1 else (f < s)


def pullback_breakout_ok(df: pd.DataFrame, direction: int, point: float, tol_points: float) -> bool:
    """
    Шаг 1: предыдущая свеча (pullback) коснулась EMA20.
    Шаг 2: сигнальная свеча ЗАКРЫЛАСЬ по нужную сторону EMA20.
    Шаг 3: сигнальная свеча обновила экстремум предыдущей свечи.
    df индексирован от старых к новым, последняя строка = последняя ЗАКРЫТАЯ свеча (signal),
    предпоследняя = pullback.
    """
    if len(df) < 3:
        return False
    sig = df.iloc[-1]
    pb = df.iloc[-2]
    tol = tol_points * point

    if direction == 1:
        touched_ema = pb["low"] <= pb["ema_fast"] + tol
        closed_above = sig["close"] > sig["ema_fast"]
        broke_high = sig["high"] > pb["high"]
        return bool(touched_ema and closed_above and broke_high)
    else:
        touched_ema = pb["high"] >= pb["ema_fast"] - tol
        closed_below = sig["close"] < sig["ema_fast"]
        broke_low = sig["low"] < pb["low"]
        return bool(touched_ema and closed_below and broke_low)
