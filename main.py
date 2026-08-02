"""
main.py — точка входа. Обходит список символов (cfg.SYMBOLS), на каждый новый
бар прогоняет полный пайплайн (режим рынка -> контекст -> score -> AI-сигнал ->
режим торговли скальпинг/новости -> риск-проверки -> ордер), а на каждый опрос
(не только новый бар) ведёт уже открытые позиции (BE/трейлинг/Profit Lock).

Это САМОСТОЯТЕЛЬНАЯ программа — запускается отдельно от MetaTrader 5 (просто
python-процесс), но требует, чтобы терминал MT5 был уже открыт и залогинен на
этом же компьютере (mt5_connector.connect() подключается к нему напрямую).

ЗАПУСК:  python main.py
ОСТАНОВ: Ctrl+C
"""

import logging
import socket
import threading
import time
from datetime import datetime, timedelta

import MetaTrader5 as mt5

import importlib
import os

import config as cfg
import mt5_connector as mt5c
import risk_manager as rm
import trade_manager as tm
import signal_engine as se
import market_regime as mr
import ai_signal as ai
import auto_learning as al
import custom_strategy as cs
import multi_indicator as mi
import news_calendar
import dashboard_state as ds
import secure_store
from control import control
from indicators import (
    add_all_indicators, pullback_breakout_ok, ema_stack_ok,
    is_bullish_confirmation, is_bearish_confirmation,
)
from state import AccountState, SymbolState

logging.basicConfig(
    level=getattr(logging, cfg.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("main")

_processed_deal_tickets: set = set()

# Кэш "полной" истории сделок из MT5 (все magic number, для сверки статистики
# с реальной историей у брокера) — см. _refresh_mt5_history_cache() и п.4
# "синхронизация с MetaTrader" в задаче пользователя.
_history_cache: dict = {"deals": [], "stats": {}, "ts": datetime.now() - timedelta(seconds=999)}

# Кэш списка ДОСТУПНЫХ у брокера символов (для выпадающего списка при
# добавлении пары на вкладке "Символы") — список символов у брокера меняется
# редко, поэтому обновляем не на каждой итерации, а раз в SYMBOLS_CACHE_SECONDS.
_symbols_cache: dict = {"list": [], "ts": datetime.now() - timedelta(seconds=999)}
SYMBOLS_CACHE_SECONDS = 300

_last_trade_permission_ok = True


def init_states() -> dict:
    sym_states = {}
    for sym in cfg.SYMBOLS:
        if mt5c.ensure_symbol(sym):
            sym_states[sym] = SymbolState(symbol=sym)
        else:
            log.warning("Символ %s недоступен у брокера — исключён из торговли.", sym)
    return sym_states


def check_new_day(acc_state: AccountState, equity: float):
    now = datetime.now()
    if acc_state.last_trade_day is None or now.date() != acc_state.last_trade_day.date():
        acc_state.trades_today = 0
        acc_state.last_trade_day = now
        acc_state.day_start_equity = equity


def process_closed_deals(acc_state: AccountState, sym_states: dict):
    """Аналог OnTradeTransaction в MQL5: опрашиваем историю сделок за последние
    сутки и реагируем на новые ЗАКРЫТИЯ позиций с нашим magic number."""
    from_time = datetime.now() - timedelta(days=1)
    deals = mt5.history_deals_get(from_time, datetime.now())
    if deals is None:
        return

    for d in deals:
        if d.magic != cfg.MAGIC_NUMBER:
            continue
        if d.entry not in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY):
            continue
        if d.ticket in _processed_deal_tickets:
            continue
        _processed_deal_tickets.add(d.ticket)

        sym_state = sym_states.get(d.symbol)
        if sym_state is None:
            continue

        profit = d.profit
        # закрывающая SELL-сделка означает, что закрылась BUY-позиция, и наоборот
        sym_state.last_close_direction = 1 if d.type == mt5.DEAL_TYPE_SELL else -1
        sym_state.last_close_bar_index = sym_state.bar_counter

        # Автообучение: копим окно последних результатов ПО ЭТОМУ символу —
        # см. auto_learning.py (адаптивный вес AI / порог входа по винрейту).
        al.record_trade_result(sym_state, profit)

        acc_state.total_trades += 1
        if profit >= 0:
            acc_state.win_trades += 1
            acc_state.gross_profit += profit
            sym_state.last_trade_result = f"ПРИБЫЛЬ +{profit:.2f}"
            control.push_notification("Сделка закрыта", f"{d.symbol}: прибыль +{profit:.2f}")
        else:
            acc_state.gross_loss += profit
            sym_state.last_trade_result = f"УБЫТОК {profit:.2f}"
            control.push_notification("Сделка закрыта", f"{d.symbol}: убыток {profit:.2f}")

        if profit < 0:
            sym_state.consecutive_losses += 1
            if sym_state.consecutive_losses >= cfg.MAX_CONSECUTIVE_LOSSES:
                sym_state.pause_until = datetime.now() + timedelta(hours=cfg.PAUSE_HOURS_AFTER_LOSS_STREAK)
                log.info("%s: серия из %d убытков подряд — пауза до %s",
                         d.symbol, sym_state.consecutive_losses, sym_state.pause_until)
                control.push_notification(
                    "Серия убытков",
                    f"{d.symbol}: пауза по этой паре до {sym_state.pause_until.strftime('%H:%M %d.%m')}",
                )
                sym_state.consecutive_losses = 0
        else:
            sym_state.consecutive_losses = 0

        tm.log_trade_csv("CLOSE", d.symbol, "CLOSE", d.price, 0, 0, d.volume, 0, profit)


def _refresh_mt5_history_cache():
    """Синхронизация с историей сделок брокера (п.4 задачи пользователя):
    раз в HISTORY_SYNC_SECONDS подтягивает историю закрытых сделок из MT5 за
    последние HISTORY_SYNC_DAYS дней, ПО ВСЕМ magic number — не только этого
    бота, значит сюда попадают и сделки, открытые вручную в терминале.
    Результат кладётся в _history_cache и попадает в снимок для дашборда/
    desktop-приложения (см. build_snapshot) — используется ТОЛЬКО для показа/
    статистики/экспорта, не влияет на торговые решения (auto-learning и серии
    убытков по-прежнему считаются process_closed_deals() отдельно, только по
    сделкам этого бота — это не трогаем)."""
    global _history_cache
    if (datetime.now() - _history_cache["ts"]).total_seconds() < getattr(cfg, "HISTORY_SYNC_SECONDS", 60):
        return
    _history_cache["ts"] = datetime.now()

    days = getattr(cfg, "HISTORY_SYNC_DAYS", 30)
    from_time = datetime.now() - timedelta(days=days)
    deals = mt5c.get_deals_history(from_time, datetime.now())
    if not deals:
        return

    out_deals = []
    total = 0
    win = 0
    gross_profit = 0.0
    gross_loss = 0.0
    for d in deals:
        if d.entry not in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY):
            continue
        total += 1
        if d.profit >= 0:
            win += 1
            gross_profit += d.profit
        else:
            gross_loss += d.profit
        out_deals.append({
            "ticket": d.ticket,
            "time": datetime.fromtimestamp(d.time).strftime("%d.%m %H:%M:%S"),
            "time_raw": d.time,
            "symbol": d.symbol,
            # d.type — тип ЗАКРЫВАЮЩЕЙ сделки: SELL закрывает BUY-позицию и наоборот
            "type": "BUY" if d.type == mt5.DEAL_TYPE_SELL else "SELL",
            "volume": d.volume,
            "price": d.price,
            "profit": round(d.profit, 2),
            "is_bot": d.magic == cfg.MAGIC_NUMBER,
        })

    out_deals.sort(key=lambda x: x["time_raw"], reverse=True)
    _history_cache["deals"] = out_deals[:200]
    _history_cache["stats"] = {
        "total_trades": total,
        "win_trades": win,
        "win_rate": round(win / total * 100.0, 1) if total else 0.0,
        "profit_factor": round(gross_profit / abs(gross_loss), 2) if gross_loss < 0 else 0.0,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "days": days,
    }


