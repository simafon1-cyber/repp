"""
multi_indicator.py — дополнительное подтверждение сигнала тремя классическими
индикаторами (MACD, Bollinger Bands, Stochastic), по просьбе пользователя
"используй как можно больше индикаторов и торговых стратегий".

Как custom_strategy.py и ai_signal.py — НЕ заменяет основной score
(signal_engine.py), а даёт ЕЩЁ ОДНО независимое мнение, подмешивается с
ограниченным весом (MULTI_INDICATOR_WEIGHT в config.py) — мягкая добавка,
а не самостоятельная команда на сделку.

ОБНОВЛЯЕМОСТЬ: версия и changelog здесь же, как в custom_strategy.py.

CHANGELOG:
  v1.0 (первая версия) — 3 индикатора: MACD (тренд/импульс, 0..40),
  позиция в канале Bollinger / %B (0..30), Stochastic %K/%D разворот (0..30).
"""

import config as cfg

MULTI_INDICATOR_VERSION = "1.0"


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _macd_factor(sig, direction: int) -> float:
    """0..40: согласован ли MACD с направлением сделки — линия MACD выше/ниже
    сигнальной линии (тренд/импульс в нужную сторону, +25) И гистограмма
    того же знака, что и направление (импульс усиливается, а не затухает, +15)."""
    macd_line = sig["macd_line"]
    macd_signal = sig["macd_signal"]
    hist = sig["macd_hist"]
    aligned_cross = (macd_line > macd_signal) if direction == 1 else (macd_line < macd_signal)
    aligned_hist = (hist > 0) if direction == 1 else (hist < 0)
    score = 0.0
    if aligned_cross:
        score += 25.0
    if aligned_hist:
        score += 15.0
    return score


def _bollinger_factor(sig, direction: int) -> float:
    """0..30: позиция цены внутри полос Боллинджера (%B, 0..1). Хотим движение
    В СТОРОНУ сделки, но НЕ у самого края канала — экстремальная растянутость
    канала снова "предвестник разворота" (та же логика, что и в фильтре
    истощения свечи в signal_engine.py)."""
    upper, lower = sig["bb_upper"], sig["bb_lower"]
    width = upper - lower
    if width <= 0:
        return 0.0
    percent_b = (sig["close"] - lower) / width
    if direction == 1:
        if 0.5 <= percent_b <= 0.95:
            return 30.0
        if percent_b > 0.95:
            return 5.0  # уже у верхней полосы — риск разворота, почти не даём баллов
        return 0.0
    else:
        if 0.05 <= percent_b <= 0.5:
            return 30.0
        if percent_b < 0.05:
            return 5.0
        return 0.0


def _stochastic_factor(sig, direction: int) -> float:
    """0..30: классический разворотный сигнал — %K пересекает %D, выходя из
    зоны перепроданности (BUY) или перекупленности (SELL). Ближе к экстремуму
    (<50 для buy / >50 для sell) — балл выше, это "свежий" разворот, а не
    догоняющий вход в середине хода."""
    k, d = sig["stoch_k"], sig["stoch_d"]
    if direction == 1:
        if k > d and k < 80:
            return 30.0 if k < 50 else 15.0
        return 0.0
    else:
        if k < d and k > 20:
            return 30.0 if k > 50 else 15.0
        return 0.0


def calc_multi_indicator_score(direction: int, df) -> float:
    """Итоговый 0..100 score для направления direction (1=BUY, -1=SELL).
    df — тот же df_ind (с колонками macd_*/bb_*/stoch_*), что уже считает
    главный цикл (add_all_indicators в indicators.py) — доп. запросов к MT5
    не требуется."""
    if not getattr(cfg, "USE_MULTI_INDICATOR", False):
        return 50.0  # нейтрально — apply_multi_indicator() всё равно даст 0 влияния
    sig = df.iloc[-1]
    total = (
        _macd_factor(sig, direction)
        + _bollinger_factor(sig, direction)
        + _stochastic_factor(sig, direction)
    )
    return round(_clamp(total), 1)


def apply_multi_indicator(score: float, mi_score: float, weight: float = None) -> float:
    """Подмешивает mi_score (0..100) в основной score с ограниченным весом:
    mi_score=50 (нейтрально) -> без изменений; 100 -> +weight; 0 -> -weight.
    Как apply_custom_strategy()/apply_ai_signal() — мягкая добавка."""
    if not getattr(cfg, "USE_MULTI_INDICATOR", False):
        return score
    w = weight if weight is not None else getattr(cfg, "MULTI_INDICATOR_WEIGHT", 12)
    delta = (mi_score / 100.0 - 0.5) * 2.0 * w
    return max(0.0, min(100.0, score + delta))
