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

import json
import logging
import os

import config as cfg
import safe_files

log = logging.getLogger("auto_learning")


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
    txt = f"винрейт {wr * 100:.0f}% (посл. {len(sym_state.recent_results)})"
    learned = learned_profit_points(sym_state, 0.0)
    if learned > 0:
        txt += f", цель {learned:.0f} пт"
    return txt


# =====================================================================
# ОБУЧЕНИЕ ЦЕЛИ ПРИБЫЛИ (по пикам реальных сделок)
# =====================================================================
# Идея простая и проверяемая: бот запоминает, НА СКОЛЬКО ПУНКТОВ каждая
# закрытая сделка успевала уйти в плюс до разворота (пик прибыли, он же MFE).
# Медиана этих пиков по последним сделкам — это честный ответ на вопрос
# "сколько обычно даёт рынок по этой паре в это время". Тейк-профит ставится
# чуть БЛИЖЕ медианы (TP_LEARN_FRACTION), чтобы успевать выйти до разворота,
# а не смотреть, как прибыль тает.
#
# Почему медиана, а не среднее: одна аномальная сделка на новостях (+800 пт)
# сдвинула бы среднее и заставила бота ждать недостижимой цели во всех
# остальных сделках. Медиана к таким выбросам нечувствительна.

def record_trade_peak(sym_state, peak_points: float):
    """Вызывается из main.py при обработке закрытой сделки — копит окно пиков
    прибыли. Отрицательные пики (сделка вообще не была в плюсе) записываем
    как 0: это тоже информация — рынок не дал ничего."""
    sym_state.recent_peaks.append(max(0.0, float(peak_points)))
    if len(sym_state.recent_peaks) > cfg.AUTO_LEARNING_WINDOW:
        sym_state.recent_peaks.pop(0)


def median(values) -> float:
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return 0.0
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def learned_profit_points(sym_state, fallback: float) -> float:
    """Выученная цель прибыли в пунктах или fallback, если данных ещё мало.

    Ноль в fallback означает "цели нет" — вызывающий код сам решит, что делать
    (см. trade_manager: без выученной цели используется цель от ATR)."""
    if not cfg.USE_AUTO_LEARNING or not getattr(cfg, "USE_TP_LEARNING", False):
        return fallback
    peaks = sym_state.recent_peaks[-cfg.AUTO_LEARNING_WINDOW:]
    if len(peaks) < cfg.AUTO_LEARNING_MIN_TRADES:
        return fallback
    target = median(peaks) * cfg.TP_LEARN_FRACTION
    if target <= 0:
        return fallback
    return max(cfg.TP_LEARN_MIN_POINTS, min(cfg.TP_LEARN_MAX_POINTS, target))


# =====================================================================
# СОХРАНЕНИЕ ВЫУЧЕННОГО МЕЖДУ ЗАПУСКАМИ
# =====================================================================
# Без этого "самообучение" было фикцией для любого, кто выключает программу
# на ночь: окно результатов жило только в памяти процесса и обнулялось при
# каждом старте. То есть бот вечно оставался в фазе "копит данные" и ни разу
# не доходил до AUTO_LEARNING_MIN_TRADES.
#
# Формат — обычный JSON, его можно открыть блокнотом и посмотреть, чему бот
# научился. Запись атомарная (safe_files), чтобы выключение питания посреди
# сохранения не оставило испорченный файл.

def _learning_path() -> str:
    return getattr(cfg, "LEARNING_STATE_PATH", "learning_state.json")


def save_learning_state(sym_states: dict) -> bool:
    """Сохраняет окна обучения всех символов. Возвращает True при успехе."""
    if not getattr(cfg, "USE_LEARNING_PERSISTENCE", True):
        return False
    data = {
        "version": 1,
        "symbols": {
            sym: {
                "results": [bool(r) for r in st.recent_results],
                "peaks": [float(p) for p in st.recent_peaks],
            }
            for sym, st in sym_states.items()
        },
    }
    try:
        # backup=False: файл маленький и полностью восстановимый (в худшем
        # случае бот просто начнёт копить статистику заново) — плодить рядом
        # с ним .bak-копии на каждой закрытой сделке незачем.
        safe_files.atomic_write_text(_learning_path(),
                                     json.dumps(data, ensure_ascii=False, indent=1),
                                     backup=False)
        return True
    except Exception as e:
        log.warning("Не удалось сохранить состояние обучения в %s: %s", _learning_path(), e)
        return False


def load_learning_state(sym_states: dict) -> int:
    """Восстанавливает окна обучения. Возвращает число восстановленных символов.

    Любая проблема с файлом (нет, испорчен, чужой формат) — не ошибка: бот
    просто начинает копить статистику заново, как при первом запуске."""
    if not getattr(cfg, "USE_LEARNING_PERSISTENCE", True):
        return 0
    path = _learning_path()
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        symbols = data.get("symbols", {})
    except Exception as e:
        log.warning("Файл обучения %s не читается (%s) — статистика начнётся заново.", path, e)
        return 0

    restored = 0
    window = cfg.AUTO_LEARNING_WINDOW
    for sym, st in sym_states.items():
        saved = symbols.get(sym)
        if not isinstance(saved, dict):
            continue
        results = saved.get("results", [])
        peaks = saved.get("peaks", [])
        if not isinstance(results, list) or not isinstance(peaks, list):
            continue
        # Обрезаем по ТЕКУЩЕМУ окну: пользователь мог уменьшить
        # AUTO_LEARNING_WINDOW между запусками, и старый длинный хвост
        # тогда сделал бы настройку бессмысленной.
        st.recent_results = [bool(r) for r in results][-window:]
        st.recent_peaks = [max(0.0, float(p)) for p in peaks
                           if isinstance(p, (int, float))][-window:]
        restored += 1
    if restored:
        log.info("Восстановлена статистика обучения по %d символам из %s", restored, path)
    return restored