def _refresh_available_symbols_cache():
    """Список символов, реально доступных у брокера — для выпадающего списка
    на вкладке "Символы" (см. п.3 задачи пользователя: пары должны
    синхронизироваться с MetaTrader, какие есть доступные на счёте)."""
    global _symbols_cache
    if (datetime.now() - _symbols_cache["ts"]).total_seconds() < SYMBOLS_CACHE_SECONDS:
        return
    _symbols_cache["ts"] = datetime.now()
    try:
        _symbols_cache["list"] = mt5c.get_all_symbols()
    except Exception as e:
        log.warning("Не удалось обновить список символов брокера: %s", e)


def _check_trading_permission():
    """Проверка AutoTrading/разрешений счёта — самая частая причина, когда
    бот считает сигналы, но ни одна сделка не открывается. Уведомляем только
    при ИЗМЕНЕНИИ состояния (чтобы не спамить одним и тем же предупреждением
    каждую итерацию, пока проблема не устранена)."""
    global _last_trade_permission_ok
    try:
        status = mt5c.trading_permission_status()
    except Exception:
        return {"ok": True, "problems": []}
    if not status["ok"] and _last_trade_permission_ok:
        msg = " | ".join(status["problems"])
        log.warning("Торговля разрешена не полностью: %s", msg)
        control.push_notification("Сделки могут не открываться", msg)
    _last_trade_permission_ok = status["ok"]
    return status


