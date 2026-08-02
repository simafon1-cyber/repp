"""
auto_learning.py — простое, прозрачное "самообучение" по факту закрытых сделок.

Никакой магии/ML тут нет специально: подход должен быть понятным и предсказуемым
(см. правило проекта — "максимально понятный, но интеллектуальный"). Логика:

  1) Берём последние AUTO_LEARNING_WINDOW сделок ПО КОНКРЕТНОМУ символу
     (SymbolState.recent_results — True/False на каждую закрытую сделку).
  2) Пока сделок меньше AUTO_LEARNING_MIN_TRADES — подстройки нет вообще,
     используются обычные (базовые) настройки из config.py/RISK_PROFILES.
  3) Если винрейт последних сделок высокий — бот НЕМНОГО больше доверяет
     AI-сигналу и НЕМНОГО охотнее входит в сделки (порог score чуть ниже).
  4) Если винрейт низкий — наоборот: меньше веса AI, порог входа строже.

Всё в рамках жёстких границ (AI_WEIGHT_MULT_MIN/MAX, SCORE_THRESHOLD_ADJUST_MIN/MAX)
— бот не может "разучиться" полностью или стать бесконтрольно агрессивным.
"""

import config as cfg


def recent_win_rate(sym_state) -> float:
    """0.5 (нейтрально) пока данных мало, иначе доля прибыльных сделок в окне."""
    results = sym_state.recent_results[-cfg.AUTO_LEARNING_WINDOW:]
    if len(results) < cfg.AUTO_LEARNING_MIN_TRADES:
        return 0.5
    return sum(1 for r in results if r) / len(results)


def has_enough_history(sym_state) -> bool:
    return len(sym_state.recent_results) >= cfg.AUTO_LEARNING_MIN_TRADES


def adaptive_ai_weight_multiplier(sym_state) -> float:
    """0% винрейт -> AI_WEIGHT_MULT_MIN, 50% -> 1.0, 100% -> AI_WEIGHT_MULT_MAX."""
    if not cfg.USE_AUTO_LEARNING:
        return 1.0
    wr = recent_win_rate(sym_state)
    if wr >= 0.5:
        # линейно от 1.0 (при 50%) до MAX (при 100%)
        mult = 1.0 + (wr - 0.5) * 2 * (cfg.AI_WEIGHT_MULT_MAX - 1.0)
    else:
        # линейно от MIN (при 0%) до 1.0 (при 50%)
        mult = cfg.AI_WEIGHT_MULT_MIN + wr * 2 * (1.0 - cfg.AI_WEIGHT_MULT_MIN)
    return max(cfg.AI_WEIGHT_MULT_MIN, min(cfg.AI_WEIGHT_MULT_MAX, mult))


def adaptive_score_threshold(base_threshold: float, sym_state) -> float:
    """Винрейт < 50% -> порог строже (выше), винрейт > 50% -> порог мягче (ниже)."""
    if not cfg.USE_AUTO_LEARNING or not has_enough_history(sym_state):
        return base_threshold
    wr = recent_win_rate(sym_state)
    delta = (0.5 - wr) * 2 * cfg.SCORE_THRESHOLD_ADJUST_MAX if wr < 0.5 else \
            (0.5 - wr) * 2 * abs(cfg.SCORE_THRESHOLD_ADJUST_MIN)
    adjusted = base_threshold + delta
    lo = base_threshold + cfg.SCORE_THRESHOLD_ADJUST_MIN
    hi = base_threshold + cfg.SCORE_THRESHOLD_ADJUST_MAX
    return max(lo, min(hi, adjusted))


def record_trade_result(sym_state, profit: float):
    """Вызывается из main.py при обработке закрытой сделки — копит окно результатов."""
    sym_state.recent_results.append(profit >= 0)
    if len(sym_state.recent_results) > cfg.AUTO_LEARNING_WINDOW:
        sym_state.recent_results.pop(0)


def learning_status_text(sym_state) -> str:
    if not cfg.USE_AUTO_LEARNING:
        return "выкл"
    if not has_enough_history(sym_state):
        return f"копит данные ({len(sym_state.recent_results)}/{cfg.AUTO_LEARNING_MIN_TRADES})"
    wr = recent_win_rate(sym_state)
    return f"винрейт {wr * 100:.0f}% (посл. {len(sym_state.recent_results)})"
