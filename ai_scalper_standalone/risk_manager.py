"""
risk_manager.py — сколько и можно ли торговать: профили риска, лот, TP/SL,
дневные лимиты, защитные фильтры, авто-адаптация порогов под инструмент.

1:1 портировано из RiskManager.mqh (AI Scalper Pro v8.0), с поправкой на то,
что здесь несколько символов торгуются одним процессом на одном счёте
(см. комментарий в state.py).
"""

import math
from datetime import datetime, timedelta

import config as cfg
import mt5_connector as mt5c
from control import control
from state import AccountState, SymbolState, pause_active


def get_profile():
    # Дашборд может переопределить профиль "на лету" (без перезапуска) —
    # см. control.set_risk_profile(). Если не переопределён — берём из config.py.
    override = control.get_risk_profile()
    profile_key = override if override is not None else cfg.RISK_PROFILE
    return cfg.RISK_PROFILES[profile_key]


# =====================================================================
# АВТОНАСТРОЙКА ПОД ИНСТРУМЕНТ (п.24)
# =====================================================================
def auto_ref_atr_points(atr_value: float, point: float) -> float:
    if atr_value <= 0 or point <= 0:
        return 0.0
    return atr_value / point


def eff_points_threshold(manual_points: float, atr_fraction: float, atr_value: float, point: float) -> float:
    if not cfg.AUTO_ADAPT_TO_SYMBOL:
        return manual_points
    ref = auto_ref_atr_points(atr_value, point)
    if ref <= 0:
        return manual_points  # индикатор ещё не готов — fallback на ручное значение
    return ref * atr_fraction


# =====================================================================
# ЖЁСТКИЕ ЗАЩИТНЫЕ ПРОВЕРКИ
# =====================================================================
def spread_ok(symbol: str, atr_value: float, point: float) -> bool:
    if not cfg.USE_SPREAD_FILTER:
        return True
    max_spread = eff_points_threshold(cfg.MAX_SPREAD_POINTS, 0.4, atr_value, point)
    return mt5c.get_spread_points(symbol) <= max_spread


def volatility_ok(atr_series, ignore_soft_filters: bool) -> bool:
    if ignore_soft_filters:
        return True
    if not cfg.USE_VOLATILITY_SPIKE_GUARD:
        return True
    window = atr_series.iloc[-(cfg.ATR_AVG_PERIOD + 1):]
    if len(window) < 2:
        return True
    avg = window.iloc[:-1].mean()
    if avg <= 0:
        return True
    return (window.iloc[-1] / avg) <= cfg.VOLATILITY_SPIKE_MULTIPLIER


def rollover_guard_ok(ignore_soft_filters: bool) -> bool:
    if ignore_soft_filters:
        return True
    if not cfg.USE_ROLLOVER_GUARD:
        return True
    now = datetime.now()
    now_minutes = now.hour * 60 + now.minute
    rollover_minutes = cfg.ROLLOVER_HOUR_SERVER * 60
    diff = abs(now_minutes - rollover_minutes)
    diff = min(diff, 1440 - diff)
    return diff > cfg.ROLLOVER_GUARD_MINUTES


def trading_hours_ok() -> bool:
    """Опциональное ограничение часов торговли (время сервера MT5) — как
    "Час начала/окончания торговли" в MQL5-советнике. По умолчанию выключено
    (USE_TRADING_HOURS=False) — торгуем круглосуточно, как раньше."""
    if not getattr(cfg, "USE_TRADING_HOURS", False):
        return True
    start = getattr(cfg, "TRADING_START_HOUR", 0)
    end = getattr(cfg, "TRADING_END_HOUR", 24)
    if start == end:
        return True  # одинаковые часы — считаем, что ограничения фактически нет
    hour = datetime.now().hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # диапазон через полночь, напр. 22..6


def reversal_cooldown_ok(sym_state: SymbolState, direction: int) -> bool:
    if sym_state.last_close_direction == 0:
        return True
    if direction != -sym_state.last_close_direction:
        return True
    return (sym_state.bar_counter - sym_state.last_close_bar_index) >= cfg.MIN_BARS_BETWEEN_REVERSAL


# =====================================================================
# ПЛАВНОЕ СНИЖЕНИЕ РИСКА ПО СЕРИИ УБЫТКОВ
# =====================================================================
def loss_streak_risk_multiplier(sym_state: SymbolState) -> float:
    if not cfg.USE_LOSS_STREAK_RISK_SCALING:
        return 1.0
    if cfg.MAX_CONSECUTIVE_LOSSES <= 0:
        return 1.0
    ratio = sym_state.consecutive_losses / cfg.MAX_CONSECUTIVE_LOSSES
    mult = 1.0 - 0.7 * ratio
    return max(cfg.MIN_LOSS_STREAK_RISK_MULTIPLIER, min(1.0, mult))


