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
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5

import importlib
import os

import config as cfg
import execution
import incident
import pending_orders as pending
import mt5_connector as mt5c
import risk_manager as rm
import trade_manager as tm
import trade_journal as tj
import signal_engine as se
import strategy_dispatcher as dispatcher
import market_regime as mr
import ai_signal as ai
import auto_learning as al
import custom_strategy as cs
import strategies as strat
import multi_indicator as mi
import market_hours
import news_calendar
import remote_settings
import risk_state
import scan_rotation
import symbol_cache
import symbol_picker
import telegram_signals
import telegram_reader
import dashboard_state as ds
import runtime_events
import pretrade_gate
import secure_store
from control import control
from indicators import (
    add_all_indicators, pullback_breakout_ok, ema_stack_ok,
    is_bullish_confirmation, is_bearish_confirmation,
)
from indicators import atr as atr_of
import reservations
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


# =====================================================================
# ATR ДЛЯ ВЕДЕНИЯ ОТКРЫТОЙ ПОЗИЦИИ — M5 ИЛИ M1
# =====================================================================
# symbol -> (время последней минутной свечи, ATR в цене). Кэш нужен, чтобы
# не запрашивать минутки у терминала на каждом такте: быстрый монитор ходит
# раз в секунду, а новая минутная свеча появляется раз в минуту.
_m1_atr_cache: dict = {}


def management_atr(symbol: str, m5_atr: float) -> float:
    """ATR, по которому считаются расстояния при ведении открытой сделки.

    ЗАЧЕМ ЭТО НУЖНО. Вход определяется на M5 — это не меняется. Но у ведения
    позиции задача другая: не «куда идёт рынок», а «где сейчас цена». M5-ATR
    для этого грубоват — он описывает размах пятиминутки, а решение о
    подтягивании стопа принимается каждую секунду.

    ЧЕГО ЭТА ФУНКЦИЯ НЕ ДЕЛАЕТ. Она НЕ трогает первоначальный стоп-лосс и НЕ
    трогает вход: и то и другое посчитано при открытии по M5 и остаётся как
    есть. Меняются только расстояния трейлинга и пороги защиты прибыли.

    При любой неудаче — нет связи, мало свечей, нечисловой ATR — честно
    возвращается M5-ATR. Отсутствие минуток не имеет права остановить
    ведение позиции."""
    if not getattr(cfg, "USE_M1_POSITION_MANAGEMENT", False):
        return m5_atr
    try:
        период = int(getattr(cfg, "M1_ATR_PERIOD", 14) or 14)
        df = mt5c.get_rates_df(symbol, "M1", count=max(60, период * 4))
        if df is None or len(df) < период + 1:
            return m5_atr
        последняя = df.iloc[-1]["time"]
        было = _m1_atr_cache.get(symbol)
        if было is not None and было[0] == последняя:
            return было[1]
        значение = float(atr_of(df, период).iloc[-1])
        if not (значение > 0):
            return m5_atr
        _m1_atr_cache[symbol] = (последняя, значение)
        return значение
    except Exception as e:
        log.debug("M1-ATR по %s недоступен (%s) — веду позицию по M5.", symbol, e)
        return m5_atr


def exit_reason_for(deal, карточка: dict) -> str:
    """Чем именно закрылась сделка.

    Знание разделено на две половины, и обе нужны:

      * БРОКЕР знает, ЧТО сработало — стоп, цель или закрытие по команде.
        Это deal.reason, и подменить его нашей догадкой нельзя;
      * МЫ знаем, КТО поставил тот стоп и ту цель, потому что это делали
        четыре разных механизма (см. trade_manager).

    Причина выхода = ответ брокера, уточнённый нашим знанием. Если брокер
    молчит или отдаёт незнакомое значение, честно пишем UNKNOWN, а не
    подставляем правдоподобное."""
    import MetaTrader5 as mt5

    reason = getattr(deal, "reason", None)
    по_стопу = карточка.get("exit_reason") or tm.ПРИЧИНА_СТОП
    по_цели = карточка.get("tp_reason") or tm.ПРИЧИНА_ЦЕЛЬ

    if reason == getattr(mt5, "DEAL_REASON_SL", -1):
        return по_стопу
    if reason == getattr(mt5, "DEAL_REASON_TP", -2):
        return по_цели
    if reason in (getattr(mt5, "DEAL_REASON_CLIENT", -3),
                  getattr(mt5, "DEAL_REASON_MOBILE", -4),
                  getattr(mt5, "DEAL_REASON_WEB", -5)):
        return tm.ПРИЧИНА_РУЧНОЕ
    if reason == getattr(mt5, "DEAL_REASON_EXPERT", -6):
        # Закрыли мы сами: спасение в безубыток, частичное закрытие или
        # кнопка в интерфейсе — все три помечают себя в trade_manager.
        return по_стопу
    if reason == getattr(mt5, "DEAL_REASON_SO", -7):
        return "STOP_OUT"
    return tm.ПРИЧИНА_НЕИЗВЕСТНО


# ПРОВОДКА В ДИСПЕТЧЕР.
#
# Окно шага меряется временем, когда программа МОГЛА торговать, и числом
# закрытых сделок. Ни то, ни другое диспетчер сам добыть не может — их
# передаёт отсюда торговый цикл.
#
# Состояние держится в памяти и пишется на диск не чаще раза в минуту:
# круг цикла идёт каждые несколько секунд, и запись на каждом круге
# истёрла бы диск ради нескольких секунд точности.
_дисп = {"каталог": None, "состояние": None, "последний_круг": 0.0,
         "последняя_запись": 0.0}
СЕКУНД_МЕЖДУ_ЗАПИСЯМИ = 60


def _дисп_готов() -> bool:
    """Подтянуть каталог и состояние один раз. Ошибка — молча не считаем.

    Диспетчер ведёт учёт окна и НЕ управляет торговлей. Если он не
    прочитался, торговля обязана продолжаться как ни в чём не бывало:
    сломанный учёт не повод останавливать работу."""
    if _дисп["каталог"] is None:
        try:
            _дисп["каталог"] = dispatcher.прочитать_каталог()
            _дисп["состояние"] = dispatcher.прочитать_состояние()
        except Exception as e:  # noqa: BLE001
            log.debug("Диспетчер не подтянулся: %s", e)
            _дисп["каталог"] = {}
            _дисп["состояние"] = dispatcher.пустое_состояние()
    return bool(_дисп["каталог"])


def _дисп_записать(принудительно: bool = False):
    сейчас = time.time()
    if not принудительно and сейчас - _дисп["последняя_запись"] < СЕКУНД_МЕЖДУ_ЗАПИСЯМИ:
        return
    _дисп["последняя_запись"] = сейчас
    try:
        dispatcher.записать_состояние(_дисп["состояние"])
    except Exception as e:  # noqa: BLE001
        log.debug("Состояние диспетчера не записано: %s", e)


def учесть_круг_в_диспетчере(sym_states: dict):
    """Один круг цикла: сколько времени прошло и шло ли оно в зачёт."""
    сейчас = time.time()
    прошло = сейчас - (_дисп["последний_круг"] or сейчас)
    _дисп["последний_круг"] = сейчас
    if not _дисп_готов() or прошло <= 0:
        return
    # Круг длиннее пяти минут означает, что программу усыпляли или
    # терминал не отвечал. Такое время в зачёт не идёт: мы не знаем,
    # могли ли мы торговать всё это время.
    if прошло > 300:
        return
    запрет_везде = all(
        control.вход_запрещён(имя)[0] for имя in (sym_states or {"": None})
    ) if sym_states else True
    идёт, почему = dispatcher.помех_нет(
        цикл_живой=True,
        реальная_торговля=bool(getattr(cfg, "LIVE_TRADING", False)),
        барьер_открыт=(pretrade_gate.открыт()
                       or not pretrade_gate.включён(cfg)),
        пар_отобрано=len(sym_states or {}),
        запрет_везде=запрет_везде)
    if not идёт:
        return
    dispatcher.учесть_работу(_дисп["каталог"], _дисп["состояние"], прошло, True)
    _дисп_записать()


def учесть_закрытие_в_диспетчере():
    """Брокер подтвердил закрытие — прибавляем сделку окну шага."""
    if not _дисп_готов():
        return
    try:
        dispatcher.учесть_закрытую_сделку(_дисп["каталог"], _дисп["состояние"])
        _дисп_записать(принудительно=True)
    except Exception as e:  # noqa: BLE001
        log.debug("Закрытая сделка не учтена диспетчером: %s", e)