def process_symbol(symbol: str, sym_state: SymbolState, acc_state: AccountState, equity: float,
                    acc_info=None, all_positions=None):
    """acc_info/all_positions: уже полученные один раз за ЭТУ итерацию главного
    цикла (см. main()) — экономит повторные запросы к MT5-терминалу на каждый
    символ (для N символов раньше было N лишних get_open_positions/
    get_account_info вызовов за итерацию; это одна из главных задержек цикла)."""
    profile = rm.get_profile()
    point = mt5c.get_symbol_point(symbol)

    df_raw = mt5c.get_rates_df(symbol, cfg.TIMEFRAME, count=max(300, cfg.EMA_TREND_PERIOD + 50))
    if df_raw is None or len(df_raw) < cfg.EMA_SLOW_PERIOD + 5:
        sym_state.last_reject_reason = "Недостаточно данных с MT5"
        return

    df_ind = add_all_indicators(df_raw, cfg)
    atr_value = float(df_ind["atr"].iloc[-1])
    sym_state.last_atr_value = atr_value  # кэш для _fast_position_monitor()

    # Ведём уже открытые позиции на КАЖДОМ опросе, не только на новый бар
    tm.manage_open_positions(symbol, atr_value, point, positions=all_positions)

    last_bar_time = df_raw.iloc[-1]["time"]
    is_new_bar = sym_state.last_bar_time is None or last_bar_time != sym_state.last_bar_time
    if not is_new_bar:
        return

    sym_state.last_bar_time = last_bar_time
    sym_state.bar_counter += 1

    # Только для мини-графика цены в desktop-приложении — на торговлю не влияет
    sym_state.recent_closes.append(float(df_ind["close"].iloc[-1]))
    if len(sym_state.recent_closes) > 60:
        sym_state.recent_closes.pop(0)

    mr.update_market_regime(sym_state, df_ind)

    if control.is_paused():
        sym_state.last_reject_reason = "Пауза (управление с дашборда/телефона)"
        return

    if not control.is_symbol_enabled(symbol):
        sym_state.last_reject_reason = "Пара отключена с дашборда (выбор пары)"
        return

    if not rm.trading_allowed(acc_state, sym_state, equity):
        sym_state.last_reject_reason = "Торговля приостановлена (лимит/просадка/пауза)"
        return
    if rm.count_open_positions(symbol, positions=all_positions) >= profile["max_open_positions"]:
        sym_state.last_reject_reason = "Достигнут лимит одновременных сделок"
        return
    if acc_state.trades_today >= profile["max_trades_per_day"]:
        sym_state.last_reject_reason = "Достигнут лимит сделок за день"
        return
    if not rm.spread_ok(symbol, atr_value, point):
        sym_state.last_reject_reason = "Спред слишком широкий"
        return
    if atr_value <= 0:
        sym_state.last_reject_reason = "Индикаторы не готовы"
        return
    if not rm.rollover_guard_ok(profile["ignore_soft_filters"]):
        sym_state.last_reject_reason = "Ролловерная дыра ликвидности — сигнал пропущен"
        return
    if not rm.trading_hours_ok():
        sym_state.last_reject_reason = "Вне разрешённых часов торговли"
        return

    direction = 0
    score = 0.0
    is_news_entry = False
    hedge_directions = None  # None = обычный вход в одну сторону; иначе [1, -1] — хедж (см. ниже)

    trend_df = mt5c.get_rates_df(symbol, cfg.TREND_TIMEFRAME, count=cfg.EMA_TREND_PERIOD + 10)

    # Режим торговли можно сменить с дашборда "на лету" — если не переопределён, берём из config.py
    trading_mode = control.get_trading_mode() or cfg.TRADING_MODE

    # Автообучение: порог входа плавно подстраивается под недавний винрейт ПО
    # ЭТОМУ символу (см. auto_learning.py) — в жёстких границах из config.py.
    adaptive_threshold = al.adaptive_score_threshold(profile["min_score_to_trade"], sym_state)

    # Новостной режим (или ОБА) — сначала пробуем поймать пробой на свежей важной новости.
    # ВНИМАНИЕ: news_calendar.py — заглушка, пока не подключишь реальный календарь (см. докстринг
    # там), это условие всегда False, т.е. MODE_NEWS_TRADING в этой версии реально не сработает.
    if trading_mode in (cfg.TradingMode.NEWS_TRADING, cfg.TradingMode.BOTH):
        has_signal, news_dir, news_conf = news_calendar.detect_news_breakout(symbol, cfg.NEWS_BREAKOUT_WINDOW_MIN)
        if has_signal and news_conf >= adaptive_threshold:
            direction, score, is_news_entry = news_dir, news_conf, True
            sym_state.last_buy_score = score if direction == 1 else 0
            sym_state.last_sell_score = score if direction == -1 else 0
        elif trading_mode == cfg.TradingMode.NEWS_TRADING:
            sym_state.last_reject_reason = "Новостной режим: свежего пробоя нет (календарь не подключен)"
            return

    # Обычный скальпинг-паттерн — если новостной вход не сработал (или режим = только скальпинг)
    if direction == 0:
        hard_block_window = getattr(cfg, "NEWS_HARD_BLOCK_WINDOW_MIN", cfg.NEWS_BREAKOUT_WINDOW_MIN)
        if news_calendar.is_high_impact_event_near(symbol, hard_block_window):
            sym_state.last_reject_reason = "Рядом важная новость"
            return
        if not rm.volatility_ok(df_ind["atr"], profile["ignore_soft_filters"]):
            sym_state.last_reject_reason = "Резкий скачок волатильности — сигнал пропущен"
            return

        # Анти-"зеркало" фильтр #2 (по факту жалобы пользователя на входы прямо
        # перед разворотом): во ФЛЭТЕ трендовый паттерн откат+пробой чаще всего
        # ложный — раньше это только штрафовало score (-REGIME_RANGE_PENALTY),
        # сделка всё равно была возможна. Теперь, если включено, вход блокируется
        # ПОЛНОСТЬЮ, пока режим не сменится на тренд/неопределённый. Действует
        # ВСЕГДА, включая профиль с ignore_soft_filters ("Истеричка") — это
        # именно тот профиль, где проблема была обнаружена.
        if getattr(cfg, "BLOCK_ENTRY_IN_RANGE", False) and sym_state.current_regime == "range":
            sym_state.last_reject_reason = "Флэт: вход заблокирован (анти-разворотный фильтр)"
            return

        buy_score = se.calc_signal_score(symbol, 1, df_ind, trend_df, point, sym_state)
        sell_score = se.calc_signal_score(symbol, -1, df_ind, trend_df, point, sym_state)

        if cfg.USE_AI_SIGNAL:
            ok, ext_dir, ext_conf = ai.fetch_ai_signal(symbol, df_ind)
            sym_state.ext_last_ok = ok
            sym_state.ext_last_direction = ext_dir
            sym_state.ext_last_confidence = ext_conf
            # Автообучение: множитель веса AI по недавнему винрейту символа
            # (больше доверия AI, если в последнее время он "прав" на этой паре).
            w = ai.effective_ai_weight(sym_state) * al.adaptive_ai_weight_multiplier(sym_state)
            buy_score = ai.apply_ai_signal(buy_score, 1, ok, ext_dir, ext_conf, w)
            sell_score = ai.apply_ai_signal(sell_score, -1, ok, ext_dir, ext_conf, w)

        # Собственная стратегия программы (custom_strategy.py, п. запроса
        # пользователя "добавить в программу, не в советник") — второе,
        # независимое мнение (momentum/ускорение/согласованность/расширение
        # диапазона), подмешивается с ограниченным весом, как AI-сигнал выше.
        if cfg.USE_CUSTOM_STRATEGY:
            custom_buy = cs.calc_custom_score(1, df_ind, atr_value)
            custom_sell = cs.calc_custom_score(-1, df_ind, atr_value)
            sym_state.last_custom_score = max(custom_buy, custom_sell)
            buy_score = cs.apply_custom_strategy(buy_score, custom_buy)
            sell_score = cs.apply_custom_strategy(sell_score, custom_sell)

        # Доп. подтверждение классическими индикаторами (multi_indicator.py:
        # MACD/Bollinger/Stochastic) — по просьбе пользователя "используй как
        # можно больше индикаторов и стратегий", то же ограниченное подмешивание.
        if cfg.USE_MULTI_INDICATOR:
            mi_buy = mi.calc_multi_indicator_score(1, df_ind)
            mi_sell = mi.calc_multi_indicator_score(-1, df_ind)
            sym_state.last_multi_indicator_score = max(mi_buy, mi_sell)
            buy_score = mi.apply_multi_indicator(buy_score, mi_buy)
            sell_score = mi.apply_multi_indicator(sell_score, mi_sell)

        sym_state.last_buy_score, sym_state.last_sell_score = buy_score, sell_score

        if cfg.USE_SCORE_FILTER:
            buy_ok = buy_score >= adaptive_threshold
            sell_ok = sell_score >= adaptive_threshold
            # Хедж-режим (сейчас только у профиля "Истеричка", по просьбе
            # пользователя): вместо выбора ОДНОЙ стороны по большему score —
            # как только хотя бы одна сторона проходит порог, открываем ОБЕ
            # стороны сразу (BUY и SELL). У каждой ноги дальше — совершенно
            # обычный SL/TP/BE/трейлинг/Profit Lock, как у любой другой сделки:
            # убыточная нога ограничена своим стоп-лоссом, прибыльная
            # закрывается как обычно (TP/трейлинг/Profit Lock).
            if profile.get("hedge_both_directions", False) and (buy_ok or sell_ok):
                hedge_directions = [1, -1]
                score = max(buy_score, sell_score)
            elif buy_ok and buy_score >= sell_score:
                direction, score = 1, buy_score
            elif sell_ok and sell_score > buy_score:
                direction, score = -1, sell_score
            else:
                sym_state.last_reject_reason = (
                    f"Score BUY={buy_score:.1f} SELL={sell_score:.1f} < {adaptive_threshold:.1f}"
                    f" (порог профиля {profile['min_score_to_trade']})"
                )
                return
        else:
            sig = df_ind.iloc[-1]
            tol = rm.eff_points_threshold(cfg.PULLBACK_TOLERANCE_POINTS, 0.10, atr_value, point)
            buy_pattern = (pullback_breakout_ok(df_ind, 1, point, tol) and ema_stack_ok(sig, 1)
                           and is_bullish_confirmation(sig, cfg))
            sell_pattern = (pullback_breakout_ok(df_ind, -1, point, tol) and ema_stack_ok(sig, -1)
                            and is_bearish_confirmation(sig, cfg))
            if buy_pattern:
                direction, score = 1, buy_score
            elif sell_pattern:
                direction, score = -1, sell_score
            else:
                sym_state.last_reject_reason = "Паттерн Pullback+PA не найден"
                return

    directions_to_open = hedge_directions if hedge_directions is not None else [direction]

    # Анти-дребезг (см. reversal_cooldown_ok) не применяется в хедж-режиме — мы
    # НАМЕРЕННО открываем обе стороны сразу, а не разворачиваемся против недавно
    # закрытой сделки.
    if hedge_directions is None and not rm.reversal_cooldown_ok(sym_state, direction):
        sym_state.last_reject_reason = f"Анти-дребезг: жду {cfg.MIN_BARS_BETWEEN_REVERSAL} бар(а)"
        return

    # Хедж открывает 2 позиции за раз — нужно, чтобы оба слота были свободны
    # (иначе получится однобокая "хеджированная" сделка, которая на деле хедж не даёт).
    if hedge_directions is not None:
        free_slots = profile["max_open_positions"] - rm.count_open_positions(symbol, positions=all_positions)
        if free_slots < len(directions_to_open):
            sym_state.last_reject_reason = (
                f"Хедж (обе стороны): не хватает свободных слотов сделок ({free_slots} из "
                f"{len(directions_to_open)} нужных)"
            )
            return

    sl_dist = atr_value * profile["atr_sl_multiplier"] * (cfg.NEWS_VOLATILITY_SL_BOOST if is_news_entry else 1.0)
    lot = rm.calc_lot(symbol, sl_dist, equity, sym_state)
    if getattr(cfg, "USE_MAX_PROFIT_RIDE", False):
        # "Тянуть максимальную прибыль" — без фиксированного TP, сделку от сих
        # пор ведёт ТОЛЬКО BE/ATR-трейлинг/Profit Lock (см. tm.manage_open_positions),
        # закрытие происходит, когда цена разворачивается и выбивает трейлинг-стоп.
        tp_dist = 0.0
    elif profile["use_money_tp"]:
        # sl_dist передаём, чтобы calc_tp_distance_money гарантированно поднял
        # TP минимум до SL*MIN_RISK_REWARD_RATIO, если денежная цель профиля
        # (target_profit_money) окажется слишком скромной относительно риска.
        tp_dist = rm.calc_tp_distance_money(symbol, lot, profile["target_profit_money"], atr_value, point, sl_dist)
    else:
        tp_dist = rm.calc_tp_distance(sl_dist, atr_value, point)

    if not rm.spread_cost_ok(symbol, lot, tp_dist, profile["ignore_soft_filters"]):
        sym_state.last_reject_reason = "Спред съедает слишком большую часть TP"
        return

    info = mt5.symbol_info(symbol)
    new_trade_risk_pct = 0.0
    if equity > 0 and info and info.trade_tick_value > 0 and info.trade_tick_size > 0:
        new_trade_risk_pct = (sl_dist / info.trade_tick_size) * info.trade_tick_value * lot / equity * 100.0

    # Используем уже полученный в начале ЭТОЙ итерации acc_info вместо
    # повторного запроса к MT5 (fallback на свежий запрос, если функцию
    # вызвали без него — на случай прямого вызова откуда-то ещё).
    risk_acc_info = acc_info if acc_info is not None else mt5c.get_account_info()
    open_risk_pct = rm.get_open_risk_percent(risk_acc_info, positions=all_positions)
    # В хедже считаем риск по ОБЕИМ ногам (консервативно — реальный чистый риск
    # обычно меньше, т.к. ноги в противоположных направлениях, но так надёжнее).
    if open_risk_pct + new_trade_risk_pct * len(directions_to_open) > profile["max_total_risk_pct"]:
        sym_state.last_reject_reason = "Превышен общий риск по открытым позициям"
        return

    opened_count = 0
    for d in directions_to_open:
        leg_score = (buy_score if d == 1 else sell_score) if hedge_directions is not None else score
        ok = tm.execute_market_order(symbol, d, lot, sl_dist, tp_dist, leg_score, point)
        if ok:
            opened_count += 1
            acc_state.trades_today += 1
            dir_txt = "BUY" if d == 1 else "SELL"
            hedge_txt = " [хедж]" if hedge_directions is not None else ""
            control.push_notification(
                "Сделка открыта",
                f"{symbol} {dir_txt} лот {lot:.2f} | score {leg_score:.1f}{hedge_txt}",
            )
    if opened_count > 0:
        sym_state.last_reject_reason = "OK" if opened_count == len(directions_to_open) else "OK (частично, хедж)"


