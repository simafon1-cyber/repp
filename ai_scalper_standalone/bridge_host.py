"""
bridge_host.py — мост для советников MQL5 ВНУТРИ программы.

ЗАЧЕМ
Советники (DualGuard EA, AI Scalper Pro) спрашивают у моста режим рынка по
HTTP. Раньше мост был отдельной программой на Python: своя папка, свои
зависимости (fastapi, uvicorn), отдельная установка и отдельный запуск.
Теперь он живёт прямо здесь: у программы уже есть и связь с MT5, и клиент
Claude, и всё это едет внутри .exe. Ставить и запускать отдельно нечего.

ПОЧЕМУ ВСТРОЕННЫЙ http.server, А НЕ FASTAPI
Эндпоинта всего два. Ради них тянуть fastapi + uvicorn в сборку — это
десятки мегабайт и лишние зависимости, которые пришлось бы ставить
пользователю. http.server входит в Python и умеет ровно то, что нужно.

БЕЗОПАСНОСТЬ
Слушаем ТОЛЬКО 127.0.0.1 — наружу мост не открывается никогда, адрес
привязки не настраивается. Это требование из исходного задания.

ЧТО МОСТ МОЖЕТ И ЧЕГО НЕ МОЖЕТ
Он отдаёт советнику ОГРАНИЧИТЕЛЬ: risk_multiplier в диапазоне 0.0–1.0,
которым советник может только УМЕНЬШИТЬ рассчитанный объём, и признак
trade_allowed. Увеличить риск, расширить стоп или отменить лимит ответ моста
не может — ни здесь, ни на стороне советника.
"""

import json
import logging
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import config as cfg

log = logging.getLogger("bridge_host")

# Адрес прибит намеренно: наружу мост не открывается ни при какой настройке.
HOST = "127.0.0.1"

_server = None
_thread = None
_cache: dict = {}          # symbol -> {"data": ..., "ts": datetime}
_status = {"running": False, "detail": "не запущен", "requests": 0, "last": None}

# Ответ, когда данных нет или они не прошли проверку. Безопасный: торговать
# нельзя, множитель риска ноль.
CHAOS = {
    "regime": "chaos",
    "trade_allowed": False,
    "risk_multiplier": 0.0,
    "reason": "мост не смог получить корректный ответ — безопасный режим",
}


def enabled() -> bool:
    return bool(getattr(cfg, "BRIDGE_ENABLED", False))


def port() -> int:
    try:
        return int(getattr(cfg, "BRIDGE_PORT", 8080))
    except (TypeError, ValueError):
        return 8080


def cache_ttl_minutes() -> int:
    try:
        return int(getattr(cfg, "BRIDGE_CACHE_TTL_MIN", 45))
    except (TypeError, ValueError):
        return 45


def status() -> dict:
    return dict(_status)


# =====================================================================
# ПРОВЕРКА ОТВЕТА
# =====================================================================
VALID_REGIMES = ("trend", "range", "chaos", "news")


def validate_response(raw) -> dict:
    """Приводит ответ модели к строгому виду или возвращает CHAOS.

    Проверяем программно, а не «доверяем ответу»: модель может вернуть текст,
    лишние поля, число вне диапазона или строку вместо числа. Любое
    несоответствие — безопасный режим, а не попытка угадать смысл."""
    if not isinstance(raw, dict):
        return dict(CHAOS)

    regime = str(raw.get("regime", "")).strip().lower()
    if regime not in VALID_REGIMES:
        return dict(CHAOS)

    trade_allowed = raw.get("trade_allowed")
    if not isinstance(trade_allowed, bool):
        return dict(CHAOS)

    try:
        multiplier = float(raw.get("risk_multiplier"))
    except (TypeError, ValueError):
        return dict(CHAOS)
    if multiplier != multiplier:            # NaN не равен сам себе
        return dict(CHAOS)
    # Зажимаем в 0..1: множитель может только УМЕНЬШАТЬ объём.
    multiplier = max(0.0, min(1.0, multiplier))

    if not trade_allowed:
        multiplier = 0.0

    reason = str(raw.get("reason", ""))[:300]
    return {
        "regime": regime,
        "trade_allowed": trade_allowed,
        "risk_multiplier": multiplier,
        "reason": reason,
    }


# =====================================================================
# ДАННЫЕ
# =====================================================================
def _build_prompt(symbol: str, snapshot: dict) -> str:
    return (
        "Ты — риск-ограничитель торгового робота. Оцени режим рынка.\n"
        f"Инструмент: {symbol}\n"
        f"Данные: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
        "Ответь СТРОГО одним JSON без пояснений:\n"
        '{"regime":"trend|range|chaos|news","trade_allowed":true|false,'
        '"risk_multiplier":0.0-1.0,"reason":"кратко по-русски"}\n'
        "risk_multiplier может только УМЕНЬШАТЬ риск: 1.0 — без снижения, "
        "0.0 — не торговать."
    )