def process_closed_deals(acc_state: AccountState, sym_states: dict):
    """Аналог OnTradeTransaction в MQL5: опрашиваем историю сделок за последние
    сутки и реагируем на новые ЗАКРЫТИЯ позиций с нашим magic number."""
    from_time = datetime.now() - timedelta(days=1)
    deals = mt5.history_deals_get(from_time, datetime.now())
    if deals is None:
        return

    learning_changed = False
    for d in deals:
        if d.magic != cfg.MAGIC_NUMBER:
            continue
        if d.entry not in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY):
            continue
        if d.ticket in _processed_deal_tickets:
            continue
        _processed_deal_tickets.add(d.ticket)
        # ОКНО ШАГА СЧИТАЕТСЯ ЗДЕСЬ. Это единственное место, где закрытие
        # подтверждено брокером и не посчитано дважды: тикеты уже отсеяны
        # выше через _processed_deal_tickets. Прибыль сделки диспетчеру не
        # передаётся — окно меряется числом сделок, а не деньгами.
        учесть_закрытие_в_диспетчере()

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

        # И отдельно — пик прибыли этой сделки в пунктах: сколько рынок
        # РЕАЛЬНО дал, прежде чем развернуться. По медиане таких пиков бот сам
        # подбирает, куда ставить тейк-профит (learned_profit_points).
        peak = tm.pop_closed_peak(d.position_id)
        if peak is not None:
            al.record_trade_peak(sym_state, peak)
        learning_changed = True

        # ЖУРНАЛ ВЫХОДОВ. Пишется здесь и только здесь: это единственное
        # место, где известно И чем сделка кончилась (брокер), И как она
        # жила (наши замеры за время ведения позиции).
        try:
            карточка = tm.pop_closed_journal(d.position_id) or {}
            риск_пт = float(карточка.get("initial_r_points", 0) or 0)
            цена_пт = mt5c.get_symbol_point(d.symbol) or 0.0
            прибыль_r = ""
            if риск_пт > 0 and цена_пт > 0:
                # Результат в долях риска считается по ЦЕНЕ, а не по деньгам:
                # деньги зависят ещё и от лота, и сделки с разным объёмом
                # оказались бы несравнимыми.
                пунктов = abs(d.price - float(карточка.get("entry", d.price))) / цена_пт
                прибыль_r = round((пунктов / риск_пт) * (1 if profit >= 0 else -1), 3)
            tm.log_exit_journal({
                "time": datetime.now().isoformat(timespec="seconds"),
                "symbol": d.symbol,
                "ticket": d.position_id,
                "direction": "SELL" if d.type == mt5.DEAL_TYPE_SELL else "BUY",
                "entry": карточка.get("entry", ""),
                "exit": f"{d.price:.5f}",
                "initial_sl": карточка.get("initial_sl", ""),
                "initial_r_points": карточка.get("initial_r_points", ""),
                "max_profit_r": карточка.get("max_profit_r", ""),
                "max_loss_r": карточка.get("max_loss_r", ""),
                "time_to_mfe_sec": карточка.get("time_to_mfe_sec", ""),
                "time_to_mae_sec": карточка.get("time_to_mae_sec", ""),
                "holding_time_sec": карточка.get("holding_time_sec", ""),
                "exit_reason": exit_reason_for(d, карточка),
                "profit": f"{profit:.2f}",
                "profit_r": прибыль_r,
            })
        except Exception as e:
            # Журнал — наблюдение, а не решение. Его сбой не имеет права
            # прервать разбор закрытых сделок и остановить торговлю.
            log.warning("Журнал выходов по %s: %s", d.symbol, e)

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
            pause_minutes = rm.loss_streak_pause_minutes()
            if sym_state.consecutive_losses >= cfg.MAX_CONSECUTIVE_LOSSES:
                if pause_minutes > 0:
                    sym_state.pause_until = datetime.now() + timedelta(minutes=pause_minutes)
                    log.info("%s: серия из %d убытков подряд — пауза до %s",
                             d.symbol, sym_state.consecutive_losses, sym_state.pause_until)
                    control.push_notification(
                        "Серия убытков",
                        f"{d.symbol}: пауза по этой паре до "
                        f"{sym_state.pause_until.strftime('%H:%M %d.%m')}",
                    )
                    # Счётчик обнуляем только вместе с паузой: пауза и есть
                    # реакция на серию, после неё считаем заново.
                    sym_state.consecutive_losses = 0
                elif sym_state.consecutive_losses == cfg.MAX_CONSECUTIVE_LOSSES:
                    # Паузы нет — торговля не прерывается. Счётчик НЕ обнуляем:
                    # он держит множитель риска на нижней границе, пока не
                    # придёт прибыльная сделка. Человеку об этом говорим один
                    # раз, а не после каждого следующего убытка.
                    log.info("%s: серия из %d убытков подряд — паузы нет, "
                             "объём сделок снижен до минимального множителя",
                             d.symbol, sym_state.consecutive_losses)
                    control.push_notification(
                        "Серия убытков",
                        f"{d.symbol}: {sym_state.consecutive_losses} убытков подряд. "
                        f"Торговля продолжается, объём сделок снижен.",
                    )
        else:
            sym_state.consecutive_losses = 0

        tm.log_trade_csv("CLOSE", d.symbol, "CLOSE", d.price, 0, 0, d.volume, 0, profit)

    # Выученное сохраняем сразу после закрытия сделки, а не при выходе из
    # программы: аварийное завершение (сбой питания, "снять задачу") не должно
    # стирать накопленную статистику — иначе бот вечно остаётся в фазе
    # "копит данные". Файл маленький, закрытия сделок редкие.
    if learning_changed:
        al.save_learning_state(sym_states)


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

    # Когда позиция ОТКРЫЛАСЬ: сделка входа (DEAL_ENTRY_IN) и сделка выхода
    # (DEAL_ENTRY_OUT) — это две РАЗНЫЕ записи с общим position_id. Без этой
    # пары нельзя узнать, сколько сделка прожила, а именно время жизни сразу
    # показывает главную болезнь: сделки, умирающие через 8-10 секунд, — это
    # стоп, поставленный внутрь рыночного шума, а не «неудачный вход».
    opened_at = {}
    open_price = {}
    for d in deals:
        if d.entry == mt5.DEAL_ENTRY_IN:
            pid = getattr(d, "position_id", 0)
            if pid and (pid not in opened_at or d.time < opened_at[pid]):
                opened_at[pid] = d.time
                open_price[pid] = d.price

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
        pid = getattr(d, "position_id", 0)
        started = opened_at.get(pid)
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
            # Ниже — для разбора убытков и журнала в облаке (cloud_journal.py).
            "open_time": (datetime.fromtimestamp(started).strftime("%d.%m %H:%M:%S")
                          if started else ""),
            "open_price": open_price.get(pid, 0.0),
            "duration_sec": int(d.time - started) if started else None,
            "commission": round(getattr(d, "commission", 0.0), 2),
            "swap": round(getattr(d, "swap", 0.0), 2),
            "comment": getattr(d, "comment", ""),
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

    # Замеры для определения «рынок закрыт или неликвиден» (market_hours.py).
    # Делаются НА КАЖДОМ проходе и ДО всех фильтров: медиана спреда и признак
    # замершей цены должны копиться всегда, иначе к моменту, когда они
    # понадобятся, сравнивать будет не с чем.
    market_hours.note_spread(symbol, mt5c.get_spread_points(symbol))
    _tick_now = mt5c.get_tick(symbol)
    _tick_time = getattr(_tick_now, "time", None) if _tick_now is not None else None
    if _tick_time:
        # Пустую отметку не записываем: иначе «время не менялось» означало бы
        # замерший рынок при живых котировках.
        market_hours.note_quote(symbol, _tick_time)

    # САМАЯ ДОРОГАЯ РАБОТА В ПРОГРАММЕ — и почти всегда напрасная. Ниже
    # выкачиваются 300 баров и считаются все индикаторы: 8.4 мс на пару
    # только на счёт, плюс сам запрос к терминалу. А вход в сделку возможен
    # ТОЛЬКО на новом баре, и на M5 бар меняется раз в 300 секунд — то есть
    # из шестидесяти проходов между барами пятьдесят девять эту работу
    # выбрасывали.
    #
    # Теперь смена бара определяется делением по времени тика, который уже
    # запрошен выше (scan_rotation.bar_start) — без единого лишнего обращения
    # к терминалу. Если бар тот же И по паре нет открытой сделки, делать
    # нечего: решение всё равно будет прежним.
    #
    # Пара с ОТКРЫТОЙ сделкой этот путь не проходит никогда: ей нужен свежий
    # ATR для трейлинг-стопа, и экономить здесь — значит вести позицию вслепую.
    if getattr(cfg, "USE_LIGHT_SCAN", True):
        bar_now = scan_rotation.bar_start(
            _tick_time, scan_rotation.timeframe_seconds(cfg.TIMEFRAME))
        has_position = rm.count_open_positions(symbol, positions=all_positions) > 0
        if bar_now and not has_position and sym_state.last_scanned_bar == bar_now:
            return

    df_raw = mt5c.get_rates_df(symbol, cfg.TIMEFRAME, count=max(300, cfg.EMA_TREND_PERIOD + 50))
    if df_raw is None or len(df_raw) < cfg.EMA_SLOW_PERIOD + 5:
        sym_state.last_reject_reason = "Недостаточно данных с MT5"
        return

    # Отмечаем разобранный бар ТОЛЬКО после удачной загрузки: иначе сбой связи
    # заставил бы пропустить весь бар целиком.
    if getattr(cfg, "USE_LIGHT_SCAN", True):
        _bar_seen = scan_rotation.bar_start(
            _tick_time, scan_rotation.timeframe_seconds(cfg.TIMEFRAME))
        if _bar_seen:
            sym_state.last_scanned_bar = _bar_seen

    df_ind = add_all_indicators(df_raw, cfg)
    atr_value = float(df_ind["atr"].iloc[-1])
    sym_state.last_atr_value = atr_value  # кэш для _fast_position_monitor()

    # Ведём уже открытые позиции на КАЖДОМ опросе, не только на новый бар
    tm.manage_open_positions(symbol, management_atr(symbol, atr_value), point,
                             positions=all_positions,
                             learned_tp_points=al.learned_profit_points(sym_state, 0.0))

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

    # ЗАПРЕТ СПРАШИВАЕТСЯ ПО ЭТОМУ ИНСТРУМЕНТУ, А НЕ ВООБЩЕ.
    # Незакрытая заявка по одной паре не должна останавливать торговлю по
    # остальным: опасность — открыть вторую позицию поверх возможной
    # неучтённой, и она касается только той самой пары. См.
    # control.вход_запрещён.
    _запрещён, _почему = control.вход_запрещён(symbol)
    if _запрещён:
        sym_state.last_reject_reason = _почему
        return

    if not control.is_symbol_enabled(symbol):
        sym_state.last_reject_reason = "Пара отключена с дашборда (выбор пары)"
        return

    # Причина ИМЕННО та, которая сработала, с числами: одна фраза на три
    # разные причины («лимит/просадка/пауза») не давала понять, что
    # происходит, а две из трёх — защёлки, снимаемые перезапуском.
    blocked = rm.trading_block_reason(acc_state, sym_state, equity)
    if blocked:
        sym_state.last_reject_reason = "Торговля приостановлена: " + blocked
        return

    # Инструмент, который стабильно тянет счёт вниз, отключается САМ. Это
    # не остановка торговли: остальные инструменты работают как обычно —
    # отсекается ровно тот, который приносит убыток. См. пояснение и живые
    # числа в auto_learning.symbol_auto_off_reason().
    auto_off = al.symbol_auto_off_reason(sym_state, equity)
    if auto_off:
        sym_state.last_reject_reason = auto_off
        return
    if rm.count_open_positions(symbol, positions=all_positions) >= profile["max_open_positions"]:
        sym_state.last_reject_reason = "Достигнут лимит одновременных сделок"
        return
    # 0 = без ограничения: бот работает всю торговую сессию, сколько бы
    # сделок ни набралось. Ограничение по ЧИСЛУ сделок само по себе ничего не
    # защищает — деньги защищают дневной лимит убытка и лимит просадки, они
    # проверяются выше в rm.trading_allowed().
    max_per_day = profile.get("max_trades_per_day", 0)
    if max_per_day and acc_state.trades_today >= max_per_day:
        sym_state.last_reject_reason = "Достигнут лимит сделок за день"
        return
    if atr_value <= 0:
        sym_state.last_reject_reason = "Индикаторы не готовы"
        return

    # ДЕШЁВЫЕ ЗАПРЕТЫ — ДО НОВОСТЕЙ. Порядок здесь не косметический: расчёт
    # новостного пробоя лезет к источнику календаря и запрашивает минутные
    # свечи. Делать эту работу по паре, которая всё равно отключена или уже
    # упёрлась в лимит сделок, — значит тратить связь и время впустую. Сами
    # запреты от переноса не ослабли: они по-прежнему возвращают управление
    # до любого входа.

    # ИНСТРУМЕНТ ВЫКЛЮЧЕН ВРУЧНУЮ (владелец: «отключи торговлю золота»).
    #
    # Просто вычеркнуть из SYMBOLS мало: список пар приходит из ЧЕТЫРЁХ мест
    # сразу — config.py, поле symbols у каждого счёта в accounts.json,
    # добавление руками на вкладке «Символы», переключатель на дашборде.
    # Вычеркнуть в одном месте значит оставить три двери открытыми. Одна
    # проверка здесь закрывает все четыре.
    #
    # СТОИТ ПОСЛЕ manage_open_positions СОЗНАТЕЛЬНО. Запрет касается только
    # НОВЫХ входов. Если по выключенному инструменту уже висит открытая
    # сделка, её обязаны довести до конца: трейлинг, безубыток, частичное
    # закрытие. Поставь проверку выше — и такая сделка осталась бы вообще без
    # присмотра, со стопом на исходном месте.
    # Имя переменной намеренно НЕ `blocked`: чуть выше в этой же функции уже
    # есть `blocked` от rm.trading_block_reason (приостановка торговли по
    # счёту). Два разных запрета под одним именем в одной функции читаются
    # неверно — и проверка, написанная на такое имя, ловила не тот блок.
    symbol_blocked = rm.blocked_symbol_reason(symbol)
    if symbol_blocked:
        sym_state.last_reject_reason = symbol_blocked
        return

    # ОБЩИЙ ПОТОЛОК ЧИСЛА ОДНОВРЕМЕННЫХ СДЕЛОК — по всем парам сразу.
    #
    # max_open_positions профиля считает позиции ПО ОДНОМУ символу: при
    # десяти настроенных парах это до ста сделок одновременно, и единственной
    # общей границей остаётся max_total_risk_pct. В отчёте владельца
    # одновременно бывало до 9 открытых сделок.
    #
    # 0 = без ограничения (значение по умолчанию — владелец предпочитает
    # больше сделок, см. его решение по потолку плеча).
    max_all = int(getattr(cfg, "MAX_SIMULTANEOUS_POSITIONS", 0) or 0)
    if max_all > 0:
        # К снимку добавляются сделки, открытые РАНЬШЕ в этом же проходе:
        # снимок берётся один раз на круг и о них ещё не знает.
        open_now = (rm.count_open_positions(None, positions=all_positions)
                    + acc_state.reservations.сколько())
        if open_now >= max_all:
            sym_state.last_reject_reason = (
                f"Уже открыто {open_now} сделок при потолке {max_all} "
                f"(MAX_SIMULTANEOUS_POSITIONS)")
            return

    # Пар в работе теперь много, и главная опасность не в их числе, а в том,
    # что они НЕ независимы: EURUSD, GBPUSD и AUDUSD — это в основном одна
    # ставка против доллара. Ограничение стоит на открытых сделках, а не на
    # списке для просмотра: смотреть можно сколько угодно пар, платим мы
    # только за одновременно открытые. См. rm.currency_exposure_reason().
    same_bet = rm.currency_exposure_reason(
        symbol,
        reservations.объединить_символы(_open_symbols(all_positions),
                                        acc_state.reservations),
        getattr(cfg, "MAX_POSITIONS_PER_CURRENCY", 0))
    if same_bet:
        sym_state.last_reject_reason = same_bet
        return

    # =================================================================
    # НОВОСТНОЙ ВХОД СЧИТАЕТСЯ ЗДЕСЬ — ДО ФИЛЬТРОВ, КОТОРЫЕ САМА НОВОСТЬ
    # И ВКЛЮЧАЕТ. Владелец: «ни разу новостная не работала».
    # =================================================================
    # ПОЧЕМУ ОНА НЕ РАБОТАЛА. Проверка новостного пробоя стояла НИЖЕ по
    # тексту — после фильтра спреда, после защиты от неликвидного рынка,
    # после ролловерной паузы. А новость всегда расширяет спред: это её
    # первое и самое надёжное следствие. Получалось так:
    #
    #     вышла новость -> спред расширился -> сработал фильтр спреда ->
    #     функция вернулась -> до новостной ветки дело не дошло.
    #
    # То есть новостной режим отключался ровно в ту минуту, ради которой он
    # и существует. Сколько бы источников календаря ни было настроено и
    # какой бы порог важности ни стоял, входа не происходило никогда.
    #
    # ЧТО ОСТАЁТСЯ НА МЕСТЕ. Всё, что защищает деньги, стоит ВЫШЕ и
    # проверено до этой строки: дневной лимит убытка, лимит просадки, пауза
    # с дашборда, отключённая пара, самоотключение убыточного инструмента,
    # лимит одновременных сделок. Новость не даёт права обойти ни одну из
    # них. Снимаются ровно те фильтры, которые сама новость и вызывает.
    news_ready, news_dir, news_conf = False, 0, 0.0
    try:
        news_ready, news_dir, news_conf = news_calendar.detect_news_breakout(
            symbol, cfg.NEWS_BREAKOUT_WINDOW_MIN)
    except Exception as e:  # noqa: BLE001
        # Источник календаря отвалился — это не повод уронить весь цикл.
        # Молчать тоже нельзя: иначе «новости не работают» опять останется
        # без объяснения.
        log.warning("Новости по %s: %s", symbol, e)
        sym_state.last_news_error = str(e)

    if not rm.spread_ok(symbol, atr_value, point):
        # У новостного входа потолок спреда свой, более широкий — но он ЕСТЬ.
        if not (news_ready and rm.news_spread_ok(symbol, atr_value, point)):
            sym_state.last_reject_reason = (
                "Спред слишком широкий даже для новостного входа"
                if news_ready else "Спред слишком широкий")
            return
    if not news_ready and not rm.rollover_guard_ok(profile["ignore_soft_filters"]):
        # Ролловер — это дыра ликвидности, а не новость. Но если новость
        # ВСЁ-ТАКИ вышла в эти минуты, пропускать её незачем: причина паузы
        # (никого нет на рынке) в этот момент как раз не выполняется.
        sym_state.last_reject_reason = "Ролловерная дыра ликвидности — сигнал пропущен"
        return
    if not rm.trading_hours_ok():
        sym_state.last_reject_reason = "Вне разрешённых часов торговли"
        return

    # РЫНОК ЗАКРЫТ ИЛИ НЕЛИКВИДЕН — спрашиваем у самого рынка, а не у часов.
    #
    # Владелец: «не по моему времени, а когда именно рынок закрыт». Часы для
    # этого не годятся: время компьютера и время сервера брокера расходятся
    # на 2-3 часа, у разных брокеров по-разному, и окно, заданное часами,
    # промахивается мимо цели целиком. Признаки берутся из рынка: запрет
    # брокера, замершая цена, спред намного шире обычного для этой же пары
    # (см. market_hours.py).
    #
    # ДЕЙСТВУЕТ ВСЕГДА, включая профиль с ignore_soft_filters («Истеричка») —
    # тот самый профиль, на котором и получены ночные убытки. Иначе вышло бы
    # как с ролловерной паузой выше: настройка включена, а профиль её молча
    # отменяет, и эффекта ноль. Тот же приём уже применён к анти-флэтовому
    # фильтру ниже.
    #
    # Открытые сделки это НЕ трогает: запрещается только вход, только по
    # этой паре. Трейлинг, безубыток и частичное закрытие работают как всегда.
    if getattr(cfg, "USE_MARKET_CLOSED_GUARD", False):
        market_reason = market_hours.market_block_reason(
            symbol,
            trade_mode=getattr(mt5.symbol_info(symbol), "trade_mode", None),
            spread_points=mt5c.get_spread_points(symbol),
            dead_seconds=float(getattr(cfg, "MARKET_DEAD_SECONDS", 90) or 0),
            # Признак «спред намного шире обычного» на новости выполняется
            # ВСЕГДА — это и есть новость. Для новостного входа его снимаем,
            # а жёсткие признаки (брокер запретил торговлю, цена замерла)
            # остаются: они с новостью никак не связаны.
            thin_ratio=(float(getattr(cfg, "THIN_SPREAD_RATIO", 0) or 0)
                        if (getattr(cfg, "USE_THIN_MARKET_GUARD", False)
                            and not news_ready) else 0.0),
            thin_min_samples=int(getattr(cfg, "THIN_MIN_SAMPLES", 30) or 30))
        if market_reason:
            sym_state.last_reject_reason = "Вход закрыт: " + market_reason
            return

    direction = 0
    score = 0.0
    is_news_entry = False
    hedge_directions = None  # None = обычный вход в одну сторону; иначе [1, -1] — хедж (см. ниже)

    trend_df = mt5c.get_rates_df(symbol, cfg.TREND_TIMEFRAME, count=cfg.EMA_TREND_PERIOD + 10)

    # Автообучение: порог входа плавно подстраивается под недавний винрейт ПО
    # ЭТОМУ символу (см. auto_learning.py) — в жёстких границах из config.py.
    adaptive_threshold = al.adaptive_score_threshold(profile["min_score_to_trade"], sym_state)

    # Новостной режим (или ОБА) — сначала пробуем поймать пробой на свежей важной новости.
    # Календарь подключён (встроенный календарь MT5 через сервис CalendarExport
    # либо внешний API — см. NEWS_PROVIDER_CHAIN), так что этот режим рабочий.
    # Если ни один источник не отвечает, пробоя просто не будет — вход не
    # состоится, а не откроется вслепую.
    # Сигнал уже посчитан ВЫШЕ, до фильтров спреда и ликвидности — см. длинное
    # пояснение там. Считать его второй раз нельзя: за это время цена ушла бы,
    # и два ответа на один вопрос разошлись бы.
    if news_ready and news_conf >= adaptive_threshold:
        direction, score, is_news_entry = news_dir, news_conf, True
        sym_state.last_buy_score = score if direction == 1 else 0
        sym_state.last_sell_score = score if direction == -1 else 0
    elif news_ready:
        # Пробой есть, но уверенности не хватило до порога. Раньше это
        # выглядело как «новости не работают»; теперь так и написано.
        sym_state.last_reject_reason = (
            f"Новость есть, но уверенность {news_conf:.0f} ниже порога "
            f"{adaptive_threshold:.0f}")

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
        # Мнение выбранной готовой стратегии (strategies.py): у каждой своя
        # логика оценки — тренд, возврат к среднему, пробой. Только добавляет
        # баллы, поэтому фильтры и лимиты риска остаются в силе.
        if getattr(cfg, "USE_STRATEGY_SIGNAL", False):
            key = getattr(cfg, "ACTIVE_STRATEGY", "balanced_hybrid")
            weight = float(getattr(cfg, "STRATEGY_SIGNAL_WEIGHT", 12))
            strat_buy = strat.calc_strategy_score(key, 1, df_ind, atr_value)
            strat_sell = strat.calc_strategy_score(key, -1, df_ind, atr_value)
            buy_score = strat.apply_strategy_score(buy_score, strat_buy, weight)
            sell_score = strat.apply_strategy_score(sell_score, strat_sell, weight)

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
            # ОБЕ стороны обязаны пройти порог, а не любая из них.
            #
            # Было `buy_ok or sell_ok`. Смысл хеджа в том, что направление
            # неизвестно и мы платим за обе ноги. Но при `or` хватало ОДНОЙ
            # сильной стороны — и вторая нога открывалась вопреки своему
            # счёту сигнала, «за компанию». Программа открывала позицию,
            # которую её же фильтр только что отверг, и платила за неё
            # спред, комиссию и своп. При `and` хедж открывается только
            # если обе стороны действительно прошли порог; иначе разбор
            # идёт ниже обычным путём — одна сторона по большему счёту.
            if profile.get("hedge_both_directions", False) and (buy_ok and sell_ok):
                # ТИП СЧЁТА РЕШАЕТ, ВОЗМОЖЕН ЛИ ХЕДЖ ВООБЩЕ. На неттинговом
                # счёте встречный ордер не создаёт вторую позицию, а закрывает
                # или разворачивает первую — вместо хеджа вышла бы закрытая
                # сделка. Проверка стоит ДО решения, а не перед отправкой:
                # иначе мы бы уже посчитали лот и риск на две ноги, которых
                # быть не может. Подробности — mt5c.hedging_block_reason().
                нельзя_хедж = mt5c.hedging_block_reason(acc_info)
                if нельзя_хедж:
                    # Молча переключаться на одну сторону НЕЛЬЗЯ: это тихая
                    # подмена того решения, которое настроил владелец. Лучше
                    # не открыть сделку и назвать причину словами.
                    sym_state.last_reject_reason = нельзя_хедж
                    return
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

    # Вето по сигналу из Telegram: чужой сигнал может ЗАПРЕТИТЬ вход, если он
    # противоречит направлению, которое программа выбрала сама. Открыть сделку,
    # поднять лот или отодвинуть стоп он не может — см. telegram_signals.py.
    # Отсутствие сигнала запретом НЕ считается: молчание источника не должно
    # останавливать торговлю. При TELEGRAM_ROLE = "show" вето отключено.
    if any(telegram_signals.veto_entry(symbol, d) for d in directions_to_open):
        sym_state.last_reject_reason = "Сигнал из Telegram против этого направления"
        return

    # Анти-дребезг (см. reversal_cooldown_ok) не применяется в хедж-режиме — мы
    # НАМЕРЕННО открываем обе стороны сразу, а не разворачиваемся против недавно
    # закрытой сделки.
    if hedge_directions is None and not rm.reversal_cooldown_ok(sym_state, direction):
        sym_state.last_reject_reason = f"Анти-дребезг: жду {cfg.MIN_BARS_BETWEEN_REVERSAL} бар(а)"
        return

    # Хедж открывает 2 позиции за раз — нужно, чтобы оба слота были свободны
    # (иначе получится однобокая "хеджированная" сделка, которая на деле хедж не даёт).
    if hedge_directions is not None:
        free_slots = (profile["max_open_positions"]
                      - rm.count_open_positions(symbol, positions=all_positions)
                      - acc_state.reservations.сколько(symbol))
        if free_slots < len(directions_to_open):
            sym_state.last_reject_reason = (
                f"Хедж (обе стороны): не хватает свободных слотов сделок ({free_slots} из "
                f"{len(directions_to_open)} нужных)"
            )
            return

    sl_dist = atr_value * profile["atr_sl_multiplier"] * (cfg.NEWS_VOLATILITY_SL_BOOST if is_news_entry else 1.0)
    # Стоп не может оказаться внутри спреда и обычного шума инструмента —
    # такие сделки закрываются за секунды и до цели не доходят никогда.
    # Расширение стопа автоматически уменьшает лот (calc_lot считает объём от
    # риска в деньгах), поэтому риск не растёт.
    sl_dist = rm.apply_min_stop_floor(symbol, sl_dist, atr_value, point)
    lot = rm.calc_lot(symbol, sl_dist, equity, sym_state)
    if lot <= 0:
        # Точную причину (сколько рискует минимальный лот и какой стоит
        # потолок) уже посчитал calc_lot — показываем её, а не общую фразу:
        # человеку нужно понять, что делать, а не что «что-то не так».
        sym_state.last_reject_reason = (
            sym_state.last_risk_warning
            or "Минимальный лот брокера рискует больше разрешённого — депозит "
               "мал для этого инструмента")
        return
    if getattr(cfg, "USE_MAX_PROFIT_RIDE", False):
        # "Тянуть максимальную прибыль" — без фиксированного TP, сделку от сих
        # пор ведёт ТОЛЬКО BE/ATR-трейлинг/Profit Lock (см. tm.manage_open_positions),
        # закрытие происходит, когда цена разворачивается и выбивает трейлинг-стоп.
        tp_dist = 0.0
    elif profile["use_money_tp"]:
        # sl_dist передаём, чтобы calc_tp_distance_money гарантированно поднял
        # TP минимум до SL*MIN_RISK_REWARD_RATIO, если денежная цель профиля
        # (target_profit_money) окажется слишком скромной относительно риска.
        # Цель берётся ОТ СЧЁТА, а не абсолютным числом из профиля: абсолютная
        # сумма не переживает смену размера депозита (см. пояснение в
        # rm.effective_target_money).
        tp_dist = rm.calc_tp_distance_money(
            symbol, lot, rm.effective_target_money(profile, equity),
            atr_value, point, sl_dist)
    else:
        tp_dist = rm.calc_tp_distance(sl_dist, atr_value, point)

    if not rm.spread_cost_ok(symbol, lot, tp_dist, profile["ignore_soft_filters"]):
        sym_state.last_reject_reason = "Спред съедает слишком большую часть TP"
        return

    # Риск новой сделки считается ТЕМ ЖЕ способом, что и риск уже открытых
    # (rm.get_open_risk_percent) — иначе общий потолок max_total_risk_pct
    # сравнивал бы числа, посчитанные по-разному. Внутри — точный расчёт от
    # терминала с приближением по цене тика в запасе (rm.money_risk_per_lot).
    new_trade_risk_pct = 0.0
    # То же число в ДЕНЬГАХ — оно уходит в бронь. Проценты для этого не
    # годятся: следующая сделка в том же проходе считает свой процент от
    # того же капитала, и складывать проценты, посчитанные от разных
    # знаменателей, было бы ошибкой. Деньги складываются всегда.
    new_trade_risk_money = 0.0
    if equity > 0:
        per_lot = rm.money_risk_per_lot(symbol, sl_dist)
        if per_lot > 0:
            new_trade_risk_money = per_lot * lot
            new_trade_risk_pct = new_trade_risk_money / equity * 100.0

    # Используем уже полученный в начале ЭТОЙ итерации acc_info вместо
    # повторного запроса к MT5 (fallback на свежий запрос, если функцию
    # вызвали без него — на случай прямого вызова откуда-то ещё).
    risk_acc_info = acc_info if acc_info is not None else mt5c.get_account_info()
    open_risk_pct = (rm.get_open_risk_percent(risk_acc_info, positions=all_positions)
                     + acc_state.reservations.риск_процент(equity))
    # В хедже считаем риск по ОБЕИМ ногам (консервативно — реальный чистый риск
    # обычно меньше, т.к. ноги в противоположных направлениях, но так надёжнее).
    # ПОЗИЦИЯ БЕЗ СТОПА БЛОКИРУЕТ НОВЫЕ ВХОДЫ.
    #
    # Проверка стоит ОТДЕЛЬНО от расчёта процента, хотя расчёт такую
    # позицию уже считает консервативно. Причина: процент можно случайно
    # ослабить настройкой потолка, а этот запрет настройкой не
    # обходится. И оператору нужен номер позиции, а не строчка «риск
    # превышен» — иначе непонятно, что чинить в терминале.
    без_стопа = rm.позиции_без_стопа(all_positions)
    if без_стопа:
        номера = ", ".join(str(p.ticket) for p in без_стопа[:5])
        причина = (f"есть открытые позиции БЕЗ стоп-лосса ({номера}) — "
                   f"их убыток ничем не ограничен. Новые входы запрещены, "
                   f"пока стоп не появится.")
        sym_state.last_reject_reason = причина
        runtime_events.record("риск", причина)
        return

    if open_risk_pct + new_trade_risk_pct * len(directions_to_open) > profile["max_total_risk_pct"]:
        sym_state.last_reject_reason = "Превышен общий риск по открытым позициям"
        return

    # СВОБОДНЫЕ СРЕДСТВА. Проверяется по каждой ноге: у хеджа их две, и
    # средств должно хватить на обе. Это не торговый фильтр — отсекаются
    # ровно те ордера, которые брокер и так отклонил бы, просто причина
    # называется словами заранее (см. rm.margin_block_reason).
    for d in directions_to_open:
        no_margin = rm.margin_block_reason(symbol, d, lot, account=acc_info)
        if no_margin:
            sym_state.last_reject_reason = no_margin
            return

    # ОТПРАВКА. У хеджа две ноги, и это НЕ одна операция: между заявками
    # меняются цена, свободная маржа и ответ брокера. Поэтому исполненные
    # ноги запоминаются — если следующая не пойдёт, их придётся закрыть.
    #
    # У каждой заявки четыре исхода, а не два (см. execution.py). Здесь
    # они разбираются так:
    #
    #   ПОЛНОЕ      — как задумано, идём дальше;
    #   ЧАСТИЧНОЕ   — объём меньше заказанного. У ОДИНОЧНОЙ сделки это
    #                 приемлемо: позиция защищена стопом, а риск получился
    #                 МЕНЬШЕ рассчитанного. У ХЕДЖА неприемлемо: две ноги
    #                 разного объёма — это не хедж, а перекошенная
    #                 направленная позиция, и её надо разобрать;
    #   ОТКАЗ       — ничего не открылось, это точно известно;
    #   НЕИЗВЕСТНО  — сверка с терминалом не дала ответа. Позиция может
    #                 быть, а может не быть. Новые входы прекращаются.
    исполненные = []          # [(направление, execution.Итог)]
    сорвалось = False
    неясно = ""               # непустое — положение дел не выяснено
    неясная_заявка = 0        # номер заявки, судьба которой неизвестна
    перекос = False           # хедж собрался ногами разного объёма
    for d in directions_to_open:
        leg_score = (buy_score if d == 1 else sell_score) if hedge_directions is not None else score
        итог = tm.execute_market_order(symbol, d, lot, sl_dist, tp_dist, leg_score, point)

        if not итог.решено:
            # Худшее из состояний: возможно, позиция есть. Ни повторять,
            # ни идти дальше нельзя — только остановиться и позвать человека.
            неясно = итог.пояснение
            # НОМЕР ЗАЯВКИ ЗАПОМИНАЕМ ОТДЕЛЬНО. Без него сверка при
            # следующем запуске (И3) не сможет ничего проследить: связь
            # «заявка → сделка → позиция» начинается именно с номера.
            неясная_заявка = итог.тикет
            сорвалось = True
            break

        if not итог.открылось:
            сорвалось = True
            # Дальше не пытаемся: если вторая нога не пошла, третьей не
            # бывает, а у обычной сделки нога всего одна.
            break

        исполненные.append((d, итог))
        acc_state.trades_today += 1
        # БРОНЬ. Ставится ровно здесь — после подтверждения ордера и до
        # перехода к следующему инструменту. Снимок позиций о этой сделке
        # ещё не знает и до конца прохода не узнает, поэтому лимиты
        # следующей пары обязаны считать её по брони.
        #
        # Бронируется ЗАКАЗАННЫЙ риск, а не исполненный, даже когда
        # исполнилось меньше. Бронь ошибается только в безопасную сторону:
        # завысить занятый риск — потерять сделку, занизить — превысить
        # лимит.
        acc_state.reservations.забронировать(symbol, d, new_trade_risk_money)
        dir_txt = "BUY" if d == 1 else "SELL"
        hedge_txt = " [хедж]" if hedge_directions is not None else ""
        часть = ("" if итог.статус == execution.ПОЛНОЕ
                 else f" | исполнено {итог.исполнено:.2f} из {итог.заказано:.2f}")
        control.push_notification(
            "Сделка открыта",
            f"{symbol} {dir_txt} лот {lot:.2f} | score {leg_score:.1f}{hedge_txt}{часть}",
        )

        if итог.статус == execution.ЧАСТИЧНОЕ and hedge_directions is not None:
            перекос = True
            сорвалось = True
            break

    # НЕВЫЯСНЕННОЕ ПОЛОЖЕНИЕ ДЕЛ.
    #
    # Сюда попадаем, когда брокер не ответил внятно и сверка с терминалом
    # тоже не помогла. Позиция может быть открыта, а может не быть.
    # Пытаться что-то закрывать вслепую нельзя: закрывать, возможно,
    # нечего, а лишний приказ откроет встречную позицию. Единственное
    # верное действие — остановиться.
    if неясно:
        # Если до неясного ответа успела открыться нога — сказать об этом
        # ОБЯЗАТЕЛЬНО. Закрыть её мы не можем (сверка не работает, значит и
        # найти позицию нечем), но человек, открывший терминал, должен
        # знать, что там уже лежит, а не искать это сам.
        уже = ", ".join(f"{'BUY' if d == 1 else 'SELL'} {i.исполнено:.2f} "
                        f"(тикет {i.тикет})" for d, i in исполненные)
        хвост = (f" ВНИМАНИЕ: до этого уже открыто — {уже}." if уже else "")
        текст = (f"{symbol}: не удалось выяснить, открылась сделка или нет "
                 f"({неясно}). Новые входы остановлены — откройте терминал "
                 f"и посмотрите, есть ли позиция.{хвост}")
        log.error(текст)
        runtime_events.record("исполнение", текст)
        control.push_notification("Положение сделки неизвестно", текст)
        # НЕ set_paused(True): та пауза жила в памяти и исчезала при
        # перезапуске — то есть защищала ровно до первого перезапуска, а он
        # в такой момент как раз и вероятен. Инцидент кладётся на диск.
        _открыть_инцидент(symbol, "исполнение", текст, {
            "заявка_без_ответа": (int(неясная_заявка)
                                  if неясная_заявка and неясная_заявка > 0
                                  else 0),
            "тикеты_исполненных_ног": [int(i.тикет) for _, i in исполненные
                                       if i.тикет and i.тикет > 0],
            "подробность": неясно,
        })
        sym_state.last_reject_reason = "Итог заявки не выяснен, торговля остановлена"
        return

    # КОМПЕНСАЦИЯ НЕПОЛНОГО ХЕДЖА.
    #
    # Раньше здесь писалось «OK (частично, хедж)» — и на этом всё. То есть
    # на счету оставалась ОДИНОЧНАЯ направленная позиция вместо хеджа:
    # не то, что просил владелец, и не то, под что считался риск.
    if сорвалось and исполненные:
        не_закрылись = []
        # НОМЕРА ОТДЕЛЬНО ОТ ПОЯСНЕНИЙ. Раньше и то и другое склеивалось в
        # одну строку, а сверка потом выковыривала оттуда «все цифры» — и
        # из «12345 (ответ 10012 объём 0.01)» получалось несуществующее
        # число. Опасности в этом не было (несуществующая заявка даёт «не
        # выяснено»), но человек лишался доказательного ответа по реальной
        # незакрытой ноге и снимал инцидент принудительно.
        номера_незакрытых = []
        for d, нога_итог in исполненные:
            закрытие = tm.close_leg(symbol, нога_итог.тикет, direction=d,
                                    volume=нога_итог.исполнено)
            # Закрытой считается только ПОЛНОСТЬЮ закрытая нога. Частичное
            # закрытие оставляет остаток на счету — это ровно тот случай,
            # ради которого проверка и переписана.
            if закрытие.статус != execution.ПОЛНОЕ:
                номера_незакрытых.append(int(нога_итог.тикет))
                не_закрылись.append(
                    f"{нога_итог.тикет} ({закрытие.статус}: {закрытие.пояснение})")
            else:
                # Бронь снимать нельзя: она живёт до конца прохода и
                # ошибается только в безопасную сторону. Здесь она как раз
                # и работает по назначению — не даёт открыть новое, пока
                # положение дел неясно.
                pass

        if не_закрылись:
            # ХУДШИЙ СЛУЧАЙ: нога открыта, закрыть не смогли. Своим стопом
            # она защищена, но это уже не наша задумка, а случайность.
            # Открывать что-то ещё в таком состоянии нельзя.
            текст = (f"{symbol}: хедж исполнился наполовину, и закрыть "
                     f"лишнюю ногу не удалось (тикеты {не_закрылись}). "
                     f"Новые входы остановлены — проверьте счёт в терминале.")
            log.error(текст)
            runtime_events.record("хедж", текст)
            control.push_notification("Хедж исполнился наполовину", текст)
            _открыть_инцидент(symbol, "хедж", текст, {
                "тикеты_незакрытых_ног": номера_незакрытых,
                "детали_незакрытых_ног": не_закрылись,
            })
            sym_state.last_reject_reason = "Хедж наполовину, компенсация не удалась"
        elif перекос:
            sym_state.last_reject_reason = (
                "Хедж не собрался: ноги разного объёма, обе закрыты")
        else:
            sym_state.last_reject_reason = (
                "Хедж не собрался: вторая нога не исполнилась, первая закрыта")
        return

    if исполненные:
        неполные = [i for _, i in исполненные if i.статус == execution.ЧАСТИЧНОЕ]
        if неполные:
            # Одиночная сделка, исполненная не целиком. Позиция есть, стоп
            # на месте, риск МЕНЬШЕ рассчитанного — останавливать нечего.
            # Но сказать об этом надо: иначе разница между заказанным и
            # реальным объёмом останется незамеченной.
            текст = (f"{symbol}: заказано {неполные[0].заказано:.2f} лота, "
                     f"исполнено {неполные[0].исполнено:.2f}. Позиция открыта "
                     f"и защищена стопом, риск получился меньше расчётного.")
            log.warning(текст)
            runtime_events.record("исполнение", текст)
            sym_state.last_reject_reason = "OK (исполнено частично)"
        else:
            sym_state.last_reject_reason = "OK"


