"""
ai_signal.py — внешний AI-сигнал (Claude/ChatGPT), встроен НАПРЯМУЮ в процесс —
без отдельного Flask-моста и без Twelve Data: рыночные данные (цены/RSI/ADX)
уже есть локально из MT5 (mt5_connector.get_rates_df), поэтому не нужен
дополнительный платный/бесплатный источник котировок.

ЧЕСТНО О ГРАНИЦАХ ПОДХОДА (см. также bridge_ai_market_analysis.py в проекте
"scalper" — та же логика, тут просто без HTTP-моста):
  1) LLM не имеет собственного live-фида — ему ВСЕГДА передаются реальные,
     только что считанные данные (цены/RSI/ADX), а не полагаемся на "память" модели.
  2) Это ДОПОЛНИТЕЛЬНОЕ мнение, не оракул — в score это по-прежнему мягкая
     добавка (AI_SIGNAL_WEIGHT баллов из 100), а не самостоятельная команда.
  3) Кэшируется на AI_SIGNAL_CACHE_SECONDS (по умолчанию 60с) НА КАЖДЫЙ СИМВОЛ
     ОТДЕЛЬНО — значит расход на AI-запросы растёт линейно с числом символов в
     SYMBOLS. Держи это в голове при выборе платного тарифа Anthropic/OpenAI.
  4) Если AI недоступен/ошибся — возвращается "neutral"/confidence=0, вызывающий
     код (main.py) просто не меняет score (fail-open).
"""

import json
import logging
import os
import re
from datetime import datetime

import config as cfg

log = logging.getLogger("ai_signal")

_cache: dict = {}  # symbol -> {"direction":..., "confidence":..., "ts": datetime}


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:
        return {}


def _build_prompt(symbol: str, df) -> str:
    closes = df["close"].tail(20).round(5).tolist()
    rsi_val = round(float(df["rsi"].iloc[-1]), 1)
    adx_val = round(float(df["adx"].iloc[-1]), 1)
    current_price = float(df["close"].iloc[-1])
    recent_high = float(df["high"].tail(20).max())
    recent_low = float(df["low"].tail(20).min())

    return f"""Ты помогаешь скальперской торговой системе оценить краткосрочное
направление рынка. Вот РЕАЛЬНЫЕ данные по инструменту {symbol}:

Текущая цена: {current_price}
Последние 20 цен закрытия: {closes}
Максимум за период: {recent_high}
Минимум за период: {recent_low}
RSI(14): {rsi_val}
ADX(14): {adx_val}

На основе ТОЛЬКО этих данных дай краткосрочную (следующие 15-60 минут) оценку
направления. Ответь СТРОГО в формате JSON, без пояснений вокруг:
{{"direction": "buy" или "sell" или "neutral", "confidence": число от 0.0 до 1.0, "reasoning": "одна короткая фраза"}}

Если данных недостаточно для уверенного вывода — верни "neutral" с низким confidence.
Не давай финансовых советов, это чисто техническая оценка направления для алгоритма."""


def _ai_timeout_seconds() -> float:
    return getattr(cfg, "AI_SIGNAL_TIMEOUT_MS", 3000) / 1000.0


def _ask_claude(prompt: str) -> dict:
    import anthropic
    api_key = cfg.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=api_key, timeout=_ai_timeout_seconds())
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_json(msg.content[0].text)


def _ask_openai(prompt: str) -> dict:
    from openai import OpenAI
    api_key = cfg.OPENAI_API_KEY or os.environ.get("OPENAI_API_KEY", "")
    client = OpenAI(api_key=api_key, timeout=_ai_timeout_seconds())
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return _extract_json(resp.choices[0].message.content)


def fetch_ai_signal(symbol: str, df):
    """Возвращает (ok: bool, direction: str, confidence: float). Кэшируется по символу."""
    if not cfg.USE_AI_SIGNAL:
        return False, "neutral", 0.0

    cached = _cache.get(symbol)
    if cached and (datetime.now() - cached["ts"]).total_seconds() < cfg.AI_SIGNAL_CACHE_SECONDS:
        return cached["ok"], cached["direction"], cached["confidence"]

    try:
        prompt = _build_prompt(symbol, df)
        result = _ask_claude(prompt) if cfg.AI_PROVIDER == "claude" else _ask_openai(prompt)
        direction = result.get("direction", "neutral")
        if direction not in ("buy", "sell", "neutral"):
            direction = "neutral"
        confidence = max(0.0, min(1.0, float(result.get("confidence", 0))))
        ok = True
    except Exception as e:
        log.warning("AI-сигнал (%s): ошибка %s — считаю neutral (fail-open)", symbol, e)
        direction, confidence, ok = "neutral", 0.0, False

    _cache[symbol] = {"ok": ok, "direction": direction, "confidence": confidence, "ts": datetime.now()}
    return ok, direction, confidence


def apply_ai_signal(score: float, direction: int, ext_ok: bool, ext_direction: str,
                     ext_confidence: float, effective_weight: float) -> float:
    if not cfg.USE_AI_SIGNAL or not ext_ok:
        return score
    want = "buy" if direction == 1 else "sell"
    if cfg.AI_SIGNAL_REQUIRE_DIRECTION and ext_direction != want:
        return 0.0
    if ext_direction == want:
        delta = effective_weight * ext_confidence
    elif ext_direction == "neutral":
        delta = 0.0
    else:
        delta = -effective_weight * ext_confidence
    return max(0.0, min(100.0, score + delta))


def effective_ai_weight(sym_state) -> float:
    """Тот же принцип, что и в MQL5 v8.0: больше веса во флэте, меньше в тренде."""
    w = cfg.AI_SIGNAL_WEIGHT
    if not cfg.USE_MARKET_REGIME_FILTER:
        return w
    if sym_state.current_regime == "range":
        w *= 1.3
    elif sym_state.current_regime == "trend":
        w *= 0.85
    return w