def _close_one_position(pos):
    if cfg.LIVE_TRADING:
        result = mt5c.close_position_partial(pos, pos.volume)
        log.info("Позиция %s (%s) закрыта вручную с дашборда: %s", pos.ticket, pos.symbol, result)
    else:
        log.info("[DRY-RUN] Дашборд запросил закрытие %s (%s), но LIVE_TRADING=False — ничего не отправлено.",
                  pos.ticket, pos.symbol)


def process_close_requests(all_positions=None):
    """Забирает заявки на закрытие позиций из дашборда/телефона (control.py)
    и исполняет их здесь, в главном потоке — единственном, кто трогает MT5.
    all_positions: уже полученный в начале ЭТОЙ итерации список (см. main()) —
    экономит отдельный запрос к MT5, если заявок на закрытие нет (обычный случай)."""
    # "Закрыть все сделки" — одной кнопкой, АБСОЛЮТНО ВСЕ позиции счёта
    # (бот + открытые вручную), с подтверждением на стороне интерфейса.
    if control.is_close_all_requested():
        positions = all_positions if all_positions is not None else mt5c.get_open_positions()
        for pos in positions:
            try:
                _close_one_position(pos)
            except Exception as e:
                log.exception("Не удалось закрыть позицию %s при 'Закрыть все': %s", pos.ticket, e)
        control.clear_close_all_requested()

    # "Закрыть прибыльные" / "Закрыть убыточные" — те же ВСЕ позиции счёта
    # (бот + открытые вручную), отфильтрованные по текущему профиту (p.profit —
    # именно "плавающая" прибыль/убыток по текущей цене, как в терминале MT5).
    if control.is_close_profitable_requested():
        positions = all_positions if all_positions is not None else mt5c.get_open_positions()
        for pos in positions:
            if pos.profit < 0:
                continue
            try:
                _close_one_position(pos)
            except Exception as e:
                log.exception("Не удалось закрыть прибыльную позицию %s: %s", pos.ticket, e)
        control.clear_close_profitable_requested()

    if control.is_close_losing_requested():
        positions = all_positions if all_positions is not None else mt5c.get_open_positions()
        for pos in positions:
            if pos.profit >= 0:
                continue
            try:
                _close_one_position(pos)
            except Exception as e:
                log.exception("Не удалось закрыть убыточную позицию %s: %s", pos.ticket, e)
        control.clear_close_losing_requested()

    while not control.close_requests.empty():
        ticket = control.close_requests.get()
        # Вкладка "Сделки" теперь показывает ВСЕ позиции счёта (не только бота),
        # поэтому и закрытие по кнопке должно находить позицию по любому magic —
        # иначе кнопка "Закрыть" молча ничего не делает для ручных сделок.
        positions = all_positions if all_positions is not None else mt5c.get_open_positions()
        pos = next((p for p in positions if p.ticket == ticket), None)
        if pos is None:
            log.warning("Дашборд запросил закрытие тикета %s, но такая позиция не найдена.", ticket)
            continue
        _close_one_position(pos)