def _история_для_сверки(ожидаемые):
    """Сделки за окно, покрывающее время отправки самых старых заявок.

    ЗАЧЕМ. Сверка смотрела только на ОТКРЫТЫЕ позиции. Заявка, успевшая
    открыться и закрыться по стопу до следующего круга, среди открытых не
    находилась и объявлялась пропавшей — открывался инцидент и торговля
    вставала. У владельца так вышло три дня без единой сделки, при том
    что заявки отработали штатно.

    None здесь означает «спросить не удалось». Возвращаем именно None, а
    не пустой список: пустой список — это утверждение «сделок не было», а
    такого утверждения из неполученного ответа делать нельзя."""
    самая_старая = None
    for з in ожидаемые or ():
        когда = з.get("когда_utc")
        if not когда:
            continue
        try:
            t = datetime.fromisoformat(str(когда))
        except (TypeError, ValueError):
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if самая_старая is None or t < самая_старая:
            самая_старая = t
    if самая_старая is None:
        # Времени нет ни у одной записи — берём заведомо широкое окно.
        самая_старая = datetime.now(timezone.utc) - timedelta(days=30)
    # Час запаса назад: часы терминала и наши могут расходиться.
    начало = самая_старая - timedelta(hours=1)
    конец = datetime.now(timezone.utc) + timedelta(hours=1)
    return mt5c.deals_or_none(начало, конец)