# =====================================================================
# ИЗДЕРЖКИ ПРОТИВ TP
# =====================================================================
def spread_cost_ok(symbol: str, lot: float, tp_dist: float, ignore_soft_filters: bool) -> bool:
    if ignore_soft_filters:
        return True
    if tp_dist <= 0:
        return True
    info = _symbol_info(symbol)
    if info is None or info.trade_tick_value <= 0 or info.trade_tick_size <= 0:
        return True
    spread_pts = mt5c.get_spread_points(symbol)
    spread_dist = spread_pts * info.point
    spread_money = (spread_dist / info.trade_tick_size) * info.trade_tick_value * lot
    tp_money = (tp_dist / info.trade_tick_size) * info.trade_tick_value * lot
    if tp_money <= 0:
        return True
    return (spread_money / tp_money * 100.0) <= cfg.MAX_SPREAD_COST_PERCENT_OF_TP


def _symbol_info(symbol: str):
    import MetaTrader5 as mt5
    return mt5.symbol_info(symbol)


# =====================================================================
# СОВОКУПНЫЙ РИСК ПО ВСЕМ ОТКРЫТЫМ СДЕЛКАМ ЭТОЙ ПРОГРАММЫ (все символы)
# =====================================================================
def get_open_risk_percent(account, positions=None) -> float:
    equity = account.equity if account else 0.0
    if equity <= 0:
        return 0.0
    # positions можно передать уже готовым списком — один запрос к MT5 на всю
    # итерацию главного цикла вместо отдельного запроса на каждый символ
    # (см. main.py: ускорение цикла, меньше лишних вызовов MT5-терминала).
    # Если не передали (например, вызов с дашборда) — запрашиваем сами, как раньше.
    if positions is None:
        positions = mt5c.get_open_positions(magic=cfg.MAGIC_NUMBER)
    else:
        positions = [p for p in positions if p.magic == cfg.MAGIC_NUMBER]
    total_risk_money = 0.0
    for p in positions:
        if p.sl <= 0:
            continue
        info = _symbol_info(p.symbol)
        if info is None or info.trade_tick_value <= 0 or info.trade_tick_size <= 0:
            continue
        dist = abs(p.price_open - p.sl)
        total_risk_money += (dist / info.trade_tick_size) * info.trade_tick_value * p.volume
    return total_risk_money / equity * 100.0


def count_open_positions(symbol: str = None, positions=None) -> int:
    if positions is None:
        return len(mt5c.get_open_positions(symbol=symbol, magic=cfg.MAGIC_NUMBER))
    return len([p for p in positions
                if p.magic == cfg.MAGIC_NUMBER and (symbol is None or p.symbol == symbol)])


# =====================================================================
# ПРОСАДКА / ДНЕВНОЙ ЛИМИТ (общие на счёт)
# =====================================================================
def daily_loss_limit_hit(acc_state: AccountState, equity: float) -> bool:
    profile = get_profile()
    if not cfg.USE_DAILY_LOSS_LIMIT or acc_state.day_start_equity <= 0:
        return False
    diff = (equity - acc_state.day_start_equity) / acc_state.day_start_equity * 100.0
    return diff <= -abs(profile["daily_loss_limit_pct"])


def max_drawdown_hit(acc_state: AccountState, equity: float) -> bool:
    profile = get_profile()
    if not cfg.USE_MAX_DRAWDOWN_LIMIT:
        return False
    if equity > acc_state.peak_equity:
        acc_state.peak_equity = equity
    if acc_state.peak_equity <= 0:
        return False
    dd = (acc_state.peak_equity - equity) / acc_state.peak_equity * 100.0
    return dd >= profile["max_drawdown_pct"]


def loss_streak_pause_active(sym_state: SymbolState) -> bool:
    return pause_active(sym_state.pause_until)


def trading_allowed(acc_state: AccountState, sym_state: SymbolState, equity: float) -> bool:
    if daily_loss_limit_hit(acc_state, equity):
        return False
    if max_drawdown_hit(acc_state, equity):
        return False
    if loss_streak_pause_active(sym_state):
        return False
    return True