def build_snapshot(acc_info, acc_state: AccountState, sym_states: dict, all_positions=None) -> dict:
    # ВСЕ открытые позиции счёта (не только этого бота по magic number) — п.4
    # "синхронизация с MetaTrader": хотим видеть в программе то же самое, что
    # в терминале MT5, включая сделки, открытые вручную.
    # all_positions — уже полученный в начале ЭТОЙ итерации список (см. main()),
    # чтобы не запрашивать MT5 второй раз только ради снимка для дашборда.
    positions = all_positions if all_positions is not None else mt5c.get_open_positions()
    positions_data = []
    for p in positions:
        tick = mt5c.get_tick(p.symbol)
        current_price = (tick.bid if p.type == mt5.POSITION_TYPE_BUY else tick.ask) if tick else 0.0
        positions_data.append({
            "ticket": p.ticket,
            "symbol": p.symbol,
            "type": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
            "volume": p.volume,
            "price_open": p.price_open,
            "price_current": current_price,
            "sl": p.sl,
            "tp": p.tp,
            "profit": p.profit,
            # Время открытия сделки — как в терминале MT5 (p.time — unix-время сервера)
            "open_time": datetime.fromtimestamp(p.time).strftime("%d.%m %H:%M:%S") if p.time else "-",
            "is_bot": p.magic == cfg.MAGIC_NUMBER,
        })

    try:
        _refresh_mt5_history_cache()
    except Exception as e:
        log.warning("Не удалось обновить кэш истории MT5 (не критично, повторим позже): %s", e)

    try:
        _refresh_available_symbols_cache()
    except Exception as e:
        log.warning("Не удалось обновить список символов брокера (не критично, повторим позже): %s", e)

    trade_permission = _check_trading_permission()

    lot_overrides = control.get_lot_overrides()
    symbols_data = {
        sym: {
            "buy_score": st.last_buy_score,
            "sell_score": st.last_sell_score,
            "regime": st.current_regime,
            "consecutive_losses": st.consecutive_losses,
            "paused_until": st.pause_until.strftime("%H:%M:%S %d.%m") if rm.loss_streak_pause_active(st) else None,
            "ai_direction": st.ext_last_direction,
            "ai_confidence": st.ext_last_confidence,
            "reject_reason": st.last_reject_reason,
            "last_trade_result": st.last_trade_result,
            "enabled": control.is_symbol_enabled(sym),
            "lot_override": lot_overrides.get(sym, 0),
            "learning_status": al.learning_status_text(st),
            "recent_closes": list(st.recent_closes),
            # Собственная стратегия программы (custom_strategy.py) — для
            # отображения на вкладке "Символы".
            "custom_score": st.last_custom_score,
            # Доп. подтверждение индикаторами (multi_indicator.py) — тоже для отображения.
            "multi_indicator_score": st.last_multi_indicator_score,
        }
        for sym, st in sym_states.items()
    }

    equity = acc_info.equity
    win_rate = (acc_state.win_trades / acc_state.total_trades * 100.0) if acc_state.total_trades > 0 else 0.0
    profit_factor = (acc_state.gross_profit / abs(acc_state.gross_loss)) if acc_state.gross_loss < 0 else 0.0
    day_pnl_pct = ((equity - acc_state.day_start_equity) / acc_state.day_start_equity * 100.0
                   if acc_state.day_start_equity > 0 else 0.0)
    dd_pct = ((acc_state.peak_equity - equity) / acc_state.peak_equity * 100.0
              if acc_state.peak_equity > 0 else 0.0)

    effective_profile = control.get_risk_profile() or cfg.RISK_PROFILE
    effective_mode = control.get_trading_mode() or cfg.TRADING_MODE

    return {
        "account": {
            "login": acc_info.login,
            "server": acc_info.server,
            "balance": acc_info.balance,
            "equity": equity,
            "currency": acc_info.currency,
        },
        "live_trading": cfg.LIVE_TRADING,
        "trading_mode": effective_mode.value,
        "risk_profile": effective_profile.value,
        "trades_today": acc_state.trades_today,
        "symbols": symbols_data,
        "positions": positions_data,
        "stats": {
            "total_trades": acc_state.total_trades,
            "win_trades": acc_state.win_trades,
            "win_rate": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "gross_profit": round(acc_state.gross_profit, 2),
            "gross_loss": round(acc_state.gross_loss, 2),
            "day_pnl_pct": round(day_pnl_pct, 2),
            "drawdown_pct": round(dd_pct, 2),
        },
        "equity_history": control.get_equity_history(),
        "last_config_reload": control.get_last_config_reload(),
        "updated_at": datetime.now().strftime("%H:%M:%S"),
        # Синхронизация с MetaTrader (п.4): полная история сделок из MT5 (все
        # magic number, за HISTORY_SYNC_DAYS дней) — см. _refresh_mt5_history_cache().
        "mt5_history": _history_cache["deals"],
        "mt5_history_stats": _history_cache["stats"],
        # Список ДОСТУПНЫХ у брокера символов (п.3: синхронизация пар с MT5) —
        # используется вкладкой "Символы" для выпадающего списка при добавлении.
        "available_symbols": _symbols_cache["list"],
        # Диагностика "сделки не открываются" — см. mt5c.trading_permission_status().
        "trade_permission": trade_permission,
    }


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


