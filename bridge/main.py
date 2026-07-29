"""Локальный HTTP-мост между советником DualGuard EA и Claude.

Запуск:  python main.py
Слушает ТОЛЬКО 127.0.0.1 (наружу не открывается).

Endpoints:
  GET /health                 — проверка, что мост работает
  GET /regime?symbol=EURUSD   — режим рынка для EURUSD
  GET /regime?symbol=XAUUSD   — режим рынка для XAUUSD
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

import market_data
from cache import cache_fresh, cache_get, cache_put, load_replay_response, log_event
from calendar_data import get_todays_news
from claude_client import ask_claude, validate_response
from config import CONFIG

app = FastAPI(title="DualGuard Bridge", docs_url=None, redoc_url=None)

ALLOWED_SYMBOLS = {"EURUSD", "XAUUSD"}

# Безопасный ответ, когда данных нет вообще
CHAOS_FALLBACK = {
    "regime": "chaos",
    "trade_allowed": False,
    "risk_multiplier": 0.0,
    "reason": "мост не смог получить корректный ответ — безопасный режим",
}


@app.get("/health")
def health() -> dict:
    mt5_ok = market_data.ensure_mt5()
    return {
        "status": "ok",
        "mt5_connected": mt5_ok,
        "model": CONFIG["anthropic"]["model"],
        "mock_mode": CONFIG["mock"]["enabled"],
        "replay_mode": CONFIG["replay"]["enabled"],
        "api_key_present": bool(CONFIG["anthropic"]["api_key"]),
        "cache_ttl_minutes": CONFIG["cache"]["ttl_minutes"],
    }


@app.get("/regime")
def regime(symbol: str = Query(...)) -> JSONResponse:
    symbol = symbol.strip().upper()
    if symbol not in ALLOWED_SYMBOLS:
        log_event({"type": "request", "symbol": symbol, "error": "неизвестный символ"})
        return JSONResponse(status_code=400, content={"error": f"символ {symbol} не поддерживается"})

    # 1. Мок-режим — для тестирования без ключа API
    if CONFIG["mock"]["enabled"]:
        mock = CONFIG["mock"]
        response = {
            "regime": mock["regime"],
            "trade_allowed": bool(mock["trade_allowed"]),
            "risk_multiplier": float(mock["risk_multiplier"]),
            "reason": str(mock["reason"]),
        }
        ok, err = validate_response(response)
        if not ok:
            log_event({"type": "mock", "symbol": symbol, "valid": False, "error": err})
            return JSONResponse(content=CHAOS_FALLBACK)
        log_event({"type": "mock", "symbol": symbol, "valid": True, "response": response})
        return JSONResponse(content=response)

    # 2. Режим воспроизведения — ответы из записанного журнала
    if CONFIG["replay"]["enabled"]:
        replayed = load_replay_response(symbol)
        if replayed is not None:
            ok, _ = validate_response(replayed)
            if ok:
                log_event({"type": "replay", "symbol": symbol, "valid": True, "response": replayed})
                return JSONResponse(content=replayed)
        log_event({"type": "replay", "symbol": symbol, "valid": False, "error": "нет записанного ответа"})
        return JSONResponse(content=CHAOS_FALLBACK)

    # 3. Свежий кэш (отдельный для каждого символа)
    cached = cache_fresh(symbol)
    if cached is not None:
        _, age = cache_get(symbol)
        log_event({"type": "cache_hit", "symbol": symbol, "cache_age_seconds": int(age), "response": cached})
        return JSONResponse(content=cached)

    # 4. Полный цикл: данные MT5 -> Claude -> проверка -> кэш
    snapshot = market_data.build_market_snapshot(symbol)
    news = get_todays_news()
    response, raw, error = ask_claude(snapshot, news)

    log_event(
        {
            "type": "claude_call",
            "symbol": symbol,
            "mt5_connected": snapshot["mt5_connected"],
            "valid": response is not None,
            "response": response,
            "raw": raw[:2000],
            "error": error,
        }
    )

    if response is not None:
        cache_put(symbol, response)
        return JSONResponse(content=response)

    # Claude не ответил корректно: отдаём старый кэш, если он есть
    # (советник сам решит по возрасту), иначе — безопасный chaos.
    stale, age = cache_get(symbol)
    if stale is not None:
        log_event({"type": "stale_cache", "symbol": symbol, "cache_age_seconds": int(age)})
        return JSONResponse(content=stale)
    return JSONResponse(content=CHAOS_FALLBACK)


if __name__ == "__main__":
    host = CONFIG["server"]["host"]
    port = int(CONFIG["server"]["port"])
    print(f"DualGuard Bridge: http://{host}:{port}  (только локально)")
    uvicorn.run(app, host=host, port=port, log_level="info")
