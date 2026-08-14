"""baseline_engine.py — прогон ЖИВОЙ стратегии по истории. Без упрощений.

=====================================================================
ГЛАВНОЕ ПРАВИЛО ЭТОГО ФАЙЛА
=====================================================================
Здесь НЕ НАПИСАНО НИ ОДНОГО ТОРГОВОГО РЕШЕНИЯ. Все решения принимают те же
самые функции, что и в живой торговле:

    signal_engine.calc_signal_score      — счёт сигнала
    indicators.add_all_indicators        — индикаторы
    market_regime.update_market_regime   — режим рынка
    strategies / custom_strategy / multi_indicator — надбавки к счёту
    auto_learning.adaptive_score_threshold — порог входа
    risk_manager.calc_lot, apply_min_stop_floor, spread_ok, volatility_ok,
                 calc_tp_distance*, apply_min_risk_reward_floor
    trade_manager.r_ladder_lock_points, _tiered_lock_percent,
                  tighten_take_profit, break_even_rescue_action

Отдельной «упрощённой стратегии для проверки на истории» не существует.
Если бы она существовала, проверялась бы она, а не то, чем вы торгуете.

Ни одна строка торговых модулей ради этого файла не изменена. Вместо правок
подменяются ИСТОЧНИКИ ДАННЫХ: там, где живая программа спрашивает у
терминала спред, здесь отдаётся спред из истории; где спрашивает у GitHub
новости — отдаётся ноль, и это честно помечено как невоспроизводимое.

=====================================================================
ЧТО ВОСПРОИЗВЕСТИ НЕЛЬЗЯ — И ПОЧЕМУ (NOT REPRODUCIBLE)
=====================================================================
Ничего из перечисленного НЕ заменяется выдуманной логикой. Это не
осторожность, а арифметика: выдуманная замена даёт выдуманный результат,
который выглядит так же убедительно, как настоящий.

  * AI-СИГНАЛ. Ответов ИИ за прошлые месяцы не существует — их никто не
    сохранял. Спросить ИИ сегодня о свече годичной давности значит показать
    ему всё, что случилось после неё: это заглядывание в будущее в чистом
    виде. Прогон идёт БЕЗ AI, вклад AI = 0.
  * НОВОСТИ. Календарь отдаёт события на ближайшие дни, а не снимок того,
    что было известно в прошлом году. Штраф за новость и жёсткая блокировка
    у новости = 0.
  * СИГНАЛЫ TELEGRAM. История чужих сигналов не хранится. Вето и надбавка = 0.
  * КОНТЕКСТ РЫНКА (корреляции). Требует истории ДРУГИХ инструментов за тот
    же период. Если они выгружены — можно включить; по умолчанию = 0.
  * РЫНОК ЗАКРЫТ / НЕЛИКВИДЕН. Определяется по живым тикам и медиане спреда,
    накопленной за работу программы. По барам истории воспроизводится лишь
    частично (спред известен, замершая цена — нет).
  * ПРОСКАЛЬЗЫВАНИЕ, РЕКВОТЫ, ОТКАЗЫ БРОКЕРА, частичное исполнение. В барах
    этого нет. Реальный результат будет ХУЖЕ показанного.
  * ВЕДЕНИЕ СДЕЛКИ ВНУТРИ БАРА. Живая программа смотрит на цену каждые
    несколько секунд, здесь известны только четыре цены бара. Порядок
    касаний внутри бара неизвестен, поэтому берётся ХУДШИЙ вариант: сначала
    стоп, потом цель.

=====================================================================
ЧТО ВОСПРОИЗВОДИТСЯ ПОЛНОСТЬЮ
=====================================================================
Счёт сигнала со всеми весами и вето, режим рынка, откат+пробой, price
action, ATR/ADX/RSI/объём, спред (берётся из истории), надбавки трёх
стратегий, порог входа с подстройкой автообучения, расчёт объёма от риска,
пол стопа, расчёт цели, безубыток, ATR-трейлинг, лестница по R, Profit Lock,
спасение в безубыток, самоотключение убыточного инструмента, анти-дребезг.
"""

import logging
import math
from datetime import datetime, timezone

log = logging.getLogger("baseline_engine")