_config_path = os.path.abspath(cfg.__file__)
_config_mtime = os.path.getmtime(_config_path) if os.path.exists(_config_path) else 0.0
_last_config_check = datetime.now()


def reload_config_if_changed(sym_states: dict):
    """Автообновление: если config.py поменяли руками, пока бот работает —
    подхватываем изменения без перезапуска python main.py. Проверяем не
    чаще раза в CONFIG_RELOAD_CHECK_SECONDS (не дёргаем диск на каждой итерации)."""
    global _config_mtime, _last_config_check
    if not cfg.USE_CONFIG_HOT_RELOAD:
        return
    if (datetime.now() - _last_config_check).total_seconds() < cfg.CONFIG_RELOAD_CHECK_SECONDS:
        return
    _last_config_check = datetime.now()

    try:
        mtime = os.path.getmtime(_config_path)
    except OSError:
        return
    if mtime == _config_mtime:
        return
    _config_mtime = mtime

    old_magic = cfg.MAGIC_NUMBER
    try:
        importlib.reload(cfg)
    except Exception as e:
        log.exception("config.py изменился, но перечитать не удалось (ошибка в файле?): %s", e)
        return

    # Секреты (MT5_PASSWORD/API-ключи) на диске хранятся зашифрованными (см.
    # secure_store.py) — reload() выше перечитал их с диска СНОВА в виде
    # "enc:...", поэтому расшифровываем заново тем же паролем входа, что был
    # введён при старте (desktop-приложение или консольный ввод в main()).
    session_password = control.get_session_password()
    if session_password:
        try:
            secure_store.unlock_config(cfg, session_password)
        except ValueError as e:
            log.error("Не удалось расшифровать секреты config.py после hot-reload: %s "
                      "(MT5-пароль/API-ключи могут быть недоступны, пока конфиг не перечитается верно)", e)

    if cfg.MAGIC_NUMBER != old_magic:
        log.warning("MAGIC_NUMBER поменялся на лету (%s -> %s) — не делай так во время работы, "
                    "программа перестанет видеть уже открытые этим процессом позиции.",
                    old_magic, cfg.MAGIC_NUMBER)

    # reload() создаёт НОВЫЕ классы Enum (RiskProfile/TradingMode) — старые
    # переопределения профиля/режима с дашборда ссылались бы на "старые" классы
    # и перестали бы совпадать при сравнении. Поэтому сбрасываем их на дефолт
    # из свежего config.py; если нужно — просто выбери профиль/режим на дашборде заново.
    control.set_risk_profile(None)
    control.set_trading_mode(None)

    # Новые символы, добавленные в SYMBOLS, подключаются на лету.
    # Убранные из SYMBOLS — не удаляются из обработки: их открытые сделки
    # продолжают вестись (BE/трейлинг), просто новых входов по ним не будет
    # (это уже проверяется через control.is_symbol_enabled/список ниже не трогаем).
    for sym in cfg.SYMBOLS:
        if sym not in sym_states:
            if mt5c.ensure_symbol(sym):
                sym_states[sym] = SymbolState(symbol=sym)
                log.info("Новый символ %s подключен на лету (добавлен в config.py).", sym)
            else:
                log.warning("Новый символ %s из config.py недоступен у брокера — пропущен.", sym)

    ts = datetime.now().strftime("%H:%M:%S")
    control.set_last_config_reload(ts)
    log.info("config.py изменился на диске — настройки перечитаны на лету в %s, без перезапуска.", ts)


