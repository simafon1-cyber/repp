"""Рыночные данные из терминала MetaTrader 5 (Python API).

Мост никогда не выдумывает данные: если что-то недоступно или устарело,
в результате явно ставится признак available=False / stale=True.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

try:
    import MetaTrader5 as mt5

    MT5_IMPORTED = True
except ImportError:  # не Windows или пакет не установлен
    mt5 = None
    MT5_IMPORTED = False

from config import CONFIG

# Данные старше этого срока считаем устаревшими
STALE_TICK_SECONDS = 300

_initialized = False


def ensure_mt5() -> bool:
    """Подключение к запущенному терминалу MT5 (однократное)."""
    global _initialized
    if not MT5_IMPORTED:
        return False
    if _initialized:
        return True
    if mt5.initialize():
        _initialized = True
        return True
    return False


def shutdown_mt5() -> None:
    global _initialized
    if MT5_IMPORTED and _initialized:
        mt5.shutdown()
        _initialized = False


def _rates_to_list(rates) -> list[dict]:
    out = []
    for r in rates:
        out.append(
            {
                "time": datetime.fromtimestamp(int(r["time"]), tz=timezone.utc).isoformat(),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "tick_volume": int(r["tick_volume"]),
            }
        )
    return out


def get_candles(symbol: str, timeframe_name: str, count: int) -> dict:
    """Последние ЗАКРЫТЫЕ свечи (без текущей формирующейся)."""
    result = {"available": False, "candles": [], "error": ""}
    if not ensure_mt5():
        result["error"] = "MT5 недоступен"
        return result
    tf = {"H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4}[timeframe_name]
    # с позиции 1 — пропускаем текущую незакрытую свечу
    rates = mt5.copy_rates_from_pos(symbol, tf, 1, count)
    if rates is None or len(rates) == 0:
        result["error"] = f"нет свечей {timeframe_name} для {symbol}"
        return result
    result["available"] = True
    result["candles"] = _rates_to_list(rates)
    return result


def get_tick(symbol: str) -> dict:
    """Актуальные данные по символу с признаком устаревания."""
    result = {"available": False, "stale": True, "bid": None, "ask": None, "time": None, "error": ""}
    if not ensure_mt5():
        result["error"] = "MT5 недоступен"
        return result
    info = mt5.symbol_info_tick(symbol)
    if info is None:
        result["error"] = f"символ {symbol} не найден"
        return result
    age = time.time() - info.time
    result.update(
        {
            "available": True,
            "stale": age > STALE_TICK_SECONDS,
            "age_seconds": int(age),
            "bid": info.bid,
            "ask": info.ask,
            "time": datetime.fromtimestamp(info.time, tz=timezone.utc).isoformat(),
        }
    )
    return result


def find_symbol(candidates: list[str]) -> str | None:
    """Первый символ из списка кандидатов, который есть у брокера."""
    if not ensure_mt5():
        return None
    for name in candidates:
        info = mt5.symbol_info(name)
        if info is not None:
            if not info.visible:
                mt5.symbol_select(name, True)
            return name
    return None


def get_context_symbol(candidates: list[str], label: str) -> dict:
    """Контекстный символ (DXY, US10Y): честное 'нет данных', если недоступен."""
    result = {"label": label, "available": False, "stale": True, "error": ""}
    name = find_symbol(candidates)
    if name is None:
        result["error"] = "символ не найден у брокера"
        return result
    tick = get_tick(name)
    if not tick["available"]:
        result["error"] = tick["error"]
        return result
    candles = get_candles(name, "H1", 24)
    result.update(
        {
            "available": True,
            "symbol": name,
            "stale": tick["stale"],
            "last_price": tick["bid"],
            "h1_closes": [c["close"] for c in candles["candles"]] if candles["available"] else [],
        }
    )
    return result


def build_market_snapshot(canon_symbol: str) -> dict:
    """Полный снимок рынка для запроса к Claude.

    canon_symbol: 'EURUSD' или 'XAUUSD' (канонические имена из запроса советника).
    """
    sym_cfg = CONFIG["symbols"]
    broker_symbol = sym_cfg["eurusd"] if canon_symbol == "EURUSD" else sym_cfg["xauusd"]

    snapshot = {
        "requested_symbol": canon_symbol,
        "broker_symbol": broker_symbol,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mt5_connected": ensure_mt5(),
        "tick": get_tick(broker_symbol),
        "h1": get_candles(broker_symbol, "H1", 48),
        "h4": get_candles(broker_symbol, "H4", 30),
    }

    if canon_symbol == "XAUUSD":
        # Для золота — дополнительный контекст. Если данных нет, так и передаём.
        snapshot["dxy"] = get_context_symbol(sym_cfg["dxy_candidates"], "Индекс доллара (DXY)")
        snapshot["us10y"] = get_context_symbol(sym_cfg["us10y_candidates"], "Доходность 10-летних облигаций США")

    return snapshot