def _открыть_инцидент(symbol: str, вид: str, текст: str, ещё: dict = None):
    """Остановить торговлю ДО РАЗБИРАТЕЛЬСТВА, пережив перезапуск.

    Раньше здесь стояло control.set_paused(True) — переменная в памяти.
    Программа перезапустилась, и торговля продолжалась как ни в чём не
    бывало. Перезапуск в такой момент как раз и вероятен: программу
    перезапускают, когда с ней что-то не так.

    Если записать на диск не вышло, остановка всё равно действует — но
    только до перезапуска, и об этом говорится вслух."""
    сведения = {"символ": symbol, "вид": вид, "причина": текст}
    сведения.update(ещё or {})
    записано = control.открыть_инцидент(сведения)
    if not записано:
        предупреждение = (
            f"{symbol}: отметку об остановке НЕ УДАЛОСЬ записать на диск. "
            f"Торговля остановлена, но перезапуск программы снимет "
            f"остановку — не перезапускайте её, пока не разобрались.")
        log.error(предупреждение)
        runtime_events.record("исполнение", предупреждение)
        control.push_notification("Остановка не сохранена на диск",
                                  предупреждение)


def _close_one_position(pos):
    """Закрыть одну позицию по приказу с дашборда, РАЗОБРАВ ответ брокера.

    ЧТО БЫЛО. Отсюда уходил close_position_partial, а ответ брокера
    просто печатался в журнал строкой. Из четырёх возможных исходов
    (см. execution.py) различался ровно ноль:

      * ЧАСТИЧНОЕ — закрылась часть объёма, остаток живёт на счету. Для
        человека кнопка сработала, а позиция осталась. Она не под
        присмотром: программа считает её закрытой;
      * НЕИЗВЕСТНО — брокер не ответил. Позиция может быть и закрыта, и
        открыта, и закрыта позже. Считать её закрытой — значит поверить
        в то, что не проверено.

    ЧТО СТАЛО. Закрытие идёт через tm.close_leg — тот же путь, что уже
    дозакрывает остаток и разбирает ответ. Неясный или неполный исход
    открывает инцидент: торговля останавливается до разбирательства и
    переживает перезапуск.

    Инцидент здесь не перестраховка. Позиция, которую программа считает
    закрытой, не ведётся: ей не двигают стоп, её не считают в риске, и
    новые входы открываются так, будто её нет."""
    # Отмечаем ДО отправки приказа: после закрытия позиции её уже не будет в
    # списке открытых, и пометить станет нечего — в журнал попал бы тот стоп,
    # который стоял последним, вместо честного «закрыто вручную».
    tm.note_manual_close(pos.ticket)
    if not cfg.LIVE_TRADING:
        log.info("[DRY-RUN] Дашборд запросил закрытие %s (%s), но LIVE_TRADING=False — ничего не отправлено.",
                  pos.ticket, pos.symbol)
        return

    итог = tm.close_leg(pos.symbol, int(pos.ticket),
                        direction=1 if pos.type == mt5.POSITION_TYPE_BUY else -1,
                        volume=float(pos.volume))

    if итог.статус == execution.ПОЛНОЕ:
        log.info("Позиция %s (%s) закрыта по приказу с дашборда.",
                 pos.ticket, pos.symbol)
        return

    if итог.статус == execution.ЧАСТИЧНОЕ:
        текст = (f"приказ закрыть позицию {pos.ticket} исполнен НЕ полностью: "
                 f"закрыто {итог.исполнено:.2f} из {pos.volume:.2f} лота. "
                 f"Остаток остался на счету.")
    elif итог.статус == execution.НЕИЗВЕСТНО:
        текст = (f"исход приказа закрыть позицию {pos.ticket} НЕИЗВЕСТЕН: "
                 f"{итог.пояснение}. Позиция может быть открыта, закрыта "
                 f"или закрыта позже — это надо проверить в терминале.")
    else:
        текст = (f"брокер отказал в закрытии позиции {pos.ticket}: "
                 f"{итог.пояснение}. Позиция осталась открытой.")

    log.error("%s: %s", pos.symbol, текст)
    _открыть_инцидент(pos.symbol, "закрытие с дашборда", текст, {
        "тикет": int(pos.ticket),
        "объём_заказан": float(pos.volume),
        "объём_закрыт": float(итог.исполнено),
        "статус": итог.статус,
    })


