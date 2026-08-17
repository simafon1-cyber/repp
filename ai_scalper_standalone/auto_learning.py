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
from datetime import datetime

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
    # То же окно, но в деньгах: по нему инструмент, который стабильно
    # тянет счёт вниз, отключается сам (см. symbol_auto_off_reason).
    sym_state.recent_profits.append(float(profit))
    if len(sym_state.recent_profits) > cfg.AUTO_LEARNING_WINDOW:
        sym_state.recent_profits.pop(0)


def symbol_recent_money(sym_state) -> float:
    """Сколько ЭТОТ инструмент дал за последние сделки, в деньгах счёта."""
    return float(sum(sym_state.recent_profits or []))


# Сколько часов инструмент остаётся отключённым, прежде чем получит новую
# попытку. Сутки — компромисс: меньше означает возврат к тому же убыточному
# поведению почти сразу, больше — потерю инструмента на несколько дней из-за
# одной неудачной серии. Значение можно задать в config.py.
ЧАСОВ_ОТДЫХА = 24


def _release_window(sym_state) -> None:
    """Освободить место в окне последних сделок при снятии отключения.

    БЕЗ ЭТОГО СНЯТИЕ НИЧЕГО НЕ ДАЁТ. Окно пополняется только закрытыми
    сделками. Если просто «разрешить снова», окно останется тем же самым,
    приговор повторится на первой же проверке, и отключение включится
    обратно, не дав совершить ни одной сделки. Получился бы круг.

    Выбрасываем СТАРШУЮ ПОЛОВИНУ: инструмент получает несколько сделок
    испытательного срока и судится заново по свежему поведению. Полностью
    очищать окно нельзя — тогда пропадёт и то, чему автообучение научилось.

    Оба списка режутся вместе: они пополняются одной функцией и обязаны
    оставаться одной длины, иначе винрейт считался бы по одним сделкам, а
    деньги по другим."""
    половина = len(sym_state.recent_profits) // 2
    if половина <= 0:
        return
    sym_state.recent_profits = sym_state.recent_profits[половина:]
    sym_state.recent_results = sym_state.recent_results[половина:]