# =====================================================================
# ЛОТ / СТОПЫ
# =====================================================================
def calc_lot(symbol: str, sl_dist: float, equity: float, sym_state: SymbolState) -> float:
    fallback_lot = getattr(cfg, "LOT_FALLBACK", 0.01)
    info = _symbol_info(symbol)
    if info is None:
        return fallback_lot
    min_lot, max_lot, lot_step = info.volume_min, info.volume_max, info.volume_step

    # Дашборд может задать фиксированный лот на символ ("выбор пары и количество
    # лота") — тогда риск-расчёт ниже не используется, просто отдаём это число,
    # округлённое под шаг лота и зажатое в допустимый брокером диапазон.
    override = control.get_lot_override(symbol)
    if override:
        lot = math.floor(override / lot_step) * lot_step
        return max(min_lot, min(max_lot, lot))

    # Если риск-расчёт по проценту эквити явно выключен (USE_RISK_BASED_LOT=False)
    # — как "выключенный расчёт лота по риску" в MQL5-советнике — всегда торгуем
    # фиксированным LOT_FALLBACK (зажатым в допустимый диапазон брокера).
    if not getattr(cfg, "USE_RISK_BASED_LOT", True):
        lot = math.floor(fallback_lot / lot_step) * lot_step
        return max(min_lot, min(max_lot, lot))

    profile = get_profile()
    mult = loss_streak_risk_multiplier(sym_state)

    risk_money = equity * profile["risk_percent"] / 100.0 * mult
    if info.trade_tick_value <= 0 or info.trade_tick_size <= 0 or sl_dist <= 0:
        return min_lot
    loss_per_lot = (sl_dist / info.trade_tick_size) * info.trade_tick_value
    if loss_per_lot <= 0:
        return min_lot
    lot = risk_money / loss_per_lot
    lot = math.floor(lot / lot_step) * lot_step
    return max(min_lot, min(max_lot, lot))


def check_stops_distance(symbol: str, price: float, sl: float, tp: float) -> bool:
    info = _symbol_info(symbol)
    if info is None:
        return True
    min_dist = info.trade_stops_level * info.point
    if min_dist <= 0:
        return True
    if sl != 0 and abs(price - sl) < min_dist:
        return False
    # tp == 0 означает "TP не задан" (см. USE_MAX_PROFIT_RIDE) — не проверяем
    # дистанцию для несуществующего уровня.
    if tp != 0 and abs(price - tp) < min_dist:
        return False
    return True


def apply_min_risk_reward_floor(tp_dist: float, sl_dist: float) -> float:
    """КРИТИЧНАЯ защита (см. MIN_RISK_REWARD_RATIO в config.py, добавлено по
    факту реальных сделок пользователя — TP всего +$1 против SL -$100+): TP
    никогда не опускается ниже sl_dist * MIN_RISK_REWARD_RATIO, даже если
    конкретный профиль/фиксированная денежная цель прибыли посчитали TP
    меньше. Без этого один убыточный трейд "съедает" много мелких прибыльных."""
    min_rr = getattr(cfg, "MIN_RISK_REWARD_RATIO", 0.0)
    if min_rr <= 0 or sl_dist <= 0:
        return tp_dist
    return max(tp_dist, sl_dist * min_rr)


def calc_tp_distance(sl_dist: float, atr_value: float, point: float) -> float:
    tp_points = (sl_dist / point) * cfg.RISK_REWARD_RATIO
    min_pts = eff_points_threshold(cfg.TP_MIN_POINTS, 0.3, atr_value, point)
    max_pts = eff_points_threshold(cfg.TP_MAX_POINTS, 8.0, atr_value, point)
    tp_points = max(min_pts, min(max_pts, tp_points))
    tp_dist = tp_points * point
    return apply_min_risk_reward_floor(tp_dist, sl_dist)


def calc_tp_distance_money(symbol: str, lot: float, target_money: float, atr_value: float, point: float,
                            sl_dist: float = None) -> float:
    info = _symbol_info(symbol)
    if info is None or info.trade_tick_value <= 0 or info.trade_tick_size <= 0 or lot <= 0:
        return 50 * point
    price_move = target_money * info.trade_tick_size / (lot * info.trade_tick_value)
    tp_points = price_move / point
    max_pts = eff_points_threshold(cfg.TP_MAX_POINTS, 8.0, atr_value, point)
    tp_points = max(1, min(max_pts, tp_points))
    tp_dist = tp_points * point
    if sl_dist is not None:
        tp_dist = apply_min_risk_reward_floor(tp_dist, sl_dist)
    return tp_dist