def _сверить_ожидаемые_заявки(all_positions):
    """Какие отправленные заявки так и не стали видимой позицией.

    Возвращает записи, под которые НЕ нашлось позиции в свежем снимке.
    Ничего не снимает: снять запись можно только по доказанному исходу —
    позиция найдена или брокер внятно отказал. «Не вижу» доказательством
    не является.

    Заявка может быть ещё активна, исполниться позже или появиться в
    истории с задержкой. Пока это не выяснено, риск учитывается так,
    будто позиция есть."""
    try:
        _ждём = pending.открытые()
        итог = pending.сверить(
            all_positions or [], magic=cfg.MAGIC_NUMBER,
            сделки=(_история_для_сверки(_ждём) if _ждём else None))
    except Exception as e:  # noqa: BLE001
        # Журнал сломался — считаем, что неподтверждённые заявки ЕСТЬ.
        # Пустой список означал бы «ничего не отправляли», а это ровно то
        # утверждение, которого мы сделать не можем.
        log.error("Сверка ожидаемых заявок не отработала (%s) — "
                  "считаю, что неподтверждённые заявки есть.", e)
        return []
    пропали = итог.get("пропали", [])
    if пропали:
        номера = ", ".join(f"{з.get('символ')} "
                           f"{'BUY' if з.get('направление') == 1 else 'SELL'}"
                           for з in пропали[:5])
        сообщение = (f"отправленных заявок без видимой позиции: "
                     f"{len(пропали)} ({номера}). Риск по ним учитывается "
                     f"как по открытым, новые входы ограничены.")
        log.warning(сообщение)
        runtime_events.record("исполнение", сообщение)
    return пропали


def _позиции_для_массового_закрытия(all_positions=None):
    """Какие позиции трогают кнопки «закрыть все / прибыльные / убыточные».

    По умолчанию — только позиции бота. Чужая сделка на счёте это чужое
    решение, и массовая кнопка не должна его отменять.

    Настройка отключается только руками в config.py: в
    remote_settings.ALLOWED её нет, из GitHub переключить нельзя."""
    positions = all_positions if all_positions is not None else mt5c.get_open_positions()
    if not getattr(cfg, "CLOSE_BOT_POSITIONS_ONLY", True):
        return list(positions)
    свои = [p for p in positions
            if int(getattr(p, "magic", 0) or 0) == int(cfg.MAGIC_NUMBER)]
    чужих = len(positions) - len(свои)
    if чужих:
        # Молчать нельзя: человек нажал «закрыть все» и вправе знать, что
        # часть позиций осталась — и почему.
        сообщение = (f"массовое закрытие не тронуло {чужих} чужих позиций "
                     f"(открыты не этим ботом). Закрыть их можно по одной "
                     f"на вкладке «Сделки».")
        log.info(сообщение)
        runtime_events.record("исполнение", сообщение)
    return свои