def symbol_auto_off_reason(sym_state, equity: float, now=None) -> str:
    """Пустая строка — инструментом можно торговать. Иначе причина отказа.

    =====================================================================
    ЗДЕСЬ БЫЛА ЗАЩЁЛКА БЕЗ ВЫХОДА — НАЙДЕНА ПРОВЕРКОЙ НА ИСТОРИИ
    =====================================================================
    Приговор выносится по окну последних сделок. Окно пополняется ТОЛЬКО
    при закрытии сделки. Значит: инструмент отключился -> сделок больше нет
    -> окно не меняется никогда -> отключён навсегда.

    Обещание в этом же тексте было прямо противоположным: «отключение НЕ
    вечное, окно скользящее, старые убытки из него выпадают». Выпасть они
    не могли: выпадают они только когда приходят новые, а новых не будет.

    Насколько это било, стало видно на восьми месяцах истории: 92% баров
    EURUSD и 91% баров XAUUSD отклонялись с причиной «инструмент отключён
    сам». Стратегия почти не торговала — и не потому, что не находила
    входов, а потому что была заперта.

    Теперь у отключения есть СРОК. По его истечении старшая половина окна
    выбрасывается, и инструмент получает несколько сделок испытательного
    срока. Продолжит терять — отключится снова, уже по свежим сделкам.

    ЗАЧЕМ ЭТО ВООБЩЕ. Владелец несколько раз писал «опять минуса», и по его
    настоящему отчёту видно, откуда они: из 148 сделок ОДИН инструмент
    (золото) дал 85% всех потерь — минус 29.56 при депозите 65, то есть
    почти половину счёта, за 37 сделок. Остальные пары в это время были в
    плюсе. Просить человека каждый раз замечать это по отчёту и вручную
    убирать пару из списка — значит перекладывать на него работу, которую
    программа может сделать сама: у неё результат каждой сделки уже есть.

    Отключение НЕ вечное: у него есть срок (см. выше), и по его истечении
    инструмент получает испытательные сделки. Раньше эта фраза была
    неправдой — выхода не существовало.

    Порог считается от РАЗМЕРА СЧЁТА, а не в абсолютных деньгах: минус 30
    для депозита 65 — это катастрофа, для депозита 10 000 — обычный шум."""
    if not getattr(cfg, "USE_SYMBOL_AUTO_OFF", False):
        sym_state.auto_off_since = None
        return ""
    min_trades = int(getattr(cfg, "SYMBOL_AUTO_OFF_MIN_TRADES", 12) or 0)
    limit_percent = float(getattr(cfg, "SYMBOL_AUTO_OFF_LOSS_PERCENT", 0) or 0)
    money = symbol_recent_money(sym_state)
    limit_money = equity * limit_percent / 100.0 if equity > 0 else 0.0

    надо_выключить = (
        min_trades > 0
        and len(sym_state.recent_profits) >= min_trades
        and limit_percent > 0 and equity > 0
        and money < 0 and -money >= limit_money
    )

    if not надо_выключить:
        # Инструмент снова в порядке — отметку о времени снимаем, иначе
        # следующее отключение засчиталось бы задним числом.
        sym_state.auto_off_since = None
        return ""

    now = now or datetime.now()
    if sym_state.auto_off_since is None:
        sym_state.auto_off_since = now

    часов = float(getattr(cfg, "SYMBOL_AUTO_OFF_COOLDOWN_HOURS", ЧАСОВ_ОТДЫХА) or 0)
    if часов > 0:
        прошло = (now - sym_state.auto_off_since).total_seconds() / 3600.0
        if прошло >= часов:
            # Срок вышел. Освобождаем место в окне и пропускаем — иначе
            # приговор повторится на этой же проверке и получится круг.
            _release_window(sym_state)
            sym_state.auto_off_since = None
            log.info("%s: срок отключения вышел, даю испытательные сделки",
                     getattr(sym_state, "symbol", "?"))
            return ""

    осталось = ""
    if часов > 0:
        прошло = (now - sym_state.auto_off_since).total_seconds() / 3600.0
        осталось = f" Снова попробует через {max(0.0, часов - прошло):.1f} ч."
    return (f"Инструмент отключён сам: за последние "
            f"{len(sym_state.recent_profits)} сделок он дал {money:.2f} — это "
            f"больше {limit_percent:.1f}% счёта.{осталось} Другие "
            f"инструменты работают как обычно.")


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
                # Деньги по каждой сделке. Без сохранения самоотключение
                # убыточного инструмента не работало бы вовсе: окно живёт
                # в памяти процесса и обнулялось бы при каждом запуске.
                "profits": [float(p) for p in st.recent_profits],
                # Когда инструмент отключился. Без этого перезапуск сбрасывал
                # бы отсчёт, и срок отключения начинался бы заново каждый раз.
                "auto_off_since": (st.auto_off_since.isoformat()
                                   if st.auto_off_since else ""),
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
        profits = saved.get("profits", [])
        if not isinstance(results, list) or not isinstance(peaks, list):
            continue
        if not isinstance(profits, list):
            profits = []   # файл со старой версии программы — это не ошибка
        # Обрезаем по ТЕКУЩЕМУ окну: пользователь мог уменьшить
        # AUTO_LEARNING_WINDOW между запусками, и старый длинный хвост
        # тогда сделал бы настройку бессмысленной.
        st.recent_results = [bool(r) for r in results][-window:]
        st.recent_peaks = [max(0.0, float(p)) for p in peaks
                           if isinstance(p, (int, float))][-window:]
        st.recent_profits = [float(p) for p in profits
                             if isinstance(p, (int, float))][-window:]
        отметка = saved.get("auto_off_since", "")
        try:
            st.auto_off_since = datetime.fromisoformat(отметка) if отметка else None
        except (TypeError, ValueError):
            st.auto_off_since = None
        restored += 1
    if restored:
        log.info("Восстановлена статистика обучения по %d символам из %s", restored, path)
    return restored