# Сколько свечей видит живая программа за один раз. Ровно столько же
# показывается и здесь: индикаторы, посчитанные на другом окне, дали бы
# другие числа, и проверялась бы уже не та стратегия.
ОКНО_БАРОВ = 300

# Во сколько раз старший таймфрейм больше рабочего. M5 -> M15.
СТАРШИЙ_ТФ_МНОЖИТЕЛЬ = 3

# Что нельзя воспроизвести по истории. Печатается в отчёте целиком.
NOT_REPRODUCIBLE = {
    "AI-сигнал": "ответов ИИ за прошлое не существует; спросить его сегодня "
                 "значит показать ему будущее относительно той свечи",
    "Новости": "календарь отдаёт будущие события, а не снимок прошлого знания",
    "Сигналы Telegram": "история чужих сигналов не сохраняется",
    "Контекст рынка": "нужна история других инструментов за тот же период",
    "Рынок закрыт/неликвиден": "определяется по живым тикам; из истории "
                               "известен только спред",
    "Проскальзывание и отказы брокера": "в барах этого нет; реальный "
                                        "результат будет хуже показанного",
    "Ведение сделки внутри бара": "известны только четыре цены бара; порядок "
                                  "касаний неизвестен, берётся худший вариант",
}


# =====================================================================
# ПОДМЕНА ИСТОЧНИКОВ ДАННЫХ (торговые модули не меняются)
# =====================================================================
class _Подмена:
    """Временно подменяет то, что живая программа спрашивает у терминала.

    Всё возвращается на место в конце прогона — иначе одна проверка на
    истории испортила бы поведение программы до перезапуска."""

    def __init__(self):
        self._сохранено = []

    def подменить(self, модуль, имя, значение):
        self._сохранено.append((модуль, имя, getattr(модуль, имя, None)))
        setattr(модуль, имя, значение)

    def вернуть(self):
        for модуль, имя, старое in reversed(self._сохранено):
            if старое is None and not hasattr(модуль, имя):
                continue
            setattr(модуль, имя, старое)
        self._сохранено = []


class ФальшивыйТерминал:
    """Отдаёт свойства инструмента из паспорта выгрузки — данные брокера."""

    def __init__(self, meta: dict):
        self.point = float(meta.get("point", 0) or 0.00001)
        self.digits = int(meta.get("digits", 5) or 5)
        self.volume_min = float(meta.get("volume_min", 0.01) or 0.01)
        self.volume_max = float(meta.get("volume_max", 100.0) or 100.0)
        self.volume_step = float(meta.get("volume_step", 0.01) or 0.01)
        self.trade_tick_value = float(meta.get("trade_tick_value", 0) or 0)
        self.trade_tick_size = float(meta.get("trade_tick_size", 0) or 0)
        self.trade_contract_size = float(meta.get("trade_contract_size", 0) or 0)
        self.trade_stops_level = int(meta.get("stops_level", 0) or 0)
        self.spread = 0


def resample(bars, factor: int):
    """Собрать старший таймфрейм из рабочего. M5 x3 = M15.

    Берутся ТОЛЬКО уже прошедшие свечи, и последняя группа собирается лишь
    если она полная: недособранная свеча старшего ТФ — это та же самая
    незакрытая свеча, из-за которой всё и затевалось."""
    итог = []
    for i in range(0, len(bars) - factor + 1, factor):
        кусок = bars[i:i + factor]
        итог.append({
            "time": кусок[0]["time"],
            "open": кусок[0]["open"],
            "high": max(b["high"] for b in кусок),
            "low": min(b["low"] for b in кусок),
            "close": кусок[-1]["close"],
            "tick_volume": sum(int(b.get("tick_volume", 0) or 0) for b in кусок),
        })
    return итог


# =====================================================================
# СЕССИИ И РЕЖИМЫ — ТОЛЬКО ДЛЯ РАЗБИВКИ СТАТИСТИКИ
# =====================================================================
def session_of(bar_time: int, server_offset_hours: float) -> str:
    """Лондон / Нью-Йорк / Азия — по времени UTC, а не по времени сервера.

    Ничего не решает и ни на что не влияет: только помечает сделку, чтобы
    потом можно было посмотреть статистику по сессиям отдельно."""
    if server_offset_hours is None:
        return "неизвестно"
    utc_час = datetime.fromtimestamp(
        bar_time - server_offset_hours * 3600, timezone.utc).hour
    # Границы взяты по обычному времени работы бирж в UTC. Это разметка для
    # отчёта, а не торговый фильтр, поэтому округление до часа допустимо.
    if 7 <= utc_час < 12:
        return "Лондон"
    if 12 <= utc_час < 16:
        return "Лондон+Нью-Йорк"
    if 16 <= utc_час < 21:
        return "Нью-Йорк"
    return "Азия/ночь"