def process_close_requests(all_positions=None):
    """Забирает заявки на закрытие позиций из дашборда/телефона (control.py)
    и исполняет их здесь, в главном потоке — единственном, кто трогает MT5.
    all_positions: уже полученный в начале ЭТОЙ итерации список (см. main()) —
    экономит отдельный запрос к MT5, если заявок на закрытие нет (обычный случай)."""
    # "Закрыть все сделки" — одной кнопкой.
    #
    # ЧТО БЫЛО. Закрывались АБСОЛЮТНО ВСЕ позиции счёта, включая
    # открытые владельцем вручную и чужими советниками. Человек жмёт
    # кнопку с мыслью «останови бота», а закрывается и его собственная
    # долгая сделка. Отменить нельзя: позиция уже закрыта по рынку.
    #
    # ЧТО СТАЛО. При CLOSE_BOT_POSITIONS_ONLY (по умолчанию True)
    # трогаются только позиции с нашим magic number. Закрыть чужую
    # можно по-прежнему — кнопкой у конкретной сделки на вкладке
    # «Сделки», где видно, какую именно закрываешь.
    if control.is_close_all_requested():
        positions = _позиции_для_массового_закрытия(all_positions)
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
        positions = _позиции_для_массового_закрытия(all_positions)
        for pos in positions:
            if pos.profit < 0:
                continue
            try:
                _close_one_position(pos)
            except Exception as e:
                log.exception("Не удалось закрыть прибыльную позицию %s: %s", pos.ticket, e)
        control.clear_close_profitable_requested()

    if control.is_close_losing_requested():
        positions = _позиции_для_массового_закрытия(all_positions)
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
            "risk_warning": st.last_risk_warning,
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

    return {
        "account": {
            "login": acc_info.login,
            "server": acc_info.server,
            "balance": acc_info.balance,
            "equity": equity,
            "currency": acc_info.currency,
            # Режим счёта нужен окну, чтобы кнопка «Разрешить этот счёт»
            # могла отказать на РЕАЛЬНОМ счёте, не спрашивая терминал сама.
            "trade_mode": getattr(acc_info, "trade_mode", None),
        },
        "live_trading": cfg.LIVE_TRADING,
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


_last_remote_sync = None
_baselines_seeded = {"done": False}


def sync_remote_settings():
    """Забрать настройки торговли из GitHub. Владелец: «чтобы настройки сами
    загрузились без всяких нажатий».

    Ничего перечитывать вручную не нужно: настройки пишутся в config.py, а
    reload_config_if_changed ниже видит изменившийся файл и подхватывает их на
    ходу. Перезапуск программы не требуется.

    Рамки того, что вообще разрешено менять из интернета, — в
    remote_settings.py. Там же объяснено, почему источник обновлений менять
    оттуда нельзя ни при каких условиях."""
    global _last_remote_sync
    if not getattr(cfg, "REMOTE_SETTINGS_ENABLED", False):
        return
    minutes = float(getattr(cfg, "REMOTE_SETTINGS_MINUTES", 10) or 10)
    now = datetime.now()
    if (_last_remote_sync is not None
            and (now - _last_remote_sync).total_seconds() < minutes * 60):
        return
    _last_remote_sync = now
    try:
        remote_settings.sync()
    except Exception as e:          # noqa: BLE001
        # Сеть, GitHub, кривой файл — что угодно. Торговый цикл из-за этого
        # останавливаться не должен.
        log.warning("Не удалось получить настройки из GitHub: %s", e)


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

    # reload() создаёт НОВЫЙ класс Enum (RiskProfile) — старые
    # переопределения профиля/режима с дашборда ссылались бы на "старые" классы
    # и перестали бы совпадать при сравнении. Поэтому сбрасываем их на дефолт
    # из свежего config.py; если нужно — просто выбери профиль/режим на дашборде заново.
    control.set_risk_profile(None)

    # Новые символы, добавленные в SYMBOLS, подключаются на лету.
    # Убранные из SYMBOLS — не удаляются из обработки: их открытые сделки
    # продолжают вестись (BE/трейлинг), просто новых входов по ним не будет
    # (это уже проверяется через control.is_symbol_enabled/список ниже не трогаем).
    for sym in cfg.SYMBOLS:
        if sym not in sym_states:
            if mt5c.ensure_symbol(sym):
                sym_states[sym] = SymbolState(symbol=sym)
                # Если по этому символу уже торговали раньше — поднимаем его
                # статистику обучения из файла, а не начинаем с нуля.
                al.load_learning_state({sym: sym_states[sym]})
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
    print("-" * 78)
    print(f"Equity: {equity:.2f} | Сделок сегодня: {acc_state.trades_today} | "
          f"LIVE_TRADING={cfg.LIVE_TRADING} | профиль={effective_profile.value}")
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


# Пульс торгового цикла: время последнего ПРОЙДЕННОГО круга. По нему видно
# не только «поток жив», но и «поток не завис»: поток может существовать и
# при этом намертво стоять внутри зависшего запроса к терминалу, а снаружи
# это выглядит одинаково — сделок нет.
_heartbeat = {"at": 0.0}


def last_heartbeat() -> float:
    """Когда главный цикл в последний раз завершил круг (time.time()).
    0 — цикл ещё ни разу не отработал."""
    return _heartbeat["at"]


def seconds_since_heartbeat() -> float:
    """Сколько секунд назад цикл подавал признаки жизни. Большое число —
    цикл умер или завис, и сделок не будет, сколько ни жди."""
    if _heartbeat["at"] <= 0:
        return 0.0
    return max(0.0, time.time() - _heartbeat["at"])


def watchdog_reason(should_run: bool, thread_alive: bool,
                    silent_seconds: float, limit_seconds: float) -> str:
    """Надо ли поднимать торговый цикл заново. Пустая строка — не надо.

    Вынесено отдельной функцией из окна программы намеренно: это решение
    важно проверить тестами, а всё, что живёт внутри tkinter-класса, на
    Linux без графики не проверить вовсе.

    should_run     — человек хочет, чтобы бот работал (не нажимал «Стоп»)
    thread_alive   — поток торгового цикла ещё существует
    silent_seconds — сколько назад цикл в последний раз прошёл круг
    limit_seconds  — после какого молчания считаем цикл вставшим
    """
    if not should_run:
        return ""              # нажали «Стоп» — это не поломка
    if not thread_alive:
        return "поток торгового цикла завершился"
    if limit_seconds > 0 and silent_seconds > limit_seconds:
        return f"цикл не подаёт признаков жизни {int(silent_seconds)} с"
    return ""


def _sleep_interruptible(seconds: float, stop_event):
    """Как time.sleep(), но если передан stop_event (десктоп-приложение нажало
    "Стоп") — просыпается СРАЗУ, а не ждёт полный POLL_SECONDS. Из CLI
    (stop_event=None) ведёт себя как обычный time.sleep()."""
    if stop_event is None:
        time.sleep(seconds)
    else:
        stop_event.wait(timeout=seconds)


def survey_symbol(symbol: str) -> dict:
    """Замерить у брокера всё, что нужно для отбора пары.

    Один замер на пару, только при отборе — не в торговом цикле. Поэтому
    здесь можно позволить себе обращения к терминалу, которых мы избегаем
    на каждом проходе."""
    import MetaTrader5 as mt5
    # БЕЗ ЭТОЙ СТРОКИ ОТБОР НЕ РАБОТАЕТ. symbols_get() отдаёт все сотни пар
    # брокера, но данные — бары и спред — терминал отдаёт только по тем, что
    # добавлены в «Обзор рынка». По остальным замер молча возвращал бы пусто,
    # и отбор видел бы лишь те несколько пар, что уже открыты у человека, —
    # то есть «весь список брокера» оказался бы неправдой.
    if not mt5c.ensure_symbol(symbol):
        return {}
    info = mt5.symbol_info(symbol)
    if info is None:
        return {}

    point = float(getattr(info, "point", 0) or 0)
    tick_value = float(getattr(info, "trade_tick_value", 0) or 0)
    tick_size = float(getattr(info, "trade_tick_size", 0) or 0)
    if point <= 0 or tick_value <= 0 or tick_size <= 0:
        return {}

    df = mt5c.get_rates_df(symbol, cfg.TIMEFRAME, count=60)
    if df is None or len(df) < 20:
        return {}
    # ATR считаем грубо — средним размахом бара. Точности индикатора здесь не
    # нужно: мы сравниваем инструменты между собой, а не принимаем решение о
    # входе.
    span = (df["high"] - df["low"]).tail(50)
    atr_points = float(span.mean()) / point if point else 0.0

    spread_points = float(getattr(info, "spread", 0) or 0)

    # Стоп оцениваем ПО ТОМУ ЖЕ правилу, что и в торговле (risk_manager.
    # min_stop_distance): пол стопа — это максимум из доли ATR, нескольких
    # спредов и минимальной дистанции брокера. Если считать только по ATR, то
    # на паре с широким спредом оценка стопа окажется заниженной, а вместе с
    # ней и риск минимального лота — и слишком дорогая пара проскочит отбор.
    atr_floor = atr_points * float(getattr(cfg, "MIN_SL_ATR_FRACTION", 1.5) or 0)
    spread_floor = spread_points * float(getattr(cfg, "MIN_SL_SPREAD_MULTIPLE", 4.0) or 0)
    broker_floor = float(getattr(info, "trade_stops_level", 0) or 0)

    return {
        "symbol": symbol,
        "spread_points": spread_points,
        "atr_points": atr_points,
        "min_lot": float(getattr(info, "volume_min", 0) or 0),
        "stop_points": max(atr_floor, spread_floor, broker_floor),
        "money_per_point": (point / tick_size) * tick_value,
        "trade_mode": getattr(info, "trade_mode", None),
        # РАЗДЕЛ БРОКЕРА СОХРАНЯЕТСЯ ВМЕСТЕ С ЗАМЕРОМ.
        #
        # Без него фильтр AUTO_PICK_GROUPS работал только на первом этапе —
        # он решал, что ЗАМЕРЯТЬ. А окончательный выбор берёт файл замеров,
        # и проверить по нему раздел было нечем. Инструмент, однажды попавший
        # в файл, выбирался дальше всегда: у владельца при AUTO_PICK_GROUPS
        # = ["Forex"] торговались американские акции (AUPH, CDW, BMNR, ARVN,
        # BHVN, CNH, CHYM) — 7 акций по 16 долларов на счёте 384. Вся логика
        # размера сделки считалась на валютных парах.
        "path": str(getattr(info, "path", "") or ""),
    }


def contract_facts(symbol: str) -> dict:
    """Постоянные данные контракта — БЕЗ добавления пары в «Обзор рынка».

    Терминал знает их и по паре, которую человек никогда не открывал: это
    описание инструмента, а не котировки. Поэтому первый этап отбора стоит
    почти ничего, а дорогая работа (история баров) достаётся только тем, кто
    его прошёл."""
    import MetaTrader5 as mt5
    info = mt5.symbol_info(symbol)
    if info is None:
        return {}
    point = float(getattr(info, "point", 0) or 0)
    tick_value = float(getattr(info, "trade_tick_value", 0) or 0)
    tick_size = float(getattr(info, "trade_tick_size", 0) or 0)
    if point <= 0 or tick_value <= 0 or tick_size <= 0:
        return {}
    return {
        "symbol": symbol,
        "min_lot": float(getattr(info, "volume_min", 0) or 0),
        "money_per_point": (point / tick_size) * tick_value,
        "trade_mode": getattr(info, "trade_mode", None),
        # Раздел, в котором инструмент лежит у брокера: "Forex\\Majors\\EURUSD",
        # "Stocks\\US\\AAPL" и т.п. По нему отличаются валютные пары от акций —
        # см. symbol_picker.prefilter и AUTO_PICK_GROUPS.
        "path": str(getattr(info, "path", "") or ""),
    }


def auto_pick_symbols(equity: float, deadline: float = None,
                      acc_server=None) -> list:
    """Взять список пар у брокера и отобрать подходящие.

    ОТБОР ИДЁТ В ДВА ЭТАПА, И ЭТО ГЛАВНОЕ В ЭТОЙ ФУНКЦИИ.

    Сначала он был в один: по каждой паре брокера сразу запрашивались бары.
    Владелец: «нет отклика от программы, виснет». Так и было. Чтобы получить
    бары, пару надо добавить в «Обзор рынка», после чего терминал идёт за
    историей на сервер — и на сотнях пар запуск растягивался на минуты.
    Программа при этом честно работала, но для человека перед экраном
    «работает минутами без признаков жизни» и «зависла» — одно и то же.

    Этап 1 — дешёвый: только описание контракта (минимальный лот, цена
    пункта, разрешена ли торговля). Ни баров, ни «Обзора рынка». Отсеивает
    то, что счёту заведомо не по карману.

    Этап 2 — дорогой: бары и спред, но только для выживших, не больше
    AUTO_PICK_SURVEY_LIMIT штук и не дольше AUTO_PICK_MAX_SECONDS.

    Время ограничено ЖЁСТКО. Кончилось — берём то, что успели: неполный
    список лучше запуска, который не кончается.

    И ЗАМЕРЫ ХРАНЯТСЯ В ФАЙЛЕ. Владелец: «пусть просто один раз загружает все
    пары и хранит у себя в файлах, чтобы не было такой долгой загрузки». Так и
    сделано: замеренная пара попадает в symbols_survey.json, и следующий
    запуск её не трогает. То, что не успели, дозамеряется при следующих
    запусках — за несколько раз покрывается весь список брокера, и ни один
    запуск не оказывается долгим."""
    if not getattr(cfg, "AUTO_PICK_SYMBOLS", False):
        return []
    if deadline is None:
        deadline = time.time() + float(
            getattr(cfg, "AUTO_PICK_MAX_SECONDS", 20) or 20)
    try:
        available = mt5c.get_all_symbols()
    except Exception as e:      # noqa: BLE001
        log.warning("Не удалось получить список пар у брокера: %s", e)
        return []
    if not available:
        return []

    # Выключенные вручную не рассматриваем вовсе — иначе отбор мог бы вернуть
    # золото, которое владелец просил отключить.
    available = [s for s in available if not rm.blocked_symbol_reason(s)]
    log.info("Отбор пар: у брокера %d инструментов, смотрю описания...",
             len(available))

    # ---- ЭТАП 1: дешёвый отсев по описанию контракта --------------------
    facts = []
    for name in available:
        if time.time() > deadline:
            log.warning("Отбор пар: время вышло на первом этапе — "
                        "успел посмотреть %d из %d", len(facts), len(available))
            break
        try:
            row = contract_facts(name)
        except Exception:       # noqa: BLE001
            continue
        if row:
            facts.append(row)

    stage1 = symbol_picker.prefilter(
        facts, equity,
        max_risk_percent=float(getattr(cfg, "MAX_TRADE_RISK_PERCENT_OF_EQUITY", 2.0) or 0),
        limit=0,              # без обрезки: обрежет очередь на замер ниже
        groups=tuple(getattr(cfg, "AUTO_PICK_GROUPS", symbol_picker.DEFAULT_GROUPS) or ()))
    passed = stage1["kept"]

    # ---- ЗАМЕРЫ БЕРУТСЯ ИЗ ФАЙЛА ---------------------------------------
    # Владелец: «пусть просто один раз загружает все пары и хранит у себя в
    # файлах, чтобы не было такой долгой загрузки». Замер стоит дорого, а
    # меряет то, что за сутки почти не меняется, — делать его каждый запуск
    # незачем. Подробности и оговорки — в symbol_cache.py.
    cached = symbol_cache.load(server=str(getattr(acc_server, "server", "") or ""))
    shortlist = symbol_cache.to_survey(
        passed, cached,
        limit=int(getattr(cfg, "AUTO_PICK_SURVEY_LIMIT",
                          symbol_picker.DEFAULT_SURVEY_LIMIT)))
    if shortlist:
        log.info("Отбор пар: подходят по размеру %d, из них надо замерить %d "
                 "(остальные уже есть в файле)", len(passed), len(shortlist))
        runtime_events.record(
            "пары", f"отбор: замеряю {len(shortlist)} пар, остальные взяты из "
                    f"сохранённых замеров")
    else:
        log.info("Отбор пар: все %d пар уже замерены — беру из файла", len(passed))

    # ---- ЭТАП 2: настоящий замер, только для выживших -------------------
    # Сначала добавляем в «Обзор рынка» ВСЕХ выживших, и только потом
    # замеряем: терминал начинает подкачивать историю в момент добавления, и
    # к моменту замера она успевает подойти. Замеряй мы сразу после
    # добавления каждой, последние пары остались бы без баров.
    added_now = []
    for name in shortlist:
        if time.time() > deadline:
            break
        try:
            if mt5c.select_symbol(name) == "добавлена":
                added_now.append(name)
        except Exception:       # noqa: BLE001
            continue

    surveyed, no_data, skipped = [], 0, 0
    for name in shortlist:
        if time.time() > deadline:
            skipped = len(shortlist) - len(surveyed) - no_data
            log.warning("Отбор пар: время вышло — замерено %d из %d, "
                        "остальные посмотрю при следующем запуске",
                        len(surveyed), len(shortlist))
            break
        try:
            row = survey_symbol(name)
        except Exception:       # noqa: BLE001
            continue
        if row:
            surveyed.append(row)
        else:
            no_data += 1
    if no_data:
        log.info("Отбор пар: по %d парам брокер не дал баров — пропущены", no_data)

    # Свежие замеры складываем к сохранённым и сохраняем обратно: то, что не
    # успели в этот раз, замерится при следующем запуске, и так пока не будет
    # покрыт весь список брокера.
    cached = symbol_cache.merge(cached, surveyed)
    line = symbol_cache.describe(cached, len(passed), len(surveyed))
    log.info("%s", line)
    # В окно — только пока замеры ещё идут: когда всё замерено, сообщать не о
    # чем, а лишняя строка в «Внимание» отвлекает от настоящих предупреждений.
    if len(cached) < len(passed):
        runtime_events.record("пары", line)

    # ОТСЕВ ПО РАЗДЕЛУ — ПЕРЕД ОКОНЧАТЕЛЬНЫМ ВЫБОРОМ, А НЕ ТОЛЬКО ПЕРЕД
    # ЗАМЕРОМ. Иначе запрет обходится через файл замеров, см. survey_symbol.
    пригодные, чужой_раздел = symbol_picker.only_allowed_groups(
        symbol_cache.usable_rows(cached, available),
        tuple(getattr(cfg, "AUTO_PICK_GROUPS", symbol_picker.DEFAULT_GROUPS) or ()))
    if чужой_раздел:
        log.warning("Отбор пар: отброшено не из разрешённого раздела: %d (%s)",
                    len(чужой_раздел), ", ".join(чужой_раздел[:5]))

    result = symbol_picker.pick(
        пригодные, equity,
        limit=int(getattr(cfg, "AUTO_PICK_LIMIT", symbol_picker.DEFAULT_LIMIT)),
        max_risk_percent=float(getattr(cfg, "MAX_TRADE_RISK_PERCENT_OF_EQUITY", 2.0) or 0),
        max_spread_ratio=float(getattr(cfg, "AUTO_PICK_MAX_SPREAD_RATIO", 0.25) or 0),
        per_currency=int(getattr(cfg, "AUTO_PICK_PER_CURRENCY",
                                 symbol_picker.DEFAULT_PER_CURRENCY)))

    chosen = result["chosen"]

    # УБИРАЕМ ЗА СОБОЙ. Программа добавляет пары в «Обзор рынка», чтобы их
    # замерить, — иначе терминал не отдаёт ни баров, ни спреда. Но оставлять
    # их там нельзя: у брокера тысячи инструментов, и «Обзор рынка», забитый
    # сотнями чужих акций, замедляет сам терминал. Владелец это и увидел на
    # снимке экрана: A, AA, AAA, AAAA...
    #
    # Убираем ТОЛЬКО то, что добавили сами, и только то, что не нужно: пары в
    # работе и пары с открытой сделкой остаются на месте.
    added_all = set(symbol_cache.load_added(
        server=str(getattr(acc_server, "server", "") or ""))) | set(added_now)
    try:
        busy = {p.symbol for p in (mt5c.get_open_positions() or ())}
    except Exception:           # noqa: BLE001
        busy = set()
    extra = symbol_cache.to_clean_up(added_all, chosen, busy)
    cleaned = 0
    for name in extra:
        if mt5c.deselect_symbol(name):
            cleaned += 1
            added_all.discard(name)
    if cleaned:
        log.info("Убрал из «Обзора рынка» %d пар, которые добавлял для замера",
                 cleaned)

    symbol_cache.save(cached, server=str(getattr(acc_server, "server", "") or ""),
                      added=sorted(added_all))

    if chosen:
        # В ЖУРНАЛ — полный список: там его и надо искать, когда разбираешься.
        log.info("%s", symbol_picker.describe(result, len(available)))
        # В ОКНО — только сколько. Полный список из сотен имён однажды занял
        # в рамке «Внимание» пол-экрана и вытеснил настоящие предупреждения.
        runtime_events.record(
            "пары", symbol_picker.describe(result, len(available), names=False))
    else:
        log.warning("Отбор пар ничего не выбрал — работаю по списку из настроек")
    for reason in (stage1["rejected"] + result["rejected"])[:10]:
        log.info("Отбор пар — %s", reason)
    if skipped > 0:
        log.info("Отбор пар — %d пар не успели попасть в замер", skipped)
    return chosen


def seed_spread_baselines(symbols, deadline: float = None) -> int:
    """Взять суточную норму спреда из минутных баров MetaTrader.

    Владелец: «скачал обновление, перезакрыл, заново открыл — и опять пошли
    сделки». Одна из причин была именно здесь, и создал её я. Защита от
    неликвида не судит, пока не накопит час наблюдений, — а после каждой
    установки программы наблюдений НЕТ, и первый час она молчала. Перезапуск
    буквально снимал защиту на час.

    Ждать час не нужно: MetaTrader хранит спред в КАЖДОМ баре. 1440 минутных
    баров — это готовая суточная норма, по этому же брокеру и этой же паре.

    ОГРАНИЧЕНО ПО ВРЕМЕНИ. Здесь запрашивается 1440 минутных баров НА КАЖДУЮ
    пару. Пока пар было четыре, это никого не касалось; когда программа стала
    отбирать десятки, тот же цикл превратился в минуты ожидания на запуске —
    ровно то, из-за чего владелец написал «нет отклика, виснет». Норма спреда
    полезна, но она НЕ условие работы: не успели по всем — возьмём остальные
    на следующем запуске, а торговать можно уже сейчас.

    Возвращает, скольким парам норма проставлена."""
    if deadline is None:
        deadline = time.time() + float(
            getattr(cfg, "BASELINE_SEED_MAX_SECONDS", 15) or 15)
    seeded = 0
    for symbol in symbols or ():
        if time.time() > deadline:
            log.info("Норма спреда: время вышло, взял по %d парам — остальные "
                     "накопятся сами или подтянутся при следующем запуске", seeded)
            break
        try:
            df = mt5c.get_rates_df(symbol, "M1", count=market_hours.BASELINE_SAMPLES)
            if df is None or "spread" not in df:
                continue
            if market_hours.seed_baseline(symbol, df["spread"].tolist()):
                seeded += 1
        except Exception as e:      # noqa: BLE001
            # История спреда — приятное дополнение, а не условие работы.
            # Не вышло по одной паре — молча идём дальше.
            log.debug("Не удалось взять историю спреда по %s: %s", symbol, e)
    if seeded:
        log.info("Норма спреда взята из истории по %d парам — защита от "
                 "неликвида работает сразу, а не через час", seeded)
        market_hours.save_baseline()
    return seeded


def _bot_tickets(positions) -> set:
    """Тикеты открытых сделок ЭТОГО бота — набор для cleanup_peak_profit().

    Отдельная функция, чтобы оба места (главный цикл и быстрый монитор)
    считали набор одинаково: разойдись они хоть раз, уборка снова начала бы
    стирать живые сделки. Чужие сделки (открытые вручную или другим
    советником) отсекаются по magic — их память бот и не заводит."""
    if not positions:
        return set()
    return {p.ticket for p in positions if getattr(p, "magic", None) == cfg.MAGIC_NUMBER}


def _open_symbols(positions) -> set:
    """Пары, по которым СЕЙЧАС есть открытая сделка бота.

    Эти пары обходятся на каждом проходе без очереди: по ним работает
    трейлинг-стоп и безубыток, и пропущенный проход стоит денег."""
    if not positions:
        return set()
    return {p.symbol for p in positions
            if getattr(p, "magic", None) == cfg.MAGIC_NUMBER}


# Состояние обхода пар по кругу: докуда дошли, сколько берём за проход и
# сколько прошлый проход занял на самом деле. Живёт между итерациями цикла.
_scan = {"cursor": 0, "size": 0, "spent": 0.0}


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
            # Уборка памяти — здесь же, по полному списку позиций. Монитор
            # ходит раз в секунду и раньше (через manage_open_positions)
            # стирал состояние чужих инструментов чаще всех остальных.
            tm.cleanup_peak_profit(_bot_tickets(positions))
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
                tm.manage_open_positions(sym, management_atr(sym, atr_value), point,
                                         positions=sym_positions,
                                         learned_tp_points=al.learned_profit_points(st, 0.0))
        except Exception as e:
            log.exception("Ошибка быстрого мониторинга позиций: %s", e)


def выгрузить_сделки_по_расписанию(acc_info):
    """Каждые два часа сложить историю сделок брокера в отдельный файл.

    ЗАЧЕМ. Регламент демо-приёмки требует разбирать каждую сделку по
    фактам БРОКЕРА, а не по нашей записи. Владелец потребовал, чтобы это
    происходило само: кнопку нажимать не надо.

    ПОЧЕМУ ЭТО БЕЗОПАСНО ДЛЯ ТОРГОВЛИ. Функция ничего не решает и не
    отправляет. Любая её ошибка гасится здесь и не доходит до цикла:
    выгрузка — наблюдатель, а наблюдатель не имеет права уронить
    наблюдаемого.

    ПОЧЕМУ НЕУДАЧА НЕ МОЛЧИТ. «Терминал не ответил» и «сделок не было» —
    разные вещи, и вторую нельзя выдать за первую. Неудача попадает в
    ленту событий, которую владелец видит в окне программы."""
    try:
        итог = tj.выгрузить_если_пора(
            mt5c,
            magic=cfg.MAGIC_NUMBER,
            счёт=getattr(acc_info, "login", None),
            сервер=getattr(acc_info, "server", ""))
    except Exception as e:  # noqa: BLE001
        log.error("Выгрузка сделок по расписанию сорвалась: %s", e)
        return

    состояние = итог.get("состояние")
    if состояние == "сделано":
        сообщение = (f"выгружена история сделок у брокера: "
                     f"{итог['сделок']} строк")
        if итог.get("неопознанные"):
            сообщение += (f"; БЕЗ НОМЕРОВ: {len(итог['неопознанные'])} — "
                          f"такие сделки нельзя сверить")
        runtime_events.record("выгрузка", сообщение)
    elif состояние == tj.НЕИЗВЕСТНО:
        runtime_events.record(
            "выгрузка",
            "историю сделок у брокера получить НЕ удалось. Это не значит, "
            "что сделок не было: терминал мог быть недоступен. Повторю "
            "через несколько минут.")
    elif состояние == "ошибка":
        runtime_events.record(
            "выгрузка", f"выгрузка сделок не состоялась: {итог.get('почему')}")


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
    # Норма спреда прошлого запуска: без неё первый час после старта защита
    # от неликвида молчит, а ночной перезапуск учил бы её заново по ночным же
    # замерам.
    restored = market_hours.load_baseline()
    if restored:
        log.info("Норма спреда восстановлена по %d парам", restored)

    log.info("Запуск AI Scalper Standalone | LIVE_TRADING=%s | профиль=%s | символы=%s",
              cfg.LIVE_TRADING, cfg.RISK_PROFILE.value, cfg.SYMBOLS)
    if not cfg.LIVE_TRADING:
        log.warning("LIVE_TRADING=False — это СУХОЙ ПРОГОН, реальные ордера отправляться НЕ будут.")

    # Если main.py запущен напрямую (не через desktop_app.py, где пароль уже
    # спросили на экране входа) — и секреты в config.py зашифрованы, спросим
    # пароль здесь. Если ничего не зашифровано — просто ничего не произойдёт.
    if stop_event is None:
        _cli_unlock_secrets_if_needed()

    acc = mt5c.connect()

    # ПРЕДТОРГОВЫЙ БАРЬЕР — СРАЗУ ПОСЛЕ ПОДКЛЮЧЕНИЯ И ДО ВСЕГО ОСТАЛЬНОГО.
    #
    # До этой правки единственным барьером перед отправкой заявок был флаг
    # LIVE_TRADING. Но он не отвечает на вопрос, НА КАКОМ СЧЁТЕ мы сейчас.
    # А счёт может оказаться не тот: если логин в настройках не заполнен,
    # программа входит в первый готовый счёт из хранилища терминала.
    #
    # Барьер выдаёт разрешение, только если счёт, сервер и режим совпали с
    # заранее заданными. Не совпало или не спросилось — разрешения нет, и
    # тогда ни одна заявка отсюда уйти не может: проверка стоит внутри
    # самих отправляющих вызовов, а не рядом с ними.
    можно_торговать, почему = pretrade_gate.открыть(cfg, mt5c.get_account_info)
    if not можно_торговать:
        текст = f"ТОРГОВЛЯ ЗАПРЕЩЕНА ПРЕДТОРГОВЫМ БАРЬЕРОМ: {почему}"
        log.error(текст)
        runtime_events.record("исполнение", текст)
    else:
        # Пишем ИМЕННО ТОТ счёт, на который выдано разрешение. Иначе в
        # журнале осталось бы «барьер открыт» без ответа на вопрос «где».
        разрешено = pretrade_gate.разрешение()
        текст = (f"Предторговый барьер открыт: счёт {разрешено.get('номер')} "
                 f"({разрешено.get('сервер')}), режим счёта "
                 f"{разрешено.get('режим')}.")
        log.info(текст)
        runtime_events.record("исполнение", текст)

    acc_state = AccountState(day_start_equity=acc.equity, peak_equity=acc.equity, last_trade_day=datetime.now())

    # ЗАЩИТА СЧЁТА ПЕРЕЖИВАЕТ ПЕРЕЗАПУСК. Лимит просадки считается от ПИКА
    # счёта, а пик жил только в памяти процесса: перезапуск обнулял его до
    # текущего эквити, просадка становилась нулевой, и запрет снимался сам
    # собой. То же с дневным лимитом убытка, который считается от эквити на
    # начало дня. Ни один порог здесь не меняется — восстанавливаются только
    # числа, от которых они отсчитываются (см. risk_state.py).
    restored = risk_state.load(acc_state, getattr(acc, "login", 0))
    if restored:
        log.info("Восстановлено состояние защиты счёта: %s", restored)

    # НЕЗАКРЫТЫЙ ИНЦИДЕНТ ИСПОЛНЕНИЯ ТОЖЕ ПЕРЕЖИВАЕТ ПЕРЕЗАПУСК.
    #
    # Раньше остановка после невыясненной заявки жила в памяти процесса:
    # перезапуск снимал её сам собой, и программа шла торговать, возможно,
    # поверх неучтённой позиции. Причём перезапуск в такой момент как раз
    # и вероятен — программу перезапускают, когда с ней что-то не так.
    #
    # Здесь отметка читается с диска. Снять её программа не может: только
    # человек и только явным подтверждением (см. incident.py).
    # ЗАПИСЬ ОДНАЖДЫ НЕ УДАЛАСЬ — ТОРГОВАТЬ НЕЛЬЗЯ.
    #
    # Проверяется ПЕРВЫМ, до обычного инцидента, и вот почему. Файл
    # инцидента мог не записаться вовсе — тогда обычная проверка ниже
    # ничего не найдёт и скажет «всё чисто». Метка о потере записи
    # остаётся единственным следом того, что происшествие было.
    #
    # Программа не знает, что именно случилось: доказательство потеряно.
    # Значит, единственный безопасный ответ — не торговать, пока человек
    # не посмотрит счёт сам. Ложная пауза неприятна, но новой позиции она
    # не создаёт; ложное «всё чисто» — создаёт.
    # СВЕРКА ОЖИДАЕМЫХ ЗАЯВОК ПРИ ЗАПУСКЕ.
    #
    # Журнал лежит на диске и переживает перезапуск. Если в нём осталась
    # заявка, которой при прошлом запуске не нашлось подтверждения, — это
    # первое, что надо выяснить, а не тринадцатое.
    try:
        _ожидаемые = pending.открытые()
        if _ожидаемые:
            _позиции_сейчас = mt5c.get_open_positions() or []
            _сверка = pending.сверить(
                _позиции_сейчас, magic=cfg.MAGIC_NUMBER,
                сделки=_история_для_сверки(_ожидаемые))
            for _н in _сверка["нашлись"]:
                log.info("Ожидаемая заявка подтверждена (%s): %s тикет %s",
                         _н.get("где", "?"), _н["запись"].get("символ"),
                         _н.get("тикет"))
            if _сверка["пропали"]:
                текст = (f"ОСТАНОВКА: в журнале осталось "
                         f"{len(_сверка['пропали'])} отправленных заявок, "
                         f"которым НЕ нашлось позиции на счету. Заявка "
                         f"могла исполниться позже или ещё быть активной. "
                         f"Проверьте счёт в терминале.")
                log.error(текст)
                runtime_events.record("исполнение", текст)
                control.push_notification("Неподтверждённые заявки", текст)
                # ЗАПИСЫВАЕМ НЕ ТОЛЬКО СКОЛЬКО, НО И ЧТО ИМЕННО.
                #
                # Раньше в отметку клалось одно число — «сколько». Дальше
                # разбор (reconcile) искал номера заявок, не находил ни
                # одного и честно отвечал «номер заявки в инциденте не
                # записан, связь проследить нечем». То есть запрет
                # ставился так, что снять его по доказательству было
                # НЕВОЗМОЖНО в принципе: данные для доказательства
                # выбрасывались в момент постановки запрета. Владелец:
                # «мне не нужно, чтобы бот постоянно стоял в запрете».
                #
                # Номер заявки известен не всегда — ради таких случаев
                # запрет и существует. Но symbol, объём, направление и
                # время известны ВСЕГДА: они пишутся до отправки. Их и
                # сохраняем — по ним человек находит сделку в терминале.
                _открыть_инцидент(
                    _сверка["пропали"][0].get("символ", "?"),
                    "неподтверждённая заявка", текст,
                    {"сколько": len(_сверка["пропали"]),
                     "тикеты_неподтверждённых": [
                         int(з.get("тикет") or 0) for з in _сверка["пропали"]
                         if int(з.get("тикет") or 0) > 0],
                     "неподтверждённые": [
                         {"символ": str(з.get("символ") or "?"),
                          "лот": float(з.get("лот") or 0),
                          "направление": int(з.get("направление") or 0),
                          "когда_utc": str(з.get("когда_utc") or ""),
                          "ид": str(з.get("ид") or "")}
                         for з in _сверка["пропали"]]})
            for _н in _сверка["нашлись"]:
                pending.подтвердить(_н["запись"]["ид"], _н["тикет"])
    except Exception as e:  # noqa: BLE001
        log.exception("Сверка ожидаемых заявок при запуске не отработала: %s", e)

    if incident.запись_терялась():
        текст = ("ОСТАНОВКА: программа однажды НЕ СМОГЛА записать отметку о "
                 "происшествии на диск, и её перезапустили. Что именно "
                 "случилось — неизвестно, запись потеряна. Новые сделки "
                 "открываться НЕ БУДУТ. Проверьте счёт в терминале и "
                 f"удалите файл {incident.ФАЙЛ_ПОТЕРИ} рядом с программой, "
                 "когда разберётесь.")
        log.error(текст)
        runtime_events.record("исполнение", текст)
        control.push_notification("Отметка о происшествии потеряна", текст)
        control.открыть_инцидент({
            "вид": "потеряна запись",
            "причина": "не удалось записать прошлый инцидент на диск",
        })

    if control.перечитать_инцидент():
        сведения = control.инцидент()
        текст = (f"НАЙДЕН НЕЗАКРЫТЫЙ ИНЦИДЕНТ от {сведения.get('открыт', '?')}: "
                 f"{сведения.get('причина', 'подробности не читаются')}. "
                 f"Новые сделки открываться НЕ БУДУТ, пока вы не проверите "
                 f"счёт в терминале и не снимете отметку.")
        log.error(текст)
        runtime_events.record("исполнение", текст)
        control.push_notification("Незакрытый инцидент", текст)

        # И СРАЗУ СОБИРАЕМ ФАКТЫ. Раньше человеку говорили «разбирайтесь»
        # и на этом всё. Теперь программа идёт и смотрит: позиции,
        # активные заявки, история заявок и сделок — и показывает, что
        # нашла. Снять инцидент она при этом НЕ МОЖЕТ ни при каком
        # вердикте: это делает только человек.
        try:
            вердикт = control.сверить_инцидент()
            log.error("СВЕРКА ПОСЛЕ ИНЦИДЕНТА:\n%s", вердикт.рассказ())
            runtime_events.record("исполнение",
                                  f"Сверка: {вердикт.состояние}. "
                                  f"{вердикт.совет}")
        except Exception as e:  # noqa: BLE001
            # Сверка — помощь человеку, а не условие запуска. Если она
            # сама сломалась, запрет всё равно остаётся в силе.
            log.exception("Сверка после инцидента не отработала: %s", e)

    # Отбор пар — ДО init_states: он и решает, с каким списком работать.
    # Делается один раз при запуске: замер всех пар брокера занимает время, а
    # спред и подвижность инструмента за час не меняются настолько, чтобы
    # пересматривать список постоянно.
    picked = auto_pick_symbols(acc.equity, acc_server=acc)
    if picked:
        cfg.SYMBOLS = picked

    sym_states = init_states()

    # ПАУЗЫ ПОСЛЕ СЕРИИ УБЫТКОВ — ПОСЛЕ построения списка инструментов.
    # Раньше их не было на диске вовсе: перезапуск снимал наказание, и
    # программа шла торговать тем же инструментом, на котором только что
    # получила серию убытков. Перезапуск в такой момент как раз и
    # вероятен — его делают, когда результат не нравится.
    try:
        вернулись = risk_state.восстановить_паузы(
            sym_states, getattr(acc, "login", 0))
        if вернулись:
            log.info("Восстановлены паузы после серии убытков: %s", вернулись)
            runtime_events.record(
                "риск", f"после перезапуска паузы сохранены: {вернулись}")
    except Exception as e:  # noqa: BLE001
        log.exception("Восстановление пауз не отработало: %s", e)
    if not sym_states:
        log.error("Ни один символ из SYMBOLS не доступен у брокера — нечего торговать. Останов.")
        mt5c.disconnect()
        return

    # Возвращаем статистику обучения с прошлого запуска — без этого окно
    # результатов обнулялось при каждом старте и бот никогда не доходил до
    # AUTO_LEARNING_MIN_TRADES (см. auto_learning.load_learning_state).
    al.load_learning_state(sym_states)

    # Чтение сигналов из Telegram — если включено. Отказ здесь НЕ мешает
    # торговле: без сигналов бот работает как обычно, просто пишет причину
    # в журнал. Ставить торговлю в зависимость от стороннего источника нельзя.
    if telegram_signals.enabled():
        problem = telegram_reader.start()
        if problem:
            log.warning("Telegram: %s", problem)

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
                    if connect_failures == 1:
                        runtime_events.record(
                            "связь", "потеряна связь с терминалом MT5 — "
                                     "торговля приостановлена до восстановления")
                    if cfg.USE_AUTO_RECONNECT and connect_failures >= cfg.RECONNECT_AFTER_FAILURES:
                        log.warning("Пробую автоматически переподключиться к MT5...")
                        try:
                            mt5c.disconnect()
                            mt5c.connect()
                            connect_failures = 0
                            runtime_events.record("связь", "переподключение к MT5 удалось")
                        except Exception as e:
                            runtime_events.record(
                                "связь", f"переподключиться не удалось: {e}. "
                                         f"Терминал MT5 открыт?")
                    _sleep_interruptible(cfg.POLL_SECONDS, stop_event)
                    continue
                connect_failures = 0

                # Норму спреда добираем из истории ОДИН раз за запуск, сразу
                # после того как связь есть: до подключения MetaTrader баров
                # не отдаст. Без этого защита от неликвида молчала первый час
                # после КАЖДОЙ установки программы — перезапуск буквально
                # снимал её (см. seed_spread_baselines).
                if not _baselines_seeded["done"]:
                    _baselines_seeded["done"] = True
                    seed_spread_baselines(list(sym_states.keys()))

                # Сначала забираем настройки из GitHub, потом перечитываем
                # config.py: так изменения применяются на этой же итерации, а
                # не через одну.
                sync_remote_settings()
                reload_config_if_changed(sym_states)
                # Норму спреда сохраняем на диск, чтобы перезапуск НОЧЬЮ не
                # заставил программу заново выучить ночной спред как
                # нормальный (см. market_hours.save_baseline).
                market_hours.save_baseline()

                equity = acc_info.equity
                check_new_day(acc_state, equity)
                process_closed_deals(acc_state, sym_states)
                учесть_круг_в_диспетчере(sym_states)
                выгрузить_сделки_по_расписанию(acc_info)

                # Пик счёта и начало дня — на диск, но ТОЛЬКО когда они
                # изменились: цикл крутится каждые несколько секунд, а пик
                # обновляется редко (см. risk_state.save_if_changed).
                risk_state.save_if_changed(acc_state, getattr(acc_info, "login", 0),
                                           sym_states=sym_states)

                # Ускорение цикла: ОДИН запрос всех открытых позиций на всю
                # итерацию вместо отдельного запроса на каждый символ (было:
                # N лишних обращений к MT5-терминалу за проход по symbols —
                # основная причина задержки в 100+ мс при нескольких парах).
                all_positions = mt5c.get_open_positions()

                # РЕЗЕРВЫ ПРОШЛОГО ПРОХОДА БОЛЬШЕ НЕ НУЖНЫ. Свежий снимок уже
                # содержит те сделки, которые они описывали. Очистка стоит
                # ВПЛОТНУЮ к запросу намеренно: между этими двумя строками не
                # должно быть ничего, что читает резервы, — иначе оно увидит
                # чужой проход. См. reservations.py.
                # ВИСЯЩИЕ ЗАЯВКИ СВЕРЯЮТСЯ ДО ОЧИСТКИ, А НЕ ПОСЛЕ.
                #
                # Раньше резервы стирались безоговорочно: считалось, что
                # свежий список позиций уже содержит всё, что они
                # описывали. Это неверно. Список приходит от терминала, а
                # терминал отвечает не мгновенно — ровно поэтому резервы и
                # понадобились. Стереть их и поверить списку значит
                # поверить тому самому источнику, чья задержка и создала
                # проблему.
                #
                # Теперь: если в журнале ожидаемых заявок есть запись, а
                # подходящей позиции в свежем снимке НЕТ, бронь остаётся.
                # Пропала не бронь — пропало подтверждение.
                _висящие = _сверить_ожидаемые_заявки(all_positions)
                acc_state.reservations.очистить()
                for _з in _висящие:
                    acc_state.reservations.забронировать(
                        _з.get("символ", ""), int(_з.get("направление", 0) or 0),
                        float(_з.get("риск_денег", 0) or 0))

                # Память о сделках (пик прибыли, просадка, исходный риск,
                # возраст) чистится РОВНО ЗДЕСЬ — там, где виден полный список
                # позиций по всем инструментам сразу. Раньше её чистила
                # manage_open_positions по каждому символу отдельно, а та знает
                # только свой инструмент — и стирала состояние чужих сделок на
                # каждом проходе. Подробный разбор последствий — в
                # trade_manager.cleanup_peak_profit().
                tm.cleanup_peak_profit(_bot_tickets(all_positions))

                process_close_requests(all_positions)

                # Обход пар: с открытой сделкой — всегда, остальные по очереди.
                # Так список может быть в разы длиннее, а круг не растягивается.
                # Разбор, почему это правильно и почему потолок в 20 пар был
                # ненастоящим, — в scan_rotation.py.
                _scan["size"] = scan_rotation.adjust_slice(
                    _scan["size"] or scan_rotation.planned_slice(
                        len(sym_states), cfg.POLL_SECONDS,
                        float(getattr(cfg, "SCAN_ROTATE_SECONDS",
                                      scan_rotation.ROTATE_SECONDS))),
                    _scan["spent"], cfg.POLL_SECONDS, len(sym_states))
                step = scan_rotation.plan(list(sym_states.keys()),
                                          _open_symbols(all_positions),
                                          _scan["cursor"], _scan["size"])
                _scan["cursor"] = step["cursor"]

                _scan_started = time.time()
                for sym in step["symbols"]:
                    st = sym_states.get(sym)
                    if st is None:
                        continue
                    try:
                        process_symbol(sym, st, acc_state, equity, acc_info, all_positions)
                    except Exception as e:
                        log.exception("Ошибка обработки %s: %s", sym, e)
                # Время замеряем ФАКТИЧЕСКОЕ: на медленном терминале порция
                # сама уменьшится, на быстром — подрастёт. Иначе моя оценка
                # «40 мс на пару» стала бы обещанием, за которое платит владелец.
                _scan["spent"] = time.time() - _scan_started

                if cfg.USE_WEB_DASHBOARD:
                    ds.update_snapshot(build_snapshot(acc_info, acc_state, sym_states, all_positions))

                if (datetime.now() - last_status_print).total_seconds() >= 30:
                    print_status(sym_states, acc_state, equity)
                    if cfg.USE_WEB_DASHBOARD:
                        control.add_equity_sample(datetime.now().strftime("%H:%M:%S"), equity)
                    last_status_print = datetime.now()
                # Пульс ставим ТОЛЬКО после полностью пройденного круга: он
                # должен означать «цикл реально работает», а не «поток ещё
                # существует».
                _heartbeat["at"] = time.time()
            except Exception as e:
                # Защита от падения цикла целиком: одна неожиданная ошибка на итерации
                # не должна убивать весь процесс — логируем и пробуем на следующем опросе.
                log.exception("Неожиданная ошибка в главном цикле (продолжаю работу): %s", e)
                runtime_events.record(
                    "ошибка", f"сбой в главном цикле: {type(e).__name__}: {e}")

            # РАНЬШЕ ЭТОТ ВЫЗОВ СТОЯЛ СНАРУЖИ try/except выше. Любая ошибка
            # внутри него улетала мимо защиты, а внешний try ловил только
            # KeyboardInterrupt — то есть цикл молча выходил через finally,
            # поток умирал, окно продолжало показывать «Работает», и сделки
            # переставали открываться до перезапуска программы. Ровно на это
            # жаловался владелец: «работает пару часов и всё, потом надо
            # перезапуск приложения».
            try:
                _fast_position_monitor(sym_states, stop_event, cfg.POLL_SECONDS)
            except Exception as e:  # noqa: BLE001
                log.exception("Ошибка в мониторе позиций (продолжаю работу): %s", e)
                runtime_events.record(
                    "ошибка", f"сбой в мониторе позиций: {type(e).__name__}: {e}")
                _sleep_interruptible(cfg.POLL_SECONDS, stop_event)
    except KeyboardInterrupt:
        log.info("Остановлено пользователем (Ctrl+C).")
    except Exception as e:  # noqa: BLE001
        # Досюда доходить не должно — но если дошло, это надо ВИДЕТЬ, а не
        # гадать, почему бот замолчал.
        log.exception("Торговый цикл аварийно завершился: %s", e)
        runtime_events.record(
            "остановка", f"торговый цикл аварийно завершился: {type(e).__name__}: {e}")
        raise
    finally:
        # Штатное завершение — сохраняем выученное ещё раз. Основное
        # сохранение идёт по факту каждой закрытой сделки (см.
        # process_closed_deals), это добор на случай правок в окнах обучения
        # без закрытия сделок.
        try:
            al.save_learning_state(sym_states)
        except Exception as e:
            log.warning("Не удалось сохранить статистику обучения при выходе: %s", e)
        mt5c.disconnect()


if __name__ == "__main__":
    main()
