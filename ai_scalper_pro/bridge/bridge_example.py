"""
Пример моста для AI Scalper Pro (вход ExternalSignalURL, см. NewsAI.mqh).

Зачем: EA уже умеет опрашивать локальный HTTP-адрес и получать оттуда
{"direction": "buy"/"sell"/"neutral", "confidence": 0.0..1.0}. Этот скрипт —
рабочий пример такого моста поверх Twelve Data (https://twelvedata.com) —
у сервиса есть бесплатный тариф и он покрывает форекс, крипту и индексы,
в отличие от парсинга investing.com (там нет официального API, сайт
блокирует ботов, а его правила прямо запрещают автоматический сбор данных —
поэтому в EA такой вариант не встроен).

Логика внутри — простая (RSI на 15м), это ТОЧКА СТАРТА. Смысл моста в том,
что можно подставить сюда любую свою модель/AI/индикатор — код EA трогать
не придётся, он просто читает JSON с этого адреса.

Установка:
    pip install flask requests

Запуск:
    export TWELVEDATA_API_KEY=твой_ключ   (или впиши в переменную ниже)
    python bridge_example.py

В MT5:
    Tools -> Options -> Expert Advisors -> Allow WebRequest for listed URL
    -> добавить http://127.0.0.1:8787

В инпутах EA (Config.mqh):
    UseExternalSignal = true
    ExternalSignalURL = http://127.0.0.1:8787/signal
"""

import os
from flask import Flask, request, jsonify
import requests

API_KEY = os.environ.get("TWELVEDATA_API_KEY", "ВСТАВЬ_СВОЙ_КЛЮЧ_ЗДЕСЬ")
app = Flask(__name__)


def to_twelvedata_symbol(mt5_symbol: str) -> str:
    """MT5 обычно шлёт 'XAUUSD'/'EURUSD'/'BTCUSD' без разделителя,
    Twelve Data ждёт 'XAU/USD'. Работает для стандартных 6-8-буквенных тикеров;
    у нестандартных суффиксов брокера (напр. 'EURUSDm') подправь вручную."""
    s = mt5_symbol.upper()
    for suffix in (".raw", ".m", "m", "_i"):
        if s.endswith(suffix.upper()):
            s = s[: -len(suffix)]
    if len(s) == 6:
        return f"{s[:3]}/{s[3:]}"
    return s


@app.route("/signal")
def signal():
    mt5_symbol = request.args.get("symbol", "XAUUSD")
    td_symbol = to_twelvedata_symbol(mt5_symbol)

    try:
        r = requests.get(
            "https://api.twelvedata.com/rsi",
            params={"symbol": td_symbol, "interval": "15min", "apikey": API_KEY},
            timeout=5,
        )
        data = r.json()
        rsi = float(data["values"][0]["rsi"])
    except Exception as e:
        # fail-open: EA игнорирует сигнал, если direction не пришёл валидным
        return jsonify({"direction": "neutral", "confidence": 0.0, "error": str(e)})

    if rsi < 30:
        direction, confidence = "buy", min(1.0, (30 - rsi) / 30)
    elif rsi > 70:
        direction, confidence = "sell", min(1.0, (rsi - 70) / 30)
    else:
        direction, confidence = "neutral", 0.0

    return jsonify({"direction": direction, "confidence": round(confidence, 2)})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8787)
