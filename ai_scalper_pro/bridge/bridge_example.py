"""Мост внешнего сигнала для AI Scalper Pro (вход ExternalSignalURL, см. NewsAI.mqh).

Советник опрашивает локальный HTTP-адрес и получает оттуда
{"direction": "buy"/"sell"/"neutral", "confidence": 0.0..1.0}.

Внутри — простая логика на RSI поверх Twelve Data (https://twelvedata.com,
есть бесплатный тариф). Это ТОЧКА СТАРТА: можно подставить любую свою
модель, код советника трогать не придётся.

Запуск вручную:
    python bridge_example.py

Автозапуск при входе в Windows:
    install\\enable-bridge-autostart.bat

Ключ API берётся из файла .env рядом с этим скриптом (см. .env.example)
или из переменной окружения TWELVEDATA_API_KEY. В код ключ не вписывается.

В MT5:
    Сервис -> Настройки -> Советники -> Разрешить WebRequest
    -> добавить http://127.0.0.1:8787
В инпутах советника (Config.mqh):
    UseExternalSignal = true
    ExternalSignalURL = http://127.0.0.1:8787/signal
"""

import logging
import os
from pathlib import Path

import requests
from flask import Flask, jsonify, request

BASE_DIR = Path(__file__).resolve().parent

HOST = "127.0.0.1"  # только локально, наружу мост не открывается
PORT = 8787
INTERVAL = "15min"
RSI_BUY_BELOW = 30.0
RSI_SELL_ABOVE = 70.0
REQUEST_TIMEOUT = 5


def load_env_file() -> None:
    """Читает .env рядом со скриптом (без внешних зависимостей).

    Переменные, уже заданные в окружении, не перезаписываются.
    """
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()

API_KEY = os.environ.get("TWELVEDATA_API_KEY", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(BASE_DIR / "bridge.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("bridge")

app = Flask(__name__)


def to_twelvedata_symbol(mt5_symbol: str) -> str:
    """MT5 обычно шлёт 'XAUUSD'/'EURUSD' без разделителя, Twelve Data ждёт 'XAU/USD'.

    Суффиксы брокера (EURUSDm, XAUUSD.raw) отбрасываются.
    """
    s = mt5_symbol.upper()
    for suffix in (".RAW", ".PRO", ".ECN", ".A", ".M", "_I", "M", "C", "Z"):
        if not s.endswith(suffix):
            continue
        rest = len(s) - len(suffix)
        # Суффикс из одной буквы отрезаем, только если остаётся ровно 6 символов,
        # иначе PLATINUM превратился бы в PLATINU (проверено тестом).
        if len(suffix) == 1 and rest != 6:
            continue
        if rest < 6:
            continue
        s = s[:rest]
        break
    if len(s) == 6:
        return f"{s[:3]}/{s[3:]}"
    return s


@app.route("/health")
def health():
    """Понятная проверка, что мост жив и ключ на месте."""
    return jsonify(
        {
            "status": "ok",
            "api_key_present": bool(API_KEY),
            "port": PORT,
            "interval": INTERVAL,
        }
    )


@app.route("/signal")
def signal():
    mt5_symbol = request.args.get("symbol", "XAUUSD")
    td_symbol = to_twelvedata_symbol(mt5_symbol)

    if not API_KEY:
        log.warning("Ключ TWELVEDATA_API_KEY не задан — отдаю neutral")
        return jsonify(
            {"direction": "neutral", "confidence": 0.0, "error": "нет ключа API"}
        )

    try:
        r = requests.get(
            "https://api.twelvedata.com/rsi",
            params={"symbol": td_symbol, "interval": INTERVAL, "apikey": API_KEY},
            timeout=REQUEST_TIMEOUT,
        )
        data = r.json()
        rsi = float(data["values"][0]["rsi"])
    except Exception as exc:  # noqa: BLE001
        # Безопасный отказ: советник игнорирует сигнал, если direction=neutral
        log.error("Не удалось получить RSI для %s: %s", td_symbol, exc)
        return jsonify({"direction": "neutral", "confidence": 0.0, "error": str(exc)})

    if rsi < RSI_BUY_BELOW:
        direction = "buy"
        confidence = min(1.0, (RSI_BUY_BELOW - rsi) / RSI_BUY_BELOW)
    elif rsi > RSI_SELL_ABOVE:
        direction = "sell"
        confidence = min(1.0, (rsi - RSI_SELL_ABOVE) / (100 - RSI_SELL_ABOVE))
    else:
        direction = "neutral"
        confidence = 0.0

    log.info("%s: RSI=%.1f -> %s (%.2f)", td_symbol, rsi, direction, confidence)
    return jsonify({"direction": direction, "confidence": round(confidence, 2)})


if __name__ == "__main__":
    log.info("Мост AI Scalper Pro: http://%s:%d (только локально)", HOST, PORT)
    if not API_KEY:
        log.warning("ВНИМАНИЕ: ключ TWELVEDATA_API_KEY не задан — сигнал всегда neutral")
    app.run(host=HOST, port=PORT)
