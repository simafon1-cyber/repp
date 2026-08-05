"""
trade_manager.py — управление уже открытыми сделками (Break Even, ATR-трейлинг,
Profit Lock, частичное закрытие), отправка ордеров с повтором, CSV-лог.

1:1 портировано из TradeManager.mqh (AI Scalper Pro v8.0). Пиковая прибыль по
позиции (для Profit Lock) и список частично закрытых тикетов хранятся в
процессе как обычные dict/set по тикету — тикеты уникальны в рамках счёта,
поэтому не привязаны к конкретному символу.
"""

import csv
import logging
import math
import os
from datetime import datetime

import config as cfg
import mt5_connector as mt5c
import risk_manager as rm
import safe_files

log = logging.getLogger("trade_manager")

_position_peak_points: dict = {}   # ticket -> peak profit, в пунктах
_partial_closed_tickets: set = set()
# Тикеты, по которым частичное закрытие включено и порог прибыли пройден, но
# закрыть нечего: половина минимального лота брокеру не отправляется. Нужен
# только чтобы написать об этом ОДИН раз на сделку, а не на каждом такте.
_partial_impossible_tickets: set = set()
# ticket -> изначальный риск сделки (price_open<->SL при первом же взгляде на
# позицию, до того как BE/трейлинг/лок успели её подтянуть), в пунктах — она
# же "1R". См. update_position_risk() и диагноз в manage_open_positions().
_position_risk_points: dict = {}
# ticket -> худшая (самая отрицательная) прибыль в пунктах за жизнь позиции.
# Нужна для "спасения в безубыток": сделку, которая ГЛУБОКО уходила в минус и
# вернулась к нулю, разумнее отпустить в ноль, чем ждать полной цели — см.
# break_even_rescue_action().
_position_trough_points: dict = {}
# position_id -> пик прибыли ЗАКРЫТОЙ позиции. Архив на момент исчезновения
# позиции из списка открытых: main.process_closed_deals() забирает его оттуда
# и отдаёт в auto_learning.record_trade_peak() — так бот учится, сколько
# пунктов реально даёт рынок по этой паре. Размер ограничен, чтобы словарь не
# рос бесконечно в долгой сессии.
_closed_peaks: dict = {}
_CLOSED_PEAKS_LIMIT = 500
# ticket -> момент (по НАШИМ часам), когда мы впервые увидели эту позицию.
# Возраст сделки НЕЛЬЗЯ считать как now() - position.time: position.time —
# это время ТОРГОВОГО СЕРВЕРА брокера, которое отличается от местного на
# часы (у многих брокеров это UTC+2/+3, а зимой и летом по-разному). Разница
# в 3 часа превратила бы только что открытую сделку либо в "висит 3 часа"
# (мгновенное сжатие цели до пола), либо в отрицательный возраст. Свои часы
# дают честный возраст без единого допущения о часовом поясе брокера.
_position_first_seen: dict = {}