def atr_bucket(atr_value: float, средний_atr: float) -> str:
    """Низкая / обычная / высокая волатильность относительно среднего."""
    if средний_atr <= 0:
        return "неизвестно"
    доля = atr_value / средний_atr
    if доля < 0.8:
        return "низкий ATR"
    if доля > 1.3:
        return "высокий ATR"
    return "обычный ATR"


# =====================================================================
# ПРОГОН
# =====================================================================
def run(symbol: str, bars, meta: dict, equity_start: float = 0.0,
        max_bars: int = 0, progress=None) -> dict:
    """Прогнать живую стратегию по истории одного инструмента.

    Возвращает {"trades", "equity_curve", "bars_seen", "not_reproducible",
    "rejects"}. Ни одной сделки не выдумывает: каждая — результат тех же
    функций, что торгуют вживую."""
    import pandas as pd

    import config as cfg
    import auto_learning as al
    import custom_strategy as cs
    import market_context
    import market_regime as mr
    import mt5_connector as mt5c
    import multi_indicator as mi
    import news_calendar
    import risk_manager as rm
    import signal_engine as se
    import strategies as strat
    import telegram_signals
    import trade_manager as tm
    from indicators import add_all_indicators
    from state import SymbolState

    ряд = list(bars or ())
    if max_bars and len(ряд) > max_bars:
        ряд = ряд[-int(max_bars):]
    if len(ряд) < ОКНО_БАРОВ + СТАРШИЙ_ТФ_МНОЖИТЕЛЬ:
        return {"trades": [], "equity_curve": [], "bars_seen": len(ряд),
                "not_reproducible": NOT_REPRODUCIBLE, "rejects": {},
                "error": (f"Свечей всего {len(ряд)}, а для одного решения нужно "
                          f"минимум {ОКНО_БАРОВ}. Выгрузите больше истории.")}

    инфо = ФальшивыйТерминал(meta)
    point = инфо.point
    цена_пункта = float(meta.get("money_per_point_per_lot", 0) or 0)
    смещение = meta.get("server_utc_offset_hours")
    equity = float(equity_start or 0) or 1000.0
    начальный_капитал = equity

    подмена = _Подмена()
    # Спред — из истории, а не из терминала. Это единственный способ дать
    # живой функции счёта настоящий спред того момента.
    текущий = {"spread": 0}
    подмена.подменить(mt5c, "get_spread_points", lambda s: текущий["spread"])
    подмена.подменить(mt5c, "get_symbol_point", lambda s: point)
    подмена.подменить(rm, "_symbol_info", lambda s: инфо)
    # Невоспроизводимое — строго в ноль, а не в выдуманное значение.
    подмена.подменить(news_calendar, "soft_news_penalty", lambda *a, **k: 0.0)
    подмена.подменить(news_calendar, "is_high_impact_event_near", lambda *a, **k: False)
    подмена.подменить(news_calendar, "detect_news_breakout", lambda *a, **k: (False, 0, 0.0))
    подмена.подменить(telegram_signals, "score_bonus", lambda *a, **k: 0.0)
    подмена.подменить(telegram_signals, "veto_entry", lambda *a, **k: False)
    подмена.подменить(market_context, "market_context_score_adjustment", lambda *a, **k: 0.0)
    подмена.подменить(se, "market_context_score_adjustment", lambda *a, **k: 0.0)
    # Деньги: цена пункта посчитана терминалом при выгрузке (см. паспорт).
    подмена.подменить(rm, "money_risk_per_lot",
                      lambda s, sl_dist, info=None: (sl_dist / point) * цена_пункта
                      if point > 0 and цена_пункта > 0 else 0.0)
    подмена.подменить(rm, "margin_block_reason", lambda *a, **k: "")

    состояние = SymbolState(symbol=symbol)
    профиль = rm.get_profile()
    сделки = []
    кривая = []
    отказы = {}
    открытая = None

    def отказ(причина: str):
        ключ = причина.split(":")[0][:60]
        отказы[ключ] = отказы.get(ключ, 0) + 1

    try:
        всего = len(ряд)
        for i in range(ОКНО_БАРОВ, всего):
            if progress and i % 5000 == 0:
                progress(f"{symbol}: {i}/{всего} свечей, сделок {len(сделки)}")

            окно = ряд[i - ОКНО_БАРОВ + 1:i + 1]
            бар = ряд[i]
            текущий["spread"] = int(бар.get("spread", 0) or 0)

            df = pd.DataFrame(окно)
            df_ind = add_all_indicators(df, cfg)
            atr_value = float(df_ind["atr"].iloc[-1])

            # 1) Ведём уже открытую сделку — ровно как живая программа делает
            #    это ДО поиска нового входа.
            if открытая is not None:
                открытая = _вести(открытая, бар, atr_value, point, cfg, rm, tm,
                                  сделки, инфо)
                if открытая is None:
                    # Сделка закрылась: тот же учёт, что в живой программе.
                    последняя = сделки[-1]
                    equity += последняя["money"]
                    кривая.append({"time": бар["time"], "equity": round(equity, 2)})
                    al.record_trade_result(состояние, последняя["money"])
                    al.record_trade_peak(состояние, последняя["mfe_points"])
                    состояние.last_close_direction = последняя["direction"]
                    состояние.last_close_bar_index = состояние.bar_counter
                    if последняя["money"] < 0:
                        состояние.consecutive_losses += 1
                    else:
                        состояние.consecutive_losses = 0

            состояние.bar_counter += 1
            mr.update_market_regime(состояние, df_ind)

            if открытая is not None:
                отказ("Сделка уже открыта")
                continue
            if atr_value <= 0:
                отказ("Индикаторы не готовы")
                continue

            # 2) Те же фильтры, что и вживую — теми же функциями.
            if not rm.spread_ok(symbol, atr_value, point):
                отказ("Спред слишком широкий")
                continue
            if not rm.volatility_ok(df_ind["atr"], профиль["ignore_soft_filters"]):
                отказ("Резкий скачок волатильности")
                continue
            авто_выкл = al.symbol_auto_off_reason(состояние, equity)
            if авто_выкл:
                отказ("Инструмент отключён сам")
                continue
            if getattr(cfg, "BLOCK_ENTRY_IN_RANGE", False) and состояние.current_regime == "range":
                отказ("Флэт: вход заблокирован")
                continue

            # 3) Счёт сигнала — ЖИВОЙ функцией, со всеми весами и вето.
            старший = resample(окно, СТАРШИЙ_ТФ_МНОЖИТЕЛЬ)
            trend_df = pd.DataFrame(старший) if старший else None
            buy = se.calc_signal_score(symbol, 1, df_ind, trend_df, point, состояние)
            sell = se.calc_signal_score(symbol, -1, df_ind, trend_df, point, состояние)

            # AI пропущен намеренно — см. NOT_REPRODUCIBLE.
            if getattr(cfg, "USE_STRATEGY_SIGNAL", False):
                ключ = getattr(cfg, "ACTIVE_STRATEGY", "balanced_hybrid")
                вес = float(getattr(cfg, "STRATEGY_SIGNAL_WEIGHT", 12))
                buy = strat.apply_strategy_score(
                    buy, strat.calc_strategy_score(ключ, 1, df_ind, atr_value), вес)
                sell = strat.apply_strategy_score(
                    sell, strat.calc_strategy_score(ключ, -1, df_ind, atr_value), вес)
            if getattr(cfg, "USE_CUSTOM_STRATEGY", False):
                buy = cs.apply_custom_strategy(buy, cs.calc_custom_score(1, df_ind, atr_value))
                sell = cs.apply_custom_strategy(sell, cs.calc_custom_score(-1, df_ind, atr_value))
            if getattr(cfg, "USE_MULTI_INDICATOR", False):
                buy = mi.apply_multi_indicator(buy, mi.calc_multi_indicator_score(1, df_ind))
                sell = mi.apply_multi_indicator(sell, mi.calc_multi_indicator_score(-1, df_ind))

            порог = al.adaptive_score_threshold(профиль["min_score_to_trade"], состояние)
            if buy >= порог and buy >= sell:
                направление, score = 1, buy
            elif sell >= порог and sell > buy:
                направление, score = -1, sell
            else:
                отказ(f"Score ниже порога {порог:.0f}")
                continue

            if not rm.reversal_cooldown_ok(состояние, направление):
                отказ("Анти-дребезг")
                continue

            # 4) Стоп, объём, цель — живыми функциями.
            sl_dist = atr_value * профиль["atr_sl_multiplier"]
            sl_dist = rm.apply_min_stop_floor(symbol, sl_dist, atr_value, point)
            lot = rm.calc_lot(symbol, sl_dist, equity, состояние)
            if lot <= 0:
                отказ("Объём не проходит по риску")
                continue

            if getattr(cfg, "USE_MAX_PROFIT_RIDE", False):
                tp_dist = 0.0
            elif профиль["use_money_tp"]:
                tp_dist = rm.calc_tp_distance_money(
                    symbol, lot, rm.effective_target_money(профиль, equity),
                    atr_value, point, sl_dist)
            else:
                tp_dist = rm.calc_tp_distance(sl_dist, atr_value, point)

            вход = float(бар["close"]) + направление * текущий["spread"] * point
            средний_atr = float(df_ind["atr"].iloc[-(cfg.ATR_AVG_PERIOD + 1):-1].mean())
            открытая = {
                "symbol": symbol, "direction": направление, "lot": lot,
                "entry_price": вход, "entry_time": int(бар["time"]),
                "sl": вход - направление * sl_dist,
                "tp": (вход + направление * tp_dist) if tp_dist > 0 else 0.0,
                "risk_points": sl_dist / point if point else 0.0,
                "peak_points": 0.0, "trough_points": 0.0,
                "score": score, "bars_held": 0,
                "session": session_of(int(бар["time"]), смещение),
                "atr_bucket": atr_bucket(atr_value, средний_atr),
                "regime": состояние.current_regime,
                "point": point, "money_per_point": цена_пункта,
            }
            кривая.append({"time": бар["time"], "equity": round(equity, 2)})

        # Незакрытую в конце истории сделку не считаем: её исход неизвестен.
        if открытая is not None:
            отказ("Сделка не закрылась до конца истории (не учтена)")
    finally:
        подмена.вернуть()

    return {
        "symbol": symbol,
        "trades": сделки,
        "equity_curve": кривая,
        "equity_start": начальный_капитал,
        "equity_end": round(equity, 2),
        "bars_seen": len(ряд) - ОКНО_БАРОВ,
        "not_reproducible": NOT_REPRODUCIBLE,
        "rejects": отказы,
    }