def _market_snapshot(symbol: str) -> dict:
    """Короткая сводка по инструменту для модели. Чего нет — того нет:
    выдумывать недостающие данные нельзя, иначе решение принимается по
    выдуманному."""
    snapshot = {"symbol": symbol}
    try:
        import mt5_connector as mt5c
        df = mt5c.get_rates_df(symbol, "H1", count=50)
        if df is not None and len(df) >= 10:
            closes = [float(x) for x in df["close"].tail(20)]
            snapshot["last"] = closes[-1]
            snapshot["change_20h_pct"] = round(
                (closes[-1] - closes[0]) / closes[0] * 100, 3) if closes[0] else None
            snapshot["high_20h"] = float(df["high"].tail(20).max())
            snapshot["low_20h"] = float(df["low"].tail(20).min())
        else:
            snapshot["bars"] = "недоступны"
        spread = mt5c.get_spread_points(symbol)
        snapshot["spread_points"] = spread if spread else "недоступен"
    except Exception as e:
        snapshot["error"] = f"данные MT5 недоступны: {e}"
    return snapshot


def _ask_model(symbol: str) -> dict:
    """Спрашивает Claude. Любая ошибка — CHAOS, а не догадка."""
    try:
        import ai_signal
        prompt = _build_prompt(symbol, _market_snapshot(symbol))
        raw = ai_signal._ask_claude(prompt)
        return validate_response(raw)
    except Exception as e:
        log.warning("Мост: не удалось получить ответ модели по %s: %s", symbol, e)
        return dict(CHAOS)


def regime_for(symbol: str, now: datetime = None) -> dict:
    """Режим рынка с кэшем по символу.

    Кэш отдельный на каждый инструмент: у золота и евро режимы разные, и
    отдавать один ответ на оба — значит принимать решение по чужому рынку."""
    now = now or datetime.now()
    symbol = (symbol or "").strip().upper()
    if not symbol:
        return dict(CHAOS)

    cached = _cache.get(symbol)
    if cached and (now - cached["ts"]) < timedelta(minutes=cache_ttl_minutes()):
        return dict(cached["data"])

    data = _ask_model(symbol)
    _cache[symbol] = {"data": data, "ts": now}
    return dict(data)


def clear_cache():
    _cache.clear()


# =====================================================================
# HTTP
# =====================================================================
class _Handler(BaseHTTPRequestHandler):
    server_version = "AIScalperBridge/1.0"

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                       # noqa: N802 — имя задано http.server
        parsed = urlparse(self.path)
        _status["requests"] += 1
        _status["last"] = datetime.now()

        if parsed.path == "/health":
            self._send(200, {
                "status": "ok",
                "source": "встроенный мост программы",
                "cache_ttl_minutes": cache_ttl_minutes(),
                "api_key_present": bool(getattr(cfg, "ANTHROPIC_API_KEY", "")),
                "requests": _status["requests"],
            })
            return

        if parsed.path == "/regime":
            symbol = (parse_qs(parsed.query).get("symbol") or [""])[0]
            if not symbol:
                self._send(400, {"error": "не указан symbol"})
                return
            self._send(200, regime_for(symbol))
            return

        self._send(404, {"error": "неизвестный адрес"})

    def log_message(self, fmt, *args):
        # По умолчанию http.server печатает в stderr — в оконной программе
        # это никуда не идёт. Пишем в общий журнал.
        log.debug("Мост: " + fmt, *args)


def start() -> str:
    """Поднимает мост. Возвращает "" при успехе или причину отказа."""
    global _server, _thread

    if _thread is not None and _thread.is_alive():
        return ""
    if not enabled():
        _status.update(running=False, detail="Мост выключен в настройках.")
        return _status["detail"]

    try:
        _server = ThreadingHTTPServer((HOST, port()), _Handler)
    except OSError as e:
        # Самая частая причина — порт занят старым отдельным мостом
        detail = (f"Не удалось занять {HOST}:{port()} ({e}). "
                  f"Возможно, уже запущен отдельный мост — закройте его или "
                  f"смените порт в настройках.")
        _status.update(running=False, detail=detail)
        log.warning("Мост: %s", detail)
        return detail

    _thread = threading.Thread(target=_server.serve_forever, daemon=True,
                               name="bridge-host")
    _thread.start()
    _status.update(running=True, detail=f"Работает на http://{HOST}:{port()}")
    log.info("Мост запущен: http://%s:%d", HOST, port())
    return ""


def stop():
    global _server, _thread
    if _server is not None:
        try:
            _server.shutdown()
            _server.server_close()
        except Exception:
            pass
    _server = None
    _thread = None
    _status.update(running=False, detail="Остановлен")


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()