def start_dashboard_thread():
    from web_dashboard import run_dashboard
    t = threading.Thread(target=run_dashboard, daemon=True)
    t.start()
    ip = _local_ip()
    log.info("Веб-дашборд запущен: http://%s:%s (с телефона — по той же Wi-Fi сети)", ip, cfg.DASHBOARD_PORT)


def print_status(sym_states: dict, acc_state: AccountState, equity: float):
    effective_profile = control.get_risk_profile() or cfg.RISK_PROFILE
    effective_mode = control.get_trading_mode() or cfg.TRADING_MODE
    print("-" * 78)
    print(f"Equity: {equity:.2f} | Сделок сегодня: {acc_state.trades_today} | "
          f"LIVE_TRADING={cfg.LIVE_TRADING} | режим={effective_mode.value} | профиль={effective_profile.value}")
    for sym, st in sym_states.items():
        print(f"  [{sym}] Score BUY {st.last_buy_score:.1f} / SELL {st.last_sell_score:.1f} | "
              f"Режим рынка: {st.current_regime} | Серия убытков: {st.consecutive_losses} | "
              f"AI: {st.ext_last_direction or '-'} ({st.ext_last_confidence * 100:.0f}%) | "
              f"Отказ: {st.last_reject_reason} | Посл. сделка: {st.last_trade_result}")
    print("-" * 78)


def _cli_unlock_secrets_if_needed():
    """Если секреты config.py зашифрованы (после того как их хоть раз сохраняли
    через desktop-приложение с экраном входа — см. secure_store.py) — при
    запуске НАПРЯМУЮ (`python main.py`, без desktop_app.py) один раз просит
    пароль в консоли, чтобы их расшифровать. Если ничего не зашифровано
    (обычный случай, desktop-приложением ещё не пользовались) — ничего не
    спрашивает, поведение как раньше."""
    if not secure_store.has_encrypted_secrets(cfg):
        return
    import getpass
    for attempt in range(3):
        password = getpass.getpass("Пароль входа (для расшифровки config.py): ")
        try:
            secure_store.unlock_config(cfg, password)
            control.set_session_password(password)
            return
        except ValueError as e:
            print(f"Ошибка: {e}")
    raise SystemExit("Не удалось расшифровать config.py — неверный пароль входа. Останов.")


def _sleep_interruptible(seconds: float, stop_event):
    """Как time.sleep(), но если передан stop_event (десктоп-приложение нажало
    "Стоп") — просыпается СРАЗУ, а не ждёт полный POLL_SECONDS. Из CLI
    (stop_event=None) ведёт себя как обычный time.sleep()."""
    if stop_event is None:
        time.sleep(seconds)
    else:
        stop_event.wait(timeout=seconds)


def _fast_position_monitor(sym_states, stop_event, total_seconds: float):
    """Заменяет собой финальный sleep(POLL_SECONDS) главного цикла: вместо
    того чтобы просто ждать, каждые POSITION_MONITOR_SECONDS дополнительно
    подтягивает SL уже открытых позиций (BE/трейлинг/Profit Lock), используя
    ПОСЛЕДНИЙ ИЗВЕСТНЫЙ ATR по символу (sym_state.last_atr_value) — без
    дорогого повторного запроса баров/индикаторов у MT5 (только облегчённые
    get_open_positions()/get_tick()).

    Зачем: жалоба пользователя — "видел +360$ плавающего профита, а
    закрылось на копейки". При нескольких одновременно торгуемых парах
    полный проход process_symbol по всем парам сам по себе не мгновенный
    (сетевые запросы к MT5), и резкий разворот цены между полными проходами
    раньше оставался незамеченным до следующего цикла — Profit Lock не успевал
    среагировать, сделку защищал только маленький Break Even. Теперь SL
    подтягивается гораздо чаще, независимо от того, сколько пар настроено."""
    step = max(0.5, getattr(cfg, "POSITION_MONITOR_SECONDS", 1))
    elapsed = 0.0
    while elapsed < total_seconds:
        if stop_event is not None and stop_event.is_set():
            return
        this_step = min(step, total_seconds - elapsed)
        _sleep_interruptible(this_step, stop_event)
        elapsed += this_step
        if stop_event is not None and stop_event.is_set():
            return
        try:
            positions = mt5c.get_open_positions()
            if not positions:
                continue
            by_symbol = {}
            for p in positions:
                by_symbol.setdefault(p.symbol, []).append(p)
            for sym, st in sym_states.items():
                sym_positions = by_symbol.get(sym)
                if not sym_positions:
                    continue
                atr_value = getattr(st, "last_atr_value", 0.0)
                if atr_value <= 0:
                    continue  # ещё не было ни одного полного прохода по этому символу
                point = mt5c.get_symbol_point(sym)
                tm.manage_open_positions(sym, atr_value, point, positions=sym_positions)
        except Exception as e:
            log.exception("Ошибка быстрого мониторинга позиций: %s", e)