def _вести(поз, бар, atr_value, point, cfg, rm, tm, сделки, инфо):
    """Один бар жизни открытой сделки. Возвращает позицию или None (закрылась).

    Порядок ровно тот же, что в trade_manager.manage_open_positions:
    безубыток -> ATR-трейлинг -> лестница по R -> Profit Lock, и только потом
    проверка стопа и цели. Сами решения принимают функции trade_manager, а не
    этот файл."""
    is_buy = поз["direction"] == 1
    high, low = float(бар["high"]), float(бар["low"])
    поз["bars_held"] += 1

    лучшая = (high - поз["entry_price"]) / point if is_buy else (поз["entry_price"] - low) / point
    худшая = (поз["entry_price"] - low) / point if is_buy else (high - поз["entry_price"]) / point
    поз["peak_points"] = max(поз["peak_points"], лучшая)
    поз["trough_points"] = min(поз["trough_points"], -худшая)

    риск = поз["risk_points"]
    лучший_sl = поз["sl"]

    def подтянуть(новый):
        nonlocal лучший_sl
        if is_buy:
            лучший_sl = max(лучший_sl, новый)
        else:
            лучший_sl = min(лучший_sl, новый) if лучший_sl > 0 else новый

    # 1) Безубыток
    if getattr(cfg, "USE_BREAK_EVEN", False):
        be_trigger = (atr_value * cfg.BREAK_EVEN_ATR_MULTIPLIER) / point if point else 0
        if поз["peak_points"] >= be_trigger:
            смещение = rm.eff_points_threshold(cfg.BREAK_EVEN_OFFSET_POINTS, 0.05,
                                               atr_value, point)
            подтянуть(поз["entry_price"] + поз["direction"] * смещение * point)

    # 2) ATR-трейлинг — от ЦЕНЫ ЗАКРЫТИЯ бара: внутрибарового хода мы не знаем.
    if getattr(cfg, "USE_TRAILING_STOP", False):
        мин = rm.eff_points_threshold(cfg.TRAILING_MIN_POINTS, 0.3, atr_value, point)
        шаг = max(мин, (atr_value * cfg.TRAILING_ATR_MULTIPLIER) / point if point else 0)
        текущая_прибыль = ((float(бар["close"]) - поз["entry_price"]) / point) * поз["direction"]
        if текущая_прибыль >= шаг:
            подтянуть(float(бар["close"]) - поз["direction"] * шаг * point)

    # 3) Лестница по R — функцией самого trade_manager
    if getattr(cfg, "USE_R_TRAIL_LADDER", False) and риск > 0:
        замок = tm.r_ladder_lock_points(поз["peak_points"], риск,
                                        getattr(cfg, "R_TRAIL_LADDER", None),
                                        float(getattr(cfg, "R_TRAIL_GIVEBACK_R", 0) or 0))
        if замок is not None:
            подтянуть(поз["entry_price"] + поз["direction"] * замок * point)

    # 4) Profit Lock — тоже функцией trade_manager
    if getattr(cfg, "USE_PROFIT_LOCK_TRAILING", False):
        порог = rm.eff_points_threshold(cfg.PROFIT_LOCK_START_POINTS, 0.15, atr_value, point)
        if риск > 0:
            порог = max(порог, риск * getattr(cfg, "PROFIT_LOCK_START_R_FRACTION", 1.0))
        if поз["peak_points"] >= порог:
            процент = (tm._tiered_lock_percent(поз["peak_points"], порог)
                       if getattr(cfg, "USE_TIERED_PROFIT_LOCK", False)
                       else cfg.PROFIT_LOCK_PERCENT)
            подтянуть(поз["entry_price"] + поз["direction"] * поз["peak_points"] * процент / 100.0 * point)

    поз["sl"] = лучший_sl

    # СНАЧАЛА СТОП. Порядок касаний внутри бара неизвестен, берётся худший
    # для нас вариант — иначе проверка на истории врала бы в нашу пользу.
    стоп_задет = (low <= поз["sl"]) if is_buy else (high >= поз["sl"])
    цель_задета = поз["tp"] > 0 and ((high >= поз["tp"]) if is_buy else (low <= поз["tp"]))

    выход = None
    if стоп_задет:
        выход = поз["sl"]
    elif цель_задета:
        выход = поз["tp"]

    if выход is None:
        return поз

    пункты = ((выход - поз["entry_price"]) if is_buy
              else (поз["entry_price"] - выход)) / point
    сделки.append({
        "symbol": поз["symbol"], "direction": поз["direction"],
        "entry_time": поз["entry_time"], "exit_time": int(бар["time"]),
        "entry_price": поз["entry_price"], "exit_price": выход,
        "lot": поз["lot"], "score": поз["score"],
        "points": round(пункты, 1),
        "money": round(пункты * поз["money_per_point"] * поз["lot"], 2),
        "r": round(пункты / риск, 3) if риск > 0 else 0.0,
        "mae_points": round(поз["trough_points"], 1),
        "mfe_points": round(поз["peak_points"], 1),
        "held_seconds": int(бар["time"]) - поз["entry_time"],
        "session": поз["session"], "atr_bucket": поз["atr_bucket"],
        "regime": поз["regime"],
        # Новости невоспроизводимы, поэтому метка честная, а не выдуманная.
        "news": "не воспроизводится",
    })
    return None


def describe_not_reproducible() -> str:
    строки = ["ЧТО НЕВОЗМОЖНО ВОСПРОИЗВЕСТИ ПО ИСТОРИИ (NOT REPRODUCIBLE):"]
    for имя, почему in NOT_REPRODUCIBLE.items():
        строки.append(f"  * {имя}: {почему}")
    строки.append("Ни одна из этих частей НЕ заменена выдуманной логикой — "
                  "каждая просто отключена и учтена как ноль.")
    return "\n".join(строки)