def _ensure_csv_header():
    if not os.path.exists(cfg.LOG_CSV_PATH):
        with open(cfg.LOG_CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(
                ["Time", "Event", "Symbol", "Direction", "Price", "SL", "TP", "Lot", "Score", "Profit"]
            )
            f.flush()
            os.fsync(f.fileno())
        try:
            safe_files.restrict_to_current_user(cfg.LOG_CSV_PATH)
            safe_files.mark_integrity_current(cfg.LOG_CSV_PATH)
        except Exception:
            pass


def log_trade_csv(evt, symbol, direction, price, sl, tp, lot, score, profit=0.0):
    if not cfg.LIVE_TRADING:
        prefix = "[DRY-RUN] "
    else:
        prefix = ""
    _ensure_csv_header()

    def _write_row(f):
        writer = csv.writer(f, delimiter=";")
        writer.writerow(
            [datetime.now().isoformat(timespec="seconds"), prefix + evt, symbol, direction,
             f"{price:.5f}", f"{sl:.5f}", f"{tp:.5f}", f"{lot:.2f}", f"{score:.1f}", f"{profit:.2f}"]
        )

    try:
        # flush+fsync сразу после записи — строка сделки гарантированно
        # попадает на диск, а не теряется в кэше ОС при внезапном завершении
        # процесса; плюс обновляется sha256-сайдкар для проверки целостности
        # журнала сделок при следующем запуске (см. safe_files.py).
        safe_files.append_line_safely(cfg.LOG_CSV_PATH, _write_row)
    except Exception as e:
        log.warning("Не удалось безопасно дозаписать в %s (%s) — пробую обычной записью.",
                    cfg.LOG_CSV_PATH, e)
        with open(cfg.LOG_CSV_PATH, "a", newline="", encoding="utf-8") as f:
            _write_row(f)


def execute_market_order(symbol, direction, lot, sl_dist, tp_dist, score, point):
    dir_txt = "BUY" if direction == 1 else "SELL"

    # tp_dist <= 0 означает "без TP" (см. USE_MAX_PROFIT_RIDE в config.py) —
    # сделка едет, пока её не остановит BE/трейлинг/Profit Lock, а не
    # фиксированная цель прибыли. tp=0.0 — стандартное для MT5 значение
    # "тейк-профит не задан" (а НЕ "цена входа").
    if not cfg.LIVE_TRADING:
        log.info("[DRY-RUN] %s %s lot=%.2f score=%.1f (ордер НЕ отправлен, LIVE_TRADING=False)",
                  symbol, dir_txt, lot, score)
        tick = mt5c.get_tick(symbol)
        price = (tick.ask if direction == 1 else tick.bid) if tick else 0.0
        sl = price - sl_dist if direction == 1 else price + sl_dist
        tp = 0.0 if tp_dist <= 0 else (price + tp_dist if direction == 1 else price - tp_dist)
        log_trade_csv("OPEN", symbol, dir_txt, price, sl, tp, lot, score)
        return True

    for attempt in range(1, cfg.ORDER_RETRY_ATTEMPTS + 1):
        tick = mt5c.get_tick(symbol)
        if tick is None:
            return False
        price = tick.ask if direction == 1 else tick.bid
        sl = price - sl_dist if direction == 1 else price + sl_dist
        tp = 0.0 if tp_dist <= 0 else (price + tp_dist if direction == 1 else price - tp_dist)

        if not rm.check_stops_distance(symbol, price, sl, tp):
            log.warning("%s: SL/TP слишком близко, ордер отменён", symbol)
            return False

        result = mt5c.send_market_order(symbol, direction, lot, sl, tp, cfg.MAGIC_NUMBER,
                                         comment="AI_Scalper_Standalone")
        if result is None:
            log.error("%s: order_send вернул None, %s", symbol, mt5c.last_error())
            return False

        if result.retcode == mt5c.RETCODE_DONE:
            log.info("%s %s | Score %.1f | Попытка %d | тикет %s",
                      symbol, dir_txt, score, attempt, result.order)
            log_trade_csv("OPEN", symbol, dir_txt, price, sl, tp, lot, score)
            return True

        log.warning("%s: ошибка %s — %s", symbol, result.retcode, result.comment)
        transient = result.retcode in (mt5c.RETCODE_REQUOTE, mt5c.RETCODE_PRICE_CHANGED, mt5c.RETCODE_PRICE_OFF)
        if not transient:
            return False
    return False


def cleanup_peak_profit(open_tickets: set):
    for ticket in list(_position_peak_points.keys()):
        if ticket not in open_tickets:
            # Пик НЕ выбрасываем, а откладываем в архив: сделка закрылась, и
            # именно сейчас её пик становится обучающим примером для
            # auto_learning (см. pop_closed_peak и _closed_peaks выше).
            _archive_closed_peak(ticket, _position_peak_points.pop(ticket))
    for ticket in list(_partial_closed_tickets):
        if ticket not in open_tickets:
            _partial_closed_tickets.discard(ticket)
    for ticket in list(_partial_impossible_tickets):
        if ticket not in open_tickets:
            _partial_impossible_tickets.discard(ticket)
    for ticket in list(_position_risk_points.keys()):
        if ticket not in open_tickets:
            _position_risk_points.pop(ticket, None)
    for ticket in list(_position_trough_points.keys()):
        if ticket not in open_tickets:
            _position_trough_points.pop(ticket, None)
    for ticket in list(_position_first_seen.keys()):
        if ticket not in open_tickets:
            _position_first_seen.pop(ticket, None)


def _archive_closed_peak(position_id, peak_points: float):
    _closed_peaks[position_id] = peak_points
    while len(_closed_peaks) > _CLOSED_PEAKS_LIMIT:
        # dict в Python сохраняет порядок вставки — вычищаем самый старый
        _closed_peaks.pop(next(iter(_closed_peaks)), None)


def pop_closed_peak(position_id):
    """Пик прибыли закрытой позиции (в пунктах) или None, если он не
    отслеживался — например, позиция открылась до запуска программы."""
    return _closed_peaks.pop(position_id, None)


def update_peak_profit(ticket, profit_points) -> float:
    peak = _position_peak_points.get(ticket, profit_points)
    if profit_points > peak:
        peak = profit_points
    _position_peak_points[ticket] = peak
    return peak


def update_position_risk(ticket, price_open: float, sl: float, point: float) -> float:
    """Запоминает изначальный риск сделки (расстояние price_open<->SL) ОДИН
    РАЗ, при первом же взгляде на позицию — до того как BE/трейлинг/Profit
    Lock успеют подтянуть SL ближе к цене. Это и есть "1R" сделки: сколько
    она может максимум потерять по своему первоначальному стопу.

    Зачем: Profit Lock раньше стартовал по ПОРОГУ ОТ ATR (PROFIT_LOCK_START_
    POINTS), никак не связанному с тем, насколько ШИРОКИЙ стоп у конкретного
    профиля. У "Истерички" (atr_sl_multiplier=0.5) это давало запуск лока
    уже на ~30% от риска сделки — РАНЬШЕ даже безубытка. Из-за этого сделка
    фиксировалась в крошечный плюс почти сразу после открытия (иногда за
    1-2 секунды на волатильном золоте), а при неудаче стоп отрабатывал на
    ПОЛНУЮ дистанцию — отсюда "мелкие плюсы, крупные минусы" в реальных
    сделках. См. PROFIT_LOCK_START_R_FRACTION в config.py."""
    if ticket not in _position_risk_points:
        risk = abs(price_open - sl) / point if (sl and point) else 0.0
        _position_risk_points[ticket] = risk
    return _position_risk_points[ticket]


def _tiered_lock_percent(peak_points: float, unit: float) -> float:
    """Ступенчатая фиксация: чем выше пик прибыли (в единицах порога
    PROFIT_LOCK_START_POINTS, авто-масштабированного под ATR — это и есть
    `unit`), тем больший % пика запирается стопом. Возвращает % САМОГО
    СТАРШЕГО тира, до которого дорос пик; если тиров нет/unit некорректен —
    падает обратно на плоский cfg.PROFIT_LOCK_PERCENT."""
    tiers = getattr(cfg, "PROFIT_LOCK_TIERS", None)
    if not tiers or unit <= 0:
        return cfg.PROFIT_LOCK_PERCENT
    best_pct = cfg.PROFIT_LOCK_PERCENT
    for mult, pct in sorted(tiers, key=lambda t: t[0]):
        if peak_points >= mult * unit:
            best_pct = pct
    return best_pct


def position_age_seconds(ticket, now: float = None) -> float:
    """Сколько секунд мы ведём эту позицию — по НАШИМ часам (см. комментарий
    к _position_first_seen: время брокера для этого не годится).

    Для позиции, открытой до запуска программы, отсчёт начинается с момента,
    когда мы её впервые увидели. Это занижает возраст, то есть ошибается в
    безопасную сторону: цель прибыли сожмётся позже, а не раньше времени."""
    if now is None:
        now = datetime.now().timestamp()
    first = _position_first_seen.setdefault(ticket, now)
    return max(0.0, now - first)


def update_position_trough(ticket, profit_points: float) -> float:
    """Запоминает самую глубокую просадку позиции (в пунктах, отрицательное
    число). Зеркало update_peak_profit()."""
    trough = _position_trough_points.get(ticket, profit_points)
    if profit_points < trough:
        trough = profit_points
    _position_trough_points[ticket] = trough
    return trough


# =====================================================================
# ПОДТЯГИВАНИЕ ТЕЙК-ПРОФИТА
# =====================================================================
# Правило проекта, зеркальное правилу для стоп-лосса: TP можно только
# ПРИБЛИЖАТЬ к цене, никогда не отодвигать дальше. Отодвинутый TP — это
# скрытое увеличение риска: сделка висит дольше, дольше платит своп и дольше
# подставляется под разворот. Приближение TP, наоборот, только ускоряет
# фиксацию прибыли, а хуже сделке от этого стать не может.
#
# Цель прибыли сжимается со временем: чем дольше сделка "не едет", тем
# скромнее цель, вплоть до пола TP_TIGHTEN_MIN_FRACTION. Так позиция не
# зависает на весь день в ожидании цели, которую рынок уже не даст.

def shrunk_target_points(target_points: float, age_seconds: float,
                         shrink_per_minute: float, min_fraction: float) -> float:
    """Цель прибыли, ужатая пропорционально возрасту сделки."""
    if target_points <= 0:
        return 0.0
    minutes = max(0.0, age_seconds) / 60.0
    fraction = 1.0 - shrink_per_minute * minutes
    fraction = max(min_fraction, min(1.0, fraction))
    return target_points * fraction


def tp_floor_price(is_buy: bool, price_open: float, price: float,
                   min_profit_points: float, point: float, broker_min_dist: float) -> float:
    """Ближайший TP, который вообще допустим:
      * не ближе min_profit_points от цены входа — TP всегда остаётся в плюсе,
        подтягивание не имеет права превратить цель прибыли в убыток;
      * не ближе минимальной дистанции брокера от ТЕКУЩЕЙ цены — иначе сервер
        отклонит изменение."""
    if is_buy:
        return max(price_open + min_profit_points * point, price + broker_min_dist)
    return min(price_open - min_profit_points * point, price - broker_min_dist)


def tighten_take_profit(is_buy: bool, price_open: float, price: float, current_tp: float,
                        target_points: float, min_profit_points: float, point: float,
                        broker_min_dist: float, step_points: float,
                        risk_points: float = 0.0, min_r: float = 1.0) -> float:
    """Новый TP или 0.0, если менять нечего.

    Возвращает 0.0 (а не current_tp) специально: вызывающий код так отличает
    "изменений нет" от "поставить TP в ноль", а ноль TP в MT5 означает
    "цель не задана" и здесь никогда не является результатом.

    risk_points — собственный риск сделки (расстояние вход<->стоп) в пунктах,
    min_r — сколько таких риском цель обязана покрывать."""
    if target_points <= 0 or point <= 0:
        return 0.0

    # КРИТИЧНО. Цель не может стать меньше собственного риска сделки.
    # Без этого пола получалось следующее: у профиля "Истеричка" стоп равен
    # 0.5*ATR, стартовая цель 1.5*ATR, а сжатие со временем опускало её до
    # 25% = 0.375*ATR. Через 7-8 минут сделка рисковала БОЛЬШЕ, чем могла
    # выиграть (1 : 0.75), и при обычном винрейте это гарантированный минус —
    # мелкие плюсы против полноразмерных стопов. Та же ошибка уже была здесь
    # с Profit Lock, её лечит PROFIT_LOCK_START_R_FRACTION; сжатие цели
    # обязано подчиняться тому же правилу.
    if risk_points > 0 and min_r > 0:
        target_points = max(target_points, risk_points * min_r)

    candidate = (price_open + target_points * point) if is_buy else (price_open - target_points * point)
    floor = tp_floor_price(is_buy, price_open, price, min_profit_points, point, broker_min_dist)
    candidate = max(candidate, floor) if is_buy else min(candidate, floor)

    if current_tp == 0:
        return candidate                      # цели не было — ставим первую

    closer = candidate < current_tp if is_buy else candidate > current_tp
    if not closer:
        return 0.0                            # дальше отодвигать запрещено
    # Эпсилон обязателен: 2001.0 - 2000.95 = 0.04999999999999982, и без допуска
    # перенос ровно на шаг отвергался бы как "мельче шага" (та же природа, что
    # и ошибка округления лота — см. FloorToStep в DualGuardEA.mq5).
    if abs(candidate - current_tp) + point * 1e-6 < step_points * point:
        return 0.0                            # мельче шага — не дёргаем сервер
    return candidate


# =====================================================================
# СПАСЕНИЕ УБЫТОЧНОЙ СДЕЛКИ В БЕЗУБЫТОК
# =====================================================================
# Сделка, которая заметно уходила в минус и вернулась к нулю, уже показала,
# что вход был неудачным: рынок сходил против неё. Ждать от такой сделки
# полной цели — значит чаще всего отдать обратно и этот возврат.
#
# ВАЖНО, чего этот механизм НЕ делает: он не трогает стоп-лосс, не отодвигает
# его и не отменяет. Он только добавляет ВЫХОД РАНЬШЕ. Если рынок не вернётся
# к нулю — сделку закроет обычный стоп-лосс на своей исходной дистанции.

def break_even_rescue_action(trough_points: float, profit_points: float, age_seconds: float,
                             min_drawdown_points: float, after_seconds: float,
                             exit_points: float) -> str:
    """"" (ничего не делать) / "arm" (подтянуть TP к нулю) / "close" (закрыть сейчас)."""
    if trough_points > -abs(min_drawdown_points):
        return ""                             # глубоко в минус не уходила — обычный режим
    if age_seconds < after_seconds:
        return ""                             # ещё рано, пусть сделка поработает
    if profit_points >= exit_points:
        return "close"                        # уже вернулась в плюс — забираем
    return "arm"


def _better_sl(is_buy: bool, a: float, b: float) -> float:
    if a == 0:
        return b
    if b == 0:
        return a
    return max(a, b) if is_buy else min(a, b)


def manage_open_positions(symbol: str, atr_value: float, point: float, positions=None,
                          learned_tp_points: float = 0.0):
    """Break Even / ATR-трейлинг / Profit Lock / частичное закрытие — на все
    открытые позиции этого символа с нашим magic number.

    positions: если передан уже готовый список (одним запросом на всю
    итерацию главного цикла — см. main.py), фильтруем его локально вместо
    отдельного обращения к MT5 на каждый символ. Иначе — запрашиваем сами,
    как раньше (для обратной совместимости с другими вызовами).

    learned_tp_points: выученная по прошлым сделкам цель прибыли в пунктах
    (auto_learning.learned_profit_points). 0 — данных ещё мало, цель берётся
    от ATR."""
    import MetaTrader5 as mt5

    if positions is None:
        positions = mt5c.get_open_positions(symbol=symbol, magic=cfg.MAGIC_NUMBER)
    else:
        positions = [p for p in positions if p.symbol == symbol and p.magic == cfg.MAGIC_NUMBER]
    open_tickets = {p.ticket for p in positions}
    cleanup_peak_profit(open_tickets)

    info = mt5.symbol_info(symbol)
    broker_min_dist = (info.trade_stops_level * point) if info else 0.0

    for p in positions:
        is_buy = p.type == mt5.POSITION_TYPE_BUY
        tick = mt5c.get_tick(symbol)
        if tick is None:
            continue
        price = tick.bid if is_buy else tick.ask
        profit_points = (price - p.price_open) / point if is_buy else (p.price_open - price) / point
        peak_points = update_peak_profit(p.ticket, profit_points)
        trough_points = update_position_trough(p.ticket, profit_points)
        risk_points = update_position_risk(p.ticket, p.price_open, p.sl, point)
        age_seconds = position_age_seconds(p.ticket)

        # 0) Частичное закрытие при достижении профита (один раз за сделку) —
        # как "Partial Close" в MQL5-советнике. Не мешает BE/трейлингу ниже —
        # они продолжают вести остаток позиции как обычно.
        if (getattr(cfg, "USE_PARTIAL_CLOSE", False) and p.ticket not in _partial_closed_tickets):
            eff_partial_trigger = rm.eff_points_threshold(
                getattr(cfg, "PARTIAL_CLOSE_TRIGGER_POINTS", 150), 0.6, atr_value, point)
            if profit_points >= eff_partial_trigger:
                close_pct = getattr(cfg, "PARTIAL_CLOSE_PERCENT", 50)
                close_volume = p.volume * close_pct / 100.0
                vol_min = info.volume_min if info else 0.01
                vol_step = info.volume_step if info else 0.01
                if vol_step > 0:
                    close_volume = math.floor(close_volume / vol_step) * vol_step
                close_volume = max(0.0, min(p.volume, close_volume))
                if close_volume < vol_min or close_volume <= 0:
                    # Молчать здесь нельзя. Настройка включена, прибыль до
                    # порога дошла — а закрытия не будет: половина от
                    # минимального лота (0.005 при 0.01) брокеру не
                    # отправишь. Человек будет ждать частичной фиксации,
                    # которой физически не может произойти. Пишем один раз
                    # на сделку, чтобы не залить лог повторами.
                    if p.ticket not in _partial_impossible_tickets:
                        _partial_impossible_tickets.add(p.ticket)
                        log.info(
                            "%s тикет %s: частичное закрытие невозможно — "
                            "%.0f%% от %.2f лота это %.3f, а брокер принимает "
                            "не меньше %.2f и шагом %.2f. Заработает при лоте "
                            "от %.2f (сейчас лот минимальный).",
                            symbol, p.ticket, close_pct, p.volume,
                            p.volume * close_pct / 100.0, vol_min, vol_step,
                            vol_min * 2)
                elif close_volume >= vol_min and close_volume > 0:
                    if cfg.LIVE_TRADING:
                        result = mt5c.close_position_partial(p, close_volume)
                        if result is not None and result.retcode == mt5c.RETCODE_DONE:
                            _partial_closed_tickets.add(p.ticket)
                            log.info("%s тикет %s: частичное закрытие %.2f лота (профит %.1f пт)",
                                     symbol, p.ticket, close_volume, profit_points)
                        else:
                            log.warning("%s тикет %s: не удалось частично закрыть (%s)",
                                        symbol, p.ticket, result)
                    else:
                        log.debug("[DRY-RUN] %s тикет %s: частичное закрытие %.2f лота (профит %.1f пт)",
                                  symbol, p.ticket, close_volume, profit_points)
                        _partial_closed_tickets.add(p.ticket)

        current_sl = p.sl
        current_tp = p.tp
        best_sl = current_sl

        eff_be_offset = rm.eff_points_threshold(cfg.BREAK_EVEN_OFFSET_POINTS, 0.05, atr_value, point)
        eff_trail_min = rm.eff_points_threshold(cfg.TRAILING_MIN_POINTS, 0.3, atr_value, point)
        eff_profit_lock = rm.eff_points_threshold(cfg.PROFIT_LOCK_START_POINTS, 0.15, atr_value, point)
        eff_trail_step = rm.eff_points_threshold(cfg.TRAILING_STEP_MIN_POINTS, 0.02, atr_value, point)

        # Порог Profit Lock не может быть меньше доли ОТ РИСКА САМОЙ СДЕЛКИ
        # (её 1R = исходное расстояние price_open<->SL). Раньше порог считался
        # ТОЛЬКО от ATR, без привязки к тому, насколько узкий стоп у профиля —
        # у "Истерички" (atr_sl_multiplier=0.5) это давало запуск лока уже на
        # ~30% риска, РАНЬШЕ безубытка: сделка фиксировалась в крошечный плюс
        # почти сразу, а неудачная — теряла всю дистанцию стопа целиком (см.
        # PROFIT_LOCK_START_R_FRACTION в config.py и update_position_risk()).
        if risk_points > 0:
            r_fraction = getattr(cfg, "PROFIT_LOCK_START_R_FRACTION", 1.0)
            eff_profit_lock = max(eff_profit_lock, risk_points * r_fraction)

        # 1) Break Even
        be_trigger_pts = (atr_value * cfg.BREAK_EVEN_ATR_MULTIPLIER) / point if point else 0
        if cfg.USE_BREAK_EVEN and profit_points >= be_trigger_pts:
            be_sl = p.price_open + eff_be_offset * point if is_buy else p.price_open - eff_be_offset * point
            best_sl = _better_sl(is_buy, best_sl, be_sl)

        # 2) ATR-трейлинг
        trail_pts = max(eff_trail_min, (atr_value * cfg.TRAILING_ATR_MULTIPLIER) / point if point else 0)
        if cfg.USE_TRAILING_STOP and profit_points >= trail_pts:
            trail_sl = price - trail_pts * point if is_buy else price + trail_pts * point
            best_sl = _better_sl(is_buy, best_sl, trail_sl)

        # 3) Profit Lock — гонится за ПИКОВОЙ прибылью, а не текущей ценой.
        # Ступенчатый % (см. _tiered_lock_percent) вместо одного фиксированного —
        # чем выше был пик, тем больше от него запирается стопом.
        if cfg.USE_PROFIT_LOCK_TRAILING and peak_points >= eff_profit_lock:
            lock_pct = (_tiered_lock_percent(peak_points, eff_profit_lock)
                        if getattr(cfg, "USE_TIERED_PROFIT_LOCK", False)
                        else cfg.PROFIT_LOCK_PERCENT)
            lock_points = peak_points * lock_pct / 100.0
            lock_sl = p.price_open + lock_points * point if is_buy else p.price_open - lock_points * point
            best_sl = _better_sl(is_buy, best_sl, lock_sl)

        improved = (best_sl > current_sl) if is_buy else (current_sl == 0 or best_sl < current_sl)
        dist_ok = broker_min_dist <= 0 or abs(price - best_sl) >= broker_min_dist
        step_ok = (current_sl == 0
                   or abs(best_sl - current_sl) + point * 1e-6 >= eff_trail_step * point)
        sl_changed = best_sl != current_sl and improved and dist_ok and step_ok
        if not sl_changed:
            best_sl = current_sl

        # 4) Спасение в безубыток: сделка глубоко проседала и вернулась к нулю.
        # Проверяем ДО подтягивания TP — если пора закрывать, TP уже не нужен.
        best_tp = current_tp
        rescued = False
        if getattr(cfg, "USE_BREAK_EVEN_RESCUE", False):
            eff_rescue_dd = rm.eff_points_threshold(
                getattr(cfg, "BE_RESCUE_MIN_DRAWDOWN_POINTS", 20), 0.25, atr_value, point)
            eff_rescue_exit = rm.eff_points_threshold(
                getattr(cfg, "BE_RESCUE_EXIT_POINTS", 3), 0.03, atr_value, point)
            action = break_even_rescue_action(
                trough_points, profit_points, age_seconds,
                eff_rescue_dd, getattr(cfg, "BE_RESCUE_AFTER_MINUTES", 10) * 60.0,
                eff_rescue_exit)

            if action == "close":
                log.info("%s тикет %s: спасение в безубыток — просадка была %.1f пт, "
                         "вернулась к %.1f пт, закрываю", symbol, p.ticket, trough_points, profit_points)
                if cfg.LIVE_TRADING:
                    result = mt5c.close_position_partial(p, p.volume)
                    if result is None or result.retcode != mt5c.RETCODE_DONE:
                        log.warning("%s тикет %s: не удалось закрыть в безубыток (%s)",
                                    symbol, p.ticket, result)
                    else:
                        continue          # позиции больше нет — TP ей уже не нужен
                else:
                    log.debug("[DRY-RUN] %s тикет %s: закрытие в безубыток", symbol, p.ticket)
                    continue
            elif action == "arm":
                # Цель прибыли переносится на уровень безубытка — сделка
                # закроется сама, как только рынок вернёт её к нулю, даже если
                # программа в этот момент занята другими символами.
                rescue_tp = tighten_take_profit(
                    is_buy, p.price_open, price, current_tp,
                    eff_rescue_exit, eff_rescue_exit, point, broker_min_dist, 0.0)
                if rescue_tp > 0:
                    best_tp = rescue_tp
                    rescued = True

        # 5) Подтягивание тейк-профита — только ближе к цене, никогда дальше.
        if getattr(cfg, "USE_TP_TIGHTEN", False) and not rescued:
            base_target = learned_tp_points if learned_tp_points > 0 else \
                (atr_value * getattr(cfg, "TP_TIGHTEN_START_ATR", 1.5)) / point if point else 0.0
            target = shrunk_target_points(
                base_target, age_seconds,
                getattr(cfg, "TP_TIGHTEN_SHRINK_PER_MINUTE", 0.10),
                getattr(cfg, "TP_TIGHTEN_MIN_FRACTION", 0.25))
            eff_min_profit = rm.eff_points_threshold(
                getattr(cfg, "TP_TIGHTEN_MIN_PROFIT_POINTS", 10), 0.08, atr_value, point)
            eff_tp_step = rm.eff_points_threshold(
                getattr(cfg, "TP_TIGHTEN_STEP_POINTS", 5), 0.02, atr_value, point)
            new_tp = tighten_take_profit(
                is_buy, p.price_open, price, current_tp, target,
                eff_min_profit, point, broker_min_dist, eff_tp_step,
                risk_points=risk_points,
                min_r=getattr(cfg, "TP_TIGHTEN_MIN_R", 1.0))
            if new_tp > 0:
                best_tp = new_tp

        tp_changed = best_tp != current_tp

        if sl_changed or tp_changed:
            if cfg.LIVE_TRADING:
                mt5c.modify_position(p.ticket, best_sl, best_tp)
            else:
                log.debug("[DRY-RUN] %s тикет %s: SL -> %.5f, TP -> %.5f",
                          symbol, p.ticket, best_sl, best_tp)