def main(stop_event=None, start_dashboard: bool = True):
    """
    stop_event: threading.Event | None — передаётся desktop_app.py (GUI), чтобы
        можно было остановить торговый цикл кнопкой "Стоп" БЕЗ завершения всего
        процесса программы (в отличие от Ctrl+C, который работает только в CLI).
        При запуске через `python main.py` остаётся None — поведение как раньше.
    start_dashboard: если GUI уже подняло веб-дашборд один раз при старте
        приложения (см. desktop_app.py) — сюда передаётся False, чтобы повторные
        нажатия "Старт" не пытались занять порт 5000 второй раз (Flask-сервер,
        запущенный при первом старте, не останавливается вместе с ботом).
    """
    log.info("Запуск AI Scalper Standalone | LIVE_TRADING=%s | режим=%s | профиль=%s | символы=%s",
              cfg.LIVE_TRADING, cfg.TRADING_MODE.value, cfg.RISK_PROFILE.value, cfg.SYMBOLS)
    if not cfg.LIVE_TRADING:
        log.warning("LIVE_TRADING=False — это СУХОЙ ПРОГОН, реальные ордера отправляться НЕ будут.")

    # Если main.py запущен напрямую (не через desktop_app.py, где пароль уже
    # спросили на экране входа) — и секреты в config.py зашифрованы, спросим
    # пароль здесь. Если ничего не зашифровано — просто ничего не произойдёт.
    if stop_event is None:
        _cli_unlock_secrets_if_needed()

    acc = mt5c.connect()
    acc_state = AccountState(day_start_equity=acc.equity, peak_equity=acc.equity, last_trade_day=datetime.now())
    sym_states = init_states()
    if not sym_states:
        log.error("Ни один символ из SYMBOLS не доступен у брокера — нечего торговать. Останов.")
        mt5c.disconnect()
        return

    if cfg.USE_WEB_DASHBOARD:
        if start_dashboard:
            start_dashboard_thread()
        control.add_equity_sample(datetime.now().strftime("%H:%M:%S"), acc.equity)

    last_status_print = datetime.now() - timedelta(seconds=999)
    connect_failures = 0
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                log.info("Остановлено (запрос от десктоп-приложения).")
                break

            try:
                acc_info = mt5c.get_account_info()
                if acc_info is None:
                    connect_failures += 1
                    log.error("Потеряно соединение с MT5 (терминал закрыт/разлогинен?) — жду и пробую снова... "
                              "(%d/%d до автопереподключения)", connect_failures, cfg.RECONNECT_AFTER_FAILURES)
                    if cfg.USE_AUTO_RECONNECT and connect_failures >= cfg.RECONNECT_AFTER_FAILURES:
                        log.warning("Пробую автоматически переподключиться к MT5...")
                        try:
                            mt5c.disconnect()
                            mt5c.connect()
                            connect_failures = 0
                            log.info("Автопереподключение к MT5 удалось.")
                        except Exception as e:
                            log.error("Автопереподключение не удалось: %s. Убедись, что терминал MT5 открыт.", e)
                    _sleep_interruptible(cfg.POLL_SECONDS, stop_event)
                    continue
                connect_failures = 0

                reload_config_if_changed(sym_states)

                equity = acc_info.equity
                check_new_day(acc_state, equity)
                process_closed_deals(acc_state, sym_states)

                # Ускорение цикла: ОДИН запрос всех открытых позиций на всю
                # итерацию вместо отдельного запроса на каждый символ (было:
                # N лишних обращений к MT5-терминалу за проход по symbols —
                # основная причина задержки в 100+ мс при нескольких парах).
                all_positions = mt5c.get_open_positions()

                process_close_requests(all_positions)

                for sym, st in sym_states.items():
                    try:
                        process_symbol(sym, st, acc_state, equity, acc_info, all_positions)
                    except Exception as e:
                        log.exception("Ошибка обработки %s: %s", sym, e)

                if cfg.USE_WEB_DASHBOARD:
                    ds.update_snapshot(build_snapshot(acc_info, acc_state, sym_states, all_positions))

                if (datetime.now() - last_status_print).total_seconds() >= 30:
                    print_status(sym_states, acc_state, equity)
                    if cfg.USE_WEB_DASHBOARD:
                        control.add_equity_sample(datetime.now().strftime("%H:%M:%S"), equity)
                    last_status_print = datetime.now()
            except Exception as e:
                # Защита от падения цикла целиком: одна неожиданная ошибка на итерации
                # не должна убивать весь процесс — логируем и пробуем на следующем опросе.
                log.exception("Неожиданная ошибка в главном цикле (продолжаю работу): %s", e)

            _fast_position_monitor(sym_states, stop_event, cfg.POLL_SECONDS)
    except KeyboardInterrupt:
        log.info("Остановлено пользователем (Ctrl+C).")
    finally:
        mt5c.disconnect()


if __name__ == "__main__":
    main()
